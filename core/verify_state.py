from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

from .agent_log import ledger_transaction
from .ledger import JsonObject, JsonValue, load_ledger, save_ledger
from .ledger_v1 import sequence_value
from .ledger_v2 import refresh_v1_projection
from .compliance import check_investigation_compliance
from .verification_covers import active_turn, covers_verified

Decision: TypeAlias = dict[str, JsonValue]

MAX_STOP_BLOCKS = 2


def _as_str_list(value: JsonValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_result_list(value: JsonValue | None) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _positive_sequence(value: JsonValue | None) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def has_successful_verification(
    ledger: Mapping[str, JsonValue],
    payload: Mapping[str, JsonValue] | None = None,
) -> bool:
    turn = active_turn(ledger, payload)
    if turn is not None and (covers_result := covers_verified(turn)) is not None:
        return covers_result
    state: Mapping[str, JsonValue] = turn if turn is not None else ledger
    results = _as_result_list(state.get("verification_results"))
    if _legacy_seq_less_verification(ledger, turn):
        return any(result.get("success") is True for result in results)
    last_change_seq = _positive_sequence(state.get("last_change_seq"))
    if last_change_seq is None:
        return any(result.get("success") is True for result in results)
    return any(
        result.get("success") is True
        and (verification_seq := _positive_sequence(result.get("seq"))) is not None
        and verification_seq > last_change_seq
        for result in results
    )


def _legacy_seq_less_verification(
    ledger: Mapping[str, JsonValue], turn: JsonObject | None
) -> bool:
    if turn is not None:
        return (
            turn.get("migration_mode") == "legacy_turn"
            and turn.get("legacy_seq_less") is True
        )
    active_turns = ledger.get("active_turns")
    if not isinstance(active_turns, dict):
        return False
    return any(
        isinstance(turn, dict)
        and turn.get("migration_mode") == "legacy_turn"
        and turn.get("legacy_seq_less") is True
        for turn in active_turns.values()
    )


def _docs_only(ledger: Mapping[str, JsonValue]) -> bool:
    changed = _as_str_list(ledger.get("changed_files_seen"))
    kinds = set(_as_str_list(ledger.get("change_kinds")))
    return bool(changed) and bool(kinds) and kinds <= {"docs"}


def _assistant_text(payload: Mapping[str, JsonValue]) -> str:
    value = payload.get("assistant_text")
    return value if isinstance(value, str) else ""


def _requires_investigation_compliance(ledger: Mapping[str, JsonValue]) -> bool:
    return ledger.get("requires_investigation_compliance") is True


def _project_root(payload: Mapping[str, JsonValue]) -> str:
    root = payload.get("project_root") or payload.get("cwd")
    return root if isinstance(root, str) and root else "."


def _stop_blocks(ledger: Mapping[str, JsonValue], payload: Mapping[str, JsonValue]) -> int:
    turn = active_turn(ledger, payload)
    if turn is None:
        return sequence_value(ledger.get("stop_blocks"))
    blocks = turn.get("blocks")
    return sequence_value(blocks.get("stop")) if isinstance(blocks, dict) else 0


def _increment_stop_block(ledger: JsonObject, payload: Mapping[str, JsonValue]) -> None:
    turn = active_turn(ledger, payload)
    if turn is None:
        ledger["stop_blocks"] = sequence_value(ledger.get("stop_blocks")) + 1
        return
    blocks = turn.get("blocks")
    if not isinstance(blocks, dict):
        blocks = {"stop": 0}
        turn["blocks"] = blocks
    blocks["stop"] = sequence_value(blocks.get("stop")) + 1
    _ = refresh_v1_projection(ledger, turn)


def _record_stop_block(
    payload: Mapping[str, JsonValue], ledger: JsonObject, decision: Decision
) -> Decision:
    if _stop_blocks(ledger, payload) >= MAX_STOP_BLOCKS:
        return {
            "decision": "allow",
            "message": "최대 2회 차단 후 통과합니다 / allowing after max 2 blocks.",
        }
    _increment_stop_block(ledger, payload)
    save_ledger(payload, ledger)
    return decision


def evaluate_without_io(
    ledger: Mapping[str, JsonValue], payload: Mapping[str, JsonValue]
) -> Decision:
    turn = active_turn(ledger, payload)
    state: Mapping[str, JsonValue] = turn if turn is not None else ledger
    mode_value = payload.get("task_mode") or state.get("task_mode")
    mode = mode_value if isinstance(mode_value, str) else "quick"
    changed = bool(_as_str_list(state.get("changed_files_seen")))
    verified = has_successful_verification(ledger, payload)

    if (
        state.get("provenance_incomplete") is True
        and state.get("provenance_mutation_capable") is True
    ):
        return {
            "decision": "block",
            "reason": (
                "fable-lite Stop gate: provenance 관측이 불완전하여 clean을 주장할 수 없습니다. "
                "재시도 가능한 관측 또는 검증을 수행하세요. "
                "/ Incomplete provenance cannot claim a clean mutation-capable turn."
            ),
        }

    if changed and _requires_investigation_compliance(state):
        compliance = check_investigation_compliance({"text": _assistant_text(payload)})
        if compliance["compliant"] is not True:
            return {
                "decision": "block",
                "reason": (
                    "fable-lite N1: 조사 팩 마커가 부족합니다. "
                    "`가설 1:`/`Hypothesis 1:`, `증거:`/`Evidence:`, `기각:`/`Rejected:`를 포함하세요. "
                    "단 마커는 하단에 각 1줄 기록이면 충분합니다 — 본문은 비개발자도 읽는 쉬운 설명을 먼저 쓰고, "
                    "보고 전체를 기술 문서로 만들지 마세요. "
                    "/ Investigation pack markers are required — one compact line each at the bottom; "
                    "keep the report body in plain language first."
                ),
            }

    if mode == "quick" or _docs_only(state) or not changed or verified:
        return {"decision": "allow", "message": "fable-lite Stop gate allow."}

    return {
        "decision": "block",
        "reason": (
            "fable-lite Stop gate: 변경 파일이 있지만 성공한 검증 증거가 없습니다. "
            "가장 좁은 테스트/실행 관측을 수행하고 evidence를 기록하세요. "
            "/ Changed files require observed successful verification."
        ),
    }


def evaluate_stop(payload: Mapping[str, JsonValue]) -> Decision:
    root = _project_root(payload)
    with ledger_transaction(root):
        ledger = load_ledger(payload)
        decision = evaluate_without_io(ledger, payload)
        if decision.get("decision") != "block":
            return decision
        return _record_stop_block(payload, ledger, decision)
