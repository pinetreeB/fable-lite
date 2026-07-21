"""# noqa: SIZE_OK  — centralized v2 trust-boundary schema; the F1 card forbids a new module"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
import json
from typing import NoReturn, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

_COORDINATION_FIELDS = frozenset(
    {
        "scorecard_coord_schema_version",
        "event",
        "event_id",
        "actor",
        "actor_turn_id",
        "subject_agent_key",
        "category",
        "outcome",
        "reason_code",
        "evidence_refs",
        "attribution",
        "occurred_at",
    }
)
_COORDINATION_CATEGORIES = frozenset(
    {
        "r2_deny",
        "peer_exclusion",
        "peer_conflict",
        "owner_settlement",
        "attribution_health",
        "turn_bootstrap",
        "invocation_lease",
        "quick_promotion",
        "cross_evidence",
    }
)
_COORDINATION_OUTCOMES = frozenset(
    {
        "blocked",
        "avoided_block",
        "entered",
        "recovered",
        "settled",
        "expired",
        "rejected",
        "degraded",
    }
)
_COORDINATION_REASONS = frozenset(
    {
        "attribution_degraded",
        "command_parse_unavailable",
        "peer_unsettled",
        "state_dir_protected",
        "unresolvable_target",
        "turn_not_started",
        "complete",
        "peer_activity",
        "peer_conflict",
        "owner_settled",
        "attribution_health",
        "invocation_lease",
        "cross_evidence",
        "quick_promotion",
    }
)

class LedgerSchemaError(ValueError):
    def __init__(self, field: str, requirement: str) -> None:
        self.field = field
        self.requirement = requirement
        super().__init__(field, requirement)

    def __str__(self) -> str:
        return f"invalid v2 ledger schema at {self.field}: {self.requirement}"


def _reject(field: str, requirement: str) -> NoReturn:
    raise LedgerSchemaError(field=field, requirement=requirement)


def _object(value: JsonValue, field: str) -> JsonObject:
    if not isinstance(value, dict):
        _reject(field, "must be an object")
    return value


def _required(value: JsonObject, key: str, field: str) -> JsonValue:
    if key not in value:
        _reject(f"{field}.{key}", "is required")
    return value[key]


def _string(value: JsonValue, field: str) -> str:
    if not isinstance(value, str) or not value:
        _reject(field, "must be a non-empty string")
    return value


def _boolean(value: JsonValue, field: str) -> bool:
    if not isinstance(value, bool):
        _reject(field, "must be a boolean")
    return value


def _nonnegative_integer(value: JsonValue, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _reject(field, "must be a non-negative integer")
    return value


def _positive_integer(value: JsonValue, field: str) -> int:
    result = _nonnegative_integer(value, field)
    if result == 0:
        _reject(field, "must be a positive integer")
    return result


def _confidence(value: JsonValue, field: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        _reject(field, "must be a number between 0.0 and 1.0")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        _reject(field, "must be between 0.0 and 1.0")
    return result


def _string_list(value: JsonValue, field: str) -> list[str]:
    if not isinstance(value, list):
        _reject(field, "must be a list")
    return [_string(item, f"{field}[{index}]") for index, item in enumerate(value)]


def _optional_digest(value: JsonValue, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)


def _v2_schema(value: JsonObject, field: str) -> None:
    schema_version = _required(value, "schema_version", field)
    if schema_version != 2 or isinstance(schema_version, bool):
        _reject(f"{field}.schema_version", "must equal 2")


def _validate_v1_projection(value: JsonObject) -> None:
    _string(_required(value, "task_mode", "ledger"), "ledger.task_mode")
    _string(_required(value, "prompt", "ledger"), "ledger.prompt")
    _string_list(_required(value, "packs", "ledger"), "ledger.packs")
    _string_list(
        _required(value, "changed_files_seen", "ledger"),
        "ledger.changed_files_seen",
    )
    _string_list(_required(value, "change_kinds", "ledger"), "ledger.change_kinds")
    _string_list(
        _required(value, "verification_commands", "ledger"),
        "ledger.verification_commands",
    )
    results = _required(value, "verification_results", "ledger")
    if not isinstance(results, list):
        _reject("ledger.verification_results", "must be a list")
    for index, raw_result in enumerate(results):
        result = _object(raw_result, f"ledger.verification_results[{index}]")
        _string(
            _required(result, "command", f"ledger.verification_results[{index}]"),
            f"ledger.verification_results[{index}].command",
        )
        _boolean(
            _required(result, "success", f"ledger.verification_results[{index}]"),
            f"ledger.verification_results[{index}].success",
        )
        _string(
            _required(result, "evidence", f"ledger.verification_results[{index}]"),
            f"ledger.verification_results[{index}].evidence",
        )
        if "seq" in result:
            _nonnegative_integer(result["seq"], f"ledger.verification_results[{index}].seq")
    for field in ("event_seq", "last_change_seq", "stop_blocks", "goals_blocks", "intent_blocks"):
        _nonnegative_integer(_required(value, field, "ledger"), f"ledger.{field}")
    for field in (
        "requires_investigation_compliance",
        "needs_goals",
        "intent_required",
    ):
        _boolean(_required(value, field, "ledger"), f"ledger.{field}")
    _nonnegative_integer(
        _required(value, "ambiguity_score", "ledger"),
        "ledger.ambiguity_score",
    )
    _string_list(_required(value, "scope_warnings", "ledger"), "ledger.scope_warnings")
    _string(_required(value, "agent", "ledger"), "ledger.agent")
    _validate_design_state(value, "ledger")


def _validate_design_state(value: JsonObject, field: str) -> None:
    if "design_required" not in value:
        return
    for name in ("design_required", "design_touched", "design_check_passed"):
        _boolean(_required(value, name, field), f"{field}.{name}")
    for name in ("design_blocks", "design_last_change_seq", "design_check_seq"):
        _nonnegative_integer(_required(value, name, field), f"{field}.{name}")
    _string(
        _required(value, "design_baseline_revision", field),
        f"{field}.design_baseline_revision",
    )
    violations = _required(value, "design_violations", field)
    if not isinstance(violations, list):
        _reject(f"{field}.design_violations", "must be a list")
    dirty_baseline = _object(
        _required(value, "design_dirty_baseline", field),
        f"{field}.design_dirty_baseline",
    )
    for path, hashes in dirty_baseline.items():
        _string(path, f"{field}.design_dirty_baseline key")
        _string_list(hashes, f"{field}.design_dirty_baseline.{path}")


def validate_v2_ledger(value: JsonValue) -> JsonObject:
    ledger = _object(value, "ledger")
    _v2_schema(ledger, "ledger")
    _validate_v1_projection(ledger)
    _nonnegative_integer(
        _required(ledger, "manifest_generation", "ledger"),
        "ledger.manifest_generation",
    )
    _validate_manifest_transaction(ledger)
    _validate_path_attribution(ledger)
    if "closed_turns" in ledger:
        _validate_closed_turns(ledger["closed_turns"])
    if "coordination_outbox" in ledger:
        _validate_coordination_outbox(ledger["coordination_outbox"])
    if "coordination_degraded" in ledger:
        _boolean(ledger["coordination_degraded"], "ledger.coordination_degraded")
    if "coordination_drain_cursor" in ledger:
        _nonnegative_integer(
            ledger["coordination_drain_cursor"],
            "ledger.coordination_drain_cursor",
        )
    if "coordination_delivered" in ledger:
        _validate_coordination_delivered(ledger["coordination_delivered"])
    if "coordination_delivered_order" in ledger:
        _validate_coordination_delivered_order(
            ledger["coordination_delivered_order"],
            ledger.get("coordination_delivered"),
        )
    outbox = ledger.get("coordination_outbox")
    delivered = ledger.get("coordination_delivered")
    if isinstance(outbox, dict) and isinstance(delivered, dict):
        if set(outbox) & set(delivered):
            _reject(
                "ledger.coordination_outbox",
                "must not overlap coordination_delivered",
            )
    active_turns = _object(_required(ledger, "active_turns", "ledger"), "ledger.active_turns")
    for agent, raw_turn in active_turns.items():
        _string(agent, "ledger.active_turns key")
        field = f"ledger.active_turns.{agent}"
        turn = _object(raw_turn, field)
        _string(_required(turn, "turn_id", field), f"{field}.turn_id")
        _nonnegative_integer(_required(turn, "start_seq", field), f"{field}.start_seq")
        _string(
            _required(turn, "baseline_snapshot_id", field),
            f"{field}.baseline_snapshot_id",
        )
        _string(
            _required(turn, "current_snapshot_id", field),
            f"{field}.current_snapshot_id",
        )
        _string_list(
            _required(turn, "pending_change_ids", field),
            f"{field}.pending_change_ids",
        )
        blocks = _object(_required(turn, "blocks", field), f"{field}.blocks")
        _nonnegative_integer(_required(blocks, "stop", f"{field}.blocks"), f"{field}.blocks.stop")
        if "agent" in turn:
            _string(turn["agent"], f"{field}.agent")
        if "migration_mode" in turn:
            migration_mode = _string(turn["migration_mode"], f"{field}.migration_mode")
            if migration_mode != "legacy_turn":
                _reject(f"{field}.migration_mode", "must equal legacy_turn")
        if "legacy_seq_less" in turn:
            _boolean(turn["legacy_seq_less"], f"{field}.legacy_seq_less")
        for timestamp_field in (
            "started_at",
            "last_event_at",
            "finish_requested_at",
            "bootstrap_recovered_at",
        ):
            if timestamp_field in turn:
                _string(turn[timestamp_field], f"{field}.{timestamp_field}")
        if "bootstrap_recovery_evidence_refs" in turn:
            evidence = _string_list(
                turn["bootstrap_recovery_evidence_refs"],
                f"{field}.bootstrap_recovery_evidence_refs",
            )
            if len(evidence) > 32:
                _reject(
                    f"{field}.bootstrap_recovery_evidence_refs",
                    "must contain at most 32 items",
                )
        if "baseline_status" in turn:
            baseline_status = _string(turn["baseline_status"], f"{field}.baseline_status")
            if baseline_status not in {"missing", "ready", "degraded"}:
                _reject(f"{field}.baseline_status", "must be missing, ready, or degraded")
            if baseline_status == "degraded":
                if turn.get("provenance_incomplete") is not True:
                    _reject(
                        f"{field}.provenance_incomplete",
                        "must be true when baseline_status is degraded",
                    )
                if turn.get("provenance_status") != "incomplete":
                    _reject(
                        f"{field}.provenance_status",
                        "must be incomplete when baseline_status is degraded",
                    )
                if turn.get("provenance_status_reason") != "baseline_state_mismatch":
                    _reject(
                        f"{field}.provenance_status_reason",
                        "must identify a baseline_state_mismatch when degraded",
                    )
        if "finish_state" in turn:
            finish_state = _string(turn["finish_state"], f"{field}.finish_state")
            if finish_state != "finish_requested":
                _reject(f"{field}.finish_state", "must equal finish_requested")
        if "finish_requested_seq" in turn:
            _nonnegative_integer(turn["finish_requested_seq"], f"{field}.finish_requested_seq")
        if "invocations" in turn:
            _validate_invocations(turn["invocations"], f"{field}.invocations")
        _validate_design_state(turn, field)
    if "scorecard_cache" in ledger:
        _validate_scorecard_cache(ledger["scorecard_cache"])
    if "scorecard_journal_offset" in ledger:
        _nonnegative_integer(
            ledger["scorecard_journal_offset"],
            "ledger.scorecard_journal_offset",
        )
    if "scorecard_evicted_keys" in ledger:
        _validate_scorecard_evicted_keys(ledger["scorecard_evicted_keys"])
    return ledger


def _validate_closed_turns(value: JsonValue) -> None:
    if not isinstance(value, list):
        _reject("ledger.closed_turns", "must be a list")
    if len(value) > 256:
        _reject("ledger.closed_turns", "must contain at most 256 entries")
    for index, raw_entry in enumerate(value):
        field = f"ledger.closed_turns[{index}]"
        entry = _object(raw_entry, field)
        _string(_required(entry, "agent_key", field), f"{field}.agent_key")
        _string(_required(entry, "turn_id", field), f"{field}.turn_id")
        _nonnegative_integer(
            _required(entry, "closed_seq", field),
            f"{field}.closed_seq",
        )


def _validate_coordination_outbox(value: JsonValue) -> None:
    _validate_coordination_event_map(value, "ledger.coordination_outbox")


def _validate_coordination_delivered(value: JsonValue) -> None:
    _validate_coordination_event_map(value, "ledger.coordination_delivered")


def _validate_coordination_delivered_order(
    value: JsonValue,
    delivered_value: JsonValue | None,
) -> None:
    order = _string_list(value, "ledger.coordination_delivered_order")
    if len(order) > 256:
        _reject(
            "ledger.coordination_delivered_order",
            "must contain at most 256 entries",
        )
    if len(order) != len(set(order)):
        _reject(
            "ledger.coordination_delivered_order",
            "must not contain duplicate event IDs",
        )
    delivered = _object(delivered_value, "ledger.coordination_delivered")
    if set(order) != set(delivered):
        _reject(
            "ledger.coordination_delivered_order",
            "must contain each delivered event ID exactly once",
        )


def _validate_coordination_event_map(value: JsonValue, field: str) -> None:
    events = _object(value, field)
    if len(events) > 256:
        _reject(field, "must contain at most 256 entries")
    for event_id, raw_event in events.items():
        _validate_coordination_outbox_entry(
            event_id,
            raw_event,
            f"{field}.{event_id}",
            key_field=f"{field} key",
        )


def _validate_coordination_outbox_entry(
    event_id: str,
    raw_event: JsonValue,
    field: str,
    *,
    key_field: str = "ledger.coordination_outbox key",
) -> None:
    _string(event_id, key_field)
    event = _object(raw_event, field)
    if set(event) != _COORDINATION_FIELDS:
        _reject(field, "must contain the exact coordination event fields")
    coordination_version = _required(
        event,
        "scorecard_coord_schema_version",
        field,
    )
    if coordination_version != 1 or isinstance(coordination_version, bool):
        _reject(f"{field}.scorecard_coord_schema_version", "must equal 1")
    if _required(event, "event", field) != "coordination_transition":
        _reject(f"{field}.event", "must equal coordination_transition")
    if _string(_required(event, "event_id", field), f"{field}.event_id") != event_id:
        _reject(f"{field}.event_id", "must match the outbox key")
    actor = _object(_required(event, "actor", field), f"{field}.actor")
    if set(actor) != {"host", "session_id", "agent"}:
        _reject(f"{field}.actor", "must contain only host/session_id/agent")
    for name in ("host", "session_id", "agent"):
        _string(_required(actor, name, f"{field}.actor"), f"{field}.actor.{name}")
    _string(_required(event, "actor_turn_id", field), f"{field}.actor_turn_id")
    subject = _required(event, "subject_agent_key", field)
    if subject is not None:
        _string(subject, f"{field}.subject_agent_key")
    category = _string(_required(event, "category", field), f"{field}.category")
    if category not in _COORDINATION_CATEGORIES:
        _reject(f"{field}.category", "must be a known coordination category")
    outcome = _string(_required(event, "outcome", field), f"{field}.outcome")
    if outcome not in _COORDINATION_OUTCOMES:
        _reject(f"{field}.outcome", "must be a known coordination outcome")
    reason = _string(_required(event, "reason_code", field), f"{field}.reason_code")
    if reason not in _COORDINATION_REASONS:
        _reject(f"{field}.reason_code", "must be a known coordination reason")
    evidence = _string_list(
        _required(event, "evidence_refs", field),
        f"{field}.evidence_refs",
    )
    if len(evidence) > 32:
        _reject(f"{field}.evidence_refs", "must contain at most 32 items")
    attribution = _string(
        _required(event, "attribution", field),
        f"{field}.attribution",
    )
    if attribution not in {"exact", "legacy_default"}:
        _reject(f"{field}.attribution", "must be exact or legacy_default")
    occurred_at = _string(
        _required(event, "occurred_at", field),
        f"{field}.occurred_at",
    )
    try:
        observed = datetime.fromisoformat(occurred_at)
    except ValueError:
        _reject(f"{field}.occurred_at", "must be a UTC ISO-8601 string")
    if observed.tzinfo is None or observed.utcoffset() != UTC.utcoffset(observed):
        _reject(f"{field}.occurred_at", "must use UTC")
    if category == "r2_deny" and (
        outcome != "blocked"
        or reason
        not in {
            "attribution_degraded",
            "command_parse_unavailable",
            "peer_unsettled",
            "state_dir_protected",
            "unresolvable_target",
        }
    ):
        _reject(field, "must satisfy the r2_deny outcome/reason contract")
    if category == "turn_bootstrap" and (
        (outcome == "entered" and reason != "turn_not_started")
        or (outcome == "recovered" and reason != "complete")
        or outcome not in {"entered", "recovered"}
    ):
        _reject(field, "must satisfy the turn_bootstrap outcome/reason contract")


def validate_coordination_outbox_entry(
    event_id: str,
    raw_event: JsonValue,
) -> None:
    """Validate one prospective outbox entry before it mutates the ledger."""
    _validate_coordination_outbox_entry(
        event_id,
        raw_event,
        f"ledger.coordination_outbox.{event_id}",
    )


def sanitize_coordination_outbox(value: JsonValue) -> JsonObject:
    """Keep independently valid entries from an untrusted persisted outbox."""
    return _sanitize_coordination_event_map(value, "ledger.coordination_outbox")


def sanitize_coordination_delivered(value: JsonValue) -> JsonObject:
    return _sanitize_coordination_event_map(value, "ledger.coordination_delivered")


def _sanitize_coordination_event_map(value: JsonValue, field: str) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    sanitized: JsonObject = {}
    for event_id, raw_event in value.items():
        if len(sanitized) >= 256:
            break
        try:
            _validate_coordination_outbox_entry(
                event_id,
                raw_event,
                f"{field}.{event_id}",
                key_field=f"{field} key",
            )
        except LedgerSchemaError:
            continue
        sanitized[event_id] = raw_event
    return sanitized


def _validate_invocations(value: JsonValue, field: str) -> None:
    records = _object(value, field)
    for invocation_id, raw_entry in records.items():
        _string(invocation_id, f"{field} key")
        entry_field = f"{field}.{invocation_id}"
        entry = _object(raw_entry, entry_field)
        _string_list(
            _required(entry, "candidate_paths", entry_field),
            f"{entry_field}.candidate_paths",
        )
        status = _string(
            _required(entry, "status", entry_field),
            f"{entry_field}.status",
        )
        if status not in {"open", "closed"}:
            _reject(f"{entry_field}.status", "must be open or closed")
        if "started_seq" in entry:
            _nonnegative_integer(
                entry["started_seq"],
                f"{entry_field}.started_seq",
            )
        if "started_at" in entry:
            _string(entry["started_at"], f"{entry_field}.started_at")
        if "completed_seq" in entry:
            _nonnegative_integer(
                entry["completed_seq"],
                f"{entry_field}.completed_seq",
            )
        if "completed_at" in entry:
            _string(entry["completed_at"], f"{entry_field}.completed_at")


def _validate_path_attribution(ledger: JsonObject) -> None:
    for name in ("attribution_capacity_exceeded", "attribution_degraded"):
        if name in ledger:
            _boolean(ledger[name], f"ledger.{name}")
    if "path_attribution" not in ledger:
        return
    index = _object(ledger["path_attribution"], "ledger.path_attribution")
    if len(index) > 10_000:
        _reject("ledger.path_attribution", "must contain at most 10000 paths")
    for path, raw_entry in index.items():
        _string(path, "ledger.path_attribution key")
        field = f"ledger.path_attribution.{path}"
        entry = _object(raw_entry, field)
        _positive_integer(_required(entry, "generation", field), f"{field}.generation")
        status = _string(_required(entry, "status", field), f"{field}.status")
        if status not in {"exclusive", "contended"}:
            _reject(f"{field}.status", "must be exclusive or contended")
        owners = _required(entry, "owners", field)
        if not isinstance(owners, list) or not owners:
            _reject(f"{field}.owners", "must be a non-empty list")
        if len(owners) > 8:
            _reject(f"{field}.owners", "must contain at most 8 owners")
        agent_keys: list[str] = []
        for index_value, raw_owner in enumerate(owners):
            owner_field = f"{field}.owners[{index_value}]"
            owner = _object(raw_owner, owner_field)
            agent_keys.append(
                _string(
                    _required(owner, "agent_key", owner_field),
                    f"{owner_field}.agent_key",
                )
            )
            _string(_required(owner, "turn_id", owner_field), f"{owner_field}.turn_id")
            _positive_integer(
                _required(owner, "revision_seq", owner_field),
                f"{owner_field}.revision_seq",
            )
            _string(
                _required(owner, "after_digest", owner_field),
                f"{owner_field}.after_digest",
            )
            _string(
                _required(owner, "invocation_id", owner_field),
                f"{owner_field}.invocation_id",
            )
            _boolean(
                _required(owner, "settled", owner_field),
                f"{owner_field}.settled",
            )
        if len(agent_keys) != len(set(agent_keys)):
            _reject(f"{field}.owners", "must contain one latest revision per agent")
        if "overflow" in entry:
            overflow = _boolean(entry["overflow"], f"{field}.overflow")
            if overflow and status != "contended":
                _reject(f"{field}.status", "must be contended when overflow is true")


def _validate_manifest_transaction(ledger: JsonObject) -> None:
    if "manifest_snapshot_id" in ledger:
        _string(ledger["manifest_snapshot_id"], "ledger.manifest_snapshot_id")
    if "manifest_pending" in ledger:
        pending = _object(ledger["manifest_pending"], "ledger.manifest_pending")
        base = _nonnegative_integer(
            _required(pending, "base_generation", "ledger.manifest_pending"),
            "ledger.manifest_pending.base_generation",
        )
        target = _nonnegative_integer(
            _required(pending, "target_generation", "ledger.manifest_pending"),
            "ledger.manifest_pending.target_generation",
        )
        if target not in {base, base + 1}:
            _reject(
                "ledger.manifest_pending.target_generation",
                "must equal base_generation or base_generation + 1",
            )
        before = _string(
            _required(pending, "snapshot_before", "ledger.manifest_pending"),
            "ledger.manifest_pending.snapshot_before",
        )
        after = _string(
            _required(pending, "snapshot_after", "ledger.manifest_pending"),
            "ledger.manifest_pending.snapshot_after",
        )
        pending_events = _required(pending, "events", "ledger.manifest_pending")
        _validate_manifest_events(
            pending_events,
            "ledger.manifest_pending.events",
            target,
            "uncommitted",
        )
        baseline_agent = pending.get("baseline_agent")
        baseline_turn_id = pending.get("baseline_turn_id")
        if (baseline_agent is None) != (baseline_turn_id is None):
            _reject(
                "ledger.manifest_pending.baseline",
                "agent and turn_id must be present together",
            )
        if baseline_agent is not None and baseline_turn_id is not None:
            _string(baseline_agent, "ledger.manifest_pending.baseline_agent")
            _string(baseline_turn_id, "ledger.manifest_pending.baseline_turn_id")
        baseline_before = pending.get("baseline_snapshot_before")
        baseline_after = pending.get("baseline_snapshot_after")
        baseline_keys = pending.get("baseline_candidate_keys")
        new_baseline_fields = (baseline_before, baseline_after, baseline_keys)
        if any(item is not None for item in new_baseline_fields):
            if baseline_agent is None or baseline_turn_id is None or any(
                item is None for item in new_baseline_fields
            ):
                _reject(
                    "ledger.manifest_pending.baseline",
                    "candidate baseline fields must be present together",
                )
            _string(baseline_before, "ledger.manifest_pending.baseline_snapshot_before")
            _string(baseline_after, "ledger.manifest_pending.baseline_snapshot_after")
            _string_list(baseline_keys, "ledger.manifest_pending.baseline_candidate_keys")
        if target == base and (
            before != after
            or baseline_before is None
            or baseline_after is None
            or baseline_before == baseline_after
            or pending_events != []
        ):
            _reject(
                "ledger.manifest_pending.target_generation",
                "baseline-only transitions require equal manifest snapshots and baseline CAS fields",
            )
    if "manifest_event_journal" in ledger:
        _validate_manifest_events(
            ledger["manifest_event_journal"],
            "ledger.manifest_event_journal",
        )


def _validate_manifest_events(
    value: JsonValue,
    field: str,
    generation: int | None = None,
    commit_state: str | None = None,
) -> None:
    if not isinstance(value, list):
        _reject(field, "must be a list")
    for index, raw_event in enumerate(value):
        event_field = f"{field}[{index}]"
        event = _object(raw_event, event_field)
        _string(_required(event, "event_id", event_field), f"{event_field}.event_id")
        _positive_integer(_required(event, "seq", event_field), f"{event_field}.seq")
        event_generation = _positive_integer(
            _required(event, "manifest_generation", event_field),
            f"{event_field}.manifest_generation",
        )
        event_state = _string(
            _required(event, "commit_state", event_field),
            f"{event_field}.commit_state",
        )
        if event_state not in {"uncommitted", "committed"}:
            _reject(
                f"{event_field}.commit_state",
                "must be uncommitted or committed",
            )
        if generation is not None and event_generation != generation:
            _reject(
                f"{event_field}.manifest_generation",
                "must match the pending target generation",
            )
        if commit_state is not None and event_state != commit_state:
            _reject(
                f"{event_field}.commit_state",
                f"must equal {commit_state}",
            )


def _validate_scorecard_cache(value: JsonValue) -> None:
    cache = _object(value, "ledger.scorecard_cache")
    if len(cache) > 64:
        _reject("ledger.scorecard_cache", "must contain at most 64 sessions")
    for key, raw_entry in cache.items():
        _string(key, "ledger.scorecard_cache key")
        field = f"ledger.scorecard_cache.{key}"
        entry = _object(raw_entry, field)
        host = _string(_required(entry, "host", field), f"{field}.host")
        session_id = _string(
            _required(entry, "session_id", field), f"{field}.session_id"
        )
        agent = _string(_required(entry, "agent", field), f"{field}.agent")
        _string(_required(entry, "activated_at", field), f"{field}.activated_at")
        if key != f"{host}:{session_id}:{agent}":
            _reject(field, "key must match host:session_id:agent")
        latest_turn_id = _required(entry, "latest_turn_id", field)
        if not isinstance(latest_turn_id, str):
            _reject(f"{field}.latest_turn_id", "must be a string")
        for name in ("observed", "complete"):
            _boolean(_required(entry, name, field), f"{field}.{name}")
        for name in (
            "blocked_attempts",
            "recovered_scopes",
            "resolved_attempts",
            "cap_allows",
        ):
            _nonnegative_integer(_required(entry, name, field), f"{field}.{name}")
        _string_list(
            _required(entry, "unresolved_block_ids", field),
            f"{field}.unresolved_block_ids",
        )
        for name in ("first_occurred_at", "last_occurred_at"):
            timestamp = _required(entry, name, field)
            if timestamp is not None:
                _string(timestamp, f"{field}.{name}")
        if "seen_event_ids" in entry:
            _string_list(entry["seen_event_ids"], f"{field}.seen_event_ids")
        if "recovered_scope_keys" in entry:
            _string_list(entry["recovered_scope_keys"], f"{field}.recovered_scope_keys")
        if "unresolved_reasons" in entry:
            reasons = _object(entry["unresolved_reasons"], f"{field}.unresolved_reasons")
            for event_id, reason in reasons.items():
                _string(event_id, f"{field}.unresolved_reasons key")
                _string(reason, f"{field}.unresolved_reasons.{event_id}")
        if "by_reason" in entry:
            rows = _object(entry["by_reason"], f"{field}.by_reason")
            for reason, raw_row in rows.items():
                _string(reason, f"{field}.by_reason key")
                row = _object(raw_row, f"{field}.by_reason.{reason}")
                for name in (
                    "blocked_attempts",
                    "recovered_scopes",
                    "resolved_attempts",
                    "cap_allows",
                ):
                    _nonnegative_integer(
                        _required(row, name, f"{field}.by_reason.{reason}"),
                        f"{field}.by_reason.{reason}.{name}",
                    )


def _validate_scorecard_evicted_keys(value: JsonValue) -> None:
    field = "ledger.scorecard_evicted_keys"
    keys = _string_list(value, field)
    if len(keys) > 64:
        _reject(field, "must contain at most 64 sessions")
    if len(keys) != len(set(keys)):
        _reject(field, "must not contain duplicate sessions")


def serialize_v2_ledger(value: JsonValue) -> str:
    return json.dumps(validate_v2_ledger(value), ensure_ascii=False, indent=2, sort_keys=True)


def deserialize_v2_ledger(serialized: str) -> JsonObject:
    try:
        value: JsonValue = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise LedgerSchemaError(field="ledger", requirement="must be valid JSON") from exc
    return validate_v2_ledger(value)
