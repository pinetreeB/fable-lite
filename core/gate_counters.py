from __future__ import annotations

from collections.abc import Mapping
from typing import Final, TypeAlias

from .agent_log import ledger_transaction
from .intent import has_intent
from .ledger import JsonValue, load_ledger, save_ledger, state_dir
from .ledger_v2 import refresh_v1_projection
from .verification_covers import active_turn


Decision: TypeAlias = dict[str, JsonValue]
MAX_GOALS_BLOCKS: Final = 2
MAX_INTENT_BLOCKS: Final = 2


def needs_goals_block(payload: Mapping[str, JsonValue]) -> bool:
    root = _project_root(payload)
    return _gate_required(load_ledger(payload), payload, "needs_goals") and not _goals_present(root)


def needs_intent_block(payload: Mapping[str, JsonValue]) -> bool:
    root = _project_root(payload)
    return _gate_required(load_ledger(payload), payload, "intent_required") and not has_intent(root)


def block_goals_once(payload: Mapping[str, JsonValue]) -> Decision:
    root = _project_root(payload)
    goals_present = _goals_present(root)
    # A checkpoint created after this hint can consume one capped block, preserving short RMW locking.
    with ledger_transaction(root):
        ledger = load_ledger(payload)
        if not _gate_required(ledger, payload, "needs_goals") or goals_present:
            return {"decision": "allow", "message": "goals checkpoint is present"}
        blocks = _counter_value(ledger, payload, "goals_blocks")
        if blocks >= MAX_GOALS_BLOCKS:
            return {
                "decision": "allow",
                "message": "goals gate max 2 blocks reached; fail-open allow",
            }
        _set_counter(ledger, payload, "goals_blocks", blocks + 1)
        save_ledger(payload, ledger)
    return {
        "decision": "block",
        "reason": (
            "fable-lite N2: 2+ 스토리 작업은 `.fable-lite/goals.json` 체크포인트가 먼저 필요합니다. "
            "goals plan을 작성하거나 명시 확인 후 다시 시도하세요. "
            "/ Multi-story work requires a goals checkpoint first."
        ),
    }


def block_intent_once(payload: Mapping[str, JsonValue], intent_command: str) -> Decision:
    root = _project_root(payload)
    intent_present = has_intent(root)
    # A checkpoint created after this hint can consume one capped block, preserving short RMW locking.
    with ledger_transaction(root):
        ledger = load_ledger(payload)
        if not _gate_required(ledger, payload, "intent_required") or intent_present:
            return {"decision": "allow", "message": "intent checkpoint is present"}
        blocks = _counter_value(ledger, payload, "intent_blocks")
        if blocks >= MAX_INTENT_BLOCKS:
            return {
                "decision": "allow",
                "message": "intent gate max 2 blocks reached; fail-open allow",
            }
        _set_counter(ledger, payload, "intent_blocks", blocks + 1)
        save_ledger(payload, ledger)
    return {
        "decision": "block",
        "reason": (
            "fable-lite intent gate: 요청 의도가 모호해 수정 전 `.fable-lite/intent.json` 확정이 필요합니다. "
            "`확인질문 N:` 형식으로 목표/범위/비목표를 확인한 뒤 "
            f"`{intent_command}` 명령을 그대로 실행해 기록하세요. "
            "/ Ambiguous edit intent requires intent.json first."
        ),
    }


def _counter_value(
    ledger: Mapping[str, JsonValue], payload: Mapping[str, JsonValue], field: str
) -> int:
    turn = active_turn(ledger, payload)
    state = turn if turn is not None else ledger
    value = state.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _set_counter(
    ledger: dict[str, JsonValue],
    payload: Mapping[str, JsonValue],
    field: str,
    value: int,
) -> None:
    turn = active_turn(ledger, payload)
    if turn is None:
        ledger[field] = value
        return
    turn[field] = value
    _ = refresh_v1_projection(ledger, turn)


def _project_root(payload: Mapping[str, JsonValue]) -> str:
    root = payload.get("project_root") or payload.get("cwd")
    return root if isinstance(root, str) and root else "."


def _goals_present(root: str) -> bool:
    return (state_dir(root) / "goals.json").exists()


def _gate_required(
    ledger: Mapping[str, JsonValue], payload: Mapping[str, JsonValue], field: str
) -> bool:
    if not _has_turn_identity(payload):
        # Identifier-free v1 calls use the projection under the legacy single-agent assumption.
        return ledger.get(field) is True
    turn = active_turn(ledger, payload)
    return turn is not None and turn.get(field) is True


def _has_turn_identity(payload: Mapping[str, JsonValue]) -> bool:
    return any(
        isinstance(payload.get(field), str) and bool(payload.get(field))
        for field in ("agent", "session_id")
    )
