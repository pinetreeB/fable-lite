from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from .ledger_schema import JsonObject, JsonValue
from .ledger_v1 import V1_PROJECTION_FIELDS, apply_v1_event, default_ledger, sequence_value
from .verification_covers import agent_key, attach_covers, record_path_revisions

SNAPSHOT_UNAVAILABLE: Final = "snapshot:unavailable"
EVENT_ONLY_PROMPT: Final = "(event-only)"
NO_TOOL_OUTPUT: Final = "(no tool output)"


def default_v2_ledger() -> JsonObject:
    ledger = default_ledger()
    ledger["schema_version"] = 2
    ledger["manifest_generation"] = 0
    ledger["active_turns"] = {}
    return ledger


def refresh_v1_projection(ledger: JsonObject, turn: JsonObject) -> JsonObject:
    global_sequence = sequence_value(ledger.get("event_seq"))
    for field in V1_PROJECTION_FIELDS:
        if field in turn:
            ledger[field] = turn[field]
    # Legacy projection counters are conservative maxima; active turn fields remain authoritative.
    ledger["last_change_seq"] = _max_active_field(ledger, "last_change_seq")
    ledger["stop_blocks"] = _max_active_stop_blocks(ledger)
    ledger["event_seq"] = global_sequence
    return ledger


def apply_v2_event(ledger: JsonObject, payload: Mapping[str, JsonValue]) -> JsonObject:
    event_seq = sequence_value(payload.get("seq"))
    ledger["event_seq"] = max(sequence_value(ledger.get("event_seq")), event_seq)
    active = _active_turns(ledger)
    key = agent_key(payload)
    event = payload.get("event")
    if event == "prompt":
        _discard_legacy_turn(active)
        turn = _new_turn(payload, event_seq)
    else:
        raw_turn = active.get(key)
        if isinstance(raw_turn, dict):
            turn = raw_turn
            _ = apply_v1_event(turn, payload)
        else:
            turn = _new_turn(payload, event_seq)
        _update_turn_after_event(turn, payload)
    active[key] = turn
    ledger["active_turns"] = active
    manifest_generation = payload.get("manifest_generation")
    if isinstance(manifest_generation, int) and not isinstance(manifest_generation, bool):
        ledger["manifest_generation"] = max(0, manifest_generation)
    _complete_v2_projection(turn)
    return refresh_v1_projection(ledger, turn)


def _active_turns(ledger: JsonObject) -> JsonObject:
    active = ledger.get("active_turns")
    return active if isinstance(active, dict) else {}


def _max_active_field(ledger: JsonObject, field: str) -> int:
    return max(
        (
            sequence_value(turn.get(field))
            for turn in _active_turns(ledger).values()
            if isinstance(turn, dict)
        ),
        default=0,
    )


def _max_active_stop_blocks(ledger: JsonObject) -> int:
    return max(
        (
            sequence_value(blocks.get("stop"))
            for turn in _active_turns(ledger).values()
            if isinstance(turn, dict)
            and isinstance(blocks := turn.get("blocks"), dict)
        ),
        default=0,
    )


def _new_turn(payload: Mapping[str, JsonValue], event_seq: int) -> JsonObject:
    event_payload = dict(payload)
    event_payload["seq"] = event_seq
    turn = apply_v1_event(default_ledger(), event_payload)
    turn["turn_id"] = _string(payload.get("turn_id"), f"turn-{event_seq}")
    turn["start_seq"] = event_seq
    turn["baseline_snapshot_id"] = _string(
        payload.get("baseline_snapshot_id"), SNAPSHOT_UNAVAILABLE
    )
    turn["current_snapshot_id"] = _string(
        payload.get("current_snapshot_id"), SNAPSHOT_UNAVAILABLE
    )
    turn["pending_change_ids"] = []
    turn["blocks"] = {"stop": 0}
    turn["agent"] = _string(payload.get("agent"), "default")
    _update_turn_after_event(turn, payload)
    return turn


def _update_turn_after_event(turn: JsonObject, payload: Mapping[str, JsonValue]) -> None:
    snapshot_id = payload.get("current_snapshot_id")
    if isinstance(snapshot_id, str) and snapshot_id:
        turn["current_snapshot_id"] = snapshot_id
    incomplete = payload.get("provenance_incomplete")
    if isinstance(incomplete, bool):
        turn["provenance_incomplete"] = incomplete
    if payload.get("provenance_mutation_capable") is True:
        turn["provenance_mutation_capable"] = True
    event = payload.get("event")
    if event == "invocation":
        _remember_invocation(turn, payload)
        return
    if event == "observation":
        return
    if event == "verification":
        covers = payload.get("covers")
        if isinstance(covers, dict):
            attach_covers(turn, covers)
        return
    if event != "change":
        return
    paths = payload.get("paths")
    if isinstance(paths, list):
        _apply_path_projection(turn, payload)
        record_path_revisions(turn, payload)
        return
    change_id = _change_id(payload)
    if change_id:
        pending = _string_list(turn.get("pending_change_ids"))
        if change_id not in pending:
            pending.append(change_id)
        turn["pending_change_ids"] = pending
    if turn.get("migration_mode") == "legacy_turn":
        turn["legacy_seq_less"] = False


def _remember_invocation(turn: JsonObject, payload: Mapping[str, JsonValue]) -> None:
    invocation_id = payload.get("invocation_id")
    if not isinstance(invocation_id, str) or not invocation_id:
        return
    records = turn.get("invocations")
    records = records if isinstance(records, dict) else {}
    candidate_paths = payload.get("candidate_paths")
    entry: JsonObject = {
        "candidate_paths": candidate_paths if isinstance(candidate_paths, list) else [],
    }
    covers = payload.get("covers")
    if isinstance(covers, dict):
        entry["covers"] = covers
    records[invocation_id] = entry
    turn["invocations"] = records


def _apply_path_projection(turn: JsonObject, payload: Mapping[str, JsonValue]) -> None:
    paths = payload.get("paths")
    if not isinstance(paths, list):
        return
    for raw_path in paths:
        if not isinstance(raw_path, dict):
            continue
        path = raw_path.get("path")
        if not isinstance(path, str) or not path:
            continue
        event_payload = dict(payload)
        event_payload["path"] = path
        kind = raw_path.get("kind")
        if isinstance(kind, str):
            event_payload["kind"] = kind
        _ = apply_v1_event(turn, event_payload)


def _change_id(payload: Mapping[str, JsonValue]) -> str:
    direct = payload.get("change_id")
    if isinstance(direct, str) and direct:
        return direct
    paths = payload.get("paths")
    if not isinstance(paths, list) or not paths:
        return ""
    first = paths[0]
    if not isinstance(first, dict):
        return ""
    value: JsonValue | None = first.get("change_id")
    return value if isinstance(value, str) else ""


def _discard_legacy_turn(active: JsonObject) -> None:
    legacy = active.get("default")
    if isinstance(legacy, dict) and legacy.get("migration_mode") == "legacy_turn":
        _ = active.pop("default", None)


def _complete_v2_projection(turn: JsonObject) -> None:
    if not _string(turn.get("prompt"), ""):
        turn["prompt"] = EVENT_ONLY_PROMPT
    results = turn.get("verification_results")
    if not isinstance(results, list):
        return
    for result in results:
        if isinstance(result, dict) and not _string(result.get("evidence"), ""):
            result["evidence"] = NO_TOOL_OUTPUT


def _string(value: JsonValue | None, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _string_list(value: JsonValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
