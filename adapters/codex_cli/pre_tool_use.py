from __future__ import annotations

import json
from pathlib import Path
import runpy
import sys


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
        from core.contract import evaluate_pretool_contract

        input_text = json.dumps(common["tool_input"](payload), ensure_ascii=False)
        result = evaluate_pretool_contract(
            {
                "project_root": common["project_root"](payload),
                "tool_name": str(payload.get("tool_name", "")),
                "file_paths": common["tool_file_paths"](payload),
                "command": common["tool_command"](payload),
                "prompt": input_text,
            }
        )
        if result["decision"] == "block":
            return common["emit"]({"decision": "block", "reason": str(result["reason"])})
        return common["emit"]({})
    except Exception as exc:
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
