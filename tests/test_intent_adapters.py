from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias


ROOT = Path(__file__).resolve().parents[1]
CLAUDE = ROOT / "adapters" / "claude_code"
CODEX = ROOT / "adapters" / "codex_cli"
AGY = ROOT / "adapters" / "antigravity" / "oma_hook.py"

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
HookPayload: TypeAlias = dict[str, JsonValue]
HookOutput: TypeAlias = dict[str, JsonValue]


def run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-m", "fable_lite", *args],
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": python_path},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def run_hook(script: Path, payload: HookPayload | str, *argv: str) -> HookOutput:
    raw_input = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    process = subprocess.run(
        [sys.executable, str(script), *argv],
        input=raw_input,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout or "{}")


def read_ledger(root: Path) -> dict[str, JsonValue]:
    raw = json.loads((root / ".fable-lite" / "ledger.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def object_value(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def launcher_command_prefix() -> str:
    return f'python "{ROOT / "fable-lite-cli.py"}" intent set --root .'


def ambiguous_prompt_payload(tmp_path: Path) -> HookPayload:
    return {"cwd": str(tmp_path), "prompt": "이거 알아서 고쳐줘", "session_id": "s-intent"}


def edit_payload(tmp_path: Path) -> HookPayload:
    return {
        "cwd": str(tmp_path),
        "tool_name": "Edit",
        "tool_input": {"file_path": "app.py", "new_string": "print('ok')\n"},
        "session_id": "s-intent",
    }


def test_claude_intent_gate_blocks_edit_until_intent_cli_set_and_clears_old_intent(tmp_path: Path) -> None:
    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir()
    (state_dir / "intent.json").write_text("{}", encoding="utf-8")

    prompt_result = run_hook(CLAUDE / "user_prompt_submit.py", ambiguous_prompt_payload(tmp_path))
    pre_block = run_hook(CLAUDE / "pre_tool_use.py", edit_payload(tmp_path))
    set_result = run_cli(
        [
            "intent",
            "set",
            "--root",
            str(tmp_path),
            "--goal",
            "app.py 수정",
            "--scope",
            "app.py",
            "--confirmed-at-prompt",
            "이거 알아서 고쳐줘",
        ]
    )
    pre_allow = run_hook(CLAUDE / "pre_tool_use.py", edit_payload(tmp_path))

    context = object_value(prompt_result["hookSpecificOutput"])["additionalContext"]
    ledger = read_ledger(tmp_path)
    packs = ledger["packs"]
    assert isinstance(context, str)
    assert "확인질문" in context
    assert launcher_command_prefix() in context
    assert isinstance(packs, list)
    assert "intent-interview" in packs
    assert ledger["intent_required"] is True
    assert ledger["ambiguity_score"] == 4
    assert pre_block["decision"] == "block"
    assert "의도" in str(pre_block["reason"])
    assert launcher_command_prefix() in str(pre_block["reason"])
    assert set_result.returncode == 0
    assert "decision" not in pre_allow


def test_claude_intent_gate_blocks_at_most_twice_and_never_blocks_bash(tmp_path: Path) -> None:
    run_hook(CLAUDE / "user_prompt_submit.py", ambiguous_prompt_payload(tmp_path))
    first = run_hook(CLAUDE / "pre_tool_use.py", edit_payload(tmp_path))
    second = run_hook(CLAUDE / "pre_tool_use.py", edit_payload(tmp_path))
    third = run_hook(CLAUDE / "pre_tool_use.py", edit_payload(tmp_path))
    bash = run_hook(
        CLAUDE / "pre_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "Bash",
            "tool_input": {"command": "python -m pytest tests/"},
            "session_id": "s-intent",
        },
    )
    ledger = read_ledger(tmp_path)

    assert first["decision"] == "block"
    assert second["decision"] == "block"
    assert "decision" not in third
    assert "decision" not in bash
    assert ledger["intent_blocks"] == 2


def test_codex_intent_gate_records_prompt_and_blocks_apply_patch(tmp_path: Path) -> None:
    run_hook(CODEX / "user_prompt_submit.py", ambiguous_prompt_payload(tmp_path))
    result = run_hook(
        CODEX / "pre_tool_use.py",
        {
            "cwd": str(tmp_path),
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** Add File: app.py\n+print('ok')\n*** End Patch\n"},
            "session_id": "s-intent",
        },
    )
    ledger = read_ledger(tmp_path)

    assert result["decision"] == "block"
    assert "의도" in str(result["reason"])
    assert launcher_command_prefix() in str(result["reason"])
    assert ledger["intent_required"] is True
    assert ledger["ambiguity_score"] == 4


def test_antigravity_intent_gate_records_prompt_and_blocks_edit(tmp_path: Path) -> None:
    prompt_result = run_hook(AGY, ambiguous_prompt_payload(tmp_path), "BeforeModel")
    result = run_hook(
        AGY,
        {
            "cwd": str(tmp_path),
            "metadata": {
                "tool_name": "replace_file_content",
                "tool_input": {"TargetFile": "app.py"},
            },
        },
        "BeforeTool",
    )
    context = object_value(prompt_result["hookSpecificOutput"])["additionalContext"]
    ledger = read_ledger(tmp_path)

    assert isinstance(context, str)
    assert "확인질문" in context
    assert launcher_command_prefix() in context
    assert result["decision"] == "block"
    assert "의도" in str(result["reason"])
    assert launcher_command_prefix() in str(result["reason"])
    assert ledger["intent_required"] is True
    assert ledger["ambiguity_score"] == 4


def test_intent_hooks_fail_open_on_malformed_payload() -> None:
    result = run_hook(CLAUDE / "pre_tool_use.py", "{not-json")

    assert str(result["systemMessage"]).startswith("fable-lite fail-open")
