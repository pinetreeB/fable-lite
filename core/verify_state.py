"""# noqa: SIZE_OK — W3 turn-scoped Stop transitions must remain in this existing gate module."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from typing import TypeAlias

from .agent_log import append_agent_event, ledger_transaction
from .ledger import JsonObject, JsonValue, load_ledger, save_ledger
from .ledger_v1 import sequence_value
from .ledger_v2 import apply_v2_event, refresh_v1_projection
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
from .project_root import HOME_ROOT_ADVISORY, is_user_home_root
from .provenance_types import (
    ProvenanceReason,
    ProvenanceStatus,
    normalize_budget_breach_path,
    normalize_budget_top_paths,
)

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
    epochs = _remote_epochs(state)
    if epochs:
        remote_verified = all(
            _has_target_verification(results, target_id, remote_seq)
            for target_id, remote_seq in epochs.items()
        )
    else:
        remote_seq = _positive_sequence(state.get("last_remote_mutation_seq"))
        remote_verified = remote_seq is None or _has_remote_verification(
            results, remote_seq
        )
    return local_verified and remote_verified


def _has_remote_verification(results: list[JsonObject], remote_seq: int) -> bool:
    return any(
        result.get("success") is True
        and isinstance(covers := result.get("covers"), dict)
        and (through_seq := _positive_sequence(covers.get("through_seq"))) is not None
        and through_seq >= remote_seq
        for result in results
    )


def _remote_epochs(state: Mapping[str, JsonValue]) -> dict[str, int]:
    raw = state.get("remote_mutation_epochs")
    if not isinstance(raw, dict):
        return {}
    return {
        target_id: sequence
        for target_id, value in raw.items()
        if isinstance(target_id, str)
        and (sequence := _positive_sequence(value)) is not None
    }


def _has_target_verification(
    results: list[JsonObject], target_id: str, remote_seq: int
) -> bool:
    return any(
        result.get("success") is True
        and isinstance(covers := result.get("covers"), dict)
        and isinstance(targets := covers.get("remote_target_ids"), list)
        and target_id in targets
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
        and not _remote_epochs(ledger)
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
        return sequence_value(ledger.get("stop_blocks")) if _legacy_identity(payload) else 0
    blocks = turn.get("blocks")
    return sequence_value(blocks.get("stop")) if isinstance(blocks, dict) else 0


def _increment_stop_block(ledger: JsonObject, payload: Mapping[str, JsonValue]) -> None:
    turn = active_turn(ledger, payload)
    if turn is None:
        if _legacy_identity(payload):
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


_CONFIG_GUIDE = (
    ".fable-lite/provenance-config.json 에 exclude 패턴을 추가한 뒤 저장하세요 "
    "(저장 후 다음 턴부터 반영됩니다). "
    "예: {\"version\": 1, \"exclude\": [\"path/to/heavy/dir/**\"]} "
    "⚠️ 빌드·캐시·데이터 산출물만 제외하세요 — 소스 디렉토리(src/, app/ 등)를 제외하면 "
    "이후 그 안의 변조가 관측되지 않습니다. "
    "/ Add an exclude pattern to .fable-lite/provenance-config.json and save "
    "(takes effect starting next turn). "
    "Example: {\"version\": 1, \"exclude\": [\"path/to/heavy/dir/**\"]} "
    "Warning: exclude only build/cache/data artifacts - excluding source directories "
    "(src/, app/, ...) hides future tampering inside them from observation."
)


def _format_budget_paths(top_paths: Sequence[Mapping[str, str | int]]) -> str:
    return ", ".join(
        f"{item['path']} (entries={item['entries']}, bytes={item['bytes']})"
        for item in top_paths
    )


def _scope_too_large_detail(state: Mapping[str, JsonValue]) -> str:
    reason = state.get("provenance_status_reason")
    top_paths = list(normalize_budget_top_paths(state.get("provenance_budget_top_paths")))
    breach_path = normalize_budget_breach_path(state.get("provenance_budget_breach_path"))
    if reason in (ProvenanceReason.BYTE_LIMIT.value, ProvenanceReason.ENTRY_LIMIT.value):
        limit_ko = "바이트 예산" if reason == ProvenanceReason.BYTE_LIMIT.value else "파일 개수 예산"
        limit_en = "byte budget" if reason == ProvenanceReason.BYTE_LIMIT.value else "entry-count budget"
        pieces = [f"{limit_ko} 초과 / {limit_en} exceeded."]
        if top_paths:
            formatted = _format_budget_paths(top_paths)
            pieces.append(
                "예산 초과 시점까지의 부분 관측 상위 경로(순회 순서에 따라 달라질 수 있음): "
                f"{formatted}. / Partial observation up to the point the budget was exceeded "
                f"(traversal order is non-deterministic): {formatted}."
            )
        if breach_path:
            pieces.append(f"예산 초과 지점: {breach_path} / Budget breached at: {breach_path}.")
        pieces.append(_CONFIG_GUIDE)
        return " ".join(pieces)
    if reason == ProvenanceReason.DEADLINE.value:
        pieces = ["관측 시간 초과 / Observation timed out."]
        hint = breach_path or (_format_budget_paths(top_paths) if top_paths else "")
        if hint:
            pieces.append(
                f"참고용 힌트(원인이 아니라 시간 초과 시점 근처 경로): {hint} "
                f"/ Reference hint only, not the cause — path near the timeout: {hint}."
            )
        pieces.append(_CONFIG_GUIDE)
        return " ".join(pieces)
    return _CONFIG_GUIDE


def evaluate_without_io(
    ledger: Mapping[str, JsonValue], payload: Mapping[str, JsonValue]
) -> Decision:
    turn = active_turn(ledger, payload)
    state = _gate_state(ledger, payload, turn)
    changed = (
        bool(_as_str_list(state.get("changed_files_seen")))
        or _positive_sequence(state.get("last_remote_mutation_seq")) is not None
        or bool(_remote_epochs(state))
    )
    verified = has_successful_verification(ledger, payload)
    home_root_unsupported = is_user_home_root(_project_root(payload))

    if (
        state.get("baseline_status") == "missing"
        and state.get("provenance_mutation_capable") is not True
    ):
        return {
            "decision": "allow",
            "message": (
                "[smtw] turn_not_started: baseline 관측이 없어 clean을 주장하지 않고 "
                "read-only 턴을 안내 통과합니다. / Missing baseline; allowing this read-only "
                "turn without a clean claim."
            ),
        }

    if (
        not home_root_unsupported
        and state.get("provenance_status") == ProvenanceStatus.SCOPE_TOO_LARGE.value
        and state.get("provenance_mutation_capable") is True
    ):
        return {
            "decision": "block",
            "reason_code": ReasonCode.STOP_PROVENANCE_INCOMPLETE.value,
            "reason": (
                "[smtw] Stop gate: provenance 범위가 너무 커서 local-or-unknown 변경 가능성을 "
                "안전하게 관측할 수 없습니다. "
                f"{_scope_too_large_detail(state)} "
                "/ Scope too large cannot prove a clean local-or-unknown turn.\n"
                "Show me the work."
            ),
        }

    if (
        not home_root_unsupported
        and state.get("provenance_incomplete") is True
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
        if home_root_unsupported:
            return {"decision": "allow", "message": HOME_ROOT_ADVISORY}
        if state.get("provenance_status") == ProvenanceStatus.SCOPE_TOO_LARGE.value:
            return {
                "decision": "allow",
                "message": (
                    "[smtw] provenance scope too large: 파일 관측 지원 범위를 초과해 "
                    "이번 턴은 차단하지 않고 안내만 제공합니다. "
                    f"{_scope_too_large_detail(state)} "
                    "/ Provenance scope too large; advisory only."
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
        ordinary_capped = False
        if decision.get("decision") == "block":
            decision = _record_stop_block(payload, ledger, decision)
            if decision.get("decision") == "block":
                return _with_scorecard_line(decision, ledger, payload)
            ordinary_capped = True
        from .design_gate_state import evaluate_design_stop

        design_decision = evaluate_design_stop(ledger, payload)
        if design_decision is not None:
            rendered = _with_scorecard_line(design_decision, ledger, payload)
            _finish_allowed_turn(ledger, payload, rendered)
            if design_decision.get("decision") != "allow" and not save_ledger(payload, ledger):
                mark_cached_session_incomplete(ledger, payload)
            return rendered
        if ordinary_capped:
            rendered = _with_scorecard_line(decision, ledger, payload)
            _finish_allowed_turn(ledger, payload, rendered)
            return rendered
        if _record_stop_recoveries(ledger, payload):
            if not save_ledger(payload, ledger):
                mark_cached_session_incomplete(ledger, payload)
        rendered = _with_scorecard_line(decision, ledger, payload)
        _finish_allowed_turn(ledger, payload, rendered)
        return rendered


def _gate_state(
    ledger: Mapping[str, JsonValue],
    payload: Mapping[str, JsonValue],
    turn: JsonObject | None,
) -> Mapping[str, JsonValue]:
    if turn is not None:
        return turn
    if ledger.get("schema_version") != 2 or _legacy_identity(payload):
        return ledger
    return {
        "baseline_status": "missing",
        "provenance_incomplete": True,
        "provenance_status": ProvenanceStatus.INCOMPLETE.value,
        "provenance_status_reason": ProvenanceReason.TURN_NOT_STARTED.value,
    }


def _legacy_identity(payload: Mapping[str, JsonValue]) -> bool:
    return (
        payload.get("attribution") == "legacy_default"
        or payload.get("identity_synthetic") is True
        or not any(
            isinstance(payload.get(field), str) and payload.get(field)
            for field in ("host", "session_id", "agent")
        )
    )


def _finish_allowed_turn(
    ledger: JsonObject,
    payload: Mapping[str, JsonValue],
    decision: Mapping[str, JsonValue],
) -> None:
    if decision.get("decision") != "allow" or active_turn(ledger, payload) is None:
        return
    event: JsonObject = {
        key: value
        for key in ("project_root", "cwd", "host", "agent", "session_id", "turn_id", "attribution")
        if (value := payload.get(key)) is not None
    }
    event["event"] = "turn_finished"
    event["seq"] = sequence_value(ledger.get("event_seq")) + 1
    _ = apply_v2_event(ledger, event)
    if not save_ledger(payload, ledger):
        mark_cached_session_incomplete(ledger, payload)
        return
    agent = payload.get("agent")
    if isinstance(agent, str) and agent:
        append_agent_event(_project_root(payload), agent, event)


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
