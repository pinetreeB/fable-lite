"""Scorecard journal and bounded ledger-cache storage boundary."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from uuid import uuid4

from .ledger import save_ledger
from .ledger_schema import JsonObject, JsonValue
from .scorecard_cache import (
    MAX_CACHED_SESSIONS,
    bounded_cache,
    bounded_cache_with_evictions,
    build_cache,
    cache_object,
    empty_entry,
    incomplete_cache,
    summary_for_key,
    string_list,
    unresolved_for_key,
    updated_entry,
)
from .scorecard import (
    Attribution,
    GateAction,
    GateTransition,
    ReasonCode,
    Resolution,
    ScorecardAggregate,
    ScorecardSchemaError,
    parse_transition,
)
from .verification_covers import active_turn


@dataclass(frozen=True, slots=True)
class JournalReplay:
    transitions: tuple[GateTransition, ...]
    complete: bool


def scorecard_journal_path(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / ".fable-lite" / "scorecard" / "gates.jsonl"


def new_transition(
    payload: Mapping[str, JsonValue],
    reason_code: ReasonCode,
    action: GateAction,
    *,
    resolves: tuple[str, ...] = (),
    resolution: Resolution = Resolution.NONE,
    attribution: Attribution | None = None,
    event_id: str | None = None,
    occurred_at: datetime | None = None,
) -> GateTransition:
    raw: JsonObject = {
        "scorecard_schema_version": 1,
        "event": "gate_transition",
        "event_id": event_id or str(uuid4()),
        "host": _required_payload_string(payload, "host"),
        "session_id": _required_payload_string(payload, "session_id"),
        "agent": _required_payload_string(payload, "agent"),
        "turn_id": _required_payload_string(payload, "turn_id"),
        "reason_code": reason_code.value,
        "action": action.value,
        "resolves": list(resolves),
        "resolution": resolution.value,
        "attribution": (
            attribution.value
            if attribution is not None
            else payload.get("attribution", Attribution.EXACT.value)
        ),
        "occurred_at": (occurred_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
    }
    return parse_transition(raw)


def transition_json(transition: GateTransition) -> JsonObject:
    return {
        "scorecard_schema_version": 1,
        "event": "gate_transition",
        "event_id": transition.event_id,
        "host": transition.identity.host,
        "session_id": transition.identity.session_id,
        "agent": transition.identity.agent,
        "turn_id": transition.turn_id,
        "reason_code": transition.reason_code.value,
        "action": transition.action.value,
        "resolves": list(transition.resolves),
        "resolution": transition.resolution.value,
        "attribution": transition.attribution.value,
        "occurred_at": transition.occurred_at.isoformat(),
    }


def load_scorecard_journal(project_root: str | Path) -> JournalReplay:
    try:
        lines = scorecard_journal_path(project_root).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return JournalReplay((), True)
    except (OSError, UnicodeDecodeError):
        return JournalReplay((), False)
    transitions: list[GateTransition] = []
    complete = True
    for line in lines:
        if not line.strip():
            continue
        try:
            raw: JsonValue = json.loads(line)
            if not isinstance(raw, dict):
                raise ScorecardSchemaError("event", "must be an object")
            transitions.append(parse_transition(raw))
        except (json.JSONDecodeError, ScorecardSchemaError):
            complete = False
    return JournalReplay(tuple(transitions), complete)


def build_scorecard_cache(
    transitions: Iterable[GateTransition], *, complete: bool
) -> JsonObject:
    return build_cache(transitions, complete=complete)


def record_gate_transition_locked(
    ledger: JsonObject,
    payload: Mapping[str, JsonValue],
    transition: GateTransition,
) -> None:
    cache = cache_object(ledger.get("scorecard_cache"))
    eviction_history_present = "scorecard_evicted_keys" in ledger
    evicted_keys = string_list(ledger.get("scorecard_evicted_keys"))
    key = transition.identity.agent_key
    existing = cache.get(key)
    if (
        isinstance(existing, dict)
        and existing.get("complete") is True
        and not _persist_incomplete_before_append(ledger, payload, key, existing)
    ):
        mark_cached_session_incomplete(ledger, payload)
        _mark_turn_scorecard_observed(ledger, payload)
        return
    appended = True
    positions: tuple[int, int] | None = None
    try:
        positions = _append_transition(_project_root(payload), transition)
    except OSError:
        appended = False
    consistent = _cache_matches_journal(ledger, positions)
    if not consistent:
        cache = incomplete_cache(cache)
    existing = cache.get(key)
    observed_before = _turn_scorecard_observed(ledger, payload)
    entry = existing if isinstance(existing, dict) else empty_entry(transition)
    identity_known_new = eviction_history_present or len(cache) < MAX_CACHED_SESSIONS
    complete = (
        appended
        and consistent
        and (
            existing is not None
            or (not observed_before and identity_known_new and key not in evicted_keys)
        )
    )
    cache[key] = updated_entry(entry, transition, complete=complete)
    bounded, evicted = bounded_cache_with_evictions(cache, evicted_keys)
    ledger["scorecard_cache"] = bounded
    ledger["scorecard_evicted_keys"] = list(evicted)
    _mark_turn_scorecard_observed(ledger, payload)
    if positions is not None:
        ledger["scorecard_journal_offset"] = positions[1]


def cached_session_summary(
    ledger: Mapping[str, JsonValue], payload: Mapping[str, JsonValue]
) -> ScorecardAggregate | None:
    return summary_for_key(ledger.get("scorecard_cache"), _payload_key(payload))


def unresolved_block_ids(
    ledger: Mapping[str, JsonValue],
    payload: Mapping[str, JsonValue],
    reason_code: ReasonCode | None = None,
) -> tuple[str, ...]:
    try:
        key = _payload_key(payload)
    except ScorecardSchemaError:
        return ()
    return unresolved_for_key(ledger.get("scorecard_cache"), key, reason_code)


def mark_cached_session_incomplete(
    ledger: JsonObject, payload: Mapping[str, JsonValue]
) -> None:
    cache = cache_object(ledger.get("scorecard_cache"))
    try:
        key = _payload_key(payload)
    except ScorecardSchemaError:
        return
    raw = cache.get(key)
    if isinstance(raw, dict):
        entry = dict(raw)
        entry["complete"] = False
        cache[key] = entry
        ledger["scorecard_cache"] = cache


def _persist_incomplete_before_append(
    ledger: JsonObject,
    payload: Mapping[str, JsonValue],
    key: str,
    existing: JsonObject,
) -> bool:
    dirty_ledger = dict(ledger)
    dirty_cache = cache_object(ledger.get("scorecard_cache"))
    dirty_entry = dict(existing)
    dirty_entry["complete"] = False
    dirty_cache[key] = dirty_entry
    dirty_ledger["scorecard_cache"] = bounded_cache(dirty_cache)
    return save_ledger(payload, dirty_ledger)


def _append_transition(
    project_root: str | Path, transition: GateTransition
) -> tuple[int, int]:
    path = scorecard_journal_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        transition_json(transition), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    with path.open("ab+") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        if size:
            handle.seek(-1, 2)
            if handle.read(1) != b"\n":
                _ = handle.write(b"\n")
        _ = handle.write(serialized + b"\n")
        return size, handle.tell()


def _cache_matches_journal(
    ledger: Mapping[str, JsonValue], positions: tuple[int, int] | None
) -> bool:
    if positions is None:
        return False
    previous = ledger.get("scorecard_journal_offset")
    return previous == positions[0] or (previous is None and positions[0] == 0)


def _turn_scorecard_observed(
    ledger: Mapping[str, JsonValue], payload: Mapping[str, JsonValue]
) -> bool:
    turn = active_turn(ledger, payload)
    state = turn if turn is not None else ledger
    return state.get("scorecard_observed") is True


def _mark_turn_scorecard_observed(
    ledger: JsonObject, payload: Mapping[str, JsonValue]
) -> None:
    turn = active_turn(ledger, payload)
    state = turn if turn is not None else ledger
    state["scorecard_observed"] = True


def _project_root(payload: Mapping[str, JsonValue]) -> str:
    value = payload.get("project_root") or payload.get("cwd")
    return value if isinstance(value, str) and value else "."


def _payload_key(payload: Mapping[str, JsonValue]) -> str:
    return ":".join(
        _required_payload_string(payload, field)
        for field in ("host", "session_id", "agent")
    )


def _required_payload_string(payload: Mapping[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ScorecardSchemaError(field, "must be a non-empty string")
    return value
