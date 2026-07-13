from __future__ import annotations

from collections.abc import Mapping
import os
from typing import TypeAlias

from .agent_log import ledger_transaction
from .ledger import JsonObject, JsonValue, load_ledger, save_ledger
from .ledger_v1 import sequence_value
from .ledger_v2 import refresh_v1_projection
from .compliance import check_investigation_compliance
from .scorecard import (
    GateAction,
    ReasonCode,
    Resolution,
    ScorecardSchemaError,
    render_stop_line,
)
from .scorecard_store import (
    cached_session_summary,
    mark_cached_session_incomplete,
    new_transition,
    record_gate_transition_locked,
    unresolved_block_ids,
)
from .verification_covers import active_turn, covers_verified
from .provenance_types import ProvenanceStatus

Decision: TypeAlias = dict[str, JsonValue]

MAX_STOP_BLOCKS = 2
SCORECARD_ENV = "FABLE_LITE_SCORECARD"


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
    state: Mapping[str, JsonValue] = turn if turn is not None else ledger
    results = _as_result_list(state.get("verification_results"))
    covers_result = covers_verified(turn) if turn is not None else None
    if covers_result is not None:
        local_verified = covers_result
    elif _legacy_seq_less_verification(ledger, turn):
        local_verified = any(result.get("success") is True for result in results)
    else:
        last_change_seq = _positive_sequence(state.get("last_change_seq"))
        if last_change_seq is None:
            local_verified = any(
                result.get("success") is True for result in results
            )
        else:
            local_verified = any(
                result.get("success") is True
                and (verification_seq := _positive_sequence(result.get("seq")))
                is not None
                and verification_seq > last_change_seq
                for result in results
            )
    remote_seq = _positive_sequence(state.get("last_remote_mutation_seq"))
    return local_verified and (
        remote_seq is None or _has_remote_verification(results, remote_seq)
    )


def _has_remote_verification(results: list[JsonObject], remote_seq: int) -> bool:
    return any(
        result.get("success") is True
        and isinstance(covers := result.get("covers"), dict)
        and (through_seq := _positive_sequence(covers.get("through_seq"))) is not None
        and through_seq >= remote_seq
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
    remote_seq = _positive_sequence(ledger.get("last_remote_mutation_seq"))
    return (
        remote_seq is None
        and bool(changed)
        and bool(kinds)
        and kinds <= {"docs"}
    )


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
    reason_code = _decision_reason(decision)
    if _stop_blocks(ledger, payload) >= MAX_STOP_BLOCKS:
        _record_scorecard(
            ledger,
            payload,
            reason_code,
            GateAction.CAP_ALLOW,
        )
        if not save_ledger(payload, ledger):
            mark_cached_session_incomplete(ledger, payload)
        return {
            "decision": "allow",
            "message": "최대 2회 차단 후 통과합니다 / allowing after max 2 blocks.",
        }
    _increment_stop_block(ledger, payload)
    _record_scorecard(ledger, payload, reason_code, GateAction.BLOCK)
    if not save_ledger(payload, ledger):
        mark_cached_session_incomplete(ledger, payload)
    return decision


def evaluate_without_io(
    ledger: Mapping[str, JsonValue], payload: Mapping[str, JsonValue]
) -> Decision:
    turn = active_turn(ledger, payload)
    state: Mapping[str, JsonValue] = turn if turn is not None else ledger
    changed = bool(_as_str_list(state.get("changed_files_seen"))) or (
        _positive_sequence(state.get("last_remote_mutation_seq")) is not None
    )
    verified = has_successful_verification(ledger, payload)

    if (
        state.get("provenance_incomplete") is True
        and state.get("provenance_mutation_capable") is True
    ):
        return {
            "decision": "block",
            "reason_code": ReasonCode.STOP_PROVENANCE_INCOMPLETE.value,
            "reason": (
                "[smtw] Stop gate: provenance 관측이 불완전하여 clean을 주장할 수 없습니다. "
                "재시도 가능한 관측 또는 검증을 수행하세요. "
                "/ Incomplete provenance cannot claim a clean mutation-capable turn.\n"
                "Show me the work."
            ),
        }

    if changed and _requires_investigation_compliance(state):
        compliance = check_investigation_compliance({"text": _assistant_text(payload)})
        if compliance["compliant"] is not True:
            return {
                "decision": "block",
                "reason_code": ReasonCode.STOP_INVESTIGATION_MARKERS.value,
                "reason": (
                    "[smtw] N1: 조사 팩 마커가 부족합니다. "
                    "`가설 1:`/`Hypothesis 1:`, `증거:`/`Evidence:`, `기각:`/`Rejected:`를 포함하세요. "
                    "단 마커는 하단에 각 1줄 기록이면 충분합니다 — 본문은 비개발자도 읽는 쉬운 설명을 먼저 쓰고, "
                    "보고 전체를 기술 문서로 만들지 마세요. "
                    "/ Investigation pack markers are required — one compact line each at the bottom; "
                    "keep the report body in plain language first.\n"
                    "Show me the work."
                ),
            }

    if _docs_only(state) or not changed or verified:
        if state.get("provenance_status") == ProvenanceStatus.SCOPE_TOO_LARGE.value:
            return {
                "decision": "allow",
                "message": (
                    "[smtw] provenance scope too large: 파일 관측 지원 범위를 초과해 "
                    "이번 턴은 차단하지 않고 안내만 제공합니다. 프로젝트 루트를 더 좁게 설정하세요. "
                    "/ Provenance scope too large; advisory only. Narrow the project root."
                ),
            }
        return {"decision": "allow", "message": "[smtw] Stop gate allow."}

    return {
        "decision": "block",
        "reason_code": ReasonCode.STOP_VERIFICATION_MISSING.value,
        "reason": (
            "[smtw] Stop gate: 변경 파일이 있지만 성공한 검증 증거가 없습니다. "
            "가장 좁은 테스트/실행 관측을 수행하고 evidence를 기록하세요. "
            "/ Changed files require observed successful verification.\n"
            "Show me the work."
        ),
    }


def evaluate_stop(payload: Mapping[str, JsonValue]) -> Decision:
    root = _project_root(payload)
    with ledger_transaction(root):
        ledger = load_ledger(payload)
        decision = evaluate_without_io(ledger, payload)
        if decision.get("decision") != "block":
            if _record_stop_recoveries(ledger, payload):
                if not save_ledger(payload, ledger):
                    mark_cached_session_incomplete(ledger, payload)
            return _with_scorecard_line(decision, ledger, payload)
        gated = _record_stop_block(payload, ledger, decision)
        return _with_scorecard_line(gated, ledger, payload)


def _with_scorecard_line(
    decision: Decision,
    ledger: Mapping[str, JsonValue],
    payload: Mapping[str, JsonValue],
) -> Decision:
    if decision.get("decision") != "allow" or os.environ.get(SCORECARD_ENV) == "0":
        return decision
    try:
        summary = cached_session_summary(ledger, payload)
    except ScorecardSchemaError:
        return decision
    line = render_stop_line(summary) if summary is not None else None
    if line is None:
        return decision
    message = decision.get("message")
    base = message if isinstance(message, str) and message else "[smtw] Stop gate allow."
    return decision | {"message": f"{base}\n{line}"}


def _record_stop_recoveries(
    ledger: JsonObject, payload: Mapping[str, JsonValue]
) -> bool:
    recorded = False
    turn = active_turn(ledger, payload)
    state: Mapping[str, JsonValue] = turn if turn is not None else ledger
    if (
        state.get("provenance_incomplete") is not True
        and state.get("provenance_mutation_capable") is True
        and state.get("provenance_status")
        != ProvenanceStatus.SCOPE_TOO_LARGE.value
    ):
        recorded = _record_scorecard(
            ledger,
            payload,
            ReasonCode.STOP_PROVENANCE_INCOMPLETE,
            GateAction.RECOVER,
            Resolution.OBSERVATION,
        ) or recorded
    if _requires_investigation_compliance(state):
        compliance = check_investigation_compliance({"text": _assistant_text(payload)})
        if compliance["compliant"] is True:
            recorded = _record_scorecard(
                ledger,
                payload,
                ReasonCode.STOP_INVESTIGATION_MARKERS,
                GateAction.RECOVER,
                Resolution.MARKERS,
            ) or recorded
    if has_successful_verification(ledger, payload):
        recorded = _record_scorecard(
            ledger,
            payload,
            ReasonCode.STOP_VERIFICATION_MISSING,
            GateAction.RECOVER,
            Resolution.VERIFICATION,
        ) or recorded
    return recorded


def _record_scorecard(
    ledger: JsonObject,
    payload: Mapping[str, JsonValue],
    reason_code: ReasonCode,
    action: GateAction,
    resolution: Resolution = Resolution.NONE,
) -> bool:
    resolves = (
        ()
        if action is GateAction.BLOCK
        else unresolved_block_ids(ledger, payload, reason_code)
    )
    if action is GateAction.CAP_ALLOW and not resolves:
        resolves = unresolved_block_ids(ledger, payload)
    if action is not GateAction.BLOCK and not resolves:
        return False
    try:
        transition = new_transition(
            payload,
            reason_code,
            action,
            resolves=resolves,
            resolution=resolution,
        )
        record_gate_transition_locked(ledger, payload, transition)
    except (OSError, ScorecardSchemaError):
        return False
    return True


def _decision_reason(decision: Mapping[str, JsonValue]) -> ReasonCode:
    value = decision.get("reason_code")
    return (
        ReasonCode(value)
        if isinstance(value, str)
        else ReasonCode.STOP_VERIFICATION_MISSING
    )
