from __future__ import annotations

from pathlib import Path

import pytest

from core.contract import evaluate_state_file_friction
from core.destructive_guard import evaluate_r2_destructive_gate, parse_destructive_command
from core.ledger import JsonObject


@pytest.mark.parametrize(
    "command",
    [
        "env rm -rf /",
        "time rm -rf /",
        "sudo rm -rf /",
        "nohup rm -rf /",
        "xargs rm -rf /",
        "timeout 5 rm -rf /",
        "doas rm -rf /",
        "nice -n 5 rm -rf /",
        "ionice -c 3 rm -rf /",
        "stdbuf -oL rm -rf /",
        "setsid rm -rf /",
    ],
)
def test_r2_blocks_passthrough_wrapper_attacks(command: str) -> None:
    # Given: a destructive command is hidden behind a known passthrough wrapper.
    # When: R2 parses the complete wrapper remainder.
    parsed = parse_destructive_command(command)

    # Then: implicit root deletion remains fail-closed.
    assert parsed is not None
    assert parsed.resolved is False


@pytest.mark.parametrize(
    "command",
    [
        'echo "hack" 1> target.py',
        'echo "hack" 2> target.py',
        'echo "hack" &> target.py',
        'echo "hack" >| target.py',
        'echo "hack" 1>| target.py',
        'echo "hack" >& target.py',
    ],
)
def test_r2_detects_truncating_redirect_variants(command: str) -> None:
    # Given: a shell truncation uses fd, merge, or force syntax.
    # When: R2 parses the redirect.
    parsed = parse_destructive_command(command)

    # Then: the static target is attributed instead of bypassing R2.
    assert parsed is not None
    assert parsed.resolved is True
    assert parsed.targets == ("target.py",)


@pytest.mark.parametrize(
    "command",
    [
        'echo "safe" >> target.py',
        'echo "safe" 1>> target.py',
        'echo "safe" &>> target.py',
    ],
)
def test_r2_keeps_append_redirects_out_of_truncate_category(command: str) -> None:
    # Given: the command only appends and does not truncate.
    # When/Then: the truncate detector stays out of scope by contract.
    assert parse_destructive_command(command) is None


@pytest.mark.parametrize(
    "command",
    [
        'echo "1> target.py"',
        'echo "literal;$runner"',
        'env echo "rm -rf /"',
    ],
)
def test_r2_does_not_treat_quoted_or_argument_data_as_execution(command: str) -> None:
    # Given: destructive-looking text is quoted data or an argument to a benign wrapped command.
    # When/Then: R2 does not reinterpret data as executable shell syntax.
    assert parse_destructive_command(command) is None


def test_r2_blocks_variable_assembled_command_head() -> None:
    # Given: shell variables assemble the executable name at a segment head.
    # When: the command is parsed without runtime variable values.
    parsed = parse_destructive_command("a=r; b=m; $a$b -rf /")

    # Then: R2 treats the dynamic command head as parse-unable.
    assert parsed is not None
    assert parsed.resolved is False
    assert parsed.reason == "parse_unable_dynamic_command"


def test_state_file_friction_blocks_legacy_contract_shell_write(tmp_path: Path) -> None:
    # Given: a shell command attempts to author the legacy contract directly.
    payload = {
        "project_root": str(tmp_path),
        "tool_name": "Bash",
        "command": (
            "echo '{\"restated_goal\":\"hack\",\"acceptance\":[\"x\"],"
            "\"evidence\":[\"x\"]}' > .fable-lite/contract.json"
        ),
    }

    # When: state-file friction evaluates the shell write.
    result = evaluate_state_file_friction(payload)

    # Then: contract authoring is not exempt outside Edit-family tools.
    assert result["decision"] == "block"


@pytest.mark.parametrize("target", ["../../outside.py", "../sibling/x.py"])
def test_r2_skips_out_of_root_targets_as_non_attributable(
    tmp_path: Path,
    target: str,
) -> None:
    # Given: a destructive target resolves outside the project root (parent traversal that
    # does NOT return inside root — contrast with the in-root traversal test below).
    looked_up: list[str] = []

    def lookup(_ledger: JsonObject, canonical_path: str) -> None:
        looked_up.append(canonical_path)

    # When: R2 evaluates the command.
    result = evaluate_r2_destructive_gate(
        {
            "project_root": str(tmp_path),
            "tool_name": "Bash",
            "command": f"rm {target}",
            "host": "claude_code",
            "agent": "claude",
            "session_id": "s1",
        },
        lookup_path_attribution=lookup,
        attribution_health=lambda _ledger: {
            "degraded": False,
            "capacity_exceeded": False,
        },
    )

    # Then: out-of-root paths are not in this project's path_attribution, so they are
    # not attributable to a peer and R2 has no basis to block — the target is skipped
    # (no attribution lookup) and the command passes. Protecting other projects/system
    # files is out of R2's scope (design §9).
    assert result["decision"] == "allow"
    assert looked_up == []


def test_r2_still_blocks_traversal_that_resolves_inside_root(tmp_path: Path) -> None:
    # Given: a traversal target (src/../peer.py) resolves back INSIDE the project root
    # and that in-root path is owned by an unsettled peer.
    def lookup(_ledger: JsonObject, canonical_path: str) -> JsonObject:
        return {
            "generation": 1,
            "status": "exclusive",
            "owners": [{"agent_key": "codex_cli:other:codex", "settled": False}],
        }

    # When: R2 evaluates the destructive command.
    result = evaluate_r2_destructive_gate(
        {
            "project_root": str(tmp_path),
            "tool_name": "Bash",
            "command": "rm src/../peer.py",
            "host": "claude_code",
            "agent": "claude",
            "session_id": "s1",
        },
        lookup_path_attribution=lookup,
        attribution_health=lambda _ledger: {
            "degraded": False,
            "capacity_exceeded": False,
        },
    )

    # Then: canonicalization happens AFTER resolve, so the traversal cannot launder an
    # in-root peer-owned file into a skip — it is still blocked.
    assert result["decision"] == "block"


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf .fable-lite/agents/peer.jsonl",
        "git checkout -- .fable-lite/",
        "rm .fable-lite/ledger.json",
    ],
)
def test_r2_hard_blocks_state_dir_destruction(tmp_path: Path, command: str) -> None:
    # Given: a destructive command targets the .fable-lite provenance/audit state dir,
    # which is never in path_attribution (so ownership lookup would return None).
    def lookup(_ledger: JsonObject, _canonical: str) -> None:
        return None

    result = evaluate_r2_destructive_gate(
        {
            "project_root": str(tmp_path),
            "tool_name": "Bash",
            "command": command,
            "host": "claude_code",
            "agent": "claude",
            "session_id": "s1",
        },
        lookup_path_attribution=lookup,
        attribution_health=lambda _l: {"degraded": False, "capacity_exceeded": False},
    )

    # Then: the state dir is hard-blocked regardless of attribution ownership (agy Critical).
    assert result["decision"] == "block"
    assert "state_dir" in str(result["reason"])


def test_r2_fail_closed_when_resolve_raises_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a target whose Path.resolve() raises OSError (circular symlink / MAX_PATH).
    import core.destructive_guard as guard

    real_resolve = Path.resolve

    def fake_resolve(self: Path, *args: object, **kwargs: object) -> Path:
        if "peer" in str(self):
            raise OSError("simulated unresolvable path")
        return real_resolve(self)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    def lookup(_ledger: JsonObject, _canonical: str) -> JsonObject:
        return {
            "generation": 1,
            "status": "exclusive",
            "owners": [{"agent_key": "codex_cli:other:codex", "settled": False}],
        }

    result = guard.evaluate_r2_destructive_gate(
        {
            "project_root": str(tmp_path),
            "tool_name": "Bash",
            "command": "rm peer.py",
            "host": "claude_code",
            "agent": "claude",
            "session_id": "s1",
        },
        lookup_path_attribution=lookup,
        attribution_health=lambda _l: {"degraded": False, "capacity_exceeded": False},
    )

    # Then: an unresolvable path is fail-closed, not skipped — deliberate OSError cannot
    # launder an in-root peer file past R2 (agy High).
    assert result["decision"] == "block"
    assert "canonicalization_unavailable" in str(result["reason"])
