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
    _ = (tmp_path / "app.py").write_text("changed", encoding="utf-8")

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
    assert "observed 1 change(s)." in str(post_result["systemMessage"])
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


def test_fake_output_verification_cannot_unlock_changed_claude_turn(
    tmp_path: Path,
) -> None:
    run_hook(
        "user_prompt_submit.py",
        {"cwd": str(tmp_path), "prompt": "app.py 함수 이름을 바꿔줘", "session_id": "s1"},
    )
    _ = (tmp_path / "app.py").write_text("changed", encoding="utf-8")
    run_hook(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Edit",
            "tool_input": {"file_path": "app.py"},
            "tool_response": {"filePath": "app.py"},
            "session_id": "s1",
            "tool_use_id": "edit-1",
        },
    )
    fake_result = run_hook(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Bash",
            "tool_input": {"command": "echo pytest"},
            "tool_response": {"exit_code": 0, "stdout": "pytest"},
            "session_id": "s1",
            "tool_use_id": "fake-verify",
        },
    )

    blocked = run_hook(
        "stop.py",
        {"cwd": str(tmp_path), "session_id": "s1", "stop_hook_active": False},
    )
    ledger = json.loads(
        (tmp_path / ".fable-lite" / "ledger.json").read_text(encoding="utf-8")
    )

    assert "recorded verification" not in str(fake_result.get("systemMessage", ""))
    assert ledger["verification_results"] == []
    assert blocked["decision"] == "block"


def test_verified_remote_turn_recovers_after_fresh_remote_evidence(
    tmp_path: Path,
) -> None:
    run_hook(
        "user_prompt_submit.py",
        {"cwd": str(tmp_path), "prompt": "원격 서비스 설정을 갱신해줘", "session_id": "s1"},
    )
    remote_command = 'ssh deploy@example.com "touch remote-marker"'
    remote_payload: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": remote_command},
        "session_id": "s1",
        "tool_use_id": "remote-change",
    }
    run_hook("pre_tool_use.py", remote_payload)
    run_hook(
        "post_tool_use.py",
        {
            **remote_payload,
            "tool_response": {"exit_code": 0, "stdout": "updated"},
        },
    )

    unverified = run_hook(
        "stop.py",
        {"cwd": str(tmp_path), "session_id": "s1", "stop_hook_active": False},
    )
    assert unverified["decision"] == "block"

    verify_command = 'ssh deploy@example.com "python -m pytest tests/"'
    verify_payload: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": verify_command},
        "session_id": "s1",
        "tool_use_id": "remote-verify",
    }
    run_hook("pre_tool_use.py", verify_payload)
    run_hook(
        "post_tool_use.py",
        {
            **verify_payload,
            "tool_response": {"exit_code": 0, "stdout": "1 passed"},
        },
    )

    verified = run_hook(
        "stop.py",
        {"cwd": str(tmp_path), "session_id": "s1", "stop_hook_active": False},
    )

    assert verified.get("decision") != "block"


def test_docs_only_local_change_does_not_exempt_unverified_remote_epoch(
    tmp_path: Path,
) -> None:
    # Given: one successful remote mutation and a docs-only local edit in the same turn.
    run_hook(
        "user_prompt_submit.py",
        {"cwd": str(tmp_path), "prompt": "원격 설정과 문서를 갱신해줘", "session_id": "s1"},
    )
    remote_payload: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": 'ssh deploy@example.com "touch remote-marker"'},
        "session_id": "s1",
        "tool_use_id": "remote-change",
    }
    run_hook("pre_tool_use.py", remote_payload)
    run_hook(
        "post_tool_use.py",
        {
            **remote_payload,
            "tool_response": {"exit_code": 0, "stdout": "updated"},
        },
    )
    readme = tmp_path / "README.md"
    _ = readme.write_text("docs", encoding="utf-8")
    run_hook(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(readme)},
            "tool_response": {"filePath": str(readme)},
            "session_id": "s1",
            "tool_use_id": "docs-change",
        },
    )

    # When: Stop is evaluated without a successful verification covering the remote epoch.
    stopped = run_hook(
        "stop.py",
        {"cwd": str(tmp_path), "session_id": "s1", "stop_hook_active": False},
    )

    # Then: docs-only local state cannot suppress the outstanding remote verification gate.
    assert stopped.get("decision") == "block"


def test_remote_possible_command_records_epoch_while_local_observation_stays_enabled(
    tmp_path: Path,
) -> None:
    run_hook(
        "user_prompt_submit.py",
        {"cwd": str(tmp_path), "prompt": "원격 작업을 실행해줘", "session_id": "s1"},
    )
    remote_payload: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {
            "command": (
                "ssh -o KexAlgorithms=curve25519-sha256 "
                'deploy@example.com "touch remote-marker"'
            )
        },
        "session_id": "s1",
        "tool_use_id": "remote-with-local-observation",
    }
    run_hook("pre_tool_use.py", remote_payload)
    run_hook(
        "post_tool_use.py",
        {
            **remote_payload,
            "tool_response": {"exit_code": 0, "stdout": "updated"},
        },
    )
    ledger = json.loads(
        (tmp_path / ".fable-lite" / "ledger.json").read_text(encoding="utf-8")
    )
    turn = object_value(object_value(ledger["active_turns"])["claude_code:s1:claude"])

    stopped = run_hook(
        "stop.py",
        {"cwd": str(tmp_path), "session_id": "s1", "stop_hook_active": False},
    )

    assert turn.get("provenance_mutation_capable") is True
    assert isinstance(turn.get("last_remote_mutation_seq"), int)
    assert stopped.get("decision") == "block"


def test_failed_remote_attempt_still_requires_fresh_verification(tmp_path: Path) -> None:
    run_hook(
        "user_prompt_submit.py",
        {"cwd": str(tmp_path), "prompt": "원격 작업을 실행해줘", "session_id": "s1"},
    )
    remote_payload: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": 'ssh host "touch /tmp/marker; false"'},
        "session_id": "s1",
        "tool_use_id": "partially-failed-remote",
    }
    run_hook("pre_tool_use.py", remote_payload)
    run_hook(
        "post_tool_use.py",
        {
            **remote_payload,
            "tool_response": {"exit_code": 1, "stderr": "remote command failed"},
        },
    )
    ledger = json.loads(
        (tmp_path / ".fable-lite" / "ledger.json").read_text(encoding="utf-8")
    )
    turn = object_value(object_value(ledger["active_turns"])["claude_code:s1:claude"])

    stopped = run_hook(
        "stop.py",
        {"cwd": str(tmp_path), "session_id": "s1", "stop_hook_active": False},
    )

    assert isinstance(turn.get("last_remote_mutation_seq"), int)
    assert stopped.get("decision") == "block"


def test_scope_too_large_turn_still_tracks_and_verifies_remote_mutation(
    tmp_path: Path,
) -> None:
    from core.provenance_types import DEFAULT_MAX_SCAN_BYTES

    with (tmp_path / "oversized.bin").open("wb") as handle:
        handle.truncate(DEFAULT_MAX_SCAN_BYTES + 1)
    run_hook(
        "user_prompt_submit.py",
        {"cwd": str(tmp_path), "prompt": "원격 서비스를 갱신해줘", "session_id": "s1"},
    )
    remote_command = 'ssh deploy@example.com "touch remote-marker"'
    remote_payload: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": remote_command},
        "session_id": "s1",
        "tool_use_id": "remote-change",
    }
    run_hook("pre_tool_use.py", remote_payload)
    remote_result_payload: HookPayload = {
        **remote_payload,
        "tool_response": {"exit_code": 0, "stdout": "updated"},
    }
    run_hook("post_tool_use.py", remote_result_payload)

    ledger_path = tmp_path / ".fable-lite" / "ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    turn = object_value(object_value(ledger["active_turns"])["claude_code:s1:claude"])
    assert isinstance(turn.get("last_remote_mutation_seq"), int)

    verify_command = 'ssh deploy@example.com "python -m pytest tests/"'
    verify_payload: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": verify_command},
        "session_id": "s1",
        "tool_use_id": "remote-verify",
    }
    run_hook("pre_tool_use.py", verify_payload)
    verify_result_payload: HookPayload = {
        **verify_payload,
        "tool_response": {"exit_code": 0, "stdout": "1 passed"},
    }
    result = run_hook("post_tool_use.py", verify_result_payload)
    stopped = run_hook(
        "stop.py",
        {"cwd": str(tmp_path), "session_id": "s1", "stop_hook_active": False},
    )

    assert "recorded verification" in str(result.get("systemMessage", ""))
    assert stopped.get("decision") != "block"


def test_scope_too_large_verification_freezes_remote_epoch_at_pretool(
    tmp_path: Path,
) -> None:
    from core.provenance_types import DEFAULT_MAX_SCAN_BYTES

    with (tmp_path / "oversized.bin").open("wb") as handle:
        handle.truncate(DEFAULT_MAX_SCAN_BYTES + 1)
    _ = run_hook(
        "user_prompt_submit.py",
        {"cwd": str(tmp_path), "prompt": "원격 서비스를 검증해줘", "session_id": "s1"},
    )

    first_remote: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": 'ssh deploy@example.com "touch first"'},
        "session_id": "s1",
        "tool_use_id": "remote-first",
    }
    _ = run_hook("pre_tool_use.py", first_remote)
    first_result: HookPayload = {
        **first_remote,
        "tool_response": {"exit_code": 0, "stdout": "updated"},
    }
    _ = run_hook("post_tool_use.py", first_result)

    verify: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": 'ssh deploy@example.com "python -m pytest tests/"'},
        "session_id": "s1",
        "tool_use_id": "remote-verify",
    }
    _ = run_hook("pre_tool_use.py", verify)

    second_remote: HookPayload = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": 'ssh deploy@example.com "touch second"'},
        "session_id": "s1",
        "tool_use_id": "remote-second",
    }
    _ = run_hook("pre_tool_use.py", second_remote)
    second_result: HookPayload = {
        **second_remote,
        "tool_response": {"exit_code": 0, "stdout": "updated"},
    }
    _ = run_hook("post_tool_use.py", second_result)

    verify_result: HookPayload = {
        **verify,
        "tool_response": {"exit_code": 0, "stdout": "1 passed"},
    }
    _ = run_hook("post_tool_use.py", verify_result)
    stopped = run_hook(
        "stop.py",
        {"cwd": str(tmp_path), "session_id": "s1", "stop_hook_active": False},
    )

    assert stopped["decision"] == "block"


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


def _write_transcript(tmp_path: Path, text: str) -> Path:
    transcript = tmp_path.parent / f"{tmp_path.name}-transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return transcript


def test_stop_blocks_missing_n1_markers_when_investigation_turn_changed_files(tmp_path: Path) -> None:
    run_hook(
        "user_prompt_submit.py",
        {
            "cwd": str(tmp_path),
            "prompt": "버그 고쳐줘 안되는데요",
            "session_id": "s1",
        },
    )
    _ = (tmp_path / "app.py").write_text("changed", encoding="utf-8")
    run_hook(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Edit",
            "tool_input": {"file_path": "app.py"},
            "tool_response": {"filePath": "app.py"},
            "session_id": "s1",
        },
    )
    transcript = _write_transcript(tmp_path, "원인은 설정입니다. 고쳤습니다.")
    stop_result = run_hook(
        "stop.py",
        {"cwd": str(tmp_path), "transcript_path": str(transcript), "stop_hook_active": False, "session_id": "s1"},
    )

    assert stop_result["decision"] == "block"
    assert "조사 팩" in str(stop_result["reason"])
    assert str(stop_result["reason"]).endswith("Show me the work.")


def test_stop_allows_answer_only_investigation_turn_without_markers(tmp_path: Path) -> None:
    # v1.1.3: 파일 변경이 없는 답변 전용 턴은 N1 마커 면제 — "이거 왜 안 돼?" 같은
    # 가벼운 질문에 가설 마커를 강제하지 않는다 (사용자 피드백).
    run_hook(
        "user_prompt_submit.py",
        {
            "cwd": str(tmp_path),
            "prompt": "버그 고쳐줘 안되는데요",
            "session_id": "s1",
        },
    )
    transcript = _write_transcript(tmp_path, "원인은 설정입니다.")
    stop_result = run_hook(
        "stop.py",
        {"cwd": str(tmp_path), "transcript_path": str(transcript), "stop_hook_active": False, "session_id": "s1"},
    )

    assert stop_result.get("decision") != "block"


def test_new_prompt_resets_turn_change_history_so_later_questions_are_exempt(tmp_path: Path) -> None:
    # v1.1.3 agy Critical-1 고정: 이전 턴에 코드를 고쳤어도, 새 프롬프트(질문 턴)에서는
    # changed가 리셋되어 N1/검증 게이트가 걸리지 않아야 한다.
    run_hook(
        "user_prompt_submit.py",
        {"cwd": str(tmp_path), "prompt": "버그 고쳐줘 안되는데요", "session_id": "s1"},
    )
    run_hook(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Edit",
            "tool_input": {"file_path": "app.py"},
            "tool_response": {"filePath": "app.py"},
            "session_id": "s1",
        },
    )
    run_hook(
        "user_prompt_submit.py",
        {"cwd": str(tmp_path), "prompt": "근데 이 에러는 왜 나는 거야?", "session_id": "s1"},
    )
    transcript = _write_transcript(tmp_path, "이유는 이렇습니다.")
    stop_result = run_hook(
        "stop.py",
        {"cwd": str(tmp_path), "transcript_path": str(transcript), "stop_hook_active": False, "session_id": "s1"},
    )

    assert stop_result.get("decision") != "block"


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
    assert json.loads(process.stdout)["systemMessage"].startswith("[smtw] fail-open")


def test_plugin_manifest_and_hooks_json_exist() -> None:
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    hooks = json.loads((ADAPTERS / "hooks.json").read_text(encoding="utf-8"))

    assert plugin["name"] == "show-me-the-work"
    assert "hooks" in plugin
    assert "Bash|PowerShell" in hooks["hooks"]["PreToolUse"][0]["matcher"]
    for hook_entries in hooks["hooks"].values():
        for entry in hook_entries:
            assert entry["hooks"][0]["timeout"] == 10


def test_tool_success_falls_back_to_stdout_when_exit_code_missing() -> None:
    # E1b F4 회귀: nested headless 세션은 exit_code/success 필드를 안 채운다.
    # stdout 텍스트로 보수적 폴백해야 진짜 통과한 검증이 게이트에 보인다.
    from adapters.claude_code.common import tool_success

    passed: HookPayload = {"tool_response": {"stdout": "3 passed in 0.02s"}}
    failed: HookPayload = {"tool_response": {"stdout": "1 failed, 2 passed\nFAILED test_x"}}
    empty: HookPayload = {"tool_response": {"stdout": ""}}
    explicit_false: HookPayload = {
        "tool_response": {"success": False, "stdout": "3 passed"}
    }

    assert tool_success(passed) is True
    assert tool_success(failed) is False       # 실패 신호 우선
    assert tool_success(empty) is False        # 판정 불가 → 보수적 실패
    assert tool_success(explicit_false) is False  # 명시 실패 필드 신뢰


def test_posttool_records_bash_script_and_make_test_as_verification(tmp_path: Path) -> None:
    # v1 릴리스 심사 H3 회귀: bash 스크립트 재실행·make test가 claude_code에서도 인식돼야 한다.
    bash_result = run_hook(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Bash",
            "tool_input": {"command": "bash test.sh"},
            "tool_response": {"exit_code": 0, "stdout": "ok"},
            "session_id": "s1",
        },
    )
    make_result = run_hook(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Bash",
            "tool_input": {"command": "make test"},
            "tool_response": {"exit_code": 0, "stdout": "ok"},
            "session_id": "s1",
        },
    )

    assert "recorded verification." in str(bash_result["systemMessage"])
    assert "recorded verification." in str(make_result["systemMessage"])


def test_verification_command_recognizes_script_reruns_but_not_ops() -> None:
    # E1c F1 회귀: "python demo.py"(스크립트 재실행)는 가장 흔한 검증 패턴인데 v5까지 미인식이었다.
    # migrate/install/build 같은 운영 명령은 검증으로 오인하면 안 된다.
    # v1 릴리스 심사 H1/H2/H3: 판정 로직 자체는 core.verification으로 이전됐다(3어댑터 공유).
    from core.verification import is_verification_command as v

    assert v("python demo.py") is True
    assert v("python3 test_calc.py") is True
    assert v("python -m pytest tests/") is True
    assert v("python manage.py migrate") is False
    assert v("python setup.py install") is False
    assert v("npm run build") is False
    assert v("ls -la") is False
