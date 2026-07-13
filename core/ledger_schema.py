from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import NoReturn, TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | Sequence["JsonValue"] | Mapping[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

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


def validate_v2_ledger(value: JsonValue) -> JsonObject:
    ledger = _object(value, "ledger")
    _v2_schema(ledger, "ledger")
    _validate_v1_projection(ledger)
    _nonnegative_integer(
        _required(ledger, "manifest_generation", "ledger"),
        "ledger.manifest_generation",
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
