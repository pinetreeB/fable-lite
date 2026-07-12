from __future__ import annotations

from dataclasses import replace
import io
import json
from pathlib import Path
from unittest.mock import patch

from core.provenance import ScanIssue, Snapshot
from core.provenance_lifecycle import ProvenanceLifecycle


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
