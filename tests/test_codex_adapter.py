from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias


ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = ROOT / "adapters" / "codex_cli"

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
HookPayload: TypeAlias = dict[str, JsonValue]
HookOutput: TypeAlias = dict[str, JsonValue]


def run_hook(name: str, payload: HookPayload | str) -> HookOutput:
    raw_input = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    process = subprocess.run(
        [sys.executable, str(ADAPTERS / name)],
        input=raw_input,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.returncode == 0
    return json.loads(process.stdout or "{}")


def object_value(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def list_value(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def read_ledger(root: Path) -> dict[str, JsonValue]:
    raw = json.loads((root / ".fable-lite" / "ledger.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def codex_prompt_payload(tmp_path: Path, prompt: str) -> HookPayload:
    return {
        "cwd": str(tmp_path),
        "hook_event_name": "UserPromptSubmit",
        "model": "gpt-5.5",
        "permission_mode": "bypassPermissions",
        "prompt": prompt,
        "session_id": "codex-session-1",
        "transcript_path": str(tmp_path / "transcript.jsonl"),
        "turn_id": "turn-1",
    }


def test_codex_user_prompt_submit_injects_pack_context_and_records_ledger(tmp_path: Path) -> None:
    payload = codex_prompt_payload(tmp_path, "버그 고쳐줘 안되는데요")

    result = run_hook("user_prompt_submit.py", payload)

    hook_output = object_value(result["hookSpecificOutput"])
    context = hook_output["additionalContext"]
    ledger = read_ledger(tmp_path)
    assert hook_output["hookEventName"] == "UserPromptSubmit"
    assert isinstance(context, str)
    assert "조사 팩" in context
    assert ledger["requires_investigation_compliance"] is True


def test_codex_pretool_blocks_high_risk_apply_patch_payload(tmp_path: Path) -> None:
    patch = "*** Begin Patch\n*** Add File: migrations/001.sql\n+DROP TABLE users;\n*** End Patch\n"
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "PreToolUse",
        "tool_name": "apply_patch",
        "tool_input": {"command": patch},
        "tool_use_id": "call_patch",
        "session_id": "codex-session-1",
    }

    result = run_hook("pre_tool_use.py", payload)

    assert result["decision"] == "block"
    assert "contract.json" in str(result["reason"])


def test_codex_posttool_records_apply_patch_file_and_scope_warning(tmp_path: Path) -> None:
    run_hook("user_prompt_submit.py", codex_prompt_payload(tmp_path, "app.py만 수정해줘"))
    patch = "*** Begin Patch\n*** Add File: settings.py\n+DEBUG=True\n*** End Patch\n"
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "PostToolUse",
        "tool_name": "apply_patch",
        "tool_input": {"command": patch},
        "tool_response": "Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nA settings.py\n",
        "tool_use_id": "call_patch",
        "session_id": "codex-session-1",
    }

    result = run_hook("post_tool_use.py", payload)

    ledger = read_ledger(tmp_path)
    assert ledger["changed_files_seen"] == ["settings.py"]
    assert "범위 이탈" in str(result["systemMessage"])


def test_codex_posttool_records_string_shell_verification_response(tmp_path: Path) -> None:
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "python -m pytest tests/"},
        "tool_response": "Exit code: 0\nWall time: 1 seconds\nOutput:\n26 passed\n",
        "session_id": "codex-session-1",
    }

    result = run_hook("post_tool_use.py", payload)

    ledger = read_ledger(tmp_path)
    verification = object_value(list_value(ledger["verification_results"])[0])
    assert "recorded verification." in str(result["systemMessage"])
    assert verification["success"] is True
    assert verification["evidence"] == "26 passed"


def test_codex_stop_uses_last_assistant_message_for_n1_gate(tmp_path: Path) -> None:
    run_hook("user_prompt_submit.py", codex_prompt_payload(tmp_path, "버그 고쳐줘 안되는데요"))
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "Stop",
        "last_assistant_message": "원인은 설정입니다.",
        "stop_hook_active": False,
        "session_id": "codex-session-1",
    }

    result = run_hook("stop.py", payload)

    assert result["decision"] == "block"
    assert "조사 팩" in str(result["reason"])


def test_codex_hooks_fail_open_on_malformed_payload() -> None:
    result = run_hook("pre_tool_use.py", "{not-json")

    assert str(result["systemMessage"]).startswith("fable-lite fail-open")
