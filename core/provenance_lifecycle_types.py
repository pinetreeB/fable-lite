from __future__ import annotations

from dataclasses import dataclass

from .provenance_types import (
    ChangeOperation,
    NetDelta,
    ProvenanceReason,
    ProvenanceStatus,
    Snapshot,
)


@dataclass(frozen=True, slots=True)
class ObservedChange:
    change_id: str
    path: str
    canonical_key: str
    op: ChangeOperation
    before_digest: str | None
    after_digest: str | None
    source: str
    owner: str | None
    attribution_status: str
    observed_by: tuple[str, ...]
    manifest_generation: int = 0


@dataclass(frozen=True, slots=True)
class Invocation:
    invocation_id: str
    agent: str
    turn_id: str
    seq: int
    snapshot_id: str
    candidate_paths: frozenset[str]
    event_agent: str = ""
    host: str = "default"
    session_id: str = "default"
    observed_at: str = "post_tool"


@dataclass(frozen=True, slots=True)
class TurnState:
    agent: str
    turn_id: str
    baseline: Snapshot
    start_seq: int
    mutation_capable: bool


@dataclass(frozen=True, slots=True)
class ObservationInput:
    deltas: tuple[NetDelta, ...]
    agent: str
    source: str
    candidate_keys: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ObservationResult:
    snapshot: Snapshot | None
    changes: tuple[ObservedChange, ...]
    pending_change_ids: tuple[str, ...]
    incomplete: bool
    full_scan: bool
    rebase_count: int
    clean_claim: bool
    stop_cap_reserved: bool
    status: ProvenanceStatus = ProvenanceStatus.COMPLETE
    status_reason: ProvenanceReason = ProvenanceReason.NONE


class LifecycleState:
    """Holds one process's mutable generation, current manifest, turns, and audit dedupe state."""

    root: str
    current: Snapshot | None
    generation: int
    event_seq: int
    changes: dict[str, ObservedChange]
    turns: dict[tuple[str, str], TurnState]
    incomplete: bool
    incomplete_reason: ProvenanceReason
    current_is_stop_full: bool
    stop_cap_reservations: set[tuple[str, str]]

    def __init__(self, root: str, current: Snapshot | None = None) -> None:
        self.root = root
        self.current = current
        self.generation = 0
        self.event_seq = 0
        self.changes = {}
        self.turns = {}
        self.incomplete = current.incomplete if current is not None else False
        self.incomplete_reason = ProvenanceReason.NONE
        self.current_is_stop_full = current.full_reconciled_at is not None if current else False
        self.stop_cap_reservations = set()
