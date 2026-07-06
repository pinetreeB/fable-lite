from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys


def _fail_open(message: str) -> int:
    data = json.dumps({"systemMessage": f"fable-lite fail-open: {message}"}, ensure_ascii=False)
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
        from core.verify_state import evaluate_stop

        result = evaluate_stop(
            {
                "project_root": common["project_root"](payload),
                "stop_hook_active": payload.get("stop_hook_active") is True,
                "assistant_text": common["last_assistant_text"](payload),
            }
        )
        if result["decision"] == "block":
            return common["emit"]({"decision": "block", "reason": str(result["reason"])})
        message = str(result.get("message", "fable-lite Stop gate allow."))
        return common["emit"]({"systemMessage": message})
    except Exception as exc:
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
