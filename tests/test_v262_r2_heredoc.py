from __future__ import annotations

from pathlib import Path

import pytest

import core.destructive_guard as guard
from core.destructive_guard import (
    evaluate_r2_destructive_gate,
    parse_destructive_commands,
)
from core.ledger import JsonObject


def _payload(root: Path, command: str) -> JsonObject:
    return {
        "project_root": str(root),
        "tool_name": "Bash",
        "command": command,
        "host": "codex_cli",
        "session_id": "v262-pr-a",
        "agent": "codex",
    }


def _healthy(_ledger: JsonObject) -> JsonObject:
    return {"degraded": False, "capacity_exceeded": False}


def _peer_record() -> JsonObject:
    return {
        "generation": 1,
        "status": "exclusive",
        "owners": [
            {
                "agent_key": "claude_code:peer-session:claude",
                "settled": False,
            }
        ],
    }


def _lookup_peer(_ledger: JsonObject, canonical_path: str) -> JsonObject | None:
    return _peer_record() if canonical_path == "peer.py" else None


def _decision(root: Path, command: str) -> str:
    result = evaluate_r2_destructive_gate(
        _payload(root, command),
        lookup_path_attribution=_lookup_peer,
        attribution_health=_healthy,
    )
    return str(result["decision"])


@pytest.mark.parametrize(
    "command",
    (
        "cat <<EOF\n$(rm peer.py)\nEOF",
        "cat <<EOF\n`rm peer.py`\nEOF",
    ),
)
def test_r2_07a_unquoted_heredoc_substitutions_are_executable(
    tmp_path: Path,
    command: str,
) -> None:
    assert _decision(tmp_path, command) == "block"
    assert parse_destructive_commands(command)


@pytest.mark.parametrize(
    "command",
    (
        "cat <<'EOF'\n$(rm peer.py)\nEOF",
        'cat <<"EOF"\n`rm peer.py`\nEOF',
        "cat <<EOF\nliteral rm peer.py\nEOF",
        "cat <<EOF\n$ONLY_VARIABLE_DATA\nEOF",
        "cat <<EOF\n\\$(rm peer.py)\nEOF",
    ),
)
def test_r2_07a_quoted_or_literal_heredoc_data_is_not_executable(
    tmp_path: Path,
    command: str,
) -> None:
    assert parse_destructive_commands(command) == ()
    assert _decision(tmp_path, command) == "allow"


@pytest.mark.parametrize(
    "command",
    (
        "cat <<EOF\n$(rm peer.py\nEOF",
        "cat <<EOF\n`rm peer.py\nEOF",
        "cat <<EOF\n$($UNKNOWN_COMMAND)\nEOF",
    ),
)
def test_r2_07a_unquoted_heredoc_unparseable_substitution_fails_closed(
    tmp_path: Path,
    command: str,
) -> None:
    parsed = parse_destructive_commands(command)
    assert parsed
    assert any(not item.resolved for item in parsed)
    assert _decision(tmp_path, command) == "block"


def test_r2_07a_heredoc_declaration_preserves_execution_metadata() -> None:
    declarations, malformed = guard._heredoc_declarations(  # noqa: SLF001
        "bash -e <<-'EOF'"
    )

    assert malformed is False
    assert len(declarations) == 1
    declaration = declarations[0]
    assert declaration.delimiter == "EOF"
    assert declaration.strip_tabs is True
    assert declaration.quoted is True
    assert declaration.consumer == "bash"


@pytest.mark.parametrize(
    "command",
    (
        "bash -e <<EOF\nrm peer.py\nEOF",
        "sh -x <<EOF\nrm peer.py\nEOF",
        "bash --noprofile <<EOF\nrm peer.py\nEOF",
        "bash --norc <<EOF\nrm peer.py\nEOF",
        "bash -o pipefail <<EOF\nrm peer.py\nEOF",
        "bash -eux <<EOF\nrm peer.py\nEOF",
        "bash -C <<EOF\nrm peer.py\nEOF",
        "bash -s peer-arg <<EOF\nrm peer.py\nEOF",
        "bash -se peer-arg <<EOF\nrm peer.py\nEOF",
        "bash - <<EOF\nrm peer.py\nEOF",
        "printf ignored | bash -e <<EOF\nrm peer.py\nEOF",
        "bash --unknown-option <<EOF\nrm peer.py\nEOF",
        "bash <<'EOF'\nrm peer.py\nEOF",
    ),
)
def test_r2_07b_shell_options_do_not_hide_heredoc_stdin_script(
    tmp_path: Path,
    command: str,
) -> None:
    assert _decision(tmp_path, command) == "block"
    assert parse_destructive_commands(command)


@pytest.mark.parametrize(
    "command",
    (
        'bash -c "printf safe" <<EOF\nrm peer.py\nEOF',
        'bash --command "printf safe" <<EOF\nrm peer.py\nEOF',
        "bash script.sh <<EOF\nrm peer.py\nEOF",
    ),
)
def test_r2_07b_separate_shell_payload_leaves_heredoc_as_data(
    tmp_path: Path,
    command: str,
) -> None:
    assert parse_destructive_commands(command) == ()
    assert _decision(tmp_path, command) == "allow"


@pytest.mark.parametrize(
    "command",
    (
        'bash -c "builtin command rm peer.py"',
        'bash -c "builtin exec rm peer.py"',
        "builtin command rm peer.py",
        "builtin exec rm peer.py",
    ),
)
def test_r2_07c_builtin_execution_prefix_reaches_destructive_command(
    tmp_path: Path,
    command: str,
) -> None:
    assert _decision(tmp_path, command) == "block"
    assert parse_destructive_commands(command)


@pytest.mark.parametrize(
    "command",
    (
        """bash -c "builtin printf '%s\\n' rm peer.py" """,
        "builtin printf '%s\\n' rm peer.py",
    ),
)
def test_r2_07c_builtin_nonexecuting_builtin_remains_allowed(
    tmp_path: Path,
    command: str,
) -> None:
    assert parse_destructive_commands(command) == ()
    assert _decision(tmp_path, command) == "allow"
