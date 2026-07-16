from __future__ import annotations

import json
from pathlib import Path
import runpy
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
        common = runpy.run_path(str(Path(__file__).with_name("common.py")))
        payload = common["read_payload"]()
        from core.adapter_observation import observe_post_tool, resolve_active_invocation, verification_covers
        from core.classify import classify_prompt
        from core.contract import EDIT_TOOLS, SHELL_TOOLS, record_contract_authored_event
        from core.ledger import load_ledger, record_event
        from core.provenance_types import ProvenanceStatus
        from core.scope_guard import evaluate_scope
        from core.verification import is_verification_command

        root = common["project_root"](payload)
        tool = payload.get("tool_name")
        family = "edit" if tool in EDIT_TOOLS else "shell" if tool in SHELL_TOOLS else "other"
        if family != "other":
            command = common["tool_command"](payload)
            invocation = common["canonical_invocation"](
                payload,
                "post_tool",
                family,
                common["tool_file_paths"](payload),
                command,
                common["tool_success"](payload),
                common["tool_output"](payload),
            )
            if family == "edit":
                record_contract_authored_event(
                    {
                        "project_root": root,
                        "file_paths": common["tool_file_paths"](payload),
                        "host": invocation.host,
                        "agent": invocation.agent,
                        "session_id": invocation.session_id,
                        "turn_id": invocation.turn_id,
                        "attribution": invocation.scorecard_attribution,
                    }
                )
            invocation = resolve_active_invocation(Path(root), invocation)
            observation = observe_post_tool(Path(root), invocation)
            verification_command = family == "shell" and is_verification_command(command)
            if verification_command:
                covers = verification_covers(Path(root), invocation)
                verification = {
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
                record_event(verification)
                return common["emit"]({"systemMessage": "[smtw] 원장: 검증 기록 / recorded verification."})
            if observation.status is ProvenanceStatus.SCOPE_TOO_LARGE:
                return common["emit"]({})
            if observation.incomplete and not verification_command:
                return common["emit"]({"systemMessage": "[smtw] provenance incomplete; fail-open observation."})
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
                record_event(
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
                return common["emit"](
                    {
                        "systemMessage": str(scope["message"]),
                        "hookSpecificOutput": {
                            "hookEventName": "PostToolUse",
                            "additionalContext": str(scope["message"]),
                        },
                    }
                )
            if family == "edit":
                return common["emit"]({"systemMessage": f"[smtw] provenance: observed {len(paths)} change(s)."})
            return common["emit"]({"systemMessage": f"[smtw] provenance: observed {len(paths)} change(s)."})
        return common["emit"]({})
    except Exception as exc:
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
