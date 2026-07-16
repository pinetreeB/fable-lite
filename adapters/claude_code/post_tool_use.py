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
        from adapters.claude_code.common import (
            canonical_invocation,
            emit,
            project_root,
            read_payload,
            tool_command,
            tool_file_paths,
            tool_output,
            tool_success,
        )
        payload = read_payload()
        raw_hook_event = payload.get("hook_event_name")
        hook_event_name = (
            raw_hook_event
            if isinstance(raw_hook_event, str)
            and raw_hook_event in {"PostToolUse", "PostToolUseFailure"}
            else "PostToolUse"
        )
        from core.adapter_observation import observe_post_tool, resolve_active_invocation, verification_covers
        from core.classify import classify_prompt
        from core.contract import EDIT_TOOLS, SHELL_TOOLS, record_contract_authored_event
        from core.ledger import JsonObject, load_ledger, record_event
        from core.provenance_types import ProvenanceStatus
        from core.scope_guard import evaluate_scope
        from core.verification import is_verification_command

        root = project_root(payload)
        tool = payload.get("tool_name")
        family = "edit" if tool in EDIT_TOOLS else "shell" if tool in SHELL_TOOLS else "other"
        if family != "other":
            command = tool_command(payload)
            invocation = canonical_invocation(
                payload,
                "post_tool",
                family,
                tool_file_paths(payload),
                command,
                tool_success(payload),
                tool_output(payload),
            )
            invocation = resolve_active_invocation(Path(root), invocation)
            attribution = invocation.scorecard_attribution
            if attribution == "legacy_default" and invocation.session_id != "default":
                attribution = "exact"
            if family == "edit":
                record_contract_authored_event(
                    {
                        "project_root": root,
                        "file_paths": tool_file_paths(payload),
                        "host": invocation.host,
                        "agent": invocation.agent,
                        "session_id": invocation.session_id,
                        "turn_id": invocation.turn_id,
                        "attribution": attribution,
                    }
                )
            observation = observe_post_tool(Path(root), invocation)
            verification_command = family == "shell" and is_verification_command(command)
            if verification_command:
                covers = verification_covers(Path(root), invocation)
                verification: JsonObject = {
                    "project_root": root,
                    "event": "verification",
                    "host": invocation.host,
                    "agent": invocation.agent,
                    "session_id": invocation.session_id,
                    "turn_id": invocation.turn_id,
                    "invocation_id": invocation.invocation_id,
                    "command": command,
                    "success": invocation.success,
                    "evidence": invocation.evidence,
                }
                if covers is not None:
                    verification["covers"] = covers
                _ = record_event(verification)
                return emit({"systemMessage": "[smtw] 원장: 검증 기록 / recorded verification."})
            if observation.status is ProvenanceStatus.SCOPE_TOO_LARGE:
                return emit({})
            if observation.incomplete and not verification_command:
                return emit({"systemMessage": "[smtw] provenance incomplete; fail-open observation."})
            paths = list(observation.changed_paths)
            ledger = load_ledger({"project_root": root})
            prompt = ledger.get("prompt")
            prompt_text = prompt if isinstance(prompt, str) else ""
            requested = classify_prompt({"prompt": prompt_text})["requested_paths"] if prompt_text else []
            scope = evaluate_scope(
                {
                    "project_root": root,
                    "prompt": prompt_text,
                    "requested_paths": requested,
                    "changed_files": paths,
                }
                )
            if scope["decision"] == "warn":
                _ = record_event(
                    {
                        "project_root": root,
                        "event": "scope_warning",
                        "host": invocation.host,
                        "agent": invocation.agent,
                        "session_id": invocation.session_id,
                        "turn_id": invocation.turn_id,
                        "message": scope["message"],
                    }
                )
                return emit(
                    {
                        "systemMessage": str(scope["message"]),
                        "hookSpecificOutput": {
                            "hookEventName": hook_event_name,
                            "additionalContext": str(scope["message"]),
                        },
                    }
                )
            if family == "edit":
                return emit({"systemMessage": f"[smtw] provenance: observed {len(paths)} change(s)."})
            return emit({"systemMessage": f"[smtw] provenance: observed {len(paths)} change(s)."})
        return emit({})
    except Exception as exc:  # noqa: BLE001
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
