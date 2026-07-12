from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .agent_log import ledger_transaction
from .provenance import (
    SnapshotScanOptions,
    calculate_net_delta,
    normalize_relative_path,
    snapshot_workspace_with_options,
    workspace_scope_policy_id,
)
from .provenance_lifecycle_types import (
    Invocation,
    LifecycleState,
    ObservationInput,
    ObservationResult,
    ObservedChange,
    TurnState,
)
from .provenance_observation import pending_change_ids, record_deltas
from .provenance_store import (
    SnapshotStoreError,
    delete_turn_baseline,
    load_workspace_current,
    save_turn_baseline,
    save_workspace_current,
    turn_baseline_path,
    workspace_current_path,
)
from .provenance_types import Snapshot


class ProvenanceLifecycle:
    """Coordinates in-process provenance observations while snapshots persist independently."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        try:
            current = load_workspace_current(self._root)
        except SnapshotStoreError:
            current = None
            self._state = LifecycleState(str(self._root))
            self._state.incomplete = True
        else:
            self._state = LifecycleState(str(self._root), current)

    @property
    def workspace_current_path(self) -> Path:
        return workspace_current_path(self._root)

    @property
    def changes(self) -> tuple[ObservedChange, ...]:
        return tuple(sorted(self._state.changes.values(), key=lambda change: change.change_id))

    @property
    def active_turns(self) -> tuple[TurnState, ...]:
        return tuple(sorted(self._state.turns.values(), key=lambda turn: (turn.agent, turn.turn_id)))

    def turn_baseline_path(self, agent: str, turn_id: str) -> Path:
        return turn_baseline_path(self._root, agent, turn_id)

    def start_turn(
        self,
        agent: str,
        turn_id: str,
        mutation_capable: bool = False,
    ) -> ObservationResult:
        result = self._observe("", "external", frozenset(), not self._can_fast_start())
        if result.incomplete or result.snapshot is None:
            return result
        turn = TurnState(agent, turn_id, result.snapshot, self._state.event_seq, mutation_capable)
        try:
            save_turn_baseline(self._root, agent, turn_id, turn.baseline)
        except SnapshotStoreError:
            return self._mark_incomplete(result)
        self._state.turns[(agent, turn_id)] = turn
        return self._turn_result(result, turn, False)

    def begin_invocation(
        self,
        agent: str,
        turn_id: str,
        invocation_id: str,
        candidate_paths: tuple[str, ...],
    ) -> Invocation:
        self._turn(agent, turn_id)
        snapshot_id = self._state.current.snapshot_id if self._state.current is not None else ""
        return Invocation(
            invocation_id,
            agent,
            turn_id,
            self._state.event_seq,
            snapshot_id,
            self._candidate_paths(candidate_paths),
        )

    def post_tool(self, invocation: Invocation, source: str = "external") -> ObservationResult:
        turn = self._turn(invocation.agent, invocation.turn_id)
        result = self._observe(invocation.agent, source, invocation.candidate_paths, False)
        if not result.incomplete and self._state.current is not None:
            record_deltas(
                self._state,
                ObservationInput(
                    calculate_net_delta(turn.baseline, self._state.current),
                    invocation.agent,
                    source,
                ),
            )
        return self._turn_result(result, turn, False)

    def finish_turn(self, agent: str, turn_id: str) -> ObservationResult:
        turn = self._turn(agent, turn_id)
        result = self._observe(agent, "external", frozenset(), True)
        if not result.incomplete and self._state.current is not None:
            finalized = replace(
                self._state.current,
                full_reconciled_at=datetime.now(UTC).isoformat(),
            )
            try:
                save_workspace_current(self._root, finalized)
            except SnapshotStoreError:
                result = self._mark_incomplete(result)
            else:
                self._state.current = finalized
                self._state.current_is_stop_full = True
                result = replace(result, snapshot=finalized)
        if not result.incomplete and self._state.current is not None:
            record_deltas(
                self._state,
                ObservationInput(
                    calculate_net_delta(turn.baseline, self._state.current),
                    agent,
                    "external",
                ),
            )
        reserved = result.incomplete and turn.mutation_capable
        if reserved:
            self._state.stop_cap_reservations.add((agent, turn_id))
        try:
            delete_turn_baseline(self._root, agent, turn_id)
        except SnapshotStoreError:
            result = self._mark_incomplete(result)
            reserved = turn.mutation_capable
        self._state.turns.pop((agent, turn_id), None)
        return self._turn_result(result, turn, reserved)

    def _scan(
        self,
        previous: Snapshot | None,
        forced_paths: frozenset[str],
        full_scan: bool,
    ) -> Snapshot:
        options = SnapshotScanOptions(
            previous=None if full_scan else previous,
            force_paths=forced_paths,
        )
        return snapshot_workspace_with_options(self._root, options)

    def _observe(
        self,
        agent: str,
        source: str,
        forced_paths: frozenset[str],
        full_scan: bool,
    ) -> ObservationResult:
        generation, previous = self._generation_current()
        first = self._scan(previous, forced_paths, full_scan)
        if first.incomplete:
            return self._mark_incomplete(self._result(first, (), full_scan, 0))
        committed = self._commit_if_current(generation, first, agent, source, full_scan)
        if committed is not None:
            return committed
        rebase_generation, rebase_previous = self._generation_current()
        rebased = self._scan(rebase_previous, forced_paths, full_scan)
        if rebased.incomplete:
            return self._mark_incomplete(self._result(rebased, (), full_scan, 1))
        committed = self._commit_if_current(rebase_generation, rebased, agent, source, full_scan)
        if committed is not None:
            return replace(committed, rebase_count=1)
        return self._mark_incomplete(self._result(None, (), full_scan, 1))

    def _generation_current(self) -> tuple[int, Snapshot | None]:
        with ledger_transaction(str(self._root)):
            return self._state.generation, self._state.current

    def _commit_if_current(
        self,
        generation: int,
        snapshot: Snapshot,
        agent: str,
        source: str,
        full_scan: bool,
    ) -> ObservationResult | None:
        with ledger_transaction(str(self._root)):
            if self._state.generation != generation:
                return None
            before = self._state.current
            deltas = () if before is None else calculate_net_delta(before, snapshot)
            changes = record_deltas(self._state, ObservationInput(deltas, agent, source))
            reconciled_at = before.full_reconciled_at if before is not None and before.snapshot_id == snapshot.snapshot_id else None
            snapshot = replace(snapshot, full_reconciled_at=reconciled_at)
            try:
                save_workspace_current(self._root, snapshot)
            except SnapshotStoreError:
                return self._mark_incomplete(self._result(snapshot, changes, full_scan, 0))
            self._state.current = snapshot
            self._state.generation += 1
            self._state.incomplete = False
            self._state.current_is_stop_full = reconciled_at is not None
            return self._result(snapshot, changes, full_scan, 0)

    def _turn_result(
        self,
        result: ObservationResult,
        turn: TurnState,
        stop_cap_reserved: bool,
    ) -> ObservationResult:
        pending = pending_change_ids(self._state, turn)
        return replace(
            result,
            pending_change_ids=pending,
            clean_claim=not result.incomplete and not pending,
            stop_cap_reserved=stop_cap_reserved,
        )

    def _can_fast_start(self) -> bool:
        current = self._state.current
        return (
            current is not None
            and self._state.current_is_stop_full
            and not self._state.incomplete
            and not current.incomplete
            and current.scope_policy_id == workspace_scope_policy_id(self._root)
        )

    def _candidate_paths(self, candidates: tuple[str, ...]) -> frozenset[str]:
        normalized: set[str] = set()
        for candidate in candidates:
            path = Path(candidate)
            absolute = path if path.is_absolute() else self._root / path
            try:
                normalized.add(normalize_relative_path(self._root, absolute))
            except ValueError:
                continue
        return frozenset(normalized)

    def _turn(self, agent: str, turn_id: str) -> TurnState:
        return self._state.turns[(agent, turn_id)]

    def _mark_incomplete(self, result: ObservationResult) -> ObservationResult:
        self._state.incomplete = True
        return replace(result, incomplete=True, clean_claim=False)

    @staticmethod
    def _result(
        snapshot: Snapshot | None,
        changes: tuple[ObservedChange, ...],
        full_scan: bool,
        rebase_count: int,
    ) -> ObservationResult:
        return ObservationResult(
            snapshot,
            changes,
            (),
            snapshot.incomplete if snapshot is not None else True,
            full_scan,
            rebase_count,
            False,
            False,
        )
