from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import TypeAlias, cast

import pytest

from core.agent_log import load_agent_events
from core.contract import namespaced_contract_path
from core.ledger import JsonValue, record_event


ROOT = Path(__file__).resolve().parents[1]
JsonObject: TypeAlias = dict[str, JsonValue]


def _run_adapter(path: Path, payload: JsonObject, *args: str) -> JsonObject:
    process = subprocess.run(
        [sys.executable, str(path), *args],
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert process.returncode == 0, process.stderr
    output = cast(object, json.loads(process.stdout or "{}"))
    assert isinstance(output, dict)
    return cast(JsonObject, output)


@pytest.mark.parametrize(
    ("adapter", "args", "tool_name", "expected_decision"),
    [
        ("antigravity/oma_hook.py", ("PreToolUse",), "run_command", "deny"),
        ("antigravity/oma_hook.py", ("PreToolUse",), "run_shell_command", "deny"),
        ("antigravity/oma_hook.py", ("PreToolUse",), "ShellTool", "deny"),
        ("codex_cli/pre_tool_use.py", (), "Bash", "block"),
    ],
)
def test_adapter_raw_shell_names_all_reach_r2(
    tmp_path: Path,
    adapter: str,
    args: tuple[str, ...],
    tool_name: str,
    expected_decision: str,
) -> None:
    # Given: each adapter receives one of its raw shell tool names.
    payload: JsonObject = {
        "cwd": str(tmp_path),
        "tool_name": tool_name,
        "tool_input": {"command": "env rm -rf /"},
    }

    # When: the real PreTool adapter evaluates the request.
    result = _run_adapter(ROOT / "adapters" / adapter, payload, *args)

    # Then: canonical shell-family wiring reaches R2 and denies the attack.
    assert result.get("decision") == expected_decision, result
    assert "R2" in str(result.get("reason", "")), result


def test_claude_adapter_recovers_exact_identity_for_contract_authoring(
    tmp_path: Path,
) -> None:
    # Given: Claude's hook payload omits session_id, but one exact active turn identifies it.
    session_id = "live-session"
    agent_key = f"claude_code:{session_id}:claude"
    contract = namespaced_contract_path(str(tmp_path), agent_key)
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "host": "claude_code",
            "session_id": session_id,
            "agent": "claude",
            "prompt": "write my exact namespaced contract",
        }
    )
    payload: JsonObject = {
        "cwd": str(tmp_path),
        "tool_name": "Write",
        "tool_input": {"file_path": str(contract), "content": "{}"},
    }

    # When: the real PreTool adapter recovers the active exact identity.
    pre_result = _run_adapter(ROOT / "adapters" / "claude_code" / "pre_tool_use.py", payload)

    # Then: the own-contract Edit-family exception avoids the authoring deadlock.
    assert pre_result.get("decision") != "block", pre_result

    contract.parent.mkdir(parents=True, exist_ok=True)
    _ = contract.write_text(
        json.dumps(
            {
                "restated_goal": "repair R2",
                "acceptance": ["tests pass"],
                "evidence": ["python -m pytest tests"],
            }
        ),
        encoding="utf-8",
    )
    post_payload = payload | {"tool_response": {"success": True}}

    # When: the real PostTool adapter observes the successful Write.
    _ = _run_adapter(ROOT / "adapters" / "claude_code" / "post_tool_use.py", post_payload)

    # Then: it records the exact identity's contract_authored dual-evidence event.
    events = load_agent_events(str(tmp_path), "claude") or []
    authored = [event for event in events if event.get("event") == "contract_authored"]
    assert len(authored) == 1
    assert str(authored[0].get("contract_path", "")).endswith(contract.name)
