from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import assert_never

from .ledger_schema import JsonObject, JsonValue
from .scorecard import (
    GateAction,
    GateTransition,
    ReasonAggregate,
    ReasonCode,
    ScorecardAggregate,
    ScorecardSchemaError,
    SessionIdentity,
)


MAX_CACHED_SESSIONS = 64


def build_cache(
    transitions: Iterable[GateTransition], *, complete: bool
) -> JsonObject:
    grouped: dict[str, list[GateTransition]] = {}
    for transition in sorted(transitions, key=lambda item: item.occurred_at):
        grouped.setdefault(transition.identity.agent_key, []).append(transition)
    cache: JsonObject = {}
    for key, session_transitions in grouped.items():
        entry = empty_entry(session_transitions[0])
        for transition in session_transitions:
            entry = updated_entry(entry, transition, complete=complete)
        cache[key] = entry
    return bounded_cache(cache)


def updated_entry(
    raw: JsonObject, transition: GateTransition, *, complete: bool
) -> JsonObject:
    entry = dict(raw)
    seen = string_list(entry.get("seen_event_ids"))
    entry["complete"] = entry.get("complete") is True and complete
    if transition.event_id in seen:
        return entry
    seen.append(transition.event_id)
    entry["seen_event_ids"] = seen
    entry["latest_turn_id"] = transition.turn_id
    entry["last_occurred_at"] = transition.occurred_at.isoformat()
    reason_rows = reason_rows_from(entry.get("by_reason"))
    row = reason_rows.setdefault(transition.reason_code.value, empty_reason_row())
    unresolved = string_list(entry.get("unresolved_block_ids"))
    unresolved_reasons = string_map(entry.get("unresolved_reasons"))
    match transition.action:
        case GateAction.BLOCK:
            entry["blocked_attempts"] = count(entry, "blocked_attempts") + 1
            row["blocked_attempts"] = count(row, "blocked_attempts") + 1
            unresolved.append(transition.event_id)
            unresolved_reasons[transition.event_id] = transition.reason_code.value
        case GateAction.RECOVER:
            resolved = [
                block_id
                for block_id in transition.resolves
                if block_id in unresolved
                and unresolved_reasons.get(block_id) == transition.reason_code.value
            ]
            if resolved:
                scopes = string_list(entry.get("recovered_scope_keys"))
                scope = "\x1f".join(transition.recovery_scope)
                if scope not in scopes:
                    scopes.append(scope)
                    entry["recovered_scopes"] = count(entry, "recovered_scopes") + 1
                    row["recovered_scopes"] = count(row, "recovered_scopes") + 1
                entry["recovered_scope_keys"] = scopes
                entry["resolved_attempts"] = count(entry, "resolved_attempts") + len(resolved)
                row["resolved_attempts"] = count(row, "resolved_attempts") + len(resolved)
                unresolved = [item for item in unresolved if item not in resolved]
                for block_id in resolved:
                    _ = unresolved_reasons.pop(block_id, None)
        case GateAction.CAP_ALLOW:
            entry["cap_allows"] = count(entry, "cap_allows") + 1
            row["cap_allows"] = count(row, "cap_allows") + 1
        case unreachable:
            assert_never(unreachable)
    entry["unresolved_block_ids"] = unresolved
    entry["unresolved_reasons"] = unresolved_reasons
    entry["by_reason"] = reason_rows
    return entry


def empty_entry(transition: GateTransition) -> JsonObject:
    occurred_at = transition.occurred_at.isoformat()
    return {
        "host": transition.identity.host,
        "session_id": transition.identity.session_id,
        "agent": transition.identity.agent,
        "activated_at": occurred_at,
        "observed": True,
        "complete": True,
        "blocked_attempts": 0,
        "recovered_scopes": 0,
        "resolved_attempts": 0,
        "cap_allows": 0,
        "unresolved_block_ids": [],
        "unresolved_reasons": {},
        "recovered_scope_keys": [],
        "seen_event_ids": [],
        "latest_turn_id": transition.turn_id,
        "first_occurred_at": occurred_at,
        "last_occurred_at": occurred_at,
        "by_reason": {},
    }


def summary_from_entry(raw: Mapping[str, JsonValue]) -> ScorecardAggregate | None:
    try:
        identity = SessionIdentity(
            entry_string(raw, "host"),
            entry_string(raw, "session_id"),
            entry_string(raw, "agent"),
        )
        reason_rows = tuple(
            ReasonAggregate(
                ReasonCode(reason),
                count(row, "blocked_attempts"),
                count(row, "recovered_scopes"),
                count(row, "resolved_attempts"),
                count(row, "cap_allows"),
            )
            for reason, row in reason_rows_from(raw.get("by_reason")).items()
        )
        return ScorecardAggregate(
            identity,
            datetime.fromisoformat(entry_string(raw, "activated_at")).astimezone(UTC),
            raw.get("observed") is True,
            True,
            count(raw, "blocked_attempts"),
            count(raw, "recovered_scopes"),
            count(raw, "resolved_attempts"),
            count(raw, "cap_allows"),
            tuple(string_list(raw.get("unresolved_block_ids"))),
            entry_string(raw, "latest_turn_id", allow_empty=True),
            optional_datetime(raw.get("first_occurred_at")),
            optional_datetime(raw.get("last_occurred_at")),
            reason_rows,
        )
    except (ScorecardSchemaError, ValueError):
        return None


def bounded_cache(cache: JsonObject) -> JsonObject:
    return bounded_cache_with_evictions(cache, ())[0]


def bounded_cache_with_evictions(
    cache: JsonObject, previous_evictions: Iterable[str]
) -> tuple[JsonObject, tuple[str, ...]]:
    items = sorted(cache.items(), key=lambda item: cache_time(item[1]))
    history = list(dict.fromkeys(previous_evictions))
    for key, _ in items[:-MAX_CACHED_SESSIONS]:
        if key in history:
            history.remove(key)
        history.append(key)
    return dict(items[-MAX_CACHED_SESSIONS:]), tuple(history[-MAX_CACHED_SESSIONS:])


def incomplete_cache(cache: JsonObject) -> JsonObject:
    result: JsonObject = {}
    for key, value in cache.items():
        if isinstance(value, dict):
            entry = dict(value)
            entry["complete"] = False
            result[key] = entry
    return result


def cache_object(value: JsonValue | None) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def summary_for_key(cache: JsonValue | None, key: str) -> ScorecardAggregate | None:
    if not isinstance(cache, dict):
        return None
    raw = cache.get(key)
    if not isinstance(raw, dict) or raw.get("complete") is not True:
        return None
    summary = summary_from_entry(raw)
    if summary is None or summary.identity.agent_key != key:
        return None
    return summary


def unresolved_for_key(
    cache: JsonValue | None, key: str, reason_code: ReasonCode | None
) -> tuple[str, ...]:
    if not isinstance(cache, dict):
        return ()
    raw = cache.get(key)
    return unresolved_for_entry(raw, reason_code) if isinstance(raw, dict) else ()


def unresolved_for_entry(
    raw: Mapping[str, JsonValue], reason_code: ReasonCode | None = None
) -> tuple[str, ...]:
    block_ids = string_list(raw.get("unresolved_block_ids"))
    if reason_code is None:
        return tuple(block_ids)
    reasons = string_map(raw.get("unresolved_reasons"))
    return tuple(
        block_id
        for block_id in block_ids
        if reasons.get(block_id) == reason_code.value
    )


def cache_time(value: JsonValue) -> str:
    if not isinstance(value, dict):
        return ""
    occurred_at = value.get("last_occurred_at")
    return occurred_at if isinstance(occurred_at, str) else ""


def reason_rows_from(value: JsonValue | None) -> dict[str, JsonObject]:
    if not isinstance(value, dict):
        return {}
    return {key: dict(row) for key, row in value.items() if isinstance(row, dict)}


def empty_reason_row() -> JsonObject:
    return {
        "blocked_attempts": 0,
        "recovered_scopes": 0,
        "resolved_attempts": 0,
        "cap_allows": 0,
    }


def string_list(value: JsonValue | None) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def string_map(value: JsonValue | None) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(item, str)}


def count(raw: Mapping[str, JsonValue], field: str) -> int:
    value = raw.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def entry_string(
    raw: Mapping[str, JsonValue], field: str, *, allow_empty: bool = False
) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ScorecardSchemaError(field, "must be a string")
    return value


def optional_datetime(value: JsonValue | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(UTC) if isinstance(value, str) else None
