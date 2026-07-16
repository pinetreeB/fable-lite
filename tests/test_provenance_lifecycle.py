from __future__ import annotations

from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from core.provenance import ScanIssue, Snapshot, snapshot_workspace_with_options
from core.provenance_lifecycle import ProvenanceLifecycle
from core.provenance_turn_resume import MissingTurnBaselineError
from core.provenance_types import (
    DEFAULT_MAX_SCAN_BYTES,
    ProvenanceReason,
    ProvenanceStatus,
    ScanBudget,
    SnapshotScanOptions,
)
from core.provenance_store import SnapshotStoreError, workspace_current_path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_turn_start_reuses_stop_current_without_content_open(tmp_path: Path) -> None:
    # Given: a completed Stop reconciliation for an unchanged regular file.
    target = tmp_path / "app.py"
    _write(target, "stable")
    lifecycle = ProvenanceLifecycle(tmp_path)
    first = lifecycle.start_turn("codex", "turn-1")
    lifecycle.finish_turn("codex", "turn-1")
    resumed = ProvenanceLifecycle(tmp_path)

    # When: the next turn starts with the persisted workspace current.
    with patch.object(io, "open", wraps=io.open) as opener:
        second = resumed.start_turn("codex", "turn-2")
    target_opens = len(
        [call for call in opener.call_args_list if call.args and call.args[0] == target]
    )

    # Then: cold start is full but the repeated turn is fast and reads no content bytes.
    assert first.full_scan is True
    assert second.full_scan is False
    assert target_opens == 0
    assert resumed.workspace_current_path.is_file()


def test_turn_start_reconciles_external_delta_before_baseline(tmp_path: Path) -> None:
    # Given: a prior Stop has committed the workspace current.
    target = tmp_path / "app.py"
    _write(target, "before")
    lifecycle = ProvenanceLifecycle(tmp_path)
    lifecycle.start_turn("codex", "turn-1")
    lifecycle.finish_turn("codex", "turn-1")
    _write(target, "external")

    # When: a later turn starts after an external filesystem mutation.
    result = lifecycle.start_turn("codex", "turn-2")

    # Then: the physical delta is committed as external before the new baseline is fixed.
    assert len(result.changes) == 1
    assert result.changes[0].source == "external"
    assert result.changes[0].owner is None
    assert result.pending_change_ids == ()


def test_generation_rebase_retries_once_then_commits_stable_scan(tmp_path: Path) -> None:
    # Given: a lifecycle whose first scan races one manifest-generation update.
    _write(tmp_path / "app.py", "stable")
    lifecycle = ProvenanceLifecycle(tmp_path)
    original_scan = lifecycle._scan
    scans = 0

    def concurrent_scan(
        previous: Snapshot | None,
        forced_paths: frozenset[str],
        full_scan: bool,
    ) -> Snapshot:
        nonlocal scans
        scans += 1
        snapshot = original_scan(previous, forced_paths, full_scan)
        if scans == 1:
            lifecycle._state.generation += 1
        return snapshot

    # When: turn start observes the one concurrent generation change.
    lifecycle._scan = concurrent_scan
    result = lifecycle.start_turn("codex", "turn-1")

    # Then: exactly one rebase produces a stable committed snapshot.
    assert result.rebase_count == 1
    assert result.incomplete is False
    assert scans == 2


def test_two_observers_dedupe_change_and_mark_contention_external(tmp_path: Path) -> None:
    # Given: two turns share the same baseline before one physical write.
    target = tmp_path / "app.py"
    _write(target, "before")
    lifecycle = ProvenanceLifecycle(tmp_path)
    lifecycle.start_turn("agent-a", "turn-a")
    lifecycle.start_turn("agent-b", "turn-b")
    first = lifecycle.begin_invocation("agent-a", "turn-a", "invoke-a", ("app.py",))
    second = lifecycle.begin_invocation("agent-b", "turn-b", "invoke-b", ("app.py",))
    _write(target, "after")

    # When: both observers reconcile the identical physical transition as their own edit.
    lifecycle.post_tool(first, source="edit")
    lifecycle.post_tool(second, source="edit")
    changes = lifecycle.changes

    # Then: one change id remains and effective attribution is safely external.
    assert len(changes) == 1
    assert changes[0].source == "external"
    assert changes[0].owner is None
    assert changes[0].attribution_status == "contended"
    assert changes[0].observed_by == ("agent-a", "agent-b")


def test_incomplete_stop_never_claims_clean_and_reserves_cap(tmp_path: Path) -> None:
    # Given: a mutation-capable turn whose final scan is incomplete.
    _write(tmp_path / "app.py", "stable")
    lifecycle = ProvenanceLifecycle(tmp_path)
    lifecycle.start_turn("codex", "turn-1", mutation_capable=True)
    original_scan = lifecycle._scan

    def incomplete_scan(
        previous: Snapshot | None,
        forced_paths: frozenset[str],
        full_scan: bool,
    ) -> Snapshot:
        snapshot = original_scan(previous, forced_paths, full_scan)
        return replace(snapshot, issues=snapshot.issues + (ScanIssue("app.py", "unstable_path"),))

    # When: Stop performs its mandatory full reconciliation.
    lifecycle._scan = incomplete_scan
    result = lifecycle.finish_turn("codex", "turn-1")

    # Then: it cannot claim clean and records a Stop cap reservation.
    assert result.incomplete is True
    assert result.clean_claim is False
    assert result.stop_cap_reserved is True


def test_baseline_revert_removes_pending_change_ids(tmp_path: Path) -> None:
    # Given: one active turn with an initial baseline file.
    target = tmp_path / "app.py"
    _write(target, "before")
    lifecycle = ProvenanceLifecycle(tmp_path)
    lifecycle.start_turn("codex", "turn-1")
    changed = lifecycle.begin_invocation("codex", "turn-1", "invoke-1", ("app.py",))
    _write(target, "after")
    lifecycle.post_tool(changed, source="edit")

    # When: the workspace returns to that turn's baseline bytes.
    reverted = lifecycle.begin_invocation("codex", "turn-1", "invoke-2", ("app.py",))
    _write(target, "before")
    result = lifecycle.post_tool(reverted, source="edit")

    # Then: audit history remains but the active pending set is empty.
    assert len(lifecycle.changes) == 2
    assert result.pending_change_ids == ()


def test_finish_turn_deletes_persisted_baseline(tmp_path: Path) -> None:
    # Given: an active turn with its baseline snapshot stored on disk.
    _write(tmp_path / "app.py", "stable")
    lifecycle = ProvenanceLifecycle(tmp_path)
    lifecycle.start_turn("codex", "turn-1")
    baseline_path = lifecycle.turn_baseline_path("codex", "turn-1")
    assert baseline_path.is_file()

    # When: the turn reaches its final reconciliation.
    lifecycle.finish_turn("codex", "turn-1")

    # Then: the baseline file and active-turn entry are removed.
    assert baseline_path.exists() is False
    assert lifecycle.active_turns == ()


def test_pretool_resume_rebuilds_missing_turn_baseline_from_persisted_current(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "app.py", "stable")
    started = ProvenanceLifecycle(tmp_path)
    result = started.start_turn("codex", "turn-recover")
    baseline_path = started.turn_baseline_path("codex", "turn-recover")
    assert result.status is ProvenanceStatus.COMPLETE
    baseline_path.unlink()

    resumed = ProvenanceLifecycle(tmp_path)
    resumed.resume_turn("codex", "turn-recover", True, allow_full_bootstrap=True)

    assert baseline_path.is_file()
    assert result.snapshot is not None
    assert resumed.active_turns[0].baseline.snapshot_id == result.snapshot.snapshot_id
    assert resumed.active_turns[0].mutation_capable is True


def test_post_mutation_resume_fails_closed_when_baseline_is_missing(
    tmp_path: Path,
) -> None:
    # Given: agent A changed a file, agent B advanced shared current, and A's baseline vanished.
    target = tmp_path / "app.py"
    _write(target, "before")
    agent_a = ProvenanceLifecycle(tmp_path)
    _ = agent_a.start_turn("agent-a", "turn-a", mutation_capable=True)
    baseline_path = agent_a.turn_baseline_path("agent-a", "turn-a")
    _write(target, "after")
    agent_b = ProvenanceLifecycle(tmp_path)
    _ = agent_b.start_turn("agent-b", "turn-b")
    baseline_path.unlink()

    # When/Then: post-mutation resume cannot reconstruct a baseline from advanced current.
    with pytest.raises(MissingTurnBaselineError):
        ProvenanceLifecycle(tmp_path).resume_turn(
            "agent-a",
            "turn-a",
            mutation_capable=True,
        )


def test_pretool_resume_full_bootstraps_after_workspace_store_failure(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "app.py", "stable")
    error = SnapshotStoreError(workspace_current_path(tmp_path), "injected")
    with patch("core.provenance_manifest.save_workspace_current", side_effect=error):
        failed = ProvenanceLifecycle(tmp_path).start_turn(
            "codex", "turn-bootstrap"
        )
    assert failed.status_reason is ProvenanceReason.STORE_WRITE_ERROR
    assert workspace_current_path(tmp_path).exists() is False

    recovered = ProvenanceLifecycle(tmp_path)
    recovered.resume_turn(
        "codex",
        "turn-bootstrap",
        True,
        allow_full_bootstrap=True,
    )

    assert workspace_current_path(tmp_path).is_file()
    assert recovered.turn_baseline_path("codex", "turn-bootstrap").is_file()
    assert recovered.active_turns[0].mutation_capable is True


def test_scope_policy_change_forces_turn_start_full_scan(tmp_path: Path) -> None:
    # Given: a reusable current from a successful Stop reconciliation.
    _write(tmp_path / "app.py", "stable")
    lifecycle = ProvenanceLifecycle(tmp_path)
    lifecycle.start_turn("codex", "turn-1")
    lifecycle.finish_turn("codex", "turn-1")
    _write(
        tmp_path / ".fable-lite" / "provenance-config.json",
        json.dumps({"version": 1, "exclude": ["vendor/**"]}),
    )

    # When: a new turn starts under the changed scope policy.
    result = lifecycle.start_turn("codex", "turn-2")

    # Then: metadata reuse is disabled for that turn.
    assert result.full_scan is True


def test_turn_start_fails_closed_when_git_tracked_discovery_fails(
    tmp_path: Path,
) -> None:
    # Given: a Git workspace whose tracked-path query fails unexpectedly.
    _write(tmp_path / "app.py", "stable")
    (tmp_path / ".git").mkdir()
    failed = subprocess.CompletedProcess[bytes](
        args=["git", "ls-files"],
        returncode=128,
        stdout=b"",
        stderr=b"fatal: corrupt index",
    )

    # When: provenance starts a turn without reliable tracked-path evidence.
    with patch(
        "core.provenance_lifecycle_scope.subprocess.run",
        return_value=failed,
    ):
        result = ProvenanceLifecycle(tmp_path).start_turn("codex", "turn-git-failure")

    # Then: the observation is incomplete rather than silently omitting tracked paths.
    assert result.status is ProvenanceStatus.INCOMPLETE
    assert result.status_reason is ProvenanceReason.SNAPSHOT_UNAVAILABLE
    assert result.incomplete is True


@pytest.mark.skipif(
    os.name != "nt",
    reason="casefold tracked-path matching relies on a case-insensitive filesystem",
)
def test_turn_start_casefolds_tracked_paths_before_policy_selection(
    tmp_path: Path,
) -> None:
    # Given: Git index casing differs from an excluded Windows filesystem path.
    _write(tmp_path / "src" / "core.py", "tracked")
    _write(
        tmp_path / ".fable-lite" / "provenance-config.json",
        json.dumps({"version": 1, "exclude": ["src/**"]}),
    )

    # When: lifecycle discovers the same tracked path with Git's different casing.
    with patch(
        "core.provenance_lifecycle_scope.git_tracked_paths",
        return_value=frozenset({"SRC/CORE.PY"}),
    ):
        result = ProvenanceLifecycle(tmp_path).start_turn("codex", "turn-casefold")

    # Then: policy selection and scanner matching both retain the source entry.
    assert result.snapshot is not None
    assert "src/core.py" in {entry.path for entry in result.snapshot.entries}


def test_scope_too_large_start_returns_explicit_status_without_committing_baseline(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "app.py", "value")
    oversized = snapshot_workspace_with_options(
        tmp_path,
        SnapshotScanOptions(
            budget=ScanBudget(max_entries=0, max_bytes=1024, max_seconds=8.0)
        ),
    )
    lifecycle = ProvenanceLifecycle(tmp_path)

    with patch.object(lifecycle, "_scan", return_value=oversized):
        result = lifecycle.start_turn("codex", "turn-large")

    assert result.status is ProvenanceStatus.SCOPE_TOO_LARGE
    assert result.incomplete is False
    assert lifecycle.workspace_current_path.exists() is False
    assert lifecycle.turn_baseline_path("codex", "turn-large").exists() is False


def test_scope_too_large_candidate_prime_preserves_current_and_turn_baseline(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "app.py", "stable")
    lifecycle = ProvenanceLifecycle(tmp_path)
    started = lifecycle.start_turn("codex", "turn-large")
    current = lifecycle._state.current
    assert current is not None

    with (tmp_path / "oversized.bin").open("wb") as handle:
        handle.truncate(DEFAULT_MAX_SCAN_BYTES + 1)
    invocation = lifecycle.begin_invocation(
        "codex",
        "turn-large",
        "invoke-large",
        ("oversized.bin",),
    )

    assert started.snapshot is not None
    assert invocation.snapshot_id == started.snapshot.snapshot_id
    assert lifecycle._state.current == current
    assert lifecycle._state.turns[("codex", "turn-large")].baseline == current


def test_scope_too_large_finish_never_claims_clean(tmp_path: Path) -> None:
    _write(tmp_path / "app.py", "stable")
    lifecycle = ProvenanceLifecycle(tmp_path)
    _ = lifecycle.start_turn("codex", "turn-large")
    oversized = snapshot_workspace_with_options(
        tmp_path,
        SnapshotScanOptions(
            budget=ScanBudget(max_entries=0, max_bytes=1024, max_seconds=8.0)
        ),
    )

    with patch.object(lifecycle, "_scan", return_value=oversized):
        result = lifecycle.finish_turn("codex", "turn-large")

    assert result.status is ProvenanceStatus.SCOPE_TOO_LARGE
    assert result.incomplete is False
    assert result.clean_claim is False


def test_missing_snapshot_store_bootstraps_normally(tmp_path: Path) -> None:
    _write(tmp_path / "app.py", "stable")

    result = ProvenanceLifecycle(tmp_path).start_turn("codex", "turn-cold")

    assert result.status is ProvenanceStatus.COMPLETE
    assert result.incomplete is False
    assert workspace_current_path(tmp_path).is_file()


def test_corrupt_snapshot_store_reports_typed_read_error_until_removed(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "app.py", "stable")
    current = workspace_current_path(tmp_path)
    _write(current, "{not json")

    failed = ProvenanceLifecycle(tmp_path).start_turn("codex", "turn-corrupt")

    assert failed.status is ProvenanceStatus.INCOMPLETE
    assert failed.incomplete is True
    assert failed.status_reason is ProvenanceReason.STORE_READ_ERROR

    current.unlink()
    recovered = ProvenanceLifecycle(tmp_path).start_turn("codex", "turn-recovered")
    assert recovered.status is ProvenanceStatus.COMPLETE
    assert recovered.incomplete is False


def test_snapshot_store_write_failure_reports_typed_reason(tmp_path: Path) -> None:
    _write(tmp_path / "app.py", "stable")
    error = SnapshotStoreError(workspace_current_path(tmp_path), "injected")

    with patch("core.provenance_manifest.save_workspace_current", side_effect=error):
        result = ProvenanceLifecycle(tmp_path).start_turn("codex", "turn-write")

    assert result.status is ProvenanceStatus.INCOMPLETE
    assert result.incomplete is True
    assert result.status_reason is ProvenanceReason.STORE_WRITE_ERROR
