from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path

import pytest

import core.provenance_manifest as provenance_manifest
import core.provenance_store as provenance_store
from core.agent_log import load_agent_events
from core.ledger import JsonValue, load_ledger
from core.provenance_lifecycle import ProvenanceLifecycle
from core.provenance_store import workspace_current_path


class SnapshotReplaceCrash(RuntimeError):
    pass


class RecoveryAuditCrash(RuntimeError):
    pass


def test_recovery_crash_preserves_pending_until_committed_audit_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a manifest is pending after the snapshot replacement crashed.
    target = tmp_path / "app.py"
    _ = target.write_text("before", encoding="utf-8")
    lifecycle = ProvenanceLifecycle(tmp_path)
    _ = lifecycle.start_turn("codex", "turn-1")
    invocation = lifecycle.begin_invocation(
        "codex",
        "turn-1",
        "invoke-1",
        ("app.py",),
    )
    _ = target.write_text("after", encoding="utf-8")
    real_replace = os.replace
    crashed = False

    def crash_after_snapshot_replace(
        source: str | Path,
        destination: str | Path,
    ) -> None:
        nonlocal crashed
        real_replace(source, destination)
        if not crashed and Path(destination) == workspace_current_path(tmp_path):
            crashed = True
            raise SnapshotReplaceCrash

    with monkeypatch.context() as context:
        context.setattr(provenance_store.os, "replace", crash_after_snapshot_replace)
        with pytest.raises(SnapshotReplaceCrash):
            _ = lifecycle.post_tool(invocation, source="edit")

    real_append = provenance_manifest.append_agent_event

    def crash_before_committed_audit(
        project_root: str,
        agent: str,
        payload: Mapping[str, JsonValue],
    ) -> None:
        if payload.get("commit_state") == "committed":
            raise RecoveryAuditCrash
        real_append(project_root, agent, payload)

    # When: recovery crashes exactly while publishing the committed audit evidence.
    with monkeypatch.context() as context:
        context.setattr(
            provenance_manifest,
            "append_agent_event",
            crash_before_committed_audit,
        )
        with pytest.raises(RecoveryAuditCrash):
            _ = ProvenanceLifecycle(tmp_path)

    pending = load_ledger({"project_root": str(tmp_path)}).get("manifest_pending")
    _ = ProvenanceLifecycle(tmp_path)
    events = load_agent_events(str(tmp_path), "codex") or []
    committed = [
        event
        for event in events
        if event.get("event") == "change"
        and event.get("commit_state") == "committed"
    ]

    # Then: pending remains retryable and the next recovery publishes committed evidence.
    assert isinstance(pending, dict)
    assert len(committed) >= 1
