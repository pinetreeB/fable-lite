from __future__ import annotations

from typing import Final

from .ledger import JsonObject, JsonValue, classify_change_kind, record_event
from .provenance_lifecycle_types import ObservedChange


SOURCE_CONFIDENCE: Final = {"edit": 1.0, "shell": 0.9, "generated": 0.85, "external": 1.0}


def record_observed_changes(
    payload: JsonObject,
    invocation_id: str,
    phase: str,
    changes: tuple[ObservedChange, ...],
    snapshot_id: str,
) -> None:
    for source in sorted({change.source for change in changes}):
        group = tuple(change for change in changes if change.source == source)
        _ = record_event(
            payload
            | {
                "schema_version": 2,
                "event": "change",
                "event_id": f"{invocation_id}:change:{source}",
                "source": source,
                "owner": _owner(group),
                "attribution_status": _attribution(group),
                "observed_by": sorted({agent for change in group for agent in change.observed_by}),
                "confidence": 1.0,
                "source_confidence": SOURCE_CONFIDENCE[source],
                "invocation_id": invocation_id,
                "observed_at": phase,
                "snapshot_before": "snapshot:unavailable",
                "snapshot_after": snapshot_id or "snapshot:unavailable",
                "current_snapshot_id": snapshot_id,
                "paths": [_path(change) for change in group],
            }
        )


def _owner(changes: tuple[ObservedChange, ...]) -> str | None:
    owners = {change.owner for change in changes}
    return next(iter(owners)) if len(owners) == 1 else None


def _attribution(changes: tuple[ObservedChange, ...]) -> str:
    return "exclusive" if all(change.attribution_status == "exclusive" for change in changes) else "contended"


def _path(change: ObservedChange) -> JsonObject:
    kind = classify_change_kind(change.path)
    return {
        "change_id": change.change_id,
        "path": change.path,
        "op": change.op.value,
        "kind": kind,
        "before": change.before_digest,
        "after": change.after_digest,
        "requires_verification": kind != "docs",
    }
