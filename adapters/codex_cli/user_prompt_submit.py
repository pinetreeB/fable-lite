from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import runpy
import sys


def _context(result: Mapping[str, object]) -> str:
    packs = result.get("packs")
    needs_goals = result.get("needs_goals") is True
    lines = [
        "fable-lite 활성화: 작업 규율을 절차로 적용하세요.",
        f"mode={result.get('mode', 'quick')}",
    ]
    if isinstance(packs, list) and "investigation" in packs:
        lines.extend(
            [
                "조사 팩 준수 필수: 출력에 `가설 1:`, `가설 2:`, `가설 3:`, `기각:`, `증거:`를 포함하세요.",
                "수정 전 재현과 경쟁 가설을 먼저 기록하세요.",
            ]
        )
    if isinstance(packs, list) and "verification-grounding" in packs:
        lines.append("렌더/실행 산출물은 RUN→OBSERVE→FIX→RE-RUN 증거 없이는 완료하지 마세요.")
    if needs_goals:
        lines.append("2+ 스토리 작업입니다. goals 체크포인트를 만들거나 사용자에게 명시 확인을 받으세요.")
    return "\n".join(lines)


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
        from core.classify import classify_prompt
        from core.ledger import record_event

        prompt_value = payload.get("prompt")
        prompt = prompt_value if isinstance(prompt_value, str) else ""
        result = classify_prompt({"prompt": prompt})
        packs = result["packs"]
        requires_compliance = isinstance(packs, list) and "investigation" in packs
        record_event(
            {
                "project_root": common["project_root"](payload),
                "event": "prompt",
                "task_mode": result["mode"],
                "prompt": prompt,
                "packs": packs,
                "needs_goals": result["needs_goals"],
                "requires_investigation_compliance": requires_compliance,
            }
        )
        return common["emit"](
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": _context(result),
                }
            }
        )
    except Exception as exc:
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
