from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final

from .adapter_change_events import record_observed_changes
from .ledger import JsonObject, capture_verification_covers, load_ledger, record_event
from .provenance_lifecycle import ProvenanceLifecycle
from .provenance_progress import scan_progress
from .provenance_lifecycle_types import ObservationResult, ObservedChange
from .provenance_store import SnapshotStoreError
from .shell_hints import shell_candidate_paths
from .verification import is_verification_command
from .verification_covers import active_turn


OBSERVABLE_FAMILIES: Final = frozenset({"edit", "shell"})


@dataclass(frozen=True, slots=True)
class CanonicalInvocation:
    host: str
    agent: str
    session_id: str
    turn_id: str
    invocation_id: str
    phase: str
    tool_family_hint: str
    candidate_paths: tuple[str, ...]
    command_hint: str
    success: bool
    evidence: str
    identity_synthetic: bool = False

    @property
    def agent_key(self) -> str:
        return f"{self.host}:{self.session_id}:{self.agent}"

    @property
    def scorecard_attribution(self) -> str:
        return "legacy_default" if self.identity_synthetic else "exact"

    def as_dict(self) -> JsonObject:
        return {
            "host": self.host,
            "agent": self.agent,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "invocation_id": self.invocation_id,
            "phase": self.phase,
            "tool_family_hint": self.tool_family_hint,
            "candidate_paths": list(self.candidate_paths),
            "command_hint": self.command_hint,
            "success": self.success,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class ObservationReport:
    snapshot_id: str
    baseline_snapshot_id: str
    changed_paths: tuple[str, ...]
    incomplete: bool
    full_reconcile: bool


def start_turn(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    try:
        lifecycle = ProvenanceLifecycle(root)
        with scan_progress(lifecycle.observed_file_count):
            result = lifecycle.start_turn(invocation.agent_key, invocation.turn_id)
    except (KeyError, OSError, SnapshotStoreError):
        return _incomplete_report()
    return _report(result, result.snapshot.snapshot_id if result.snapshot is not None else "")


def begin_invocation(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    invocation = _with_shell_candidates(_active_invocation(root, invocation))
    try:
        lifecycle = ProvenanceLifecycle(root)
        lifecycle.resume_turn(invocation.agent_key, invocation.turn_id, _mutation_capable(invocation))
        with scan_progress(lifecycle.observed_file_count):
            started = lifecycle.begin_invocation(
                invocation.agent_key,
                invocation.turn_id,
                invocation.invocation_id,
                invocation.candidate_paths,
            )
        covers = _covers(root, invocation)
    except (KeyError, OSError, SnapshotStoreError):
        report = _incomplete_report()
        _record_status(root, invocation, report)
        return report
    _record_invocation(root, invocation, covers)
    return ObservationReport(started.snapshot_id, "", (), False, False)


def observe_post_tool(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    invocation = _with_shell_candidates(_active_invocation(root, invocation))
    try:
        lifecycle = ProvenanceLifecycle(root)
        lifecycle.resume_turn(invocation.agent_key, invocation.turn_id, _mutation_capable(invocation))
        started = lifecycle.begin_invocation(
            invocation.agent_key,
            invocation.turn_id,
            invocation.invocation_id,
            _stored_candidates(root, invocation),
            False,
        )
        with scan_progress(lifecycle.observed_file_count):
            result = lifecycle.post_tool(started, _source(invocation))
    except (KeyError, OSError, SnapshotStoreError):
        report = _incomplete_report()
        _record_status(root, invocation, report)
        return report
    report = _report(result, "")
    _record_changes(root, invocation, result.changes, report.snapshot_id)
    _record_status(root, invocation, report)
    return report


def finish_turn(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    invocation = _active_invocation(root, invocation)
    try:
        lifecycle = ProvenanceLifecycle(root)
        lifecycle.resume_turn(invocation.agent_key, invocation.turn_id, _mutation_capable(invocation))
        with scan_progress(lifecycle.observed_file_count):
            result = lifecycle.finish_turn(invocation.agent_key, invocation.turn_id)
    except KeyError:
        return ObservationReport("", "", (), False, True)
    except (OSError, SnapshotStoreError):
        report = _incomplete_report(True)
        _record_status(root, invocation, report)
        return report
    report = _report(result, "")
    _record_changes(root, invocation, result.changes, report.snapshot_id)
    _record_status(root, invocation, report)
    return report


def verification_covers(root: Path, invocation: CanonicalInvocation) -> JsonObject | None:
    resolved = _active_invocation(root, invocation)
    return _stored_covers(root, resolved) or _covers(root, resolved)


def resolve_active_invocation(root: Path, invocation: CanonicalInvocation) -> CanonicalInvocation:
    return _active_invocation(root, invocation)


def restart_blocked_turn(root: Path, invocation: CanonicalInvocation) -> None:
    ledger = load_ledger({"project_root": str(root)})
    turns = ledger.get("active_turns")
    if not isinstance(turns, dict) or not isinstance(turns.get(invocation.agent_key), dict):
        return
    _ = start_turn(root, invocation)


def _report(result: ObservationResult, baseline_snapshot_id: str) -> ObservationReport:
    snapshot = result.snapshot
    snapshot_id = snapshot.snapshot_id if snapshot is not None else ""
    return ObservationReport(
        snapshot_id,
        baseline_snapshot_id or snapshot_id,
        tuple(change.path for change in result.changes),
        result.incomplete,
        result.full_scan,
    )


def _incomplete_report(full_reconcile: bool = False) -> ObservationReport:
    return ObservationReport("", "", (), True, full_reconcile)


def _covers(root: Path, invocation: CanonicalInvocation) -> JsonObject | None:
    if not is_verification_command(invocation.command_hint):
        return None
    try:
        return capture_verification_covers(_ledger_payload(root, invocation))
    except ValueError:
        return None


def _record_invocation(
    root: Path, invocation: CanonicalInvocation, covers: JsonObject | None
) -> None:
    payload = _ledger_payload(root, invocation) | {"event": "invocation"}
    if covers is not None:
        payload["covers"] = covers
    _ = record_event(payload)


def _record_changes(
    root: Path,
    invocation: CanonicalInvocation,
    changes: tuple[ObservedChange, ...],
    snapshot_id: str,
) -> None:
    if not changes:
        return
    record_observed_changes(
        _ledger_payload(root, invocation),
        invocation.invocation_id,
        invocation.phase,
        changes,
        snapshot_id,
    )


def _record_status(root: Path, invocation: CanonicalInvocation, report: ObservationReport) -> None:
    payload = _ledger_payload(root, invocation) | {
        "event": "observation",
        "current_snapshot_id": report.snapshot_id,
        "provenance_incomplete": report.incomplete,
    }
    if _mutation_capable(invocation):
        payload["provenance_mutation_capable"] = True
    _ = record_event(payload)


def _ledger_payload(root: Path, invocation: CanonicalInvocation) -> JsonObject:
    return {
        "project_root": str(root),
        "host": invocation.host,
        "agent": invocation.agent,
        "session_id": invocation.session_id,
        "turn_id": invocation.turn_id,
        "invocation_id": invocation.invocation_id,
        "attribution": invocation.scorecard_attribution,
    }


def _active_invocation(root: Path, invocation: CanonicalInvocation) -> CanonicalInvocation:
    ledger = load_ledger({"project_root": str(root)})
    turn = active_turn(ledger, _ledger_payload(root, invocation))
    if turn is not None:
        turn_id = turn.get("turn_id")
        return replace(invocation, turn_id=turn_id) if isinstance(turn_id, str) and turn_id else invocation
    turns = ledger.get("active_turns")
    if not isinstance(turns, dict):
        return invocation
    matches = [
        (key, candidate)
        for key, candidate in turns.items()
        if isinstance(key, str)
        and isinstance(candidate, dict)
        and key.startswith(f"{invocation.host}:")
        and candidate.get("agent") == invocation.agent
    ]
    if len(matches) != 1:
        return invocation
    key, candidate = matches[0]
    parts = key.split(":", 2)
    turn_id = candidate.get("turn_id")
    if len(parts) != 3 or not isinstance(turn_id, str) or not turn_id:
        return invocation
    return replace(invocation, session_id=parts[1], turn_id=turn_id)


def _with_shell_candidates(invocation: CanonicalInvocation) -> CanonicalInvocation:
    if invocation.tool_family_hint != "shell":
        return invocation
    candidates = tuple(dict.fromkeys((*invocation.candidate_paths, *shell_candidate_paths(invocation.command_hint))))
    return replace(invocation, candidate_paths=candidates)


def _stored_candidates(root: Path, invocation: CanonicalInvocation) -> tuple[str, ...]:
    turn = active_turn(load_ledger({"project_root": str(root)}), _ledger_payload(root, invocation))
    if turn is None:
        return invocation.candidate_paths
    invocations = turn.get("invocations")
    if not isinstance(invocations, dict):
        return invocation.candidate_paths
    stored = invocations.get(invocation.invocation_id)
    if not isinstance(stored, dict):
        return invocation.candidate_paths
    paths = stored.get("candidate_paths")
    if not isinstance(paths, list):
        return invocation.candidate_paths
    return tuple(path for path in paths if isinstance(path, str))


def _stored_covers(root: Path, invocation: CanonicalInvocation) -> JsonObject | None:
    turn = active_turn(load_ledger({"project_root": str(root)}), _ledger_payload(root, invocation))
    if turn is None:
        return None
    invocations = turn.get("invocations")
    if not isinstance(invocations, dict):
        return None
    stored = invocations.get(invocation.invocation_id)
    if not isinstance(stored, dict):
        return None
    covers = stored.get("covers")
    return covers if isinstance(covers, dict) else None


def _source(invocation: CanonicalInvocation) -> str:
    return invocation.tool_family_hint if invocation.tool_family_hint in OBSERVABLE_FAMILIES else "external"


def _mutation_capable(invocation: CanonicalInvocation) -> bool:
    return invocation.tool_family_hint in OBSERVABLE_FAMILIES
