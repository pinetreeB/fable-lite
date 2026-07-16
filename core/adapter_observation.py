"""# noqa: SIZE_OK — W3 must extend this existing adapter-core boundary without new production modules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, cast

from .adapter_change_events import record_observed_changes
from .ledger import JsonObject, capture_verification_covers, load_ledger, record_event
from .provenance_lifecycle import ProvenanceLifecycle
from .provenance_progress import scan_progress
from .provenance_lifecycle_types import ObservationResult, ObservedChange
from .project_root import is_user_home_root
from .provenance_store import SnapshotStoreError
from .provenance_turn_resume import MissingTurnBaselineError, TurnBootstrapError
from .provenance_types import (
    ProvenanceReason,
    ProvenanceStatus,
    Snapshot,
    normalize_budget_breach_path,
    normalize_budget_top_paths,
)
from .shell_hints import shell_candidate_paths
from .shell_command import (
    ShellClassification,
    ShellEffect,
    classify_shell_effect,
    is_remote_mutation_command,
)
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
    status: ProvenanceStatus = ProvenanceStatus.COMPLETE
    status_reason: ProvenanceReason = ProvenanceReason.NONE
    budget_top_paths: tuple[JsonObject, ...] = ()
    budget_breach_path: str | None = None
    issue_sample: tuple[JsonObject, ...] = ()
    rebase_count: int = 0
    error_kind: str = ""


def start_turn(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    if report := _home_root_unsupported_report(root):
        return report
    try:
        lifecycle = ProvenanceLifecycle(root)
        with scan_progress(lifecycle.observed_file_count):
            result = lifecycle.start_turn(
                invocation.agent_key,
                invocation.turn_id,
                event_agent=invocation.agent,
                host=invocation.host,
                session_id=invocation.session_id,
                invocation_id=invocation.invocation_id,
                observed_at=invocation.phase,
            )
    except (KeyError, OSError, SnapshotStoreError) as exc:
        return _incomplete_report(error_kind=type(exc).__name__)
    return _report(result, result.snapshot.snapshot_id if result.snapshot is not None else "")


def begin_invocation(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    if report := _home_root_unsupported_report(root):
        _record_invocation(root, invocation, _covers(root, invocation))
        return report
    invocation = _with_shell_candidates(_active_invocation(root, invocation))
    if report := _scope_too_large_report(root, invocation):
        _record_invocation(root, invocation, _covers(root, invocation))
        return report
    try:
        lifecycle = ProvenanceLifecycle(root)
        lifecycle.resume_turn(
            invocation.agent_key,
            invocation.turn_id,
            _mutation_capable(invocation),
            allow_full_bootstrap=True,
        )
        with scan_progress(lifecycle.observed_file_count):
            started = lifecycle.begin_invocation(
                invocation.agent_key,
                invocation.turn_id,
                invocation.invocation_id,
                invocation.candidate_paths,
                event_agent=invocation.agent,
                host=invocation.host,
                session_id=invocation.session_id,
                observed_at=invocation.phase,
            )
        covers = _covers(root, invocation)
    except TurnBootstrapError as exc:
        report = ObservationReport(
            "",
            "",
            (),
            exc.incomplete,
            True,
            exc.status,
            exc.reason,
        )
        _record_status(root, invocation, report)
        return report
    except (KeyError, OSError, SnapshotStoreError) as exc:
        report = _incomplete_report(error_kind=type(exc).__name__)
        _record_status(root, invocation, report)
        return report
    _record_invocation(root, invocation, covers, started.snapshot_id)
    return ObservationReport(started.snapshot_id, "", (), False, False)


def observe_post_tool(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    if report := _home_root_unsupported_report(root):
        _record_status(root, invocation, report)
        return report
    invocation = _with_shell_candidates(_active_invocation(root, invocation))
    if report := _scope_too_large_report(root, invocation):
        _record_status(root, invocation, report)
        return report
    try:
        lifecycle = ProvenanceLifecycle(root)
        lifecycle.resume_turn(invocation.agent_key, invocation.turn_id, _mutation_capable(invocation))
        started = lifecycle.begin_invocation(
            invocation.agent_key,
            invocation.turn_id,
            invocation.invocation_id,
            _stored_candidates(root, invocation),
            False,
            event_agent=invocation.agent,
            host=invocation.host,
            session_id=invocation.session_id,
            observed_at=invocation.phase,
        )
        with scan_progress(lifecycle.observed_file_count):
            result = lifecycle.post_tool(started, _source(invocation))
    except (KeyError, OSError, SnapshotStoreError) as exc:
        report = _incomplete_report(error_kind=type(exc).__name__)
        _record_status(root, invocation, report)
        return report
    report = _report(result, "")
    _record_changes(root, invocation, result.changes, report.snapshot_id)
    _record_status(root, invocation, report)
    return report


def finish_turn(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    if report := _home_root_unsupported_report(root):
        return report
    invocation = _active_invocation(root, invocation)
    _record_finish_requested(root, invocation)
    if report := _scope_too_large_report(root, invocation):
        return report
    try:
        lifecycle = ProvenanceLifecycle(root)
        lifecycle.resume_turn(invocation.agent_key, invocation.turn_id, _mutation_capable(invocation))
        with scan_progress(lifecycle.observed_file_count):
            result = lifecycle.reconcile_turn(
                invocation.agent_key,
                invocation.turn_id,
                event_agent=invocation.agent,
                host=invocation.host,
                session_id=invocation.session_id,
                invocation_id=invocation.invocation_id,
                observed_at=invocation.phase,
            )
    except MissingTurnBaselineError:
        ledger = load_ledger({"project_root": str(root)})
        turns = ledger.get("active_turns")
        if not isinstance(turns, dict) or not isinstance(
            turns.get(invocation.agent_key), dict
        ):
            return ObservationReport("", "", (), False, True)
        report = _incomplete_report(True, error_kind="MissingTurnBaselineError")
        _record_status(root, invocation, report)
        return report
    except KeyError:
        return ObservationReport("", "", (), False, True)
    except (OSError, SnapshotStoreError) as exc:
        report = _incomplete_report(True, error_kind=type(exc).__name__)
        _record_status(root, invocation, report)
        return report
    report = _report(result, "")
    _record_changes(root, invocation, result.changes, report.snapshot_id)
    _record_status(root, invocation, report)
    return report


def reconcile_turn(root: Path, invocation: CanonicalInvocation) -> ObservationReport:
    if report := _home_root_unsupported_report(root):
        return report
    invocation = _active_invocation(root, invocation)
    if report := _scope_too_large_report(root, invocation):
        return report
    try:
        lifecycle = ProvenanceLifecycle(root)
        lifecycle.resume_turn(
            invocation.agent_key,
            invocation.turn_id,
            _mutation_capable(invocation),
        )
        with scan_progress(lifecycle.observed_file_count):
            result = lifecycle.reconcile_turn(
                invocation.agent_key,
                invocation.turn_id,
                event_agent=invocation.agent,
                host=invocation.host,
                session_id=invocation.session_id,
                invocation_id=invocation.invocation_id,
                observed_at=invocation.phase,
            )
    except (KeyError, OSError, SnapshotStoreError) as exc:
        report = _incomplete_report(True, error_kind=type(exc).__name__)
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
    if result.incomplete:
        return ObservationReport(
            "",
            "",
            (),
            True,
            result.full_scan,
            result.status,
            result.status_reason,
            issue_sample=_issue_sample(result.snapshot),
            rebase_count=result.rebase_count,
        )
    if result.status is ProvenanceStatus.SCOPE_TOO_LARGE:
        top_paths, breach_path = _snapshot_budget_fields(result.snapshot)
        return ObservationReport(
            "",
            "",
            (),
            False,
            result.full_scan,
            result.status,
            result.status_reason,
            top_paths,
            breach_path,
        )
    snapshot = result.snapshot
    snapshot_id = snapshot.snapshot_id if snapshot is not None else ""
    return ObservationReport(
        snapshot_id,
        baseline_snapshot_id or snapshot_id,
        tuple(change.path for change in result.changes),
        result.incomplete,
        result.full_scan,
        result.status,
        result.status_reason,
    )


def _snapshot_budget_fields(snapshot: Snapshot | None) -> tuple[tuple[JsonObject, ...], str | None]:
    top_paths = getattr(snapshot, "budget_top_paths", None)
    breach_path = getattr(snapshot, "budget_breach_path", None)
    if not top_paths:
        return (), breach_path if isinstance(breach_path, str) and breach_path else None
    return (
        tuple(
            {"path": item.path, "bytes": item.total_bytes, "entries": item.total_entries}
            for item in top_paths
        ),
        breach_path if isinstance(breach_path, str) and breach_path else None,
    )


def _incomplete_report(full_reconcile: bool = False, error_kind: str = "") -> ObservationReport:
    return ObservationReport(
        "",
        "",
        (),
        True,
        full_reconcile,
        ProvenanceStatus.INCOMPLETE,
        ProvenanceReason.OBSERVATION_ERROR,
        error_kind=error_kind,
    )


def _issue_sample(snapshot: Snapshot | None, limit: int = 5) -> tuple[JsonObject, ...]:
    if snapshot is None or not snapshot.issues:
        return ()
    return tuple(
        {"path": issue.path, "reason": issue.reason} for issue in snapshot.issues[:limit]
    )


def _home_root_unsupported_report(root: Path) -> ObservationReport | None:
    if not is_user_home_root(root):
        return None
    return ObservationReport(
        "",
        "",
        (),
        False,
        False,
        ProvenanceStatus.UNSUPPORTED,
        ProvenanceReason.HOME_ROOT,
    )


def _covers(root: Path, invocation: CanonicalInvocation) -> JsonObject | None:
    if not is_verification_command(invocation.command_hint):
        return None
    try:
        return capture_verification_covers(
            _ledger_payload(root, invocation)
            | {"remote_target_ids": list(_remote_target_ids(invocation))}
        )
    except ValueError:
        return None


def _record_invocation(
    root: Path,
    invocation: CanonicalInvocation,
    covers: JsonObject | None,
    baseline_snapshot_id: str = "",
) -> None:
    payload = _ledger_payload(root, invocation) | {
        "event": "invocation",
        "candidate_paths": list(invocation.candidate_paths),
    }
    classification = _shell_classification(invocation)
    if _mutation_capable(invocation, classification):
        payload["provenance_mutation_capable"] = True
    target_ids = _remote_target_ids(invocation, classification)
    if target_ids and not is_verification_command(invocation.command_hint):
        payload["provenance_remote_mutation"] = True
        payload["remote_target_ids"] = list(target_ids)
    if covers is not None:
        payload["covers"] = covers
    if baseline_snapshot_id:
        payload["baseline_status"] = "ready"
        payload["baseline_snapshot_id"] = baseline_snapshot_id
    _ = record_event(payload)


def _record_finish_requested(root: Path, invocation: CanonicalInvocation) -> None:
    if load_ledger({"project_root": str(root)}).get("schema_version") != 2:
        return
    _ = record_event(
        _ledger_payload(root, invocation)
        | {
            "event": "finish_requested",
        }
    )


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
    classification = _shell_classification(invocation)
    payload = _ledger_payload(root, invocation) | {
        "event": "observation",
        "current_snapshot_id": report.snapshot_id,
        "provenance_incomplete": report.incomplete,
        "provenance_status": report.status.value,
        "provenance_status_reason": report.status_reason,
        "provenance_budget_top_paths": list(report.budget_top_paths),
        "provenance_budget_breach_path": report.budget_breach_path,
    }
    if report.issue_sample:
        payload["provenance_issue_sample"] = list(report.issue_sample)
    if report.rebase_count:
        payload["provenance_rebase_count"] = report.rebase_count
    if report.error_kind:
        payload["provenance_error_kind"] = report.error_kind
    if _mutation_capable(invocation, classification):
        payload["provenance_mutation_capable"] = True
    target_ids = _remote_target_ids(invocation, classification)
    if target_ids and not is_verification_command(invocation.command_hint):
        payload["provenance_remote_mutation"] = True
        payload["remote_target_ids"] = list(target_ids)
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


def _mutation_capable(
    invocation: CanonicalInvocation,
    classification: ShellClassification | None = None,
) -> bool:
    if invocation.tool_family_hint == "edit":
        return True
    if invocation.tool_family_hint != "shell":
        return False
    resolved = classification or classify_shell_effect(invocation.command_hint)
    return resolved.effect is ShellEffect.LOCAL_OR_UNKNOWN


def _remote_mutation(invocation: CanonicalInvocation) -> bool:
    return (
        invocation.tool_family_hint == "shell"
        and is_remote_mutation_command(invocation.command_hint)
    )


def _shell_classification(invocation: CanonicalInvocation) -> ShellClassification | None:
    if invocation.tool_family_hint != "shell":
        return None
    return classify_shell_effect(invocation.command_hint)


def _remote_target_ids(
    invocation: CanonicalInvocation,
    classification: ShellClassification | None = None,
) -> tuple[str, ...]:
    if not _remote_mutation(invocation):
        return ()
    resolved = classification or classify_shell_effect(invocation.command_hint)
    return resolved.remote_target_ids


def _scope_too_large_report(
    root: Path,
    invocation: CanonicalInvocation,
) -> ObservationReport | None:
    turn = active_turn(load_ledger({"project_root": str(root)}), _ledger_payload(root, invocation))
    if turn is None or turn.get("provenance_status") != ProvenanceStatus.SCOPE_TOO_LARGE.value:
        return None
    reason = turn.get("provenance_status_reason")
    top_paths = cast(
        tuple[JsonObject, ...],
        normalize_budget_top_paths(turn.get("provenance_budget_top_paths")),
    )
    breach_path = normalize_budget_breach_path(turn.get("provenance_budget_breach_path"))
    return ObservationReport(
        "",
        "",
        (),
        False,
        False,
        ProvenanceStatus.SCOPE_TOO_LARGE,
        ProvenanceReason(reason)
        if isinstance(reason, str) and reason in ProvenanceReason
        else ProvenanceReason.NONE,
        top_paths,
        breach_path,
    )
