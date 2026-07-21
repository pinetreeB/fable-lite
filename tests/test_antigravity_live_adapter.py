from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import cast

from adapters.antigravity import tool_io
from adapters.antigravity.hook_common import canonical_invocation, project_root
from core.agent_log import load_agent_events
from core.ledger import JsonObject, load_ledger


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "adapters" / "antigravity" / "oma_hook.py"
HOOKS = ROOT / "adapters" / "antigravity" / "hooks.json"
LIVE_EVENTS = (
    "PreInvocation",
    "PostInvocation",
    "PreToolUse",
    "PostToolUse",
    "Stop",
)
TOOL_EVENTS = ("PreToolUse", "PostToolUse")
FLAT_EVENTS = ("PreInvocation", "PostInvocation", "Stop")


def _live_payload(workspace: Path, tool_name: str = "view_file") -> JsonObject:
    return {
        "conversationId": "agy-live-session",
        "modelName": "gemini-3.1-pro",
        "stepIdx": 7,
        "artifactDirectoryPath": str(workspace / ".agy" / "artifacts"),
        "transcriptPath": str(workspace / ".agy" / "transcript.json"),
        "workspacePaths": [str(workspace)],
        "toolCall": {"name": tool_name, "args": {"path": "src/app.py"}},
        "error": False,
    }


def _run(event: str, stdin: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK), event],
        input=stdin,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
    )


def test_hooks_template_uses_official_mixed_schema_and_absolute_path_placeholders() -> None:
    config = cast(dict[str, object], json.loads(HOOKS.read_text(encoding="utf-8")))

    assert set(config) == {"show-me-the-work"}
    group = cast(dict[str, object], config["show-me-the-work"])
    assert set(group) == set(LIVE_EVENTS)
    for event in TOOL_EVENTS:
        registrations = cast(list[object], group[event])
        assert len(registrations) == 1
        registration = cast(dict[str, object], registrations[0])
        assert registration["matcher"] == ""
        hooks = cast(list[object], registration["hooks"])
        assert len(hooks) == 1
        hook = cast(dict[str, object], hooks[0])
        assert set(hook) == {"type", "command", "timeout"}
        assert hook["type"] == "command"
        command = cast(str, hook["command"])
        assert command.startswith("{PYTHON_EXECUTABLE} ")
        assert "{SMTW_ROOT}/adapters/antigravity/oma_hook.py" in command
        # Antigravity(Go)가 cmd /c 재인용으로 따옴표 포함 command를 깨뜨리므로
        # 템플릿은 따옴표 없는 공백-미포함 절대 경로만 허용한다 (2026-07-16 실측).
        assert '"' not in command
        assert command.endswith(event)
        assert hook["timeout"] == 30

    for event in FLAT_EVENTS:
        registrations = cast(list[object], group[event])
        assert len(registrations) == 1
        hook = cast(dict[str, object], registrations[0])
        assert set(hook) == {"type", "command", "timeout"}
        assert hook["type"] == "command"
        command = cast(str, hook["command"])
        assert command.startswith("{PYTHON_EXECUTABLE} ")
        assert "{SMTW_ROOT}/adapters/antigravity/oma_hook.py" in command
        assert '"' not in command
        assert command.endswith(event)
        assert hook["timeout"] == 30


def test_real_tool_names_map_to_core_names_and_families() -> None:
    cases = {
        "view_file": ("Read", "read"),
        "write_to_file": ("Edit", "edit"),
        "run_command": ("Bash", "shell"),
        "call_mcp_tool": ("call_mcp_tool", "other"),
        "manage_task": ("manage_task", "other"),
    }

    for real_name, expected in cases.items():
        name, tool_input = tool_io.extract_tool_info(
            {"toolCall": {"name": real_name, "args": {"path": "src/app.py"}}}
        )
        assert (name, tool_io.tool_family(name)) == expected
        assert tool_input == {"path": "src/app.py"}


def test_real_payload_uses_workspace_conversation_and_step_identity(tmp_path: Path) -> None:
    payload = _live_payload(tmp_path)

    pre = canonical_invocation(payload, "pre_tool", "read", ["src/app.py"], "", True, "")
    post = canonical_invocation(payload, "post_tool", "read", ["src/app.py"], "", True, "")

    assert project_root(payload) == str(tmp_path)
    assert pre.session_id == "agy-live-session"
    assert pre.turn_id == "turn:agy-live-session:7"
    assert pre.invocation_id == post.invocation_id == "tool:agy-live-session:7:read"
    assert pre.identity_synthetic is False


def test_post_tool_error_is_a_failed_result() -> None:
    success, evidence = tool_io.verification_result(
        {"toolCall": {"name": "run_command", "args": {"command": "pytest"}}, "error": "boom"}
    )

    assert success is False
    assert evidence == "boom"


def test_five_live_events_dispatch_and_all_inputs_fail_open_with_exit_zero(tmp_path: Path) -> None:
    payload = _live_payload(tmp_path)
    valid = json.dumps(payload, ensure_ascii=False)

    for event in LIVE_EVENTS:
        for stdin in (valid, "{not-json", ""):
            process = _run(event, stdin, tmp_path)
            assert process.returncode == 0, (event, stdin, process.stderr)
            output = cast(object, json.loads(process.stdout))
            assert isinstance(output, dict)

    ledger = load_ledger({"project_root": str(tmp_path)})
    turns = cast(dict[str, object], ledger["active_turns"])
    assert "antigravity:agy-live-session:antigravity" not in turns
    events = load_agent_events(str(tmp_path), "antigravity")
    assert events is not None
    assert any(event.get("event") == "turn_finished" for event in events)
