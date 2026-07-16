"""# noqa: SIZE_OK — the W3 card fixes all four cover-filter stages in this frozen boundary."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import json
import os
from pathlib import Path
from typing import Final

from .agent_log import agent_log_path
from .ledger_schema import JsonObject, JsonValue
from .ledger_v1 import sequence_value
from .provenance_policy import canonical_manifest_key

RECENT_PEER_LOG_BYTES: Final = 1024 * 1024
RECENT_PEER_LOG_EVENTS: Final = 512


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
        if not _legacy_payload(payload):
            return None
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


def _legacy_payload(payload: Mapping[str, JsonValue]) -> bool:
    return (
        payload.get("attribution") == "legacy_default"
        or payload.get("identity_synthetic") is True
        or not any(isinstance(payload.get(field), str) and payload.get(field) for field in ("host", "session_id", "agent"))
    )


def capture_covers(
    ledger: Mapping[str, JsonValue],
    turn: JsonObject,
    remote_target_ids: Iterable[JsonValue] = (),
) -> JsonObject:
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
    covers: JsonObject = {
        "through_seq": sequence_value(ledger.get("event_seq")),
        "snapshot_id": _string(turn.get("current_snapshot_id"), "snapshot:unavailable"),
        "change_ids": change_ids,
        "change_event_ids": event_ids,
        "path_revisions": path_revisions,
    }
    targets = _unique_strings(remote_target_ids)
    if targets:
        covers["remote_target_ids"] = targets
    return covers


def record_path_revisions(
    turn: JsonObject,
    payload: Mapping[str, JsonValue],
    ledger: Mapping[str, JsonValue] | None = None,
) -> None:
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
        attribution = _revision_attribution(turn, payload, raw_path, ledger, key, after)
        if key not in baselines:
            baselines[key] = before
        audit.append(
            {
                "change_id": change_id,
                "path": path,
                "after": after,
                "change_event_id": event_id,
                "canonical_key": key,
                "attribution": attribution,
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
            "attribution": attribution,
            "revision_seq": sequence_value(payload.get("seq")),
            "manifest_generation": sequence_value(payload.get("manifest_generation")),
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
        and revision.get("attribution") != "peer"
    ]


def _revision_attribution(
    turn: JsonObject,
    payload: Mapping[str, JsonValue],
    raw_path: JsonObject,
    ledger: Mapping[str, JsonValue] | None,
    canonical_key: str,
    after: str | None,
) -> str:
    owner = payload.get("owner")
    event_agent = payload.get("agent")
    if payload.get("attribution_status") == "contended":
        return "contended"
    if isinstance(owner, str) and owner and owner == event_agent:
        return "self"
    if ledger is None or after is None or _mitigation_unavailable(ledger):
        return "external"
    index = ledger.get("path_attribution")
    if not isinstance(index, dict):
        return "external"
    entry = index.get(canonical_key)
    if not isinstance(entry, dict):
        return "external"
    if entry.get("status") != "exclusive" or _turn_has_self_history(turn, canonical_key):
        return "contended" if entry.get("status") == "contended" else "external"
    owners = entry.get("owners")
    if not isinstance(owners, list):
        return "external"
    caller = agent_key(payload)
    for raw_owner in owners:
        if not isinstance(raw_owner, dict):
            continue
        if raw_owner.get("agent_key") == caller or raw_owner.get("after_digest") != after:
            continue
        if _peer_change_event_matches(payload, raw_owner, raw_path, canonical_key):
            return "peer"
    return "external"


def _mitigation_unavailable(ledger: Mapping[str, JsonValue]) -> bool:
    return (
        ledger.get("attribution_degraded") is True
        or ledger.get("attribution_capacity_exceeded") is True
    )


def _turn_has_self_history(turn: JsonObject, canonical_key: str) -> bool:
    revisions = turn.get("path_revisions")
    if isinstance(revisions, dict):
        previous = revisions.get(canonical_key)
        if isinstance(previous, dict) and previous.get("attribution") in {"self", "contended"}:
            return True
    audit = turn.get("change_audit")
    return isinstance(audit, list) and any(
        isinstance(item, dict)
        and item.get("canonical_key") == canonical_key
        and item.get("attribution") in {"self", "contended"}
        for item in audit
    )


def _peer_change_event_matches(
    payload: Mapping[str, JsonValue],
    owner: JsonObject,
    raw_path: JsonObject,
    canonical_key: str,
) -> bool:
    root = payload.get("project_root") or payload.get("cwd")
    peer_key = owner.get("agent_key")
    if not isinstance(root, str) or not isinstance(peer_key, str):
        return False
    parts = peer_key.split(":", 2)
    if len(parts) != 3:
        return False
    peer_agent = parts[2]
    audit_path = agent_log_path(root, peer_agent).resolve()
    if audit_path.name != f"{peer_agent}.jsonl":
        return False
    audit_root = Path(root).resolve() / ".fable-lite" / "agents"
    try:
        _ = audit_path.relative_to(audit_root.resolve())
    except (OSError, ValueError):
        return False
    expected_seq = sequence_value(owner.get("revision_seq"))
    expected_generation = sequence_value(owner.get("manifest_generation"))
    expected_digest = raw_path.get("after")
    if expected_seq <= 0 or expected_generation <= 0 or not isinstance(expected_digest, str):
        return False
    for event in _recent_agent_events(audit_path):
        if sequence_value(event.get("seq")) < expected_seq:
            break
        if (
            event.get("event") == "change"
            and event.get("commit_state") == "committed"
            and sequence_value(event.get("seq")) == expected_seq
            and sequence_value(event.get("manifest_generation")) == expected_generation
            and _event_has_revision(event, canonical_key, expected_digest)
        ):
            return True
    return False


def _recent_agent_events(path: Path) -> Iterable[JsonObject]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - RECENT_PEER_LOG_BYTES)
            handle.seek(start)
            chunk = handle.read()
    except OSError:
        return ()
    lines = chunk.splitlines()
    if start and lines:
        lines = lines[1:]
    events: list[JsonObject] = []
    for line in reversed(lines[-RECENT_PEER_LOG_EVENTS:]):
        try:
            raw: JsonValue = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            events.append(raw)
    return events


def _event_has_revision(event: JsonObject, canonical_key: str, digest: str) -> bool:
    paths = event.get("paths")
    return isinstance(paths, list) and any(
        isinstance(item, dict)
        and isinstance(path := item.get("path"), str)
        and canonical_manifest_key(path, os.name == "nt") == canonical_key
        and item.get("after") == digest
        for item in paths
    )


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
