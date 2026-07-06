from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias


ROOT = Path(__file__).resolve().parents[1]
ADAPTERS = ROOT / "adapters" / "claude_code"

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
HookPayload: TypeAlias = dict[str, JsonValue]
HookOutput: TypeAlias = dict[str, JsonValue]


def object_value(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def run_hook(name: str, payload: HookPayload) -> HookOutput:
    process = subprocess.run(
        [sys.executable, str(ADAPTERS / name)],
        input=json.dumps(payload, ensure_ascii=False),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert process.returncode == 0
    return json.loads(process.stdout or "{}")


def test_adapters_handle_realistic_claude_code_nested_payloads(tmp_path: Path) -> None:
    prompt_payload: HookPayload = {
        "cwd": str(tmp_path),
        "prompt": "app.py만 수정해줘",
        "session_id": "s1",
    }
    prompt_result = run_hook("user_prompt_submit.py", prompt_payload)

    post_result = run_hook(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Edit",
            "tool_input": {"file_path": "app.py"},
            "tool_response": {"filePath": "app.py"},
            "session_id": "s1",
        },
    )
    ledger = json.loads((tmp_path / ".fable-lite" / "ledger.json").read_text(encoding="utf-8"))

    assert "hookSpecificOutput" in prompt_result
    assert "recorded 1 change(s)." in str(post_result["systemMessage"])
    assert ledger["changed_files_seen"] == ["app.py"]


def test_pretool_blocks_realistic_high_risk_edit_and_shell_payloads(tmp_path: Path) -> None:
    edit_result = run_hook(
        "pre_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Edit",
            "tool_input": {"file_path": "migrations/001_init.sql", "new_string": "DROP TABLE users;"},
            "session_id": "s1",
        },
    )
    bash_result = run_hook(
        "pre_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Bash",
            "tool_input": {"command": "python manage.py migrate && psql -c 'DROP TABLE users'"},
            "session_id": "s1",
        },
    )

    assert edit_result["decision"] == "block"
    assert bash_result["decision"] == "block"


def test_posttool_records_nested_shell_verification(tmp_path: Path) -> None:
    result = run_hook(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest tests/"},
            "tool_response": {"exit_code": 0, "stdout": "10 passed"},
            "session_id": "s1",
        },
    )
    ledger = json.loads((tmp_path / ".fable-lite" / "ledger.json").read_text(encoding="utf-8"))

    assert "recorded verification." in str(result["systemMessage"])
    assert ledger["verification_results"][0]["success"] is True
    assert ledger["verification_results"][0]["evidence"] == "10 passed"


def test_goals_nudge_and_n2_pretool_gate_use_persisted_prompt_state(tmp_path: Path) -> None:
    prompt_result = run_hook(
        "user_prompt_submit.py",
        {
            "cwd": str(tmp_path),
            "prompt": "로그인 고치고 결제 페이지도 만들어줘",
            "session_id": "s1",
        },
    )
    hook_output = object_value(prompt_result["hookSpecificOutput"])
    context = hook_output["additionalContext"]
    pre_result = run_hook(
        "pre_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Edit",
            "tool_input": {"file_path": "app.py"},
            "session_id": "s1",
        },
    )

    assert isinstance(context, str)
    assert "goals 체크포인트" in context
    assert pre_result["decision"] == "block"
    assert "goals" in str(pre_result["reason"]).lower()


def test_stop_blocks_missing_n1_markers_from_transcript_when_investigation_pack_was_injected(tmp_path: Path) -> None:
    run_hook(
        "user_prompt_submit.py",
        {
            "cwd": str(tmp_path),
            "prompt": "버그 고쳐줘 안되는데요",
            "session_id": "s1",
        },
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "원인은 설정입니다."}]},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    stop_result = run_hook(
        "stop.py",
        {"cwd": str(tmp_path), "transcript_path": str(transcript), "stop_hook_active": False, "session_id": "s1"},
    )

    assert stop_result["decision"] == "block"
    assert "조사 팩" in str(stop_result["reason"])


def test_hooks_fail_open_on_malformed_payload() -> None:
    process = subprocess.run(
        [sys.executable, str(ADAPTERS / "pre_tool_use.py")],
        input="{not-json",
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert process.returncode == 0
    assert json.loads(process.stdout)["systemMessage"].startswith("fable-lite fail-open")


def test_plugin_manifest_and_hooks_json_exist() -> None:
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    hooks = json.loads((ADAPTERS / "hooks.json").read_text(encoding="utf-8"))

    assert plugin["name"] == "fable-lite"
    assert "hooks" in plugin
    assert "Bash|PowerShell" in hooks["hooks"]["PreToolUse"][0]["matcher"]
    for hook_entries in hooks["hooks"].values():
        for entry in hook_entries:
            assert entry["hooks"][0]["timeout"] == 10


def test_tool_success_falls_back_to_stdout_when_exit_code_missing() -> None:
    # E1b F4 회귀: nested headless 세션은 exit_code/success 필드를 안 채운다.
    # stdout 텍스트로 보수적 폴백해야 진짜 통과한 검증이 게이트에 보인다.
    from adapters.claude_code.common import tool_success

    passed = {"tool_response": {"stdout": "3 passed in 0.02s"}}
    failed = {"tool_response": {"stdout": "1 failed, 2 passed\nFAILED test_x"}}
    empty = {"tool_response": {"stdout": ""}}
    explicit_false = {"tool_response": {"success": False, "stdout": "3 passed"}}

    assert tool_success(passed) is True
    assert tool_success(failed) is False       # 실패 신호 우선
    assert tool_success(empty) is False        # 판정 불가 → 보수적 실패
    assert tool_success(explicit_false) is False  # 명시 실패 필드 신뢰
