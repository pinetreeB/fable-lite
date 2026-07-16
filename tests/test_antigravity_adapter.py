from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "adapters" / "antigravity" / "oma_hook.py"

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
HookPayload: TypeAlias = dict[str, JsonValue]
HookOutput: TypeAlias = dict[str, JsonValue]

def run_oma_hook(event: str, payload: HookPayload | str) -> HookOutput:
    raw_input = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    process = subprocess.run(
        [sys.executable, str(ADAPTER), event],
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

def oma_prompt_payload(tmp_path: Path, prompt: str) -> HookPayload:
    return {
        "cwd": str(tmp_path),
        "prompt": prompt,
        "session_id": "oma-session-1"
    }

def test_oma_pre_invocation_injects_ephemeral_pack_context_and_records_ledger(
    tmp_path: Path,
) -> None:
    payload = oma_prompt_payload(tmp_path, "버그 고쳐줘 안되는데요")

    result = run_oma_hook("PreInvocation", payload)

    steps = list_value(result["injectSteps"])
    step = object_value(steps[0])
    context = step["ephemeralMessage"]
    ledger = read_ledger(tmp_path)
    assert set(result) == {"injectSteps"}
    assert isinstance(context, str)
    assert "조사 팩" in context
    assert ledger["requires_investigation_compliance"] is True


def test_oma_before_tool_uses_actual_top_level_tool_as_authority(tmp_path: Path) -> None:
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "run_shell_command",
        "tool_input": {"command": "rm -rf /"},
        "metadata": {"tool_name": "read_file", "tool_input": {}},
        "llm_request": {
            "tool_calls": [
                {
                    "name": "read_file",
                    "args": {"file_path": "README.md"},
                }
            ]
        },
    }

    result = run_oma_hook("PreToolUse", payload)

    assert result["decision"] == "deny"
    # 최상위 tool_name의 command가 판정 근거라는 것이 검증 대상이다. 이 명령은
    # R2 파기 차단(§6-3 R2-first)이 R1보다 먼저 잡으므로 어느 게이트의 차단이든 수용한다.
    reason = str(result.get("reason", ""))
    assert "contract.json" in reason or "R2" in reason


def test_oma_pre_tool_allow_uses_official_decision(tmp_path: Path) -> None:
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "view_file",
        "tool_input": {"path": "README.md"},
    }

    result = run_oma_hook("PreToolUse", payload)

    assert result == {"decision": "allow"}


def test_oma_after_tool_records_file_changes_and_scope_warning(tmp_path: Path) -> None:
    run_oma_hook("BeforeModel", oma_prompt_payload(tmp_path, "app.py만 수정해줘"))
    _ = (tmp_path / "settings.py").write_text("changed", encoding="utf-8")
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "metadata": {
            "tool_name": "replace_file_content",
            "tool_input": {"TargetFile": "settings.py"}
        }
    }

    result = run_oma_hook("PostToolUse", payload)

    ledger = read_ledger(tmp_path)
    assert ledger["changed_files_seen"] == ["settings.py"]
    assert result == {}


def test_oma_after_tool_records_shell_verification(tmp_path: Path) -> None:
    # Given: a verification command with explicit successful tool-result fields.
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "run_shell_command",
        "tool_input": {"command": "python -m pytest tests/"},
        "tool_response": {
            "llmContent": "Output: 3 passed in 0.02s",
            "returnDisplay": "3 passed in 0.02s",
            "data": {"exitCode": 0, "isError": False},
        },
    }

    # When: Antigravity reports the completed tool call.
    result = run_oma_hook("PostToolUse", payload)

    # Then: the ledger records the observed successful output as evidence.
    ledger = read_ledger(tmp_path)
    verification = object_value(list_value(ledger["verification_results"])[0])
    assert result == {}
    assert verification["success"] is True
    assert "3 passed in 0.02s" in str(verification["evidence"])


def test_oma_after_tool_records_failed_shell_verification_evidence(tmp_path: Path) -> None:
    # Given: pytest returned a nonzero exit and concrete failure output.
    failed_stdout = "1 failed in 0.02s\nFAILED tests/test_app.py::test_value"
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "run_shell_command",
        "tool_input": {"command": "python -m pytest tests/test_app.py"},
        "tool_response": {
            "llmContent": f"Output: {failed_stdout}\nExit Code: 1",
            "returnDisplay": failed_stdout,
            "data": {"exitCode": 1, "isError": True},
        },
    }

    # When: the failed verification reaches AfterTool.
    run_oma_hook("AfterTool", payload)

    # Then: failure and its real output are preserved in the ledger.
    ledger = read_ledger(tmp_path)
    verification = object_value(list_value(ledger["verification_results"])[0])
    assert verification["command"] == "python -m pytest tests/test_app.py"
    assert verification["success"] is False
    assert failed_stdout in str(verification["evidence"])
    assert isinstance(verification["seq"], int)


def test_oma_after_agent_blocks_after_code_change_and_failed_verification(tmp_path: Path) -> None:
    # Given: a normal code task changed a file and its verification failed.
    run_oma_hook("BeforeModel", oma_prompt_payload(tmp_path, "app.py에 계산 페이지를 만들어줘"))
    _ = (tmp_path / "app.py").write_text("changed", encoding="utf-8")
    run_oma_hook(
        "AfterTool",
        {
            "cwd": str(tmp_path),
            "tool_name": "replace",
            "tool_input": {"file_path": "app.py", "old_string": "a", "new_string": "b"},
            "tool_response": {"llmContent": "Updated app.py", "returnDisplay": "Updated app.py"},
        },
    )
    run_oma_hook(
        "AfterTool",
        {
            "cwd": str(tmp_path),
            "tool_name": "run_shell_command",
            "tool_input": {"command": "python -m pytest tests/test_app.py"},
            "tool_response": {
                "llmContent": "Output: 1 failed\nFAILED tests/test_app.py::test_value\nExit Code: 1",
                "returnDisplay": "1 failed\nFAILED tests/test_app.py::test_value",
                "data": {"exitCode": 1, "isError": True},
            },
        },
    )
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "termination_reason": "completed",
        "llm_request": {
            "messages": [{"role": "assistant", "content": "작업을 완료했습니다."}],
        },
    }

    # When: the agent attempts to finish the changed task.
    result = run_oma_hook("Stop", payload)

    # Then: the missing successful verification keeps completion blocked.
    assert result["decision"] == "continue"
    assert "성공한 검증 증거가 없습니다" in str(result.get("reason", ""))
    assert str(result.get("reason", "")).endswith("Show me the work.")


def test_oma_after_tool_classifies_verification_result_conservatively(
    tmp_path: Path,
) -> None:
    cases: list[tuple[str, HookPayload, bool]] = [
        ("nested-error", {"tool_response": {"error": "exit status 1"}}, False),
        ("nonzero-exit", {"tool_response": {"data": {"exitCode": 2, "isError": True}}}, False),
        ("failure-output", {"tool_response": {"llmContent": "FAILED test_value"}}, False),
        ("missing-result-fields", {"tool_response": {}}, False),
        ("success-output", {"tool_response": {"llmContent": "3 passed"}}, True),
        ("zero-exit", {"tool_response": {"data": {"exitCode": 0, "isError": False}}}, True),
    ]

    for case_name, result_fields, expected_success in cases:
        # Given: one isolated, observable Antigravity tool-result shape.
        case_root = tmp_path / case_name
        case_root.mkdir()
        payload: HookPayload = {
            "cwd": str(case_root),
            "tool_name": "run_shell_command",
            "tool_input": {"command": "python -m pytest tests/test_app.py"},
            **result_fields,
        }

        # When: the result is recorded by AfterTool.
        run_oma_hook("AfterTool", payload)

        # Then: ambiguity fails closed while explicit success or exit zero passes.
        ledger = read_ledger(case_root)
        results = list_value(ledger["verification_results"])
        verification = object_value(results[0])
        assert verification["success"] is expected_success, case_name


def test_oma_after_agent_blocks_if_n1_missing(tmp_path: Path) -> None:
    run_oma_hook("BeforeModel", oma_prompt_payload(tmp_path, "버그 고쳐줘 안되는데요"))
    # v1.1.3: N1 마커는 파일 변경이 있는 턴에만 요구되므로 변경 이벤트를 먼저 기록한다.
    _ = (tmp_path / "app.py").write_text("changed", encoding="utf-8")
    run_oma_hook(
        "AfterTool",
        {
            "cwd": str(tmp_path),
            "metadata": {
                "tool_name": "replace_file_content",
                "tool_input": {"TargetFile": "app.py"},
            },
        },
    )
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "termination_reason": "completed",
        "llm_request": {
            "messages": [
                {"role": "assistant", "content": "원인은 설정입니다."}
            ]
        }
    }

    result = run_oma_hook("Stop", payload)

    assert result["decision"] == "continue"
    assert "조사 팩" in str(result.get("reason", ""))


def test_oma_after_agent_allows_answer_only_investigation_turn(tmp_path: Path) -> None:
    # v1.1.3: 변경 없는 답변 전용 턴은 N1 마커 면제.
    run_oma_hook("BeforeModel", oma_prompt_payload(tmp_path, "버그 고쳐줘 안되는데요"))
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "termination_reason": "completed",
        "llm_request": {
            "messages": [
                {"role": "assistant", "content": "원인은 설정입니다."}
            ]
        }
    }

    result = run_oma_hook("Stop", payload)

    assert result == {}


def test_oma_after_tool_records_bash_script_and_make_test_as_verification(tmp_path: Path) -> None:
    # v1 릴리스 심사 H3 회귀: antigravity도 이전엔 좁은 로컬 TEST_TERMS를 썼다.
    # core.verification 공유 후 bash 스크립트 재실행·make test가 인식돼야 한다.
    bash_payload: HookPayload = {
        "cwd": str(tmp_path),
        "metadata": {"tool_name": "run_command", "tool_input": {"CommandLine": "bash test.sh"}},
    }
    make_payload: HookPayload = {
        "cwd": str(tmp_path),
        "metadata": {"tool_name": "run_command", "tool_input": {"CommandLine": "make test"}},
    }

    bash_result = run_oma_hook("PostToolUse", bash_payload)
    make_result = run_oma_hook("PostToolUse", make_payload)

    assert bash_result == {}
    assert make_result == {}


def test_oma_hooks_fail_open_on_malformed_payload() -> None:
    result = run_oma_hook("PreToolUse", "{not-json")

    assert result["decision"] == "allow"
    assert str(result.get("reason", "")).startswith("[smtw] fail-open")
