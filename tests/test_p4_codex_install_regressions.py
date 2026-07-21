from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import TypeAlias
from unittest.mock import patch

import pytest

from adapters.codex_cli import install as codex_install
from core.ledger_storage import ledger_path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "adapters" / "codex_cli" / "install.py"

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


def _object(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def _list(value: JsonValue) -> list[JsonValue]:
    assert isinstance(value, list)
    return value


def _isolated_env(home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "CODEX_HOME": str(home / ".codex"),
            "HOME": str(home),
            "USERPROFILE": str(home),
        }
    )
    return env


def _install(
    target: Path,
    env: dict[str, str],
    *,
    upgrade: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(INSTALLER), "--target", str(target)]
    if upgrade:
        command.append("--upgrade")
    return subprocess.run(
        command,
        cwd=target,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_codex_installer_refuses_existing_hooks_without_changing_bytes(tmp_path: Path) -> None:
    # Given: a target project already owns a Codex hooks manifest.
    target = tmp_path / "existing project"
    hooks_path = target / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    original = b'{"owner":"user","hooks":[]}\r\n'
    hooks_path.write_bytes(original)
    home = tmp_path / "isolated home"
    home.mkdir()

    # When: the self-locating installer is run again.
    result = _install(target, _isolated_env(home))

    # Then: installation fails closed and preserves the exact user bytes.
    assert result.returncode == 1
    assert hooks_path.read_bytes() == original
    assert "Refusing to overwrite" in result.stderr


def test_codex_installer_upgrade_replaces_only_owned_entries(
    tmp_path: Path,
) -> None:
    target = tmp_path / "upgrade project"
    hooks_path = target / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    foreign_entry = {
        "type": "command",
        "command": "python foreign.py",
        "foreign": True,
    }
    existing = {
        "owner": "user",
        "hooks": {
            "UserPromptSubmit": [
                {"matcher": "foreign", "hooks": [foreign_entry]},
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python C:/old/adapters/codex_cli/user_prompt_submit.py",
                            "statusMessage": "old smtw hook",
                        }
                    ]
                },
            ],
            "ForeignEvent": [{"hooks": [foreign_entry]}],
        },
    }
    hooks_path.write_text(json.dumps(existing), encoding="utf-8")
    home = tmp_path / "isolated home"
    home.mkdir()

    result = _install(target, _isolated_env(home), upgrade=True)

    assert result.returncode == 0, result.stderr
    upgraded = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert upgraded["owner"] == "user"
    assert upgraded["hooks"]["ForeignEvent"] == [{"hooks": [foreign_entry]}]
    prompt_matchers = upgraded["hooks"]["UserPromptSubmit"]
    prompt_entries = [
        entry
        for matcher in prompt_matchers
        for entry in matcher.get("hooks", [])
    ]
    assert foreign_entry in prompt_entries
    owned = [
        entry
        for entry in prompt_entries
        if "/adapters/codex_cli/user_prompt_submit.py"
        in str(entry.get("command", "")).replace("\\", "/")
    ]
    assert len(owned) == 1
    assert "C:/old/" not in str(owned[0]["command"])
    assert set(codex_install.EVENT_SCRIPTS) <= set(upgraded["hooks"])


def test_codex_installer_upgrade_preserves_invalid_existing_bytes(
    tmp_path: Path,
) -> None:
    target = tmp_path / "invalid upgrade"
    hooks_path = target / ".codex" / "hooks.json"
    hooks_path.parent.mkdir(parents=True)
    original = b"{not-json\r\n"
    hooks_path.write_bytes(original)
    home = tmp_path / "isolated home"
    home.mkdir()

    result = _install(target, _isolated_env(home), upgrade=True)

    assert result.returncode == 1
    assert hooks_path.read_bytes() == original


def test_atomic_upgrade_failure_leaves_existing_manifest_intact(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "hooks.json"
    original = b'{"owner":"user"}\n'
    destination.write_bytes(original)

    with patch.object(
        codex_install.os,
        "replace",
        side_effect=OSError("injected replace failure"),
    ):
        with pytest.raises(OSError, match="injected replace failure"):
            codex_install._atomic_replace_text(destination, '{"owner":"smtw"}\n')

    assert destination.read_bytes() == original
    assert list(tmp_path.glob("hooks.json.*.tmp")) == []


def test_external_self_located_stop_blocks_unverified_change(tmp_path: Path) -> None:
    # Given: hooks are installed into an external CJK path with no PYTHONPATH.
    target = tmp_path / "외부 프로젝트 with spaces"
    target.mkdir()
    home = tmp_path / "격리 home"
    home.mkdir()
    env = _isolated_env(home)
    install = _install(target, env)
    assert install.returncode == 0, install.stderr
    raw = json.loads((target / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    manifest: JsonObject = raw
    hooks = _object(manifest["hooks"])

    def run_installed(event: str, payload: JsonObject) -> JsonObject:
        matchers = _list(hooks[event])
        command_hooks = _list(_object(matchers[0])["hooks"])
        entry = _object(command_hooks[0])
        command = entry["commandWindows" if os.name == "nt" else "command"]
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
        output = json.loads(process.stdout or "{}")
        assert isinstance(output, dict)
        return output

    run_installed(
        "UserPromptSubmit",
        {
            "cwd": str(target),
            "prompt": "app.py에 계산 페이지를 구현하고 회귀 테스트까지 실행해줘",
            "session_id": "external-p4",
        },
    )
    (target / "app.py").write_text("FIX=True\n", encoding="utf-8")
    run_installed(
        "PostToolUse",
        {
            "cwd": str(target),
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Update File: app.py\n+FIX=True\n*** End Patch\n"
            },
            "tool_response": "Exit code: 0\nOutput:\nSuccess. Updated app.py",
            "session_id": "external-p4",
        },
    )

    # When: the externally loaded Stop hook sees no later successful verification.
    result = run_installed(
        "Stop",
        {
            "cwd": str(target),
            "last_assistant_message": "변경을 완료했습니다.",
            "stop_hook_active": False,
            "session_id": "external-p4",
        },
    )

    # Then: self-location reaches the real core gate and blocks the unverified change.
    ledger = _object(json.loads(ledger_path(str(target)).read_text(encoding="utf-8")))
    assert ledger["task_mode"] == "normal"
    assert result["decision"] == "block"
