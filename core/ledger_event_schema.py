from __future__ import annotations

from collections.abc import Callable
import json
from typing import Final

from .ledger_schema import (
    JsonObject,
    JsonValue,
    LedgerSchemaError,
    _boolean,
    _confidence,
    _nonnegative_integer,
    _object,
    _optional_digest,
    _positive_integer,
    _reject,
    _required,
    _string,
    _string_list,
    _v2_schema,
)

CHANGE_SOURCES: Final = frozenset({"edit", "shell", "generated", "external"})
ATTRIBUTION_STATUSES: Final = frozenset({"exclusive", "contended"})
CHANGE_OPERATIONS: Final = frozenset(
    {"create", "modify", "delete", "type_change", "mode_change"}
)
CHANGE_KINDS: Final = frozenset({"code", "docs", "artifact"})
COMMIT_STATES: Final = frozenset({"uncommitted", "committed"})
TRANSITION_REQUIREMENTS: Final = {
    "create": (False, True),
    "modify": (True, True),
    "delete": (True, False),
    "type_change": (True, True),
    "mode_change": (True, True),
}


def _common_event(value: JsonObject) -> int:
    _v2_schema(value, "event")
    _ = _string(_required(value, "event_id", "event"), "event.event_id")
    seq = _positive_integer(_required(value, "seq", "event"), "event.seq")
    _ = _string(_required(value, "turn_id", "event"), "event.turn_id")
    _ = _string(_required(value, "agent", "event"), "event.agent")
    return seq


def _path_revision(value: JsonValue, field: str) -> tuple[str, str]:
    revision = _object(value, field)
    change_id = _string(_required(revision, "change_id", field), f"{field}.change_id")
    _ = _string(_required(revision, "path", field), f"{field}.path")
    _ = _optional_digest(_required(revision, "after", field), f"{field}.after")
    event_id = _string(
        _required(revision, "change_event_id", field),
        f"{field}.change_event_id",
    )
    return change_id, event_id


def _change_event(value: JsonObject) -> None:
    _ = _common_event(value)
    _ = _positive_integer(
        _required(value, "manifest_generation", "event"),
        "event.manifest_generation",
    )
    commit_state = _string(
        _required(value, "commit_state", "event"),
        "event.commit_state",
    )
    if commit_state not in COMMIT_STATES:
        _reject("event.commit_state", "must be uncommitted or committed")
    source = _string(_required(value, "source", "event"), "event.source")
    if source not in CHANGE_SOURCES:
        _reject("event.source", "must be a recognized source")
    owner = _required(value, "owner", "event")
    if owner is not None:
        _ = _string(owner, "event.owner")
    attribution = _string(
        _required(value, "attribution_status", "event"),
        "event.attribution_status",
    )
    if attribution not in ATTRIBUTION_STATUSES:
        _reject("event.attribution_status", "must be exclusive or contended")
    observed_by = _string_list(_required(value, "observed_by", "event"), "event.observed_by")
    if not observed_by:
        _reject("event.observed_by", "must not be empty")
    _ = _confidence(_required(value, "confidence", "event"), "event.confidence")
    _ = _confidence(
        _required(value, "source_confidence", "event"),
        "event.source_confidence",
    )
    _ = _string(_required(value, "invocation_id", "event"), "event.invocation_id")
    _ = _string(_required(value, "observed_at", "event"), "event.observed_at")
    _ = _string(_required(value, "snapshot_before", "event"), "event.snapshot_before")
    _ = _string(_required(value, "snapshot_after", "event"), "event.snapshot_after")
    paths = _required(value, "paths", "event")
    if not isinstance(paths, list) or not paths:
        _reject("event.paths", "must be a non-empty list")
    for index, raw_path in enumerate(paths):
        field = f"event.paths[{index}]"
        path = _object(raw_path, field)
        _ = _string(_required(path, "change_id", field), f"{field}.change_id")
        _ = _string(_required(path, "path", field), f"{field}.path")
        operation = _string(_required(path, "op", field), f"{field}.op")
        if operation not in CHANGE_OPERATIONS:
            _reject(f"{field}.op", "must be a recognized change operation")
        kind = _string(_required(path, "kind", field), f"{field}.kind")
        if kind not in CHANGE_KINDS:
            _reject(f"{field}.kind", "must be code, docs, or artifact")
        before = _optional_digest(_required(path, "before", field), f"{field}.before")
        after = _optional_digest(_required(path, "after", field), f"{field}.after")
        needs_before, needs_after = TRANSITION_REQUIREMENTS[operation]
        if needs_before != (before is not None):
            _reject(f"{field}.before", "does not match the change operation")
        if needs_after != (after is not None):
            _reject(f"{field}.after", "does not match the change operation")
        _ = _boolean(
            _required(path, "requires_verification", field),
            f"{field}.requires_verification",
        )


def _verification_event(value: JsonObject) -> None:
    seq = _common_event(value)
    _ = _string(_required(value, "invocation_id", "event"), "event.invocation_id")
    _ = _string(_required(value, "command", "event"), "event.command")
    _ = _boolean(_required(value, "success", "event"), "event.success")
    _ = _string(_required(value, "evidence", "event"), "event.evidence")
    covers = _object(_required(value, "covers", "event"), "event.covers")
    through_seq = _nonnegative_integer(
        _required(covers, "through_seq", "event.covers"),
        "event.covers.through_seq",
    )
    if through_seq >= seq:
        _reject("event.covers.through_seq", "must precede the verification event")
    _ = _string(
        _required(covers, "snapshot_id", "event.covers"),
        "event.covers.snapshot_id",
    )
    change_ids = _string_list(
        _required(covers, "change_ids", "event.covers"),
        "event.covers.change_ids",
    )
    change_event_ids = _string_list(
        _required(covers, "change_event_ids", "event.covers"),
        "event.covers.change_event_ids",
    )
    revisions = _required(covers, "path_revisions", "event.covers")
    if not isinstance(revisions, list):
        _reject("event.covers.path_revisions", "must be a list")
    if len(set(change_ids)) != len(change_ids):
        _reject("event.covers.change_ids", "must not contain duplicates")
    if len(set(change_event_ids)) != len(change_event_ids):
        _reject("event.covers.change_event_ids", "must not contain duplicates")
    for index, revision in enumerate(revisions):
        change_id, event_id = _path_revision(
            revision,
            f"event.covers.path_revisions[{index}]",
        )
        if change_id not in change_ids:
            _reject(
                f"event.covers.path_revisions[{index}].change_id",
                "must be listed in covers.change_ids",
            )
        if event_id not in change_event_ids:
            _reject(
                f"event.covers.path_revisions[{index}].change_event_id",
                "must be listed in covers.change_event_ids",
            )


EVENT_VALIDATORS: Final[dict[str, Callable[[JsonObject], None]]] = {
    "change": _change_event,
    "verification": _verification_event,
}


def validate_v2_event(value: JsonValue) -> JsonObject:
    event = _object(value, "event")
    event_type = _string(_required(event, "event", "event"), "event.event")
    validator = EVENT_VALIDATORS.get(event_type)
    if validator is None:
        _reject("event.event", "must be change or verification")
    validator(event)
    return event


def serialize_v2_event(value: JsonValue) -> str:
    return json.dumps(validate_v2_event(value), ensure_ascii=False, indent=2, sort_keys=True)


def deserialize_v2_event(serialized: str) -> JsonObject:
    try:
        value: JsonValue = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise LedgerSchemaError(field="event", requirement="must be valid JSON") from exc
    return validate_v2_event(value)
