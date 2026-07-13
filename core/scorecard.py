"""Pure Session Quality Scorecard domain model."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum, unique

from .ledger_schema import JsonValue


SCORECARD_SCHEMA_VERSION = 1


@unique
class Host(StrEnum):
    CLAUDE_CODE = "claude_code"
    CODEX_CLI = "codex_cli"
    ANTIGRAVITY = "antigravity"


@unique
class ReasonCode(StrEnum):
    STOP_PROVENANCE_INCOMPLETE = "stop.provenance_incomplete"
    STOP_INVESTIGATION_MARKERS = "stop.investigation_markers"
    STOP_VERIFICATION_MISSING = "stop.verification_missing"
    PRETOOL_GOALS_MISSING = "pretool.goals_missing"
    PRETOOL_INTENT_MISSING = "pretool.intent_missing"
    PRETOOL_CONTRACT_MISSING = "pretool.contract_missing"


@unique
class GateAction(StrEnum):
    BLOCK = "block"
    RECOVER = "recover"
    CAP_ALLOW = "cap_allow"


@unique
class Resolution(StrEnum):
    VERIFICATION = "verification"
    OBSERVATION = "observation"
    MARKERS = "markers"
    GOALS_CHECKPOINT = "goals_checkpoint"
    INTENT_CHECKPOINT = "intent_checkpoint"
    CONTRACT = "contract"
    NONE = "none"


@unique
class Attribution(StrEnum):
    EXACT = "exact"
    LEGACY_DEFAULT = "legacy_default"


@dataclass(frozen=True, slots=True)
class ScorecardSchemaError(ValueError):
    field: str
    requirement: str

    def __str__(self) -> str:
        return f"invalid scorecard schema at {self.field}: {self.requirement}"


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    host: str
    session_id: str
    agent: str

    @property
    def agent_key(self) -> str:
        return f"{self.host}:{self.session_id}:{self.agent}"


@dataclass(frozen=True, slots=True)
class GateTransition:
    event_id: str
    identity: SessionIdentity
    turn_id: str
    reason_code: ReasonCode
    action: GateAction
    resolves: tuple[str, ...]
    resolution: Resolution
    attribution: Attribution
    occurred_at: datetime

    @property
    def recovery_scope(self) -> tuple[str, str, str, str, str]:
        return (
            self.identity.host,
            self.identity.session_id,
            self.identity.agent,
            self.turn_id,
            self.reason_code.value,
        )


@dataclass(frozen=True, slots=True)
class ReasonAggregate:
    reason_code: ReasonCode
    blocked_attempts: int
    recovered_scopes: int
    resolved_attempts: int
    cap_allows: int


@dataclass(frozen=True, slots=True)
class ScorecardAggregate:
    identity: SessionIdentity
    activated_at: datetime
    observed: bool
    complete: bool
    blocked_attempts: int
    recovered_scopes: int
    resolved_attempts: int
    cap_allows: int
    unresolved_block_ids: tuple[str, ...]
    latest_turn_id: str
    first_occurred_at: datetime | None
    last_occurred_at: datetime | None
    by_reason: tuple[ReasonAggregate, ...]

    @property
    def has_activity(self) -> bool:
        return bool(self.blocked_attempts or self.recovered_scopes or self.cap_allows)


def render_stop_line(aggregate: ScorecardAggregate) -> str | None:
    if not aggregate.complete or not aggregate.has_activity:
        return None
    return (
        f"[smtw] 이번 세션 · 차단 시도 {aggregate.blocked_attempts} · "
        f"회복 턴 {aggregate.recovered_scopes} · cap 통과 {aggregate.cap_allows}"
    )


def parse_transition(raw: Mapping[str, JsonValue]) -> GateTransition:
    version = raw.get("scorecard_schema_version")
    if version != SCORECARD_SCHEMA_VERSION or isinstance(version, bool):
        raise ScorecardSchemaError("scorecard_schema_version", "must equal 1")
    if raw.get("event") != "gate_transition":
        raise ScorecardSchemaError("event", "must equal gate_transition")
    action = _parse_enum(GateAction, raw.get("action"), "action")
    resolution = _parse_enum(Resolution, raw.get("resolution"), "resolution")
    resolves = _string_tuple(raw.get("resolves"), "resolves")
    _validate_action(action, resolution, resolves)
    return GateTransition(
        event_id=_required_string(raw, "event_id"),
        identity=SessionIdentity(
            _parse_enum(Host, raw.get("host"), "host").value,
            _required_string(raw, "session_id"),
            _required_string(raw, "agent"),
        ),
        turn_id=_required_string(raw, "turn_id"),
        reason_code=_parse_enum(ReasonCode, raw.get("reason_code"), "reason_code"),
        action=action,
        resolves=resolves,
        resolution=resolution,
        attribution=_parse_enum(
            Attribution, raw.get("attribution"), "attribution"
        ),
        occurred_at=_utc_datetime(raw.get("occurred_at"), "occurred_at"),
    )


def aggregate_transitions(
    transitions: Iterable[GateTransition], *, complete: bool = True
) -> ScorecardAggregate:
    unique: dict[str, GateTransition] = {}
    for transition in transitions:
        existing = unique.get(transition.event_id)
        if existing is not None and existing != transition:
            raise ScorecardSchemaError(
                "transitions.event_id", "conflicting duplicate event"
            )
        unique[transition.event_id] = transition
    ordered = tuple(sorted(unique.values(), key=lambda item: item.occurred_at))
    if not ordered:
        raise ScorecardSchemaError("transitions", "must not be empty")
    identity = ordered[0].identity
    if any(item.identity != identity for item in ordered):
        raise ScorecardSchemaError("transitions.identity", "must be one session")
    blocks, recovered_ids, recovery_scopes = _resolved_blocks(ordered)
    reason_rows = tuple(
        _aggregate_reason(
            reason_code, ordered, blocks, recovered_ids, recovery_scopes
        )
        for reason_code in ReasonCode
        if any(item.reason_code is reason_code for item in ordered)
    )
    return ScorecardAggregate(
        identity=identity,
        activated_at=ordered[0].occurred_at,
        observed=True,
        complete=complete,
        blocked_attempts=len(blocks),
        recovered_scopes=len(recovery_scopes),
        resolved_attempts=len(recovered_ids),
        cap_allows=sum(item.action is GateAction.CAP_ALLOW for item in ordered),
        unresolved_block_ids=tuple(sorted(set(blocks) - recovered_ids)),
        latest_turn_id=ordered[-1].turn_id,
        first_occurred_at=ordered[0].occurred_at,
        last_occurred_at=ordered[-1].occurred_at,
        by_reason=reason_rows,
    )


def empty_aggregate(
    identity: SessionIdentity,
    *,
    activated_at: datetime,
    session_started_at: datetime,
    complete: bool = True,
) -> ScorecardAggregate:
    return ScorecardAggregate(
        identity=identity,
        activated_at=activated_at,
        observed=session_started_at >= activated_at,
        complete=complete,
        blocked_attempts=0,
        recovered_scopes=0,
        resolved_attempts=0,
        cap_allows=0,
        unresolved_block_ids=(),
        latest_turn_id="",
        first_occurred_at=None,
        last_occurred_at=None,
        by_reason=(),
    )


def _aggregate_reason(
    reason_code: ReasonCode,
    transitions: Sequence[GateTransition],
    blocks: Mapping[str, GateTransition],
    recovered_ids: set[str],
    recovery_scopes: set[tuple[str, str, str, str, str]],
) -> ReasonAggregate:
    reason_blocks = {
        event_id for event_id, item in blocks.items() if item.reason_code is reason_code
    }
    scopes = {
        scope for scope in recovery_scopes if scope[-1] == reason_code.value
    }
    return ReasonAggregate(
        reason_code,
        len(reason_blocks),
        len(scopes),
        len(reason_blocks & recovered_ids),
        sum(
            item.action is GateAction.CAP_ALLOW and item.reason_code is reason_code
            for item in transitions
        ),
    )


def _resolved_blocks(
    transitions: Sequence[GateTransition],
) -> tuple[
    dict[str, GateTransition],
    set[str],
    set[tuple[str, str, str, str, str]],
]:
    blocks: dict[str, GateTransition] = {}
    unresolved: dict[str, GateTransition] = {}
    recovered_ids: set[str] = set()
    recovery_scopes: set[tuple[str, str, str, str, str]] = set()
    for transition in transitions:
        if transition.action is GateAction.BLOCK:
            blocks[transition.event_id] = transition
            unresolved[transition.event_id] = transition
            continue
        if transition.action is not GateAction.RECOVER:
            continue
        resolved_now = {
            block_id
            for block_id in transition.resolves
            if (block := unresolved.get(block_id)) is not None
            and block.reason_code is transition.reason_code
        }
        if not resolved_now:
            continue
        recovered_ids.update(resolved_now)
        recovery_scopes.add(transition.recovery_scope)
        for block_id in resolved_now:
            _ = unresolved.pop(block_id, None)
    return blocks, recovered_ids, recovery_scopes


def _required_string(raw: Mapping[str, JsonValue], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value:
        raise ScorecardSchemaError(field, "must be a non-empty string")
    return value


def _string_tuple(value: JsonValue | None, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ScorecardSchemaError(field, "must be a list of non-empty strings")
    return tuple(item for item in value if isinstance(item, str))


def _parse_enum[EnumT: StrEnum](
    enum_type: type[EnumT], value: JsonValue | None, field: str
) -> EnumT:
    if not isinstance(value, str):
        raise ScorecardSchemaError(field, "must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ScorecardSchemaError(field, "contains an unknown value") from exc


def _utc_datetime(value: JsonValue | None, field: str) -> datetime:
    if not isinstance(value, str):
        raise ScorecardSchemaError(field, "must be a UTC ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ScorecardSchemaError(field, "must be a UTC ISO-8601 string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ScorecardSchemaError(field, "must use UTC")
    return parsed.astimezone(UTC)


def _validate_action(
    action: GateAction, resolution: Resolution, resolves: tuple[str, ...]
) -> None:
    if action is GateAction.BLOCK and (
        resolves or resolution is not Resolution.NONE
    ):
        raise ScorecardSchemaError("action", "block cannot resolve events")
    if action is GateAction.RECOVER and (
        not resolves or resolution is Resolution.NONE
    ):
        raise ScorecardSchemaError(
            "action", "recover requires resolves and a concrete resolution"
        )
    if action is GateAction.CAP_ALLOW and resolution is not Resolution.NONE:
        raise ScorecardSchemaError("resolution", "cap_allow must use none")
    if action is GateAction.CAP_ALLOW and not resolves:
        raise ScorecardSchemaError("resolves", "cap_allow requires blocked attempts")
