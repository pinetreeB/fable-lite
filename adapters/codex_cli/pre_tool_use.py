from __future__ import annotations

import json
from pathlib import Path
import sys


def _fail_open(message: str) -> int:
    data = json.dumps({"systemMessage": f"[smtw] fail-open(게이트 오류, 통과 처리): {message}"}, ensure_ascii=False)
    _ = sys.stdout.buffer.write(data.encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def main() -> int:
    try:
        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from adapters.codex_cli.common import canonical_invocation, emit, project_root, read_payload, tool_command, tool_file_paths, tool_input
        from adapters.intent_command import intent_set_command
        payload = read_payload()
        from core.contract import EDIT_TOOLS, SHELL_TOOLS, evaluate_pretool_contract
        from core.destructive_guard import evaluate_r2_destructive_gate

        tool_name = payload.get("tool_name")
        family = "edit" if tool_name in EDIT_TOOLS else "shell" if tool_name in SHELL_TOOLS else "other"
        root = project_root(payload)
        invocation = canonical_invocation(
            payload,
            "pre_tool",
            family,
            tool_file_paths(payload),
            tool_command(payload),
            False,
            "",
        )
        # R2 first: run before any other ledger read such as resolve_active_invocation.
        # Any exception raised here is absorbed inside destructive_guard as "degraded"
        # so a later broad except fail-open cannot undo this decision.
        r2_result = evaluate_r2_destructive_gate(
            {
                "project_root": root,
                "tool_name": "Bash" if family == "shell" else str(payload.get("tool_name", "")),
                "command": tool_command(payload),
                "host": invocation.host,
                "agent": invocation.agent,
                "session_id": invocation.session_id,
            }
        )
        if r2_result["decision"] == "block":
            return emit({"decision": "block", "reason": str(r2_result["reason"])})

        from core.adapter_observation import begin_invocation, resolve_active_invocation

        invocation = resolve_active_invocation(Path(root), invocation)
        input_text = json.dumps(tool_input(payload), ensure_ascii=False)
        result = evaluate_pretool_contract(
            {
                "project_root": project_root(payload),
                "tool_name": str(payload.get("tool_name", "")),
                "file_paths": tool_file_paths(payload),
                "command": tool_command(payload),
                "prompt": input_text,
                "intent_set_command": intent_set_command(__file__),
                "host": invocation.host,
                "agent": invocation.agent,
                "session_id": invocation.session_id,
                "turn_id": invocation.turn_id,
                "attribution": invocation.scorecard_attribution,
            }
        )
        if result["decision"] == "block":
            return emit({"decision": "block", "reason": str(result["reason"])})
        _ = begin_invocation(Path(project_root(payload)), invocation)
        return emit({})
    except Exception as exc:
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
