from __future__ import annotations

from importlib import import_module
from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adapters.claude_code.bootstrap import (
        HookContext,
        bootstrap,
        emit,
        fail_open,
        response,
    )
else:
    _bootstrap_module = import_module(
        "adapters.claude_code.bootstrap" if __package__ else "bootstrap"
    )
    HookContext = _bootstrap_module.HookContext
    bootstrap = _bootstrap_module.bootstrap
    emit = _bootstrap_module.emit
    fail_open = _bootstrap_module.fail_open
    response = _bootstrap_module.response


def main() -> int:
    context: HookContext | None = None
    try:
        context = bootstrap("Stop")
        if not context.active or context.root is None:
            return emit(response(context, {}))
        if context.task_mode == "quick":
            return emit(response(context, {}))
        repo_root = Path(__file__).resolve().parents[2]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from adapters.claude_code.common import (
            canonical_invocation,
            transcript_last_assistant_text,
        )

        payload = context.payload
        from core.adapter_observation import (
            finish_turn,
            resolve_active_invocation,
            restart_blocked_turn,
        )
        from core.runtime_env import SCORECARD, smtw_env
        from core.verify_state import evaluate_stop

        root = str(context.root)
        invocation = canonical_invocation(payload, "stop", "other", [], "", True, "")
        invocation = resolve_active_invocation(context.root, invocation)
        _ = finish_turn(context.root, invocation)
        stop_payload = {
            "project_root": root,
            "stop_hook_active": payload.get("stop_hook_active") is True,
            "assistant_text": transcript_last_assistant_text(payload),
            "host": invocation.host,
            "agent": invocation.agent,
            "session_id": invocation.session_id,
            "turn_id": invocation.turn_id,
            "attribution": invocation.scorecard_attribution,
        }
        result = evaluate_stop(stop_payload)
        if result["decision"] == "block":
            restart_blocked_turn(context.root, invocation)
            return emit(
                response(
                    context,
                    {"decision": "block", "reason": str(result["reason"])},
                )
            )
        if smtw_env(SCORECARD) == "1":
            message = str(result.get("message", ""))
            scorecard = "\n".join(
                line
                for line in message.splitlines()
                if line.startswith("[smtw] 이번 세션")
            )
            if scorecard:
                return emit(response(context, {"systemMessage": scorecard}))
        return emit(response(context, {}))
    except Exception as exc:  # noqa: BLE001
        return fail_open(str(exc), context)


if __name__ == "__main__":
    raise SystemExit(main())
