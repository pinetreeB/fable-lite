from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import core.adapter_observation as adapter_observation
import core.provenance_lifecycle as lifecycle_module
from core.adapter_observation import CanonicalInvocation, begin_invocation
from core.ledger import JsonValue, load_ledger, record_event
from core.provenance_lifecycle import ProvenanceLifecycle, adjust_snapshot_for_peer_activity
from core.provenance_turn_resume import TurnBootstrapError
from core.provenance_types import (
    EntryKind,
    ManifestEntry,
    ProvenanceReason,
    ProvenanceStatus,
    ScanIssue,
    Snapshot,
)
from core.verification_covers import active_turn


def _snapshot(
    root: Path,
    *,
    entries: tuple[ManifestEntry, ...],
    issues: tuple[ScanIssue, ...] = (),
) -> Snapshot:
    return Snapshot(
        root=root,
        entries=entries,
        reparse_observations=(),
        issues=issues,
        snapshot_id="snapshot:input",
        scope_policy_id="policy",
        generated_patterns=(),
        status=ProvenanceStatus.INCOMPLETE if issues else ProvenanceStatus.COMPLETE,
    )


def _entry(path: str, digest: str) -> ManifestEntry:
    return ManifestEntry(
        path=path,
        canonical_key=path,
        file_type=EntryKind.REGULAR,
        size=7,
        mtime_ns=1,
        mode=0o644,
        digest=digest,
    )


def _record_peer_window(root: Path, path: str) -> None:
    base: dict[str, JsonValue] = {
        "project_root": str(root),
        "host": "host",
        "session_id": "peer-session",
        "agent": "peer",
        "turn_id": "peer-turn",
        "attribution": "exact",
    }
    _ = record_event(
        base
        | {
            "event": "prompt",
            "prompt": "peer work",
            "baseline_snapshot_id": "snapshot:base",
            "current_snapshot_id": "snapshot:base",
        }
    )
    _ = record_event(
        base
        | {
            "event": "invocation",
            "invocation_id": "peer-open",
            "candidate_paths": [path],
        }
    )


def test_recorded_peer_hot_write_becomes_complete_with_exclusions(tmp_path: Path) -> None:
    # Given: a prior entry becomes unstable while a recorded peer invocation owns that candidate.
    previous_entry = _entry("hot.py", "digest:before")
    previous = _snapshot(tmp_path, entries=(previous_entry,))
    unstable = _snapshot(
        tmp_path,
        entries=(),
        issues=(ScanIssue("hot.py", "unstable_path"),),
    )
    _record_peer_window(tmp_path, "hot.py")

    # When: provenance reconciles the failed capture against active peer evidence.
    adjusted = adjust_snapshot_for_peer_activity(
        tmp_path,
        unstable,
        previous,
        "host:caller-session:caller",
        "caller-turn",
        now=datetime.now(UTC),
    )

    # Then: the previous entry and auditable exclusion evidence replace a false delete delta.
    assert adjusted.status is ProvenanceStatus.COMPLETE_WITH_EXCLUSIONS
    assert adjusted.status_reason is ProvenanceReason.PEER_ACTIVITY
    assert adjusted.entries == (previous_entry,)
    assert adjusted.issues == ()
    assert len(adjusted.exclusions) == 1
    assert adjusted.exclusions[0].path == "hot.py"
    assert adjusted.exclusions[0].invocation_id == "peer-open"
    assert adjusted.snapshot_id != unstable.snapshot_id


def test_unrecorded_hot_write_remains_observation_error(tmp_path: Path) -> None:
    # Given: an unstable path has no matching recorded peer invocation.
    previous = _snapshot(tmp_path, entries=(_entry("hot.py", "digest:before"),))
    unstable = _snapshot(
        tmp_path,
        entries=(),
        issues=(ScanIssue("hot.py", "unreadable_path"),),
    )

    # When: peer adjustment is attempted without ledger evidence.
    adjusted = adjust_snapshot_for_peer_activity(
        tmp_path,
        unstable,
        previous,
        "host:caller-session:caller",
        "caller-turn",
    )

    # Then: the original incomplete snapshot remains conservative.
    assert adjusted.status is ProvenanceStatus.INCOMPLETE
    assert adjusted.issues == unstable.issues
    assert adjusted.exclusions == ()


def test_excluded_path_is_forced_on_the_next_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a workspace snapshot carries one turn-scoped peer exclusion.
    previous = _snapshot(tmp_path, entries=(_entry("hot.py", "digest:before"),))
    unstable = _snapshot(
        tmp_path,
        entries=(),
        issues=(ScanIssue("hot.py", "unstable_path"),),
    )
    _record_peer_window(tmp_path, "hot.py")
    adjusted = adjust_snapshot_for_peer_activity(
        tmp_path,
        unstable,
        previous,
        "host:caller-session:caller",
        "caller-turn",
    )
    seen: list[frozenset[str]] = []

    def fake_scan(root: Path, prior: Snapshot | None, forced: frozenset[str], full: bool) -> Snapshot:
        seen.append(forced)
        return previous

    # When: the next lifecycle scan starts with no explicit candidate paths.
    monkeypatch.setattr(lifecycle_module, "scan_snapshot", fake_scan)
    result = ProvenanceLifecycle(tmp_path)._scan(adjusted, frozenset(), False)

    # Then: the excluded path is reobserved instead of being reused indefinitely.
    assert result is previous
    assert seen == [frozenset({"hot.py"})]


def test_followup_hook_preserves_turn_not_started_without_keyerror_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: turn start failed and its ledger state explicitly records a missing baseline.
    invocation = CanonicalInvocation(
        "host",
        "caller",
        "session",
        "turn",
        "edit-1",
        "pre_tool",
        "edit",
        ("app.py",),
        "",
        True,
        "",
    )
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "host": "host",
            "session_id": "session",
            "agent": "caller",
            "turn_id": "turn",
            "attribution": "exact",
            "prompt": "work",
            "baseline_snapshot_id": "snapshot:unavailable",
            "current_snapshot_id": "snapshot:unavailable",
            "provenance_incomplete": True,
            "provenance_status": "incomplete",
            "provenance_status_reason": "observation_error",
        }
    )

    class FailedBootstrapLifecycle:
        observed_file_count = 0

        def __init__(self, root: Path) -> None:
            self.root = root

        def resume_turn(
            self,
            agent: str,
            turn_id: str,
            mutation_capable: bool = False,
            *,
            allow_full_bootstrap: bool = False,
        ) -> None:
            raise TurnBootstrapError(
                ProvenanceStatus.INCOMPLETE,
                ProvenanceReason.OBSERVATION_ERROR,
                True,
            )

    # When: a later mutation-capable hook also cannot bootstrap the baseline.
    monkeypatch.setattr(adapter_observation, "ProvenanceLifecycle", FailedBootstrapLifecycle)
    report = begin_invocation(tmp_path, invocation)
    ledger = load_ledger({"project_root": str(tmp_path)})

    # Then: the same explicit degraded turn remains, with no KeyError fallback cascade.
    turn = active_turn(
        ledger,
        {
            "host": "host",
            "session_id": "session",
            "agent": "caller",
            "attribution": "exact",
        },
    )
    assert report.incomplete is True
    assert report.error_kind != "KeyError"
    assert turn is not None
    assert turn["baseline_status"] == "missing"
    assert turn["provenance_status_reason"] == "turn_not_started"
    assert turn["provenance_mutation_capable"] is True
