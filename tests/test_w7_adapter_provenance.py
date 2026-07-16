from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from adapters.antigravity.hook_common import canonical_invocation as antigravity_invocation
from adapters.antigravity.tool_io import extract_command, extract_paths_from_input, extract_tool_info, verification_result
from adapters.claude_code.common import canonical_invocation as claude_invocation
from adapters.claude_code.common import tool_command as claude_command
from adapters.claude_code.common import tool_file_paths as claude_paths
from adapters.claude_code.common import tool_output as claude_output
from adapters.claude_code.common import tool_success as claude_success
from adapters.codex_cli.common import canonical_invocation as codex_invocation
from adapters.codex_cli.common import tool_command as codex_command
from adapters.codex_cli.common import tool_file_paths as codex_paths
from adapters.codex_cli.common import tool_output as codex_output
from adapters.codex_cli.common import tool_success as codex_success
from core.ledger import JsonObject, JsonValue, load_ledger, record_event
from core.verification_covers import active_turn
from core.provenance_types import (
    DEFAULT_MAX_SCAN_BYTES,
    ProvenanceReason,
    ProvenanceStatus,
)
from core.shell_hints import shell_candidate_paths


ROOT = Path(__file__).resolve().parents[1]
CLAUDE = ROOT / "adapters" / "claude_code"
CODEX = ROOT / "adapters" / "codex_cli"
ANTIGRAVITY = ROOT / "adapters" / "antigravity" / "oma_hook.py"
CONFORMANCE = ROOT / "tests" / "fixtures" / "v2-provenance" / "adapter-conformance.json"


def _run(script: Path, payload: JsonObject, event: str | None = None) -> JsonObject:
    command = [sys.executable, str(script)]
    if event:
        command.append(event)
    process = subprocess.run(
        command,
        input=json.dumps(payload, ensure_ascii=False),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.returncode == 0, process.stderr
    result: JsonValue = json.loads(process.stdout or "{}")
    assert isinstance(result, dict)
    return result


def _claude_prompt(root: Path, prompt: str = "app.py만 수정해줘") -> None:
    _ = _run(
        CLAUDE / "user_prompt_submit.py",
        {
            "cwd": str(root),
            "prompt": prompt,
            "session_id": "session",
        },
    )


def _post_edit(root: Path) -> JsonObject:
    return _run(
        CLAUDE / "post_tool_use.py",
        {
            "cwd": str(root),
            "tool_name": "Edit",
            "tool_input": {"file_path": "app.py"},
            "tool_response": {"filePath": "app.py"},
            "session_id": "session",
            "tool_use_id": "edit-1",
        },
    )


def test_edit_hint_without_filesystem_delta_records_no_change(tmp_path: Path) -> None:
    # Given: a v2 turn with no physical file mutation after its baseline.
    _claude_prompt(tmp_path)

    # When: Claude Code reports an Edit tool solely through its structured hint.
    _ = _post_edit(tmp_path)

    # Then: provenance records no change event from the hint alone.
    ledger = load_ledger({"project_root": str(tmp_path)})
    assert ledger["changed_files_seen"] == []


def test_unrecognized_shell_write_is_found_by_stop_full_reconcile(tmp_path: Path) -> None:
    # Given: a shell command will alter a file after the prompt baseline.
    target = tmp_path / "app.py"
    target.write_text("before", encoding="utf-8")
    _claude_prompt(tmp_path, "app.py에 계산 페이지를 만들어줘")
    target.write_text("after", encoding="utf-8")
    assert shell_candidate_paths("opaque-shell-writer") == ()
    _ = _run(
        CLAUDE / "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Bash",
            "tool_input": {"command": "opaque-shell-writer"},
            "tool_response": {"exit_code": 0, "stdout": "done"},
            "session_id": "session",
            "tool_use_id": "shell-1",
        },
    )

    # When: Stop runs its mandatory full provenance reconciliation.
    result = _run(
        CLAUDE / "stop.py",
        {"cwd": str(tmp_path), "session_id": "session", "stop_hook_active": False},
    )

    # Then: the unverified physical write blocks completion.
    assert result["decision"] == "block"


def _semantic_projection(ledger: JsonObject) -> tuple[list[JsonValue], list[JsonValue]]:
    changed = ledger["changed_files_seen"]
    kinds = ledger["change_kinds"]
    assert isinstance(changed, list)
    assert isinstance(kinds, list)
    return changed, kinds


def test_three_adapter_replay_observes_one_equivalent_filesystem_change(tmp_path: Path) -> None:
    # Given: each real host payload describes the same completed edit and bytes.
    roots = {name: tmp_path / name for name in ("claude", "codex", "antigravity")}
    for root in roots.values():
        root.mkdir()
        (root / "app.py").write_text("before", encoding="utf-8")

    _claude_prompt(roots["claude"])
    _ = _run(
        CODEX / "user_prompt_submit.py",
        {"cwd": str(roots["codex"]), "prompt": "app.py만 수정해줘", "session_id": "session"},
    )
    _ = _run(
        ANTIGRAVITY,
        {"cwd": str(roots["antigravity"]), "prompt": "app.py만 수정해줘", "session_id": "session"},
        "BeforeModel",
    )
    for root in roots.values():
        (root / "app.py").write_text("after", encoding="utf-8")

    _ = _post_edit(roots["claude"])
    _ = _run(
        CODEX / "post_tool_use.py",
        {
            "cwd": str(roots["codex"]),
            "tool_name": "Edit",
            "tool_input": {"file_path": "app.py"},
            "session_id": "session",
            "tool_use_id": "edit-1",
        },
    )
    _ = _run(
        ANTIGRAVITY,
        {
            "cwd": str(roots["antigravity"]),
            "metadata": {"tool_name": "replace_file_content", "tool_input": {"TargetFile": "app.py"}},
            "session_id": "session",
            "tool_use_id": "edit-1",
        },
        "AfterTool",
    )

    projections = [
        _semantic_projection(load_ledger({"project_root": str(root)}))
        for root in roots.values()
    ]

    # Then: all adapters replay the same observed change semantics.
    assert projections == [(["app.py"], ["code"])] * 3


def test_host_payload_fixture_normalizes_to_canonical_invocation_dict() -> None:
    # Given: three real host payload shapes for one completed edit invocation.
    fixture = json.loads(CONFORMANCE.read_text(encoding="utf-8"))
    expected = fixture["expected"]
    payloads = fixture["host_payloads"]
    assert isinstance(expected, dict)
    assert isinstance(payloads, dict)

    claude_payload = payloads["claude_code"]
    codex_payload = payloads["codex_cli"]
    antigravity_payload = payloads["antigravity"]
    assert isinstance(claude_payload, dict)
    assert isinstance(codex_payload, dict)
    assert isinstance(antigravity_payload, dict)
    tool_name, tool_input = extract_tool_info(antigravity_payload)
    ant_success, ant_evidence = verification_result(antigravity_payload)
    invocations = (
        claude_invocation(
            claude_payload, "post_tool", "edit", claude_paths(claude_payload),
            claude_command(claude_payload), claude_success(claude_payload), claude_output(claude_payload),
        ),
        codex_invocation(
            codex_payload, "post_tool", "edit", codex_paths(codex_payload),
            codex_command(codex_payload), codex_success(codex_payload), codex_output(codex_payload),
        ),
        antigravity_invocation(
            antigravity_payload, "post_tool", "edit", extract_paths_from_input(tool_input),
            extract_command(tool_input), ant_success, ant_evidence,
        ),
    )

    # When: adapters expose their invocation to the shared core boundary.
    normalized = [invocation.as_dict() for invocation in invocations]

    # Then: only host identity differs; every canonical contract field is shared.
    assert tool_name == "Edit"
    assert all(list(value) == fixture["canonical_fields"] for value in normalized)
    assert [value.pop("host") for value in normalized] == ["claude_code", "codex_cli", "antigravity"]
    assert normalized == [expected] * 3


def test_agentless_stop_keeps_unmigrated_v1_ledger_semantics(tmp_path: Path) -> None:
    # Given: an unmigrated deep v1 ledger with an unverified code change.
    legacy = (ROOT / "tests" / "fixtures" / "v2-provenance" / "v1-ledger.json").read_text(encoding="utf-8")
    ledger_path = tmp_path / ".fable-lite" / "ledger.json"
    ledger_path.parent.mkdir()
    ledger_path.write_text(legacy, encoding="utf-8")

    # When: a legacy Claude Stop payload has no agent identity.
    result = _run(CLAUDE / "stop.py", {"cwd": str(tmp_path), "session_id": "legacy"})

    # Then: the pre-v2 Stop decision remains a verification block without migration.
    assert result["decision"] == "block"
    assert "schema_version" not in load_ledger({"project_root": str(tmp_path)})


def test_observation_failure_is_incomplete_without_raising(tmp_path: Path) -> None:
    # Given: a prompt turn exists before a transient provenance scan failure.
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "agent": "claude",
            "host": "claude_code",
            "session_id": "session",
            "turn_id": "turn-session",
            "prompt": "app.py만 수정해줘",
        }
    )
    from core.adapter_observation import CanonicalInvocation, observe_post_tool

    invocation = CanonicalInvocation(
        "claude_code", "claude", "session", "turn-session", "edit-1", "post_tool", "edit", ("app.py",), "", True, "",
    )

    # When: the observation lifecycle raises an expected filesystem error.
    with patch("core.adapter_observation.ProvenanceLifecycle", side_effect=OSError("injected")):
        result = observe_post_tool(tmp_path, invocation)

    # Then: callers receive incomplete state instead of a hook-crashing exception.
    assert result.incomplete is True
    assert result.status_reason is ProvenanceReason.OBSERVATION_ERROR


def test_adapter_start_reports_scope_too_large_before_hashing_oversized_root(
    tmp_path: Path,
) -> None:
    from core.adapter_observation import CanonicalInvocation, start_turn

    oversized = tmp_path / "oversized.bin"
    with oversized.open("wb") as handle:
        handle.truncate(DEFAULT_MAX_SCAN_BYTES + 1)
    invocation = CanonicalInvocation(
        "claude_code",
        "claude",
        "session",
        "turn-session",
        "turn-start",
        "turn_start",
        "other",
        (),
        "",
        True,
        "",
    )

    report = start_turn(tmp_path, invocation)

    assert report.status is ProvenanceStatus.SCOPE_TOO_LARGE
    assert report.status_reason == "byte_limit"
    assert report.incomplete is False
    assert report.snapshot_id == ""
    assert report.baseline_snapshot_id == ""
    assert (
        tmp_path / ".fable-lite" / "snapshots" / "workspace-current.json"
    ).exists() is False


def test_incomplete_adapter_start_never_exposes_phantom_snapshot_ids(
    tmp_path: Path,
) -> None:
    from core.adapter_observation import CanonicalInvocation, start_turn
    from core.provenance_store import SnapshotStoreError, workspace_current_path

    (tmp_path / "app.py").write_text("stable", encoding="utf-8")
    invocation = CanonicalInvocation(
        "codex_cli",
        "codex",
        "session",
        "turn-session",
        "turn-start",
        "turn_start",
        "other",
        (),
        "",
        True,
        "",
    )
    error = SnapshotStoreError(workspace_current_path(tmp_path), "injected")

    with patch(
        "core.provenance_manifest.save_turn_baseline_from_current",
        side_effect=error,
    ):
        report = start_turn(tmp_path, invocation)

    assert report.incomplete is True
    assert report.status_reason is ProvenanceReason.STORE_WRITE_ERROR
    assert report.snapshot_id == ""
    assert report.baseline_snapshot_id == ""


def test_pretool_edit_marks_scope_too_large_turn_mutation_capable(
    tmp_path: Path,
) -> None:
    from core.adapter_observation import CanonicalInvocation, begin_invocation

    payload = {
        "project_root": str(tmp_path),
        "event": "prompt",
        "host": "codex_cli",
        "agent": "codex",
        "session_id": "session",
        "turn_id": "turn-session",
        "prompt": "app.py 수정",
        "provenance_incomplete": False,
        "provenance_status": ProvenanceStatus.SCOPE_TOO_LARGE.value,
        "provenance_status_reason": ProvenanceReason.ENTRY_LIMIT.value,
    }
    _ = record_event(payload)
    invocation = CanonicalInvocation(
        "codex_cli",
        "codex",
        "session",
        "turn-session",
        "edit-pre",
        "pre_tool",
        "edit",
        ("app.py",),
        "",
        True,
        "",
    )

    report = begin_invocation(tmp_path, invocation)
    turn = active_turn(load_ledger({"project_root": str(tmp_path)}), payload)

    assert report.status is ProvenanceStatus.SCOPE_TOO_LARGE
    assert turn is not None
    assert turn["provenance_mutation_capable"] is True


def test_adapter_records_shell_effect_and_remote_target_epochs(tmp_path: Path) -> None:
    from core.adapter_observation import (
        CanonicalInvocation,
        ObservationReport,
        _record_status,
    )

    base = {
        "project_root": str(tmp_path),
        "event": "prompt",
        "host": "codex_cli",
        "agent": "codex",
        "session_id": "session",
        "turn_id": "turn-session",
        "prompt": "work",
    }
    _ = record_event(base)
    report = ObservationReport("snapshot", "baseline", (), False, False)

    def invocation(invocation_id: str, family: str, command: str) -> CanonicalInvocation:
        return CanonicalInvocation(
            "codex_cli",
            "codex",
            "session",
            "turn-session",
            invocation_id,
            "post_tool",
            family,
            (),
            command,
            True,
            "",
        )

    _record_status(
        tmp_path, invocation("read", "shell", "rg --no-config provenance core"), report
    )
    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, base)
    assert turn is not None
    assert turn.get("provenance_mutation_capable") is not True
    assert turn.get("remote_mutation_epochs") in (None, {})

    _record_status(
        tmp_path,
        invocation(
            "remote",
            "shell",
            'ssh -F none -o StrictHostKeyChecking=yes deploy@host "touch marker"',
        ),
        report,
    )
    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, base)
    assert turn is not None
    assert turn.get("provenance_mutation_capable") is not True
    assert turn["remote_mutation_epochs"] == {
        "ssh://deploy@host:22": ledger["event_seq"]
    }

    _record_status(tmp_path, invocation("unknown", "shell", "opaque-writer"), report)
    ledger = load_ledger({"project_root": str(tmp_path)})
    turn = active_turn(ledger, base)
    assert turn is not None
    assert turn["provenance_mutation_capable"] is True


def test_plain_git_status_remains_local_even_without_exec_config(
    tmp_path: Path,
) -> None:
    from core.adapter_observation import (
        CanonicalInvocation,
        ObservationReport,
        _record_status,
    )

    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    report = ObservationReport("snapshot", "baseline", (), False, False)

    def observe(session_id: str) -> JsonObject:
        payload: JsonObject = {
            "project_root": str(tmp_path),
            "event": "prompt",
            "host": "codex_cli",
            "agent": "codex",
            "session_id": session_id,
            "turn_id": f"turn-{session_id}",
            "prompt": "boot",
        }
        _ = record_event(payload)
        invocation = CanonicalInvocation(
            "codex_cli",
            "codex",
            session_id,
            f"turn-{session_id}",
            f"status-{session_id}",
            "post_tool",
            "shell",
            (),
            "git status --short",
            True,
            "",
        )
        _record_status(tmp_path, invocation, report)
        turn = active_turn(load_ledger({"project_root": str(tmp_path)}), payload)
        assert turn is not None
        return turn

    safe_turn = observe("safe")
    assert safe_turn["provenance_mutation_capable"] is True

    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "core.fsmonitor", "opaque-writer"],
        check=True,
        capture_output=True,
    )
    unsafe_turn = observe("unsafe")
    assert unsafe_turn["provenance_mutation_capable"] is True

    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "--unset", "core.fsmonitor"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "pager.status", "true"],
        check=True,
        capture_output=True,
    )
    pager_turn = observe("pager")
    assert pager_turn["provenance_mutation_capable"] is True

    (tmp_path / "git.cmd").write_text("opaque-writer", encoding="utf-8")
    shadowed_turn = observe("shadowed")
    assert shadowed_turn["provenance_mutation_capable"] is True


def test_edit_family_always_remains_local_mutation_capable(tmp_path: Path) -> None:
    from core.adapter_observation import (
        CanonicalInvocation,
        ObservationReport,
        _record_status,
    )

    payload = {
        "project_root": str(tmp_path),
        "event": "prompt",
        "host": "codex_cli",
        "agent": "codex",
        "session_id": "session",
        "turn_id": "turn-session",
        "prompt": "work",
    }
    _ = record_event(payload)
    invocation = CanonicalInvocation(
        "codex_cli",
        "codex",
        "session",
        "turn-session",
        "edit",
        "post_tool",
        "edit",
        ("app.py",),
        "",
        True,
        "",
    )

    _record_status(
        tmp_path,
        invocation,
        ObservationReport("snapshot", "baseline", (), False, False),
    )

    turn = active_turn(load_ledger({"project_root": str(tmp_path)}), payload)
    assert turn is not None
    assert turn["provenance_mutation_capable"] is True
