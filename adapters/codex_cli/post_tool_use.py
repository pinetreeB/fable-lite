from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"}
SHELL_TOOLS = {"Bash", "PowerShell"}
TEST_TERMS = ("pytest", "python -m pytest", "npm test", "go test", "cargo test", "node --test")


def _fail_open(message: str) -> int:
    data = json.dumps({"systemMessage": f"fable-lite fail-open(게이트 오류, 통과 처리): {message}"}, ensure_ascii=False)
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
        from core.classify import classify_prompt
        from core.ledger import classify_change_kind, load_ledger, record_event
        from core.scope_guard import evaluate_scope

        root = common["project_root"](payload)
        tool = payload.get("tool_name")
        if tool in EDIT_TOOLS:
            paths = common["tool_file_paths"](payload)
            for path in paths:
                record_event(
                    {
                        "project_root": root,
                        "event": "change",
                        "path": path,
                        "kind": classify_change_kind(path),
                    }
                )
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
                record_event({"project_root": root, "event": "scope_warning", "message": scope["message"]})
                return common["emit"](
                    {
                        "systemMessage": str(scope["message"]),
                        "hookSpecificOutput": {
                            "hookEventName": "PostToolUse",
                            "additionalContext": str(scope["message"]),
                        },
                    }
                )
            return common["emit"]({"systemMessage": f"fable-lite 원장: 변경 {len(paths)}건 기록 / recorded {len(paths)} change(s)."})
        if tool in SHELL_TOOLS:
            command = common["tool_command"](payload)
            if any(term in command.lower() for term in TEST_TERMS):
                record_event(
                    {
                        "project_root": root,
                        "event": "verification",
                        "command": command,
                        "success": common["tool_success"](payload),
                        "evidence": common["tool_output"](payload),
                    }
                )
                return common["emit"]({"systemMessage": "fable-lite 원장: 검증 기록 / recorded verification."})
        return common["emit"]({})
    except Exception as exc:
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
