from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import os as os
from pathlib import Path
from typing import Final
from uuid import uuid4

from .agent_log import (
    _LedgerTransaction,
    agent_log_path as agent_log_path,
    append_agent_event,
    ledger_transaction,
    load_agent_events,
)
from .ledger_migration import (
    LedgerMigrationError as LedgerMigrationError,
    backfill_invocation_statuses,
    invocation_status_backfill_required,
    migrate_v1_ledger,
    migrate_v2_invocation_statuses,
)
from .ledger_schema import (
    JsonObject as JsonObject,
    JsonScalar as JsonScalar,
    JsonValue as JsonValue,
    LedgerSchemaError,
    sanitize_coordination_delivered,
    sanitize_coordination_outbox,
    serialize_v2_ledger,
    validate_coordination_outbox_entry,
    validate_v2_ledger,
)
from .ledger_storage import atomic_write_text, ledger_path as ledger_path, state_dir as state_dir
from .ledger_v1 import apply_v1_event, classify_change_kind as classify_change_kind, default_ledger, sequence_value
from .ledger_v2 import apply_v2_event, default_v2_ledger, turn_is_closed
from .release_gate import auto_migration_enabled, status_backfill_enabled
from .verification_covers import active_turn, agent_key, capture_covers


MAX_COORDINATION_OUTBOX: Final = 256
COORDINATION_DRAIN_BATCH: Final = 16
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _RecordedEvent:
    ledger: JsonObject
    payload: JsonObject
    saved: bool


class _StatusMigrationRequiredLedger(dict[str, JsonValue]):
    """A fail-closed compatibility view that must never reach persistence."""


def _project_root(payload: Mapping[str, JsonValue]) -> str:
    root = payload.get("project_root") or payload.get("cwd")
    return root if isinstance(root, str) and root else "."


def _agent(payload: Mapping[str, JsonValue]) -> str:
    value = payload.get("agent")
    return value if isinstance(value, str) and value else ""


def load_ledger(payload: Mapping[str, JsonValue]) -> JsonObject:
    path = ledger_path(_project_root(payload))
    try:
        loaded: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        _preserve_corrupt_ledger(path)
        return _expose_attribution_health(default_ledger(), path, degraded=True)
    except FileNotFoundError:
        return _expose_attribution_health(default_ledger(), path)
    except OSError:
        return _expose_attribution_health(default_ledger(), path, degraded=True)
    if isinstance(loaded, dict):
        schema_version = loaded.get("schema_version")
        if schema_version == 2:
            if invocation_status_backfill_required(loaded):
                backfilled, _changed = backfill_invocation_statuses(loaded)
                backfilled["attribution_degraded"] = True
                return _StatusMigrationRequiredLedger(
                    _expose_attribution_health(
                        _validate_v2_with_derived_cache_fail_open(backfilled),
                        path,
                        degraded=True,
                    )
                )
            return _expose_attribution_health(
                _validate_v2_with_derived_cache_fail_open(loaded),
                path,
            )
        if schema_version is not None and (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != 1
        ):
            raise LedgerSchemaError("ledger.schema_version", "must be 1 or 2")
        merged = default_ledger()
        merged.update(loaded)
        return _expose_attribution_health(merged, path)
    _preserve_corrupt_ledger(path)
    return _expose_attribution_health(default_ledger(), path, degraded=True)


def _expose_attribution_health(
    ledger: JsonObject,
    path: Path,
    *,
    degraded: bool = False,
) -> JsonObject:
    ledger["attribution_degraded"] = degraded or _has_corrupt_backup(path)
    return ledger


def _has_corrupt_backup(path: Path) -> bool:
    try:
        return next(path.parent.glob("*.corrupt-*.bak"), None) is not None
    except OSError:
        return True


def _validate_v2_with_derived_cache_fail_open(loaded: JsonObject) -> JsonObject:
    sanitized = dict(loaded)
    while True:
        try:
            return validate_v2_ledger(sanitized)
        except LedgerSchemaError as exc:
            if exc.field.startswith("ledger.scorecard_"):
                _ = sanitized.pop("scorecard_cache", None)
                _ = sanitized.pop("scorecard_journal_offset", None)
                _ = sanitized.pop("scorecard_evicted_keys", None)
                continue
            if (
                ".bootstrap_recovered_at" in exc.field
                or ".bootstrap_recovery_evidence_refs" in exc.field
            ):
                if not _sanitize_bootstrap_coordination_fields(sanitized):
                    raise
                sanitized["coordination_degraded"] = True
                continue
            if exc.field.startswith("ledger.coordination_"):
                outbox = sanitize_coordination_outbox(
                    sanitized.get("coordination_outbox")
                )
                delivered = sanitize_coordination_delivered(
                    sanitized.get("coordination_delivered")
                )
                for event_id in set(outbox) & set(delivered):
                    if outbox[event_id] == delivered[event_id]:
                        del outbox[event_id]
                    else:
                        del delivered[event_id]
                sanitized["coordination_outbox"] = outbox
                sanitized["coordination_delivered"] = delivered
                raw_order = sanitized.get("coordination_delivered_order")
                order = (
                    [
                        item
                        for item in raw_order
                        if isinstance(item, str) and item in delivered
                    ]
                    if isinstance(raw_order, list)
                    else []
                )
                order = list(dict.fromkeys(order))
                order.extend(event_id for event_id in delivered if event_id not in order)
                sanitized["coordination_delivered_order"] = order[-256:]
                sanitized["coordination_delivered"] = {
                    event_id: delivered[event_id]
                    for event_id in sanitized["coordination_delivered_order"]
                }
                cursor = sanitized.get("coordination_drain_cursor")
                if (
                    not isinstance(cursor, int)
                    or isinstance(cursor, bool)
                    or cursor < 0
                ):
                    sanitized["coordination_drain_cursor"] = 0
                sanitized["coordination_degraded"] = True
                continue
            raise


def _sanitize_bootstrap_coordination_fields(ledger: JsonObject) -> bool:
    active_value = ledger.get("active_turns")
    if not isinstance(active_value, dict):
        return False
    active = dict(active_value)
    changed = False
    for key, raw_turn in active.items():
        if not isinstance(raw_turn, dict):
            continue
        turn = dict(raw_turn)
        recovered_at = turn.get("bootstrap_recovered_at")
        if "bootstrap_recovered_at" in turn and (
            not isinstance(recovered_at, str) or not recovered_at
        ):
            _ = turn.pop("bootstrap_recovered_at", None)
            changed = True
        evidence = turn.get("bootstrap_recovery_evidence_refs")
        if "bootstrap_recovery_evidence_refs" in turn and (
            not isinstance(evidence, list)
            or len(evidence) > 32
            or not all(isinstance(item, str) and item for item in evidence)
        ):
            _ = turn.pop("bootstrap_recovery_evidence_refs", None)
            changed = True
        active[key] = turn
    if changed:
        ledger["active_turns"] = active
    return changed


def _preserve_corrupt_ledger(path: Path) -> None:
    if not path.exists():
        return
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.corrupt-{timestamp}-{uuid4().hex}.bak")
    try:
        path.replace(backup)
    except OSError:
        return


def save_ledger(payload: Mapping[str, JsonValue], ledger: JsonObject) -> bool:
    if isinstance(ledger, _StatusMigrationRequiredLedger):
        return False
    destination = ledger_path(_project_root(payload))
    if _invocation_status_migration_required(destination):
        return False
    schema_version = ledger.get("schema_version")
    serialized = serialize_v2_ledger(ledger) if schema_version == 2 else json.dumps(
        ledger, ensure_ascii=False, indent=2, sort_keys=True
    )
    try:
        atomic_write_text(destination, serialized)
    except OSError:
        return False
    return True


def record_event(payload: Mapping[str, JsonValue]) -> JsonObject:
    root = _project_root(payload)
    destination = ledger_path(root)
    if not _auto_migrate_ledger(root, destination):
        return load_ledger(payload)
    with ledger_transaction(root) as transaction:
        recorded = _record_event_locked(payload, transaction)
    if recorded.saved:
        _record_coordination_after_event(root, recorded.payload)
    return recorded.ledger


def record_event_if_current_turn(
    payload: Mapping[str, JsonValue],
    *,
    allow_missing: bool = False,
) -> bool:
    """Record only when an existing actor turn has not been replaced."""
    root = _project_root(payload)
    destination = ledger_path(root)
    if not _auto_migrate_ledger(root, destination):
        return False
    recorded: _RecordedEvent | None = None
    with ledger_transaction(root) as transaction:
        ledger = load_ledger(payload)
        turn = active_turn(ledger, payload)
        requested_turn_id = payload.get("turn_id")
        active = ledger.get("active_turns")
        raw_turn = active.get(agent_key(payload)) if isinstance(active, dict) else None
        if turn is None:
            if not allow_missing or isinstance(raw_turn, dict):
                return False
            if requested_turn_id is not None and not isinstance(requested_turn_id, str):
                return False
            if isinstance(requested_turn_id, str) and turn_is_closed(
                ledger, agent_key(payload), requested_turn_id
            ):
                return False
        elif (
            not isinstance(requested_turn_id, str)
            or turn.get("turn_id") != requested_turn_id
        ):
            return False
        recorded = _record_event_locked(payload, transaction)
    if recorded.saved:
        _record_coordination_after_event(root, recorded.payload)
    return recorded.saved


def _record_event_locked(
    payload: Mapping[str, JsonValue],
    transaction: _LedgerTransaction,
) -> _RecordedEvent:
    root = _project_root(payload)
    transaction.assert_active_for(root)
    destination = ledger_path(root)
    existed_before_load = destination.exists()
    ledger = load_ledger(payload)
    if not existed_before_load:
        ledger = default_v2_ledger()
    event_payload = dict(payload)
    _ = event_payload.pop("event_seq", None)
    _decorate_design_prompt(root, event_payload)
    event_payload["seq"] = sequence_value(ledger.get("event_seq")) + 1
    if ledger.get("schema_version") == 2:
        coordination_transition_applicable = _coordination_transition_applicable(
            ledger,
            event_payload,
        )
        _ = apply_v2_event(ledger, event_payload)
        _enqueue_coordination_after_event(
            root,
            ledger,
            event_payload,
            transition_applicable=coordination_transition_applicable,
        )
    else:
        _ = apply_v1_event(ledger, event_payload)
    saved = save_ledger(payload, ledger)
    if saved:
        append_agent_event(root, _agent(payload), event_payload)
    return _RecordedEvent(ledger, event_payload, saved)


def _record_coordination_after_event(
    root: str, payload: Mapping[str, JsonValue]
) -> None:
    """Project committed coordination without changing the gate result."""
    try:
        ledger = load_ledger({"project_root": root})
        if ledger.get("schema_version") == 2:
            outbox = ledger.get("coordination_outbox")
            if not isinstance(outbox, dict) or not outbox:
                return
            _drain_coordination_outbox(root)
            return
        raw = _coordination_event_for_payload(root, payload, ledger)
        if raw is None:
            return
        from .scorecard_coordination import (
            parse_coordination_event,
            try_record_coordination_event,
        )

        _ = try_record_coordination_event(root, parse_coordination_event(raw))
    except Exception:  # noqa: BLE001 - coordination is fail-open observability.
        return


def _enqueue_coordination_after_event(
    root: str,
    ledger: JsonObject,
    payload: Mapping[str, JsonValue],
    *,
    transition_applicable: bool,
) -> None:
    if not _coordination_source(payload) or not transition_applicable:
        return
    try:
        raw = _coordination_event_for_payload(root, payload, ledger)
        if raw is None:
            return
        event_id = _required_event_string(raw, "event_id")
        for field in ("coordination_outbox", "coordination_delivered"):
            existing = ledger.get(field)
            if isinstance(existing, dict) and event_id in existing:
                return
        _accepted, _changed = _enqueue_coordination_raw(ledger, raw)
    except Exception:  # noqa: BLE001 - the authoritative event remains fail-open.
        ledger["coordination_degraded"] = True


def enqueue_coordination_event(
    root: str,
    raw: Mapping[str, JsonValue],
) -> bool | None:
    """Durably accept a derived event, or return None for a legacy ledger."""
    destination = ledger_path(root)
    accepted = False
    saved = False
    with ledger_transaction(root):
        existed_before_load = destination.exists()
        ledger = load_ledger({"project_root": root})
        if not existed_before_load:
            ledger = default_v2_ledger()
        if ledger.get("schema_version") != 2:
            return None
        try:
            accepted, changed = _enqueue_coordination_raw(ledger, dict(raw))
        except Exception:  # noqa: BLE001 - malformed observations fail open.
            accepted = False
            changed = ledger.get("coordination_degraded") is not True
            ledger["coordination_degraded"] = True
        saved = not changed or save_ledger({"project_root": root}, ledger)
    if accepted and saved:
        try:
            _drain_coordination_outbox(root)
        except Exception:  # noqa: BLE001 - durable pending state is sufficient.
            pass
    return accepted and saved


def _enqueue_coordination_raw(
    ledger: JsonObject,
    raw: JsonObject,
) -> tuple[bool, bool]:
    event_id = _required_event_string(raw, "event_id")
    validate_coordination_outbox_entry(event_id, raw)
    outbox_value = ledger.get("coordination_outbox")
    outbox = outbox_value if isinstance(outbox_value, dict) else {}
    changed = outbox is not outbox_value
    ledger["coordination_outbox"] = outbox
    delivered_value = ledger.get("coordination_delivered")
    delivered = delivered_value if isinstance(delivered_value, dict) else {}
    if event_id in delivered:
        if delivered[event_id] == raw:
            return True, changed
        degraded_changed = ledger.get("coordination_degraded") is not True
        ledger["coordination_degraded"] = True
        return False, changed or degraded_changed
    if event_id in outbox:
        if outbox[event_id] == raw:
            return True, changed
        degraded_changed = ledger.get("coordination_degraded") is not True
        ledger["coordination_degraded"] = True
        return False, changed or degraded_changed
    if len(outbox) >= MAX_COORDINATION_OUTBOX:
        degraded_changed = ledger.get("coordination_degraded") is not True
        ledger["coordination_degraded"] = True
        return False, changed or degraded_changed
    outbox[event_id] = raw
    return True, True


def load_accepted_coordination_event(
    root: str,
    event_id: str,
) -> JsonObject | None:
    with ledger_transaction(root):
        ledger = load_ledger({"project_root": root})
        if ledger.get("schema_version") != 2:
            return None
        for field in ("coordination_outbox", "coordination_delivered"):
            events = ledger.get(field)
            raw = events.get(event_id) if isinstance(events, dict) else None
            if isinstance(raw, dict):
                return dict(raw)
    return None


def _drain_coordination_outbox(root: str) -> None:
    with ledger_transaction(root):
        ledger = load_ledger({"project_root": root})
        if ledger.get("schema_version") != 2:
            return
        outbox = ledger.get("coordination_outbox")
        if not isinstance(outbox, dict) or not outbox:
            return
        items = list(outbox.items())
        cursor = sequence_value(ledger.get("coordination_drain_cursor"))
        start = cursor % len(items)
        pending = (items[start:] + items[:start])[:COORDINATION_DRAIN_BATCH]
    from .scorecard_coordination import (
        CoordinationSchemaError,
        coordination_event_json,
        load_coordination_journal,
        parse_coordination_event,
        record_coordination_event_for_delivery,
    )

    delivered: dict[str, JsonValue] = {}
    delivered_receipts: dict[str, JsonValue] = {}
    schema_error = False
    delivery_error = False
    degraded = not load_coordination_journal(root).complete
    attempted = 0
    for event_id, raw in pending:
        attempted += 1
        if not isinstance(raw, dict):
            schema_error = True
            continue
        try:
            event = parse_coordination_event(raw)
            canonical = record_coordination_event_for_delivery(root, event)
        except CoordinationSchemaError:
            schema_error = True
            continue
        except Exception:  # noqa: BLE001 - transient delivery failures stay pending.
            delivery_error = True
            break
        delivered[event_id] = raw
        delivered_receipts[event_id] = coordination_event_json(canonical)
    _ack_coordination_outbox(
        root,
        delivered,
        delivered_receipts=delivered_receipts,
        degraded=degraded or schema_error or delivery_error,
        drain_cursor=cursor + attempted,
    )


def _ack_coordination_outbox(
    root: str,
    acknowledged: Mapping[str, JsonValue],
    *,
    delivered_receipts: Mapping[str, JsonValue] | None = None,
    degraded: bool,
    drain_cursor: int | None = None,
) -> None:
    if not acknowledged and not degraded and drain_cursor is None:
        return
    with ledger_transaction(root):
        ledger = load_ledger({"project_root": root})
        if ledger.get("schema_version") != 2:
            return
        outbox = ledger.get("coordination_outbox")
        delivered_value = ledger.get("coordination_delivered")
        delivered_events = (
            delivered_value if isinstance(delivered_value, dict) else {}
        )
        order_value = ledger.get("coordination_delivered_order")
        delivered_order = (
            [
                item
                for item in order_value
                if isinstance(item, str) and item in delivered_events
            ]
            if isinstance(order_value, list)
            else []
        )
        delivered_order = list(dict.fromkeys(delivered_order))
        delivered_order.extend(
            event_id for event_id in delivered_events if event_id not in delivered_order
        )
        changed = False
        if isinstance(outbox, dict):
            for event_id, raw in acknowledged.items():
                if outbox.get(event_id) == raw:
                    del outbox[event_id]
                    delivered_events[event_id] = (
                        delivered_receipts.get(event_id, raw)
                        if delivered_receipts is not None
                        else raw
                    )
                    if event_id in delivered_order:
                        delivered_order.remove(event_id)
                    delivered_order.append(event_id)
                    changed = True
        if len(delivered_order) > MAX_COORDINATION_OUTBOX:
            delivered_order = delivered_order[-MAX_COORDINATION_OUTBOX:]
            delivered_events = {
                event_id: delivered_events[event_id]
                for event_id in delivered_order
            }
            changed = True
        if ledger.get("coordination_delivered") != delivered_events:
            ledger["coordination_delivered"] = delivered_events
            changed = True
        if ledger.get("coordination_delivered_order") != delivered_order:
            ledger["coordination_delivered_order"] = delivered_order
            changed = True
        if degraded and ledger.get("coordination_degraded") is not True:
            ledger["coordination_degraded"] = True
            changed = True
        if drain_cursor is not None:
            current_cursor = sequence_value(ledger.get("coordination_drain_cursor"))
            if drain_cursor > current_cursor:
                ledger["coordination_drain_cursor"] = drain_cursor
                changed = True
        if changed:
            _ = save_ledger({"project_root": root}, ledger)


def _coordination_source(payload: Mapping[str, JsonValue]) -> bool:
    event = payload.get("event")
    entered = event == "prompt" and _missing_bootstrap(payload)
    recovered = event == "turn_bootstrap_recovered" and _recovered_bootstrap(payload)
    return entered or recovered


def _coordination_transition_applicable(
    ledger: Mapping[str, JsonValue],
    payload: Mapping[str, JsonValue],
) -> bool:
    if not _coordination_source(payload):
        return False
    if payload.get("event") != "turn_bootstrap_recovered":
        return True
    turn = active_turn(ledger, payload)
    return turn is not None and turn.get("baseline_status") == "missing"


def _coordination_event_for_payload(
    root: str,
    payload: Mapping[str, JsonValue],
    ledger: JsonObject | None = None,
) -> JsonObject | None:
    event = payload.get("event")
    entered = event == "prompt" and _missing_bootstrap(payload)
    recovered = event == "turn_bootstrap_recovered" and _recovered_bootstrap(payload)
    if not entered and not recovered:
        return None
    if ledger is not None and ledger.get("schema_version") == 2:
        turn = active_turn(ledger, payload)
        if turn is None:
            return None
        if entered and turn.get("baseline_status") != "missing":
            return None
        if recovered and not (
            turn.get("baseline_status") == "ready"
            and turn.get("provenance_incomplete") is False
            and turn.get("provenance_status") == "complete"
            and turn.get("provenance_status_reason") == ""
            and turn.get("baseline_snapshot_id")
            == payload.get("baseline_snapshot_id")
        ):
            return None
    try:
        from .scorecard import Attribution, SessionIdentity
        from .scorecard_coordination import (
            CoordinationCategory,
            CoordinationOutcome,
            CoordinationReason,
            coordination_event_json,
            new_coordination_event,
            stable_coordination_event_id,
        )

        actor = SessionIdentity(
            _required_event_string(payload, "host"),
            _required_event_string(payload, "session_id"),
            _required_event_string(payload, "agent"),
        )
        turn_id = _required_event_string(payload, "turn_id")
        outcome = (
            CoordinationOutcome.ENTERED
            if entered
            else CoordinationOutcome.RECOVERED
        )
        reason = (
            CoordinationReason.TURN_NOT_STARTED
            if entered
            else CoordinationReason.COMPLETE
        )
        invocation_id = payload.get("invocation_id")
        evidence_refs = (
            (f"invocation:{invocation_id}",)
            if recovered and isinstance(invocation_id, str) and invocation_id
            else ()
        )
        if recovered:
            evidence_refs = _bootstrap_coordination_evidence(
                ledger,
                payload,
                evidence_refs,
            )
        attribution_value = payload.get("attribution")
        attribution = (
            Attribution.LEGACY_DEFAULT
            if attribution_value == Attribution.LEGACY_DEFAULT.value
            else Attribution.EXACT
        )
        event_id = stable_coordination_event_id(
            root,
            actor,
            turn_id,
            CoordinationCategory.TURN_BOOTSTRAP,
            outcome,
            reason,
            evidence_refs,
        )
        occurred_at = _bootstrap_coordination_time(
            ledger,
            payload,
            recovered=recovered,
        )
        if (
            ledger is not None
            and ledger.get("schema_version") == 2
            and occurred_at is None
        ):
            raise ValueError("missing canonical bootstrap coordination timestamp")
        coordination = new_coordination_event(
            actor,
            turn_id,
            CoordinationCategory.TURN_BOOTSTRAP,
            outcome,
            reason,
            evidence_refs=evidence_refs,
            attribution=attribution,
            event_id=event_id,
            occurred_at=occurred_at,
        )
        return coordination_event_json(coordination)
    except Exception:  # noqa: BLE001 - caller reports degraded observability.
        raise


def _bootstrap_coordination_time(
    ledger: JsonObject | None,
    payload: Mapping[str, JsonValue],
    *,
    recovered: bool,
) -> datetime | None:
    if ledger is None or ledger.get("schema_version") != 2:
        return None
    turn = active_turn(ledger, payload)
    if turn is None:
        return None
    field = "bootstrap_recovered_at" if recovered else "started_at"
    value = turn.get(field)
    if not isinstance(value, str):
        return None
    try:
        observed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if observed.tzinfo is None or observed.utcoffset() != UTC.utcoffset(observed):
        return None
    return observed.astimezone(UTC)


def _bootstrap_coordination_evidence(
    ledger: JsonObject | None,
    payload: Mapping[str, JsonValue],
    fallback: tuple[str, ...],
) -> tuple[str, ...]:
    if ledger is None or ledger.get("schema_version") != 2:
        return fallback
    turn = active_turn(ledger, payload)
    if turn is None:
        return fallback
    raw = turn.get("bootstrap_recovery_evidence_refs")
    if not isinstance(raw, list) or not all(
        isinstance(item, str) and item for item in raw
    ):
        return fallback
    return tuple(raw)


def _missing_bootstrap(payload: Mapping[str, JsonValue]) -> bool:
    if payload.get("provenance_incomplete") is True:
        return True
    baseline = payload.get("baseline_snapshot_id")
    return "baseline_snapshot_id" in payload and (
        not isinstance(baseline, str)
        or not baseline
        or baseline == "snapshot:unavailable"
    )


def _recovered_bootstrap(payload: Mapping[str, JsonValue]) -> bool:
    return (
        payload.get("turn_bootstrap_recovered") is True
        and payload.get("baseline_status") == "ready"
        and payload.get("provenance_incomplete") is False
        and payload.get("provenance_status") == "complete"
        and payload.get("provenance_status_reason") == ""
    )


def _required_event_string(
    payload: Mapping[str, JsonValue], field: str
) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing coordination identity field: {field}")
    return value


def _decorate_design_prompt(root: str, payload: dict[str, JsonValue]) -> None:
    if payload.get("event") != "prompt":
        return
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        return
    from .classify import classify_prompt
    from .design_gate import dirty_ui_line_baseline, git_head

    result = classify_prompt({"prompt": prompt, "project_root": root})
    if result.get("design_required") is not True:
        return
    payload["design_required"] = True
    payload["design_baseline_revision"] = git_head(Path(root))
    payload["design_dirty_baseline"] = dirty_ui_line_baseline(Path(root))


def _legacy_ledger_exists(destination: Path) -> bool:
    try:
        loaded: JsonValue = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(loaded, dict):
        return False
    schema_version = loaded.get("schema_version")
    return schema_version is None or (
        isinstance(schema_version, int)
        and not isinstance(schema_version, bool)
        and schema_version == 1
    )


def _invocation_status_migration_required(destination: Path) -> bool:
    try:
        loaded: JsonValue = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return invocation_status_backfill_required(loaded)


def _auto_migrate_ledger(root: str, destination: Path) -> bool:
    try:
        if auto_migration_enabled() and _legacy_ledger_exists(destination):
            _ = migrate_v1_ledger(root)
        elif status_backfill_enabled() and _invocation_status_migration_required(destination):
            _ = migrate_v2_invocation_statuses(root)
    except LedgerMigrationError as exc:
        _log_auto_migration_failure(exc)
        return False
    return True


def _log_auto_migration_failure(exc: LedgerMigrationError) -> None:
    try:
        LOGGER.warning(
            "automatic ledger migration failed: stage=%s detail=%s",
            exc.stage,
            exc.detail,
        )
    except Exception:  # noqa: BLE001 - diagnostics must not break hook fail-open.
        return


def migrate_ledger_to_v2(payload: Mapping[str, JsonValue]) -> JsonObject:
    return migrate_v1_ledger(_project_root(payload))


def capture_verification_covers(payload: Mapping[str, JsonValue]) -> JsonObject:
    root = _project_root(payload)
    with ledger_transaction(root):
        ledger = load_ledger(payload)
        if ledger.get("schema_version") != 2:
            raise LedgerSchemaError("ledger.schema_version", "must equal 2 for covers capture")
        turn = active_turn(ledger, payload)
        if turn is None:
            raise LedgerSchemaError("ledger.active_turns", "must contain the verification agent turn")
        target_ids = payload.get("remote_target_ids")
        return capture_covers(
            ledger,
            turn,
            target_ids if isinstance(target_ids, list) else (),
        )


def load_agent_ledger(payload: Mapping[str, JsonValue]) -> JsonObject:
    agent = _agent(payload)
    if not agent:
        return load_ledger(payload)
    root = _project_root(payload)
    events = load_agent_events(root, agent)
    if events is None:
        return load_ledger(payload)
    if _agent_events_have_v2_state(events):
        ledger = default_v2_ledger()
        for event in events:
            _ = apply_v2_event(ledger, event)
        return ledger
    ledger = default_ledger()
    for event in events:
        _ = apply_v1_event(ledger, event)
    return ledger


def _agent_events_have_v2_state(events: list[JsonObject]) -> bool:
    return any(
        isinstance(event.get("paths"), list)
        or isinstance(event.get("covers"), dict)
        or isinstance(event.get("baseline_snapshot_id"), str)
        for event in events
    )
