from __future__ import annotations

import json
from pathlib import Path
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
        from adapters.claude_code.common import canonical_invocation, emit, fail_open, project_root, read_payload, transcript_last_assistant_text
        payload = read_payload()
        from core.adapter_observation import finish_turn, resolve_active_invocation
        from core.verify_state import evaluate_stop

        root = project_root(payload)
        invocation = canonical_invocation(payload, "stop", "other", [], "", True, "")
        invocation = resolve_active_invocation(Path(root), invocation)
        _ = finish_turn(Path(root), invocation)
        stop_payload = {
            "project_root": root,
            "stop_hook_active": payload.get("stop_hook_active") is True,
            "assistant_text": transcript_last_assistant_text(payload),
        }
        if isinstance(payload.get("agent"), str) and payload.get("agent"):
            stop_payload.update(
                {
                    "host": invocation.host,
                    "agent": invocation.agent,
                    "session_id": invocation.session_id,
                    "turn_id": invocation.turn_id,
                }
            )
        result = evaluate_stop(stop_payload)
        if result["decision"] == "block":
            return emit({"decision": "block", "reason": str(result["reason"])})
        # allow 경로: systemMessage(사용자 정보용)만 반환한다. hookSpecificOutput.additionalContext를
        # 채우면 Claude Code가 이를 "계속 진행" 신호로 받아 stop_hook_active 사이클에서 모델을 반복
        # 재호출한다(라이브 E2E 발견 A). 통과 시엔 모델에 줄 추가 컨텍스트가 없다.
        message = str(result.get("message", "fable-lite Stop gate allow."))
        return emit({"systemMessage": message})
    except Exception as exc:  # noqa: BLE001
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
