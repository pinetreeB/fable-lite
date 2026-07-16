from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import core.provenance_lifecycle_scope as lifecycle_scope
from core.ledger import JsonObject
from core.provenance_lifecycle import ProvenanceLifecycle
from core.provenance_manifest import ManifestCommit
from core.provenance_store import load_turn_baseline
from core.provenance_types import Snapshot


def test_candidate_scope_prime_rebases_once_after_cas_conflict(tmp_path: Path) -> None:
    # Given: a new candidate appears after turn start and its first CAS loses a race.
    lifecycle = ProvenanceLifecycle(tmp_path)
    _ = lifecycle.start_turn("codex", "turn-1")
    (tmp_path / "late.py").write_text("new candidate", encoding="utf-8")
    real_commit = lifecycle_scope.commit_manifest
    calls = 0

    def conflict_once(
        root: Path,
        expected_generation: int,
        expected_snapshot_id: str,
        snapshot: Snapshot,
        event_templates: tuple[JsonObject, ...],
        baseline: tuple[str, str] | None = None,
    ) -> ManifestCommit | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return real_commit(
            root,
            expected_generation,
            expected_snapshot_id,
            snapshot,
            event_templates,
            baseline,
        )

    # When: candidate priming retries against the latest persisted generation.
    with patch(
        "core.provenance_lifecycle_scope.commit_manifest", side_effect=conflict_once
    ):
        _ = lifecycle.begin_invocation("codex", "turn-1", "invoke-late", ("late.py",))

    # Then: the second scan commits and the exact candidate enters the turn baseline.
    turn_baseline = load_turn_baseline(tmp_path, "codex", "turn-1")
    assert calls == 2
    assert turn_baseline is not None
    assert any(entry.path == "late.py" for entry in turn_baseline.entries)
