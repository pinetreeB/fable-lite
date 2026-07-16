"""# noqa: SIZE_OK — W3 must keep lifecycle coordination in the card's named production modules."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from .adapter_change_events import build_observed_change_events
from .ledger import load_ledger
from .ledger_schema import JsonObject, JsonValue
from .ledger_v2 import attribution_health, open_peer_invocation_candidates
from .provenance import (
    ChangeOperation,
    NetDelta,
    calculate_net_delta,
)
from .provenance_policy import (
    canonical_manifest_key,
    is_path_in_scope,
    is_user_config_excluded,
    load_provenance_config,
)
from .provenance_lifecycle_types import (
    Invocation,
    LifecycleState,
    ObservationInput,
    ObservationResult,
    ObservedChange,
    TurnState,
)
from .provenance_observation import change_id_for, pending_change_ids, record_deltas
from .provenance_manifest import (
    ManifestStoreError,
    commit_manifest,
    load_manifest_view,
    save_manifest_baseline,
)
from .provenance_store import (
    SnapshotStoreError,
    delete_turn_baseline,
    load_workspace_current,
    save_turn_baseline,
    turn_baseline_path,
    workspace_current_path,
)
from .provenance_turn_resume import (
    MissingTurnBaselineError,
    TurnBootstrapError,
    load_resumed_turn,
)
from .provenance_lifecycle_start import (
    can_fast_start,
    candidate_paths as candidate_paths_for_root,
    trusted_default_policy_migration,
)
from .provenance_lifecycle_scope import prime_candidate_scope, scan_snapshot
from .provenance_snapshot import snapshot_id_for
from .project_root import is_user_home_root
from .provenance_types import (
    ProvenanceReason,
    ProvenanceStatus,
    Snapshot,
    SnapshotExclusion,
)


def adjust_snapshot_for_peer_activity(
    root: Path,
    snapshot: Snapshot,
    previous: Snapshot | None,
    caller_agent_key: str,
    observer_turn_id: str,
    *,
    now: datetime | None = None,
) -> Snapshot:
    if not snapshot.incomplete or not snapshot.issues:
        return snapshot
    try:
        ledger = load_ledger({"project_root": str(root)})
    except (OSError, ValueError):
        return snapshot
    health = attribution_health(ledger)
    if health.get("degraded") is True or health.get("capacity_exceeded") is True:
        return snapshot
    peer_candidates = open_peer_invocation_candidates(
        ledger,
        caller_agent_key,
        now=now,
    )
    evidence: list[SnapshotExclusion] = []
    for issue in snapshot.issues:
        key = canonical_manifest_key(issue.path, snapshot.is_casefolded)
        match = peer_candidates.get(key)
        if issue.reason not in {"unstable_path", "unreadable_path"} or match is None:
            return snapshot
        evidence.append(
            SnapshotExclusion(
                path=issue.path,
                reason=issue.reason,
                peer_agent_key=_json_string(match.get("peer_agent_key")),
                peer_turn_id=_json_string(match.get("peer_turn_id")),
                invocation_id=_json_string(match.get("invocation_id")),
                started_seq=_json_integer(match.get("started_seq")),
                started_at=_json_string(match.get("started_at")),
                observer_turn_id=observer_turn_id,
            )
        )
    excluded_keys = {
        canonical_manifest_key(item.path, snapshot.is_casefolded) for item in evidence
    }
    entries = {
        entry.canonical_key: entry
        for entry in snapshot.entries
        if entry.canonical_key not in excluded_keys
    }
    if previous is not None:
        for entry in previous.entries:
            if entry.canonical_key in excluded_keys:
                entries[entry.canonical_key] = entry
    ordered_entries = tuple(sorted(entries.values(), key=lambda item: (item.canonical_key, item.path)))
    exclusions = tuple(sorted(evidence, key=lambda item: item.path))
    return replace(
        snapshot,
        entries=ordered_entries,
        issues=(),
        exclusions=exclusions,
        snapshot_id=snapshot_id_for(ordered_entries, exclusions),
        status=ProvenanceStatus.COMPLETE_WITH_EXCLUSIONS,
        status_reason=ProvenanceReason.PEER_ACTIVITY,
    )


def _json_string(value: JsonValue | None) -> str:
    return value if isinstance(value, str) else ""


def _json_integer(value: JsonValue | None) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _complete_observation(status: ProvenanceStatus) -> bool:
    return status in {
        ProvenanceStatus.COMPLETE,
        ProvenanceStatus.COMPLETE_WITH_EXCLUSIONS,
    }


class ProvenanceLifecycle:
    """Coordinates in-process provenance observations while snapshots persist independently."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._home_root_unsupported = is_user_home_root(self._root)
        if self._home_root_unsupported:
            self._state = LifecycleState(str(self._root))
            return
        try:
            _ = load_workspace_current(self._root)
            view = load_manifest_view(self._root)
        except (ManifestStoreError, SnapshotStoreError):
            self._state = LifecycleState(str(self._root))
            self._state.incomplete = True
            self._state.incomplete_reason = ProvenanceReason.STORE_READ_ERROR
        else:
            self._state = LifecycleState(str(self._root), view.snapshot)
            self._state.generation = view.generation

    @property
    def workspace_current_path(self) -> Path:
        return workspace_current_path(self._root)

    @property
    def observed_file_count(self) -> int:
        return len(self._state.current.entries) if self._state.current is not None else 0

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
        *,
        event_agent: str = "",
        host: str = "default",
        session_id: str = "default",
        invocation_id: str = "",
        observed_at: str = "turn_start",
    ) -> ObservationResult:
        if self._home_root_unsupported:
            return ObservationResult(
                None,
                (),
                (),
                False,
                False,
                0,
                False,
                False,
                ProvenanceStatus.UNSUPPORTED,
                ProvenanceReason.HOME_ROOT,
            )
        if self._state.incomplete_reason is ProvenanceReason.STORE_READ_ERROR:
            return self._mark_incomplete(
                self._result(None, (), True, 0),
                ProvenanceReason.STORE_READ_ERROR,
            )
        snapshot_id = self._state.current.snapshot_id if self._state.current else ""
        context = Invocation(
            invocation_id or f"{turn_id}:start",
            agent,
            turn_id,
            self._state.event_seq,
            snapshot_id,
            frozenset(),
            event_agent or agent,
            host,
            session_id,
            observed_at,
        )
        result = self._observe(
            agent,
            "external",
            frozenset(),
            not can_fast_start(
                self._state.current,
                self._state.current_is_stop_full,
                self._state.incomplete,
                self._root,
            ),
            context,
        )
        if not _complete_observation(result.status) or result.snapshot is None:
            return result
        turn = TurnState(agent, turn_id, result.snapshot, self._state.event_seq, mutation_capable)
        try:
            save_manifest_baseline(
                self._root,
                self._state.generation,
                turn.baseline,
                agent,
                turn_id,
            )
        except (ManifestStoreError, SnapshotStoreError):
            return self._mark_incomplete(result, ProvenanceReason.STORE_WRITE_ERROR)
        self._state.turns[(agent, turn_id)] = turn
        return self._turn_result(result, turn, False)

    def begin_invocation(
        self,
        agent: str,
        turn_id: str,
        invocation_id: str,
        candidate_paths: tuple[str, ...],
        prime_candidates: bool = True,
        *,
        event_agent: str = "",
        host: str = "default",
        session_id: str = "default",
        observed_at: str = "post_tool",
    ) -> Invocation:
        candidates = candidate_paths_for_root(self._root, candidate_paths)
        if prime_candidates and not prime_candidate_scope(self._root, self._state, agent, turn_id, candidates):
            self._state.incomplete = True
        snapshot_id = self._state.current.snapshot_id if self._state.current is not None else ""
        return Invocation(
            invocation_id,
            agent,
            turn_id,
            self._state.event_seq,
            snapshot_id,
            candidates,
            event_agent or agent,
            host,
            session_id,
            observed_at,
        )

    def resume_turn(
        self,
        agent: str,
        turn_id: str,
        mutation_capable: bool = False,
        *,
        allow_full_bootstrap: bool = False,
    ) -> None:
        if (agent, turn_id) in self._state.turns:
            return
        try:
            turn = load_resumed_turn(
                self._root, agent, turn_id, self._state.event_seq, mutation_capable
            )
        except MissingTurnBaselineError:
            if not allow_full_bootstrap:
                raise
            current = self._state.current
            if current is None:
                result = self.start_turn(agent, turn_id, mutation_capable)
                if (
                    result.status is ProvenanceStatus.COMPLETE
                    and not result.incomplete
                ):
                    return
                raise TurnBootstrapError(
                    result.status,
                    result.status_reason,
                    result.incomplete,
                )
            _ = save_turn_baseline(self._root, agent, turn_id, current)
            turn = TurnState(
                agent,
                turn_id,
                current,
                self._state.event_seq,
                mutation_capable,
            )
        self._state.turns[(agent, turn_id)] = turn

    def post_tool(self, invocation: Invocation, source: str = "external") -> ObservationResult:
        turn = self._state.turns[(invocation.agent, invocation.turn_id)]
        result = self._observe(
            invocation.agent,
            source,
            invocation.candidate_paths,
            False,
            invocation,
        )
        if result.status is ProvenanceStatus.COMPLETE and self._state.current is not None:
            record_deltas(
                self._state,
                ObservationInput(
                    self._observable_deltas(turn.baseline, self._state.current),
                    invocation.agent,
                    source,
                    self._candidate_keys(
                        invocation.candidate_paths,
                        self._state.current,
                    ),
                ),
            )
        return self._turn_result(result, turn, False)

    def reconcile_turn(
        self,
        agent: str,
        turn_id: str,
        *,
        event_agent: str = "",
        host: str = "default",
        session_id: str = "default",
        invocation_id: str = "",
        observed_at: str = "stop",
    ) -> ObservationResult:
        turn = self._state.turns[(agent, turn_id)]
        snapshot_id = self._state.current.snapshot_id if self._state.current else ""
        context = Invocation(
            invocation_id or f"{turn_id}:reconcile",
            agent,
            turn_id,
            self._state.event_seq,
            snapshot_id,
            frozenset(),
            event_agent or agent,
            host,
            session_id,
            observed_at,
        )
        result = self._observe(
            agent,
            "external",
            frozenset(),
            True,
            context,
            True,
        )
        if not result.incomplete and self._state.current is not None:
            record_deltas(
                self._state,
                ObservationInput(
                    self._observable_deltas(turn.baseline, self._state.current),
                    agent,
                    "external",
                ),
            )
        return self._turn_result(result, turn, False)

    def finish_turn(
        self,
        agent: str,
        turn_id: str,
        *,
        event_agent: str = "",
        host: str = "default",
        session_id: str = "default",
        invocation_id: str = "",
        observed_at: str = "stop",
    ) -> ObservationResult:
        turn = self._state.turns[(agent, turn_id)]
        result = self.reconcile_turn(
            agent,
            turn_id,
            event_agent=event_agent,
            host=host,
            session_id=session_id,
            invocation_id=invocation_id,
            observed_at=observed_at,
        )
        reserved = result.incomplete and turn.mutation_capable
        if reserved:
            self._state.stop_cap_reservations.add((agent, turn_id))
        try:
            delete_turn_baseline(self._root, agent, turn_id)
        except SnapshotStoreError:
            result = self._mark_incomplete(result, ProvenanceReason.STORE_WRITE_ERROR)
            reserved = turn.mutation_capable
        self._state.turns.pop((agent, turn_id), None)
        return replace(result, stop_cap_reserved=reserved)

    def _scan(
        self,
        previous: Snapshot | None,
        forced_paths: frozenset[str],
        full_scan: bool,
    ) -> Snapshot:
        carried = (
            frozenset(item.path for item in previous.exclusions)
            if previous is not None
            else frozenset()
        )
        return scan_snapshot(self._root, previous, forced_paths | carried, full_scan)

    def _observe(
        self,
        agent: str,
        source: str,
        forced_paths: frozenset[str],
        full_scan: bool,
        invocation: Invocation | None = None,
        mark_full_reconcile: bool = False,
    ) -> ObservationResult:
        generation, previous = self._generation_current()
        first = self._scan(previous, forced_paths, full_scan)
        first = adjust_snapshot_for_peer_activity(
            self._root,
            first,
            previous,
            agent,
            invocation.turn_id if invocation is not None else "",
        )
        if first.status is ProvenanceStatus.SCOPE_TOO_LARGE:
            return self._mark_scope_too_large(self._result(first, (), full_scan, 0))
        if first.incomplete:
            reason = (
                first.status_reason
                if first.status_reason is not ProvenanceReason.NONE
                else ProvenanceReason.OBSERVATION_ERROR
            )
            return self._mark_incomplete(
                self._result(first, (), full_scan, 0),
                reason,
            )
        committed = self._commit_if_current(
            generation,
            first,
            agent,
            source,
            full_scan,
            invocation,
            mark_full_reconcile,
        )
        if committed is not None:
            return committed
        rebase_generation, rebase_previous = self._generation_current()
        rebased = self._scan(rebase_previous, forced_paths, full_scan)
        rebased = adjust_snapshot_for_peer_activity(
            self._root,
            rebased,
            rebase_previous,
            agent,
            invocation.turn_id if invocation is not None else "",
        )
        if rebased.status is ProvenanceStatus.SCOPE_TOO_LARGE:
            return self._mark_scope_too_large(self._result(rebased, (), full_scan, 1))
        if rebased.incomplete:
            return self._mark_incomplete(self._result(rebased, (), full_scan, 1))
        committed = self._commit_if_current(
            rebase_generation,
            rebased,
            agent,
            source,
            full_scan,
            invocation,
            mark_full_reconcile,
        )
        if committed is not None:
            return replace(committed, rebase_count=1)
        return self._mark_incomplete(
            self._result(None, (), full_scan, 1),
            ProvenanceReason.OBSERVATION_ERROR,
        )

    def _generation_current(self) -> tuple[int, Snapshot | None]:
        view = load_manifest_view(self._root)
        self._state.generation = view.generation
        self._state.current = view.snapshot
        self._state.current_is_stop_full = (
            view.snapshot.full_reconciled_at is not None
            if view.snapshot is not None
            else False
        )
        return view.generation, view.snapshot

    def _commit_if_current(
        self,
        generation: int,
        snapshot: Snapshot,
        agent: str,
        source: str,
        full_scan: bool,
        invocation: Invocation | None = None,
        mark_full_reconcile: bool = False,
    ) -> ObservationResult | None:
        if self._state.generation != generation:
            return None
        before = self._state.current
        same_content = (
            before is not None
            and before.snapshot_id == snapshot.snapshot_id
            and before.scope_policy_id == snapshot.scope_policy_id
        )
        if mark_full_reconcile:
            snapshot = replace(
                snapshot,
                full_reconciled_at=datetime.now(UTC).isoformat(),
            )
        elif same_content and before is not None:
            snapshot = before
        else:
            snapshot = replace(snapshot, full_reconciled_at=None)
        deltas = (
            ()
            if before is None or same_content
            else self._observable_deltas(before, snapshot)
        )
        candidate_keys: frozenset[str] = (
            self._candidate_keys(invocation.candidate_paths, snapshot)
            if invocation is not None
            else frozenset()
        )
        previous_changes = dict(self._state.changes)
        previous_event_seq = self._state.event_seq
        changes = record_deltas(
            self._state,
            ObservationInput(deltas, agent, source, candidate_keys),
        )
        templates = self._change_event_templates(
            invocation,
            changes,
            before,
            snapshot,
        )
        try:
            committed = commit_manifest(
                self._root,
                generation,
                self._snapshot_id(before),
                snapshot,
                templates,
            )
        except (ManifestStoreError, SnapshotStoreError):
            self._state.changes = previous_changes
            self._state.event_seq = previous_event_seq
            return self._mark_incomplete(
                self._result(snapshot, changes, full_scan, 0),
                ProvenanceReason.STORE_WRITE_ERROR,
            )
        if committed is None:
            self._state.changes = previous_changes
            self._state.event_seq = previous_event_seq
            return None
        changes = tuple(
            replace(change, manifest_generation=committed.generation)
            for change in changes
        )
        for change in changes:
            self._state.changes[change.change_id] = change
        self._state.current = committed.snapshot
        self._state.generation = committed.generation
        self._state.incomplete = False
        self._state.incomplete_reason = ProvenanceReason.NONE
        self._state.current_is_stop_full = (
            committed.snapshot.full_reconciled_at is not None
        )
        return self._result(committed.snapshot, changes, full_scan, 0)

    def _change_event_templates(
        self,
        invocation: Invocation | None,
        changes: tuple[ObservedChange, ...],
        before: Snapshot | None,
        snapshot: Snapshot,
    ) -> tuple[JsonObject, ...]:
        if invocation is None or not changes:
            return ()
        payload: JsonObject = {
            "project_root": str(self._root),
            "schema_version": 2,
            "host": invocation.host,
            "session_id": invocation.session_id,
            "agent": invocation.event_agent,
            "turn_id": invocation.turn_id,
        }
        return build_observed_change_events(
            payload,
            invocation.invocation_id,
            invocation.observed_at,
            changes,
            self._snapshot_id(before),
            snapshot.snapshot_id,
        )

    @staticmethod
    def _candidate_keys(
        candidates: frozenset[str],
        snapshot: Snapshot,
    ) -> frozenset[str]:
        return frozenset(
            canonical_manifest_key(path, snapshot.is_casefolded)
            for path in candidates
        )

    @staticmethod
    def _snapshot_id(snapshot: Snapshot | None) -> str:
        return snapshot.snapshot_id if snapshot is not None else "snapshot:unavailable"

    def _observable_deltas(
        self,
        before: Snapshot,
        snapshot: Snapshot,
    ) -> tuple[NetDelta, ...]:
        deltas = calculate_net_delta(before, snapshot)
        if not trusted_default_policy_migration(before, self._root):
            return deltas
        config = load_provenance_config(self._root)
        return tuple(
            delta
            for delta in deltas
            if not (
                delta.op is ChangeOperation.DELETE
                and not is_path_in_scope(delta.path, config)
                and not is_user_config_excluded(delta.path, config)
            )
        )

    def _turn_result(self, result: ObservationResult, turn: TurnState, stop_cap_reserved: bool) -> ObservationResult:
        current = self._state.current
        if current is not None and trusted_default_policy_migration(turn.baseline, self._root):
            pending = tuple(
                sorted(
                    change_id_for(delta)
                    for delta in self._observable_deltas(turn.baseline, current)
                )
            )
        else:
            pending = pending_change_ids(self._state, turn)
        return replace(
            result,
            pending_change_ids=pending,
            clean_claim=(
                _complete_observation(result.status)
                and not result.incomplete
                and not pending
            ),
            stop_cap_reserved=stop_cap_reserved,
        )

    def _mark_incomplete(
        self,
        result: ObservationResult,
        reason: ProvenanceReason = ProvenanceReason.OBSERVATION_ERROR,
    ) -> ObservationResult:
        self._state.incomplete = True
        self._state.incomplete_reason = reason
        return replace(
            result,
            incomplete=True,
            clean_claim=False,
            status=ProvenanceStatus.INCOMPLETE,
            status_reason=reason,
        )

    def _mark_scope_too_large(self, result: ObservationResult) -> ObservationResult:
        return replace(
            result,
            incomplete=False,
            clean_claim=False,
            status=ProvenanceStatus.SCOPE_TOO_LARGE,
        )

    @staticmethod
    def _result(
        snapshot: Snapshot | None,
        changes: tuple[ObservedChange, ...],
        full_scan: bool,
        rebase_count: int,
    ) -> ObservationResult:
        status = snapshot.status if snapshot is not None else ProvenanceStatus.INCOMPLETE
        reason = (
            snapshot.status_reason
            if snapshot is not None
            else ProvenanceReason.SNAPSHOT_UNAVAILABLE
        )
        return ObservationResult(
            snapshot,
            changes,
            (),
            snapshot.incomplete if snapshot is not None else True,
            full_scan,
            rebase_count,
            False,
            False,
            status,
            reason,
        )
