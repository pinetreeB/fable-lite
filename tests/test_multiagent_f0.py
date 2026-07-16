from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import core.provenance_store as provenance_store
from core.agent_log import load_agent_events
from core.ledger import JsonObject, JsonValue, load_ledger
from core.ledger_v2 import apply_v2_event, default_v2_ledger
from core.provenance_lifecycle import ProvenanceLifecycle
from core.provenance_store import load_workspace_current, workspace_current_path
from core.provenance_types import Snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "v2-provenance" / "change-event.json"


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _entry_digest(snapshot: Snapshot, path: str) -> str | None:
    return next(entry.digest for entry in snapshot.entries if entry.path == path)


def _change_fixture() -> JsonObject:
    value: JsonValue = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_cross_instance_stale_commit_is_rejected_and_preserves_newer_snapshot(
    tmp_path: Path,
) -> None:
    # Given: two lifecycle instances read the same committed generation.
    _write(tmp_path / "a.py", "base")
    seed = ProvenanceLifecycle(tmp_path)
    _ = seed.start_turn("seed", "seed-turn")
    stale = ProvenanceLifecycle(tmp_path)
    fresh = ProvenanceLifecycle(tmp_path)
    generation, previous = stale._generation_current()
    _write(tmp_path / "b.py", "captured-by-stale")
    stale_snapshot = stale._scan(previous, frozenset(), True)
    _write(tmp_path / "a.py", "fresh-wins")
    fresh_result = fresh.start_turn("fresh", "fresh-turn")
    assert fresh_result.snapshot is not None

    # When: the stale instance tries to commit its pre-race snapshot.
    committed = stale._commit_if_current(
        generation,
        stale_snapshot,
        "stale",
        "external",
        True,
    )

    # Then: persisted CAS rejects it and the fresh digest remains authoritative.
    current = load_workspace_current(tmp_path)
    assert committed is None
    assert current is not None
    assert _entry_digest(current, "a.py") == _entry_digest(
        fresh_result.snapshot, "a.py"
    )
    assert _entry_digest(current, "a.py") != _entry_digest(stale_snapshot, "a.py")


def test_snapshot_save_before_finalize_crash_recovers_event_on_next_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one candidate-matched edit and a crash injected after snapshot replace.
    target = tmp_path / "app.py"
    _write(target, "before")
    lifecycle = ProvenanceLifecycle(tmp_path)
    _ = lifecycle.start_turn("codex", "turn-1")
    invocation = lifecycle.begin_invocation("codex", "turn-1", "invoke-1", ("app.py",))
    generation_before = load_ledger({"project_root": str(tmp_path)}).get(
        "manifest_generation", 0
    )
    assert isinstance(generation_before, int) and not isinstance(
        generation_before, bool
    )
    _write(target, "after")
    real_replace = os.replace
    crashed = False

    def crash_after_snapshot_replace(
        source: str | Path, destination: str | Path
    ) -> None:
        nonlocal crashed
        real_replace(source, destination)
        if not crashed and Path(destination) == workspace_current_path(tmp_path):
            crashed = True
            raise RuntimeError("crash after snapshot replace")

    # When: the process stops between snapshot persistence and event finalization.
    with monkeypatch.context() as context:
        context.setattr(provenance_store.os, "replace", crash_after_snapshot_replace)
        with pytest.raises(RuntimeError, match="crash after snapshot replace"):
            _ = lifecycle.post_tool(invocation, source="edit")

    uncommitted_events = load_agent_events(str(tmp_path), "codex")
    assert uncommitted_events is not None
    uncommitted_changes = [
        event
        for event in uncommitted_events
        if event.get("event") == "change" and event.get("commit_state") == "uncommitted"
    ]
    assert len(uncommitted_changes) == 1
    assert (
        load_ledger({"project_root": str(tmp_path)}).get("manifest_generation", 0)
        == generation_before
    )

    # Then: the next lifecycle finalizes the same evidence without losing the bytes.
    _ = ProvenanceLifecycle(tmp_path)
    recovered_events = load_agent_events(str(tmp_path), "codex")
    assert recovered_events is not None
    committed_changes = [
        event
        for event in recovered_events
        if event.get("event_id") == uncommitted_changes[0].get("event_id")
        and event.get("commit_state") == "committed"
    ]
    assert len(committed_changes) == 1
    assert committed_changes[0]["manifest_generation"] == generation_before + 1
    assert (
        load_ledger({"project_root": str(tmp_path)})["manifest_generation"]
        == generation_before + 1
    )
    current = load_workspace_current(tmp_path)
    assert current is not None
    recovered_paths = committed_changes[0]["paths"]
    assert isinstance(recovered_paths, list)
    assert recovered_paths and isinstance(recovered_paths[0], dict)
    assert _entry_digest(current, "app.py") == recovered_paths[0]["after"]


def test_candidate_match_is_self_and_non_candidate_delta_is_external(
    tmp_path: Path,
) -> None:
    # Given: a turn whose invocation names only target.py as its candidate.
    _write(tmp_path / "target.py", "before")
    _write(tmp_path / "peer.py", "before")
    lifecycle = ProvenanceLifecycle(tmp_path)
    _ = lifecycle.start_turn("codex", "turn-1")
    first = lifecycle.begin_invocation("codex", "turn-1", "invoke-peer", ("target.py",))
    _write(tmp_path / "peer.py", "peer-change")

    # When: PostTool observes a delta outside the canonical candidate set.
    peer_result = lifecycle.post_tool(first, source="edit")

    # Then: the peer delta is external, while a later exact candidate remains self.
    assert len(peer_result.changes) == 1
    assert peer_result.changes[0].path == "peer.py"
    assert peer_result.changes[0].source == "external"
    assert peer_result.changes[0].owner is None
    second = lifecycle.begin_invocation(
        "codex", "turn-1", "invoke-target", ("target.py",)
    )
    _write(tmp_path / "target.py", "self-change")
    self_result = lifecycle.post_tool(second, source="edit")
    assert len(self_result.changes) == 1
    assert self_result.changes[0].path == "target.py"
    assert self_result.changes[0].source == "edit"
    assert self_result.changes[0].owner == "codex"


def test_turn_start_external_delta_uses_the_same_committed_wal(tmp_path: Path) -> None:
    # Given: a prior committed snapshot changes outside any open invocation.
    target = tmp_path / "app.py"
    _write(target, "before")
    first = ProvenanceLifecycle(tmp_path)
    _ = first.start_turn("codex", "turn-1")
    _ = first.finish_turn("codex", "turn-1")
    _write(target, "external-change")

    # When: the next turn observes and commits that external delta.
    result = ProvenanceLifecycle(tmp_path).start_turn("codex", "turn-2")

    # Then: the snapshot transition has a generation-bound committed event.
    assert len(result.changes) == 1
    assert result.changes[0].source == "external"
    events = load_agent_events(str(tmp_path), "codex")
    assert events is not None
    committed = [
        event
        for event in events
        if event.get("event") == "change" and event.get("commit_state") == "committed"
    ]
    assert committed
    assert committed[-1]["manifest_generation"] == result.changes[0].manifest_generation


def test_uncommitted_and_stale_generation_changes_are_non_authoritative() -> None:
    # Given: one valid uncommitted event and a later committed generation.
    uncommitted = _change_fixture()
    uncommitted["commit_state"] = "uncommitted"
    ledger = default_v2_ledger()

    # When: the recovery-only event is reduced.
    _ = apply_v2_event(ledger, uncommitted)

    # Then: only global audit sequence moves; no gate projection is created.
    assert ledger["event_seq"] == uncommitted["seq"]
    assert ledger["manifest_generation"] == 0
    assert ledger["active_turns"] == {}
    assert ledger["changed_files_seen"] == []
    committed = _change_fixture()
    committed["manifest_generation"] = 2
    _ = apply_v2_event(ledger, committed)
    stale = _change_fixture()
    stale["seq"] = 18
    stale["event_id"] = "stale-change"
    stale["manifest_generation"] = 1
    paths = stale["paths"]
    assert isinstance(paths, list) and isinstance(paths[0], dict)
    paths[0]["path"] = "stale.py"
    _ = apply_v2_event(ledger, stale)
    assert ledger["manifest_generation"] == 2
    changed_files = ledger["changed_files_seen"]
    assert isinstance(changed_files, list)
    assert "stale.py" not in changed_files
