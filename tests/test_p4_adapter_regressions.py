from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import TypeAlias

from adapters.antigravity.tool_io import extract_tool_info, verification_result
from adapters.claude_code.common import tool_success as claude_tool_success
from adapters.codex_cli.common import tool_success as codex_tool_success


ROOT = Path(__file__).resolve().parents[1]
ANTIGRAVITY_HOOK = ROOT / "adapters" / "antigravity" / "oma_hook.py"
CODEX_ADAPTER = ROOT / "adapters" / "codex_cli"

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
HookPayload: TypeAlias = dict[str, JsonValue]
HookOutput: TypeAlias = dict[str, JsonValue]


def _run_hook(command: list[str], payload: HookPayload) -> HookOutput:
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
    output = json.loads(process.stdout or "{}")
    assert isinstance(output, dict)
    return output


def _run_codex(name: str, payload: HookPayload) -> HookOutput:
    return _run_hook([sys.executable, str(CODEX_ADAPTER / name)], payload)


def _run_antigravity(event: str, payload: HookPayload) -> HookOutput:
    return _run_hook([sys.executable, str(ANTIGRAVITY_HOOK), event], payload)


def _ledger(root: Path) -> HookOutput:
    raw = json.loads((root / ".fable-lite" / "ledger.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _first_verification(root: Path) -> HookOutput:
    results = _ledger(root)["verification_results"]
    assert isinstance(results, list) and results
    result = results[0]
    assert isinstance(result, dict)
    return result


def test_explicit_zero_exit_ignores_failure_words_across_all_adapters() -> None:
    # Given: all adapters receive exit zero with a passing test name containing "error".
    output = "test_error_handling passed"
    antigravity = {
        "tool_response": {
            "data": {"exitCode": 0, "isError": False},
            "llmContent": output,
        }
    }
    claude: HookPayload = {"tool_response": {"exitCode": 0, "stdout": output}}
    codex: HookPayload = {"tool_response": f"Exit code: 0\nOutput:\n{output}"}

    # When: each adapter classifies the verification result.
    antigravity_success = verification_result(antigravity)[0]

    # Then: explicit exit zero wins over text heuristics for all three adapters.
    assert (antigravity_success, claude_tool_success(claude), codex_tool_success(codex)) == (
        True,
        True,
        True,
    )


def test_antigravity_normalizes_string_booleans_and_strips_ansi_before_fallback() -> None:
    # Given: explicit booleans arrive as strings and one fallback failure is ANSI-split.
    cases = (
        ("success-true", {"tool_response": {"success": "true", "llmContent": "ERROR test name"}}, True),
        ("success-false", {"tool_response": {"success": "false", "llmContent": "3 passed"}}, False),
        ("is-error-false", {"tool_response": {"isError": "false", "llmContent": "ERROR test name"}}, True),
        ("is-error-true", {"tool_response": {"isError": "true", "llmContent": "3 passed"}}, False),
        ("ansi-traceback", {"tool_response": {"llmContent": "1 passed\n\x1b[31mTrace\x1b[0mback"}}, False),
    )

    # When/Then: explicit strings are authoritative and stripped fallback text stays conservative.
    for case_name, payload, expected in cases:
        assert verification_result(payload)[0] is expected, case_name


def test_antigravity_tool_identity_falls_through_when_top_level_name_is_missing() -> None:
    # Given: top-level input is incomplete while toolCall carries the authoritative identity.
    payload = {
        "tool_input": {"command": "ignored"},
        "toolCall": {"name": "run_shell_command", "args": {"command": "python -m pytest"}},
    }

    # When: Antigravity resolves the tool identity.
    name, tool_input = extract_tool_info(payload)

    # Then: the incomplete candidate does not mask the next authoritative candidate.
    assert name == "Bash"
    assert tool_input == {"command": "python -m pytest"}


def test_codex_failed_verification_is_recorded_false_and_blocks_stop(tmp_path: Path) -> None:
    # Given: a deep Codex task records a code change and a failed verification.
    _run_codex(
        "user_prompt_submit.py",
        {"cwd": str(tmp_path), "prompt": "app.py에 계산 페이지를 구현하고 테스트해줘", "session_id": "p4"},
    )
    patch = "*** Begin Patch\n*** Update File: app.py\n+FIX=True\n*** End Patch\n"
    _ = (tmp_path / "app.py").write_text("FIX=True\n", encoding="utf-8")
    _run_codex(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "apply_patch",
            "tool_input": {"command": patch},
            "tool_response": "Exit code: 0\nOutput:\nSuccess. Updated app.py",
            "session_id": "p4",
        },
    )
    _run_codex(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest tests/test_app.py"},
            "tool_response": "Exit code: 1\nOutput:\nFAILED tests/test_app.py::test_value",
            "session_id": "p4",
        },
    )

    # When: Codex attempts to stop.
    result = _run_codex(
        "stop.py",
        {"cwd": str(tmp_path), "last_assistant_message": "검증을 수행했습니다.", "session_id": "p4"},
    )

    # Then: failure is preserved and the unverified change blocks completion.
    assert _first_verification(tmp_path)["success"] is False
    assert result["decision"] == "block"


def test_antigravity_fresh_successful_verification_allows_after_agent(tmp_path: Path) -> None:
    # Given: an Antigravity deep task changes code and then records a fresh success.
    _run_antigravity(
        "BeforeModel",
        {"cwd": str(tmp_path), "prompt": "app.py에 계산 페이지를 구현하고 테스트해줘"},
    )
    _ = (tmp_path / "app.py").write_text("changed\n", encoding="utf-8")
    _run_antigravity(
        "AfterTool",
        {
            "cwd": str(tmp_path),
            "tool_name": "replace",
            "tool_input": {"file_path": "app.py", "old_string": "a", "new_string": "b"},
        },
    )
    _run_antigravity(
        "AfterTool",
        {
            "cwd": str(tmp_path),
            "tool_name": "run_shell_command",
            "tool_input": {"command": "python -m pytest tests/test_app.py"},
            "tool_response": {"data": {"exitCode": 0, "isError": False}, "llmContent": "1 passed"},
        },
    )

    # When: AfterAgent evaluates completion.
    result = _run_antigravity(
        "AfterAgent",
        {"cwd": str(tmp_path), "llm_request": {"messages": [{"role": "assistant", "content": "완료"}]}},
    )

    # Then: the latest successful evidence is sequenced after the change and allows completion.
    ledger = _ledger(tmp_path)
    verification = _first_verification(tmp_path)
    assert ledger["task_mode"] == "normal"
    assert isinstance(verification["seq"], int)
    assert isinstance(ledger["last_change_seq"], int)
    assert verification["seq"] > ledger["last_change_seq"]
    assert result["decision"] == "allow"
