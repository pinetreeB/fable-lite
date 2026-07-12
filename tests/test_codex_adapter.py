from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias

from adapters.codex_cli.install import render_hooks


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
    _ = (tmp_path / "settings.py").write_text("DEBUG=True\n", encoding="utf-8")
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
    # v1.1.3: N1 마커는 파일 변경이 있는 턴에만 요구되므로 변경 이벤트를 먼저 기록한다.
    patch = "*** Begin Patch\n*** Update File: app.py\n+FIX=True\n*** End Patch\n"
    _ = (tmp_path / "app.py").write_text("FIX=True\n", encoding="utf-8")
    run_hook(
        "post_tool_use.py",
        {
            "cwd": str(tmp_path),
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": patch},
            "tool_response": "Exit code: 0\nWall time: 0 seconds\nOutput:\nSuccess. Updated the following files:\nM app.py\n",
            "session_id": "codex-session-1",
        },
    )
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


def test_codex_stop_allows_answer_only_investigation_turn(tmp_path: Path) -> None:
    # v1.1.3: 변경 없는 답변 전용 턴은 N1 마커 면제.
    run_hook("user_prompt_submit.py", codex_prompt_payload(tmp_path, "버그 고쳐줘 안되는데요"))
    payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "Stop",
        "last_assistant_message": "원인은 설정입니다.",
        "stop_hook_active": False,
        "session_id": "codex-session-1",
    }

    result = run_hook("stop.py", payload)

    assert result.get("decision") != "block"


def test_codex_posttool_records_bash_script_and_make_test_as_verification(tmp_path: Path) -> None:
    # v1 릴리스 심사 H1/H3 회귀: codex_cli는 구버전 TEST_TERMS를 쓰고 있어
    # bash 스크립트 재실행·make test 둘 다 미인식이었다. core.verification 공유 후 인식돼야 한다.
    bash_payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "bash test.sh"},
        "tool_response": "Exit code: 0\nWall time: 1 seconds\nOutput:\nok\n",
        "session_id": "codex-session-1",
    }
    make_payload: HookPayload = {
        "cwd": str(tmp_path),
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "make test"},
        "tool_response": "Exit code: 0\nWall time: 1 seconds\nOutput:\nok\n",
        "session_id": "codex-session-1",
    }

    bash_result = run_hook("post_tool_use.py", bash_payload)
    make_result = run_hook("post_tool_use.py", make_payload)

    assert "recorded verification." in str(bash_result["systemMessage"])
    assert "recorded verification." in str(make_result["systemMessage"])


def test_codex_hooks_fail_open_on_malformed_payload() -> None:
    result = run_hook("pre_tool_use.py", "{not-json")

    assert str(result["systemMessage"]).startswith("fable-lite fail-open")


def test_codex_installer_loads_all_hook_commands_from_external_project(tmp_path: Path) -> None:
    target = tmp_path / "외부 프로젝트 with spaces"
    target.mkdir()
    isolated_home = tmp_path / "격리 home"
    isolated_home.mkdir()
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update({"CODEX_HOME": str(isolated_home / ".codex"), "HOME": str(isolated_home), "USERPROFILE": str(isolated_home)})

    install = subprocess.run(
        [sys.executable, str(ADAPTERS / "install.py"), "--target", str(target)],
        cwd=target,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert install.returncode == 0, install.stderr
    hooks_path = target / ".codex" / "hooks.json"
    assert hooks_path.is_file()
    assert not (target / ".codex" / "config.toml").exists()

    installed = object_value(json.loads(hooks_path.read_text(encoding="utf-8")))
    hooks = object_value(installed["hooks"])
    payloads: dict[str, HookPayload] = {
        "UserPromptSubmit": codex_prompt_payload(target, "README.md 제목 오타를 수정하고 테스트를 실행해줘"),
        "PreToolUse": {
            "cwd": str(target),
            "hook_event_name": "PreToolUse",
            "tool_name": "PowerShell" if os.name == "nt" else "Bash",
            "tool_input": {"command": "pwd"},
            "tool_use_id": "call_pre",
            "session_id": "external-session",
        },
        "PostToolUse": {
            "cwd": str(target),
            "hook_event_name": "PostToolUse",
            "tool_name": "PowerShell" if os.name == "nt" else "Bash",
            "tool_input": {"command": "python -m pytest -q"},
            "tool_response": "Exit code: 0\nWall time: 1 seconds\nOutput:\n1 passed\n",
            "tool_use_id": "call_post",
            "session_id": "external-session",
        },
        "Stop": {
            "cwd": str(target),
            "hook_event_name": "Stop",
            "last_assistant_message": "README 제목 오타를 수정하고 테스트했습니다.",
            "stop_hook_active": False,
            "session_id": "external-session",
        },
    }
    results: dict[str, HookOutput] = {}

    for event, payload in payloads.items():
        event_hooks = list_value(hooks[event])
        command_hooks = list_value(object_value(event_hooks[0])["hooks"])
        command = object_value(command_hooks[0])["commandWindows" if os.name == "nt" else "command"]
        assert isinstance(command, str)
        args: str | list[str] = command if os.name == "nt" else shlex.split(command)
        process = subprocess.run(
            args,
            cwd=target,
            env=env,
            input=json.dumps(payload, ensure_ascii=False),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.returncode == 0, f"{event}: {process.stderr}"
        output = json.loads(process.stdout)
        assert isinstance(output, dict)
        results[event] = output

    prompt_output = object_value(results["UserPromptSubmit"]["hookSpecificOutput"])
    assert prompt_output["hookEventName"] == "UserPromptSubmit"
    assert isinstance(prompt_output["additionalContext"], str)
    assert results["PreToolUse"] == {}
    assert isinstance(results["PostToolUse"]["systemMessage"], str)
    assert isinstance(results["Stop"]["systemMessage"], str)


def test_codex_hook_renderer_quotes_windows_source_path(tmp_path: Path) -> None:
    if os.name != "nt":
        return
    marker = tmp_path / "injected.txt"
    fake_root = Path(f"C:/fable'; Set-Content -LiteralPath '{marker.as_posix()}' -Value 'INJECTED'; #")
    assert isinstance(rendered := render_hooks(fake_root), str)
    command = json.loads(rendered)["hooks"]["UserPromptSubmit"][0]["hooks"][0]["commandWindows"]
    _ = subprocess.run(command, input="{}", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False)
    assert not marker.exists()
