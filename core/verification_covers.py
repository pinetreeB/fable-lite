from __future__ import annotations

from collections.abc import Iterable, Mapping
import os

from .ledger_schema import JsonObject, JsonValue
from .ledger_v1 import sequence_value
from .provenance_policy import canonical_manifest_key


def agent_key(payload: Mapping[str, JsonValue]) -> str:
    host = _string(payload.get("host"), "default")
    session_id = _string(payload.get("session_id"), "default")
    agent = _string(payload.get("agent"), "default")
    return f"{host}:{session_id}:{agent}"


def active_turn(ledger: Mapping[str, JsonValue], payload: Mapping[str, JsonValue] | None = None) -> JsonObject | None:
    turns = ledger.get("active_turns")
    if not isinstance(turns, dict):
        return None
    if payload is not None:
        turn = turns.get(agent_key(payload))
        if isinstance(turn, dict):
            return turn
        legacy = turns.get("default:default:default")
        return legacy if len(turns) == 1 and isinstance(legacy, dict) else None
    top_agent = ledger.get("agent")
    if isinstance(top_agent, str):
        for turn in turns.values():
            if isinstance(turn, dict) and turn.get("agent") == top_agent:
                return turn
    default = turns.get("default")
    if isinstance(default, dict):
        return default
    return next((turn for turn in turns.values() if isinstance(turn, dict)), None)


def capture_covers(ledger: Mapping[str, JsonValue], turn: JsonObject) -> JsonObject:
    revisions = pending_revisions(turn)
    path_revisions = [
        {
            "change_id": revision["change_id"],
            "path": revision["path"],
            "after": revision["after"],
            "change_event_id": revision["change_event_id"],
        }
        for revision in revisions
    ]
    change_ids = _unique_strings(revision["change_id"] for revision in revisions)
    event_ids = _unique_strings(revision["change_event_id"] for revision in revisions)
    return {
        "through_seq": sequence_value(ledger.get("event_seq")),
        "snapshot_id": _string(turn.get("current_snapshot_id"), "snapshot:unavailable"),
        "change_ids": change_ids,
        "change_event_ids": event_ids,
        "path_revisions": path_revisions,
    }


def record_path_revisions(turn: JsonObject, payload: Mapping[str, JsonValue]) -> None:
    baselines = _object(turn.get("path_baselines"))
    revisions = _object(turn.get("path_revisions"))
    audit = _list(turn.get("change_audit"))
    event_id = _string(payload.get("event_id"), f"change-{sequence_value(payload.get('seq'))}")
    for raw_path in _paths(payload):
        path = _string(raw_path.get("path"), "")
        change_id = _string(raw_path.get("change_id"), "")
        if not path or not change_id:
            continue
        key = canonical_manifest_key(path, os.name == "nt")
        before = _digest(raw_path.get("before"))
        after = _digest(raw_path.get("after"))
        kind = _string(raw_path.get("kind"), "artifact")
        requires = raw_path.get("requires_verification") is not False and kind != "docs"
        if key not in baselines:
            baselines[key] = before
        audit.append(
            {
                "change_id": change_id,
                "path": path,
                "after": after,
                "change_event_id": event_id,
            }
        )
        if after == baselines[key]:
            _ = revisions.pop(key, None)
            continue
        revisions[key] = {
            "change_id": change_id,
            "path": path,
            "after": after,
            "change_event_id": event_id,
            "requires_verification": requires,
        }
    turn["path_baselines"] = baselines
    turn["path_revisions"] = revisions
    turn["change_audit"] = audit
    turn["pending_change_ids"] = _unique_strings(
        revision["change_id"] for revision in pending_revisions(turn)
    )


def attach_covers(turn: JsonObject, covers: JsonObject) -> None:
    results = turn.get("verification_results")
    if not isinstance(results, list) or not results:
        return
    result = results[-1]
    if isinstance(result, dict):
        result["covers"] = covers


def covers_verified(turn: JsonObject) -> bool | None:
    if "path_revisions" not in turn:
        return None
    revisions = pending_revisions(turn)
    if not revisions:
        return True
    results = turn.get("verification_results")
    if not isinstance(results, list):
        return False
    return any(
        result.get("success") is True
        and isinstance(covers := result.get("covers"), dict)
        and _covers_all(covers, revisions)
        for result in results
        if isinstance(result, dict)
    )


def pending_revisions(turn: JsonObject) -> list[JsonObject]:
    revisions = turn.get("path_revisions")
    if not isinstance(revisions, dict):
        return []
    return [
        revision
        for revision in revisions.values()
        if isinstance(revision, dict)
        and revision.get("requires_verification") is True
    ]


def _covers_all(covers: JsonObject, revisions: list[JsonObject]) -> bool:
    recorded = covers.get("path_revisions")
    if not isinstance(recorded, list):
        return False
    return all(
        any(
            isinstance(candidate, dict)
            and candidate.get("change_id") == revision.get("change_id")
            and candidate.get("path") == revision.get("path")
            and candidate.get("after") == revision.get("after")
            and candidate.get("change_event_id") == revision.get("change_event_id")
            for candidate in recorded
        )
        for revision in revisions
    )


def _paths(payload: Mapping[str, JsonValue]) -> list[JsonObject]:
    paths = payload.get("paths")
    if not isinstance(paths, list):
        return []
    return [path for path in paths if isinstance(path, dict)]


def _object(value: JsonValue | None) -> JsonObject:
    return value if isinstance(value, dict) else {}


def _list(value: JsonValue | None) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _digest(value: JsonValue | None) -> str | None:
    return value if isinstance(value, str) else None


def _string(value: JsonValue | None, default: str) -> str:
    return value if isinstance(value, str) and value else default


def _unique_strings(values: Iterable[JsonValue]) -> list[str]:
    return list(dict.fromkeys(value for value in values if isinstance(value, str)))
