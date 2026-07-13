from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from core.ledger import JsonObject, JsonValue
from core.ledger_schema import LedgerSchemaError, validate_v2_ledger
from core.ledger_v2 import apply_v2_event, default_v2_ledger
from core.scorecard import GateAction, GateTransition, ReasonCode, Resolution
import core.scorecard_store as scorecard_store


def _payload(root: Path, session_id: str = "session-1") -> JsonObject:
    return {
        "project_root": str(root),
        "host": "codex_cli",
        "session_id": session_id,
        "agent": "codex",
        "turn_id": f"turn-{session_id}",
    }


def _block(
    root: Path, event_id: str = "block-1", session_id: str = "session-1"
) -> GateTransition:
    return scorecard_store.new_transition(
        _payload(root, session_id),
        ReasonCode.STOP_VERIFICATION_MISSING,
        GateAction.BLOCK,
        event_id=event_id,
        occurred_at=datetime(2026, 7, 13, 12, tzinfo=UTC),
    )


def test_record_gate_transition_locked_writes_independent_journal_and_cache(
    tmp_path: Path,
) -> None:
    # Given: a v2 ledger already inside its owner transaction.
    ledger = default_v2_ledger()
    payload = _payload(tmp_path)

    # When: one stop block transition is recorded.
    scorecard_store.record_gate_transition_locked(
        ledger, payload, _block(tmp_path)
    )

    # Then: the independent journal and optional O(1) cache agree.
    journal = scorecard_store.load_scorecard_journal(tmp_path)
    summary = scorecard_store.cached_session_summary(ledger, payload)
    assert scorecard_store.scorecard_journal_path(tmp_path) == (
        tmp_path / ".fable-lite" / "scorecard" / "gates.jsonl"
    )
    assert journal.complete is True
    assert [item.event_id for item in journal.transitions] == ["block-1"]
    assert summary is not None
    assert summary.blocked_attempts == 1
    assert summary.unresolved_block_ids == ("block-1",)


def test_load_scorecard_journal_when_last_line_is_partial_marks_incomplete(
    tmp_path: Path,
) -> None:
    # Given: one valid event and a writer-crash partial tail.
    transition = _block(tmp_path)
    path = scorecard_store.scorecard_journal_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(scorecard_store.transition_json(transition)) + "\n{\"partial\":" ,
        encoding="utf-8",
    )

    # When: the journal is replayed.
    replay = scorecard_store.load_scorecard_journal(tmp_path)

    # Then: the valid prefix survives and incompleteness is explicit.
    assert [item.event_id for item in replay.transitions] == ["block-1"]
    assert replay.complete is False


def test_load_scorecard_journal_when_version_is_unknown_marks_incomplete(
    tmp_path: Path,
) -> None:
    # Given: an event from an unsupported independent scorecard version.
    raw = scorecard_store.transition_json(_block(tmp_path))
    raw["scorecard_schema_version"] = 2
    path = scorecard_store.scorecard_journal_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(raw) + "\n", encoding="utf-8")

    # When: replay sees the unknown version.
    replay = scorecard_store.load_scorecard_journal(tmp_path)

    # Then: it does not misparse the event as v1.
    assert replay.transitions == ()
    assert replay.complete is False


def test_incremental_cache_matches_journal_rebuild() -> None:
    # Given: two blocks, one recovery scope, and one cap allow.
    root = Path("synthetic-root")
    payload = _payload(root)
    ledger = default_v2_ledger()
    block_1 = _block(root, "block-1")
    block_2 = _block(root, "block-2")
    recovered = scorecard_store.new_transition(
        payload,
        ReasonCode.STOP_VERIFICATION_MISSING,
        GateAction.RECOVER,
        resolves=("block-1", "block-2"),
        resolution=Resolution.VERIFICATION,
        event_id="recover-1",
        occurred_at=datetime(2026, 7, 13, 12, 1, tzinfo=UTC),
    )
    capped = scorecard_store.new_transition(
        payload,
        ReasonCode.STOP_VERIFICATION_MISSING,
        GateAction.CAP_ALLOW,
        resolves=("block-3",),
        event_id="cap-1",
        occurred_at=datetime(2026, 7, 13, 12, 2, tzinfo=UTC),
    )

    # When: the same transitions feed incremental and replay paths.
    with (
        patch(
            "core.scorecard_store._append_transition",
            side_effect=[(index, index + 1) for index in range(4)],
        ),
        patch("core.scorecard_store.save_ledger", return_value=True),
    ):
        for transition in (block_1, block_2, recovered, capped):
            scorecard_store.record_gate_transition_locked(
                ledger, payload, transition
            )
    rebuilt = scorecard_store.build_scorecard_cache(
        (block_1, block_2, recovered, capped), complete=True
    )

    # Then: the optional cache is exactly reproducible from truth.
    assert ledger["scorecard_cache"] == rebuilt


def test_scorecard_cache_keeps_only_most_recent_64_sessions(tmp_path: Path) -> None:
    # Given: transitions for 129 canonical sessions.
    ledger = default_v2_ledger()
    started = datetime(2026, 7, 13, 12, tzinfo=UTC)

    # When: each session is incrementally recorded.
    with patch(
        "core.scorecard_store._append_transition",
        side_effect=[(index, index + 1) for index in range(129)],
    ):
        for index in range(129):
            payload = _payload(tmp_path, f"session-{index:02d}")
            transition = scorecard_store.new_transition(
                payload,
                ReasonCode.STOP_VERIFICATION_MISSING,
                GateAction.BLOCK,
                event_id=f"block-{index:02d}",
                occurred_at=started + timedelta(seconds=index),
            )
            scorecard_store.record_gate_transition_locked(
                ledger, payload, transition
            )

    # Then: the oldest session is evicted and the newest 64 remain.
    cache = ledger["scorecard_cache"]
    assert isinstance(cache, dict)
    assert len(cache) == 64
    assert "codex_cli:session-00:codex" not in cache
    assert "codex_cli:session-128:codex" in cache
    evicted_keys = ledger.get("scorecard_evicted_keys")
    assert isinstance(evicted_keys, list)
    assert len(evicted_keys) == 64
    assert "codex_cli:session-00:codex" not in evicted_keys
    assert "codex_cli:session-64:codex" in evicted_keys


def test_saturated_cache_keeps_never_seen_session_complete_and_visible(
    tmp_path: Path,
) -> None:
    # Given: 64 independently observed sessions fill the bounded cache.
    ledger = default_v2_ledger()
    started = datetime(2026, 7, 13, 12, tzinfo=UTC)
    payloads = tuple(
        _payload(tmp_path, f"filler-{index:02d}")
        | {"event": "prompt", "prompt": "fill cache", "seq": index + 1}
        for index in range(64)
    )
    new_payload = _payload(tmp_path, "never-seen") | {
        "event": "prompt",
        "prompt": "first activity",
        "seq": 65,
    }

    # When: a never-seen session records its first block after saturation.
    with patch(
        "core.scorecard_store._append_transition",
        side_effect=[(index, index + 1) for index in range(65)],
    ):
        for index, payload in enumerate((*payloads, new_payload)):
            _ = apply_v2_event(ledger, payload)
            transition = scorecard_store.new_transition(
                payload,
                ReasonCode.STOP_VERIFICATION_MISSING,
                GateAction.BLOCK,
                event_id=f"block-{index:02d}",
                occurred_at=started + timedelta(seconds=index),
            )
            scorecard_store.record_gate_transition_locked(
                ledger, payload, transition
            )

    # Then: complete journal history remains visible to the Stop scorecard line.
    cache = ledger.get("scorecard_cache")
    assert isinstance(cache, dict)
    entry = cache.get("codex_cli:never-seen:codex")
    assert isinstance(entry, dict)
    assert entry["complete"] is True
    summary = scorecard_store.cached_session_summary(ledger, new_payload)
    assert summary is not None
    assert summary.blocked_attempts == 1


def test_reactivated_evicted_session_is_not_exposed_as_complete(
    tmp_path: Path,
) -> None:
    # Given: an active session has two journaled blocks before 64 newer sessions evict it.
    ledger = default_v2_ledger()
    started = datetime(2026, 7, 13, 12, tzinfo=UTC)
    original_payload = _payload(tmp_path, "reactivated") | {
        "event": "prompt",
        "prompt": "original turn",
        "seq": 1,
    }
    _ = apply_v2_event(ledger, original_payload)
    original_transitions = tuple(
        scorecard_store.new_transition(
            original_payload,
            ReasonCode.STOP_VERIFICATION_MISSING,
            GateAction.BLOCK,
            event_id=f"original-{index}",
            occurred_at=started + timedelta(seconds=index),
        )
        for index in range(2)
    )
    filler_transitions = tuple(
        scorecard_store.new_transition(
            _payload(tmp_path, f"filler-{index:02d}"),
            ReasonCode.STOP_VERIFICATION_MISSING,
            GateAction.BLOCK,
            event_id=f"filler-{index:02d}",
            occurred_at=started + timedelta(minutes=1, seconds=index),
        )
        for index in range(64)
    )
    append_positions = [(index, index + 1) for index in range(67)]
    with (
        patch(
            "core.scorecard_store._append_transition",
            side_effect=append_positions,
        ),
        patch("core.scorecard_store.save_ledger", return_value=True),
    ):
        for transition in original_transitions:
            scorecard_store.record_gate_transition_locked(
                ledger, original_payload, transition
            )
        for index, transition in enumerate(filler_transitions):
            scorecard_store.record_gate_transition_locked(
                ledger, _payload(tmp_path, f"filler-{index:02d}"), transition
            )

        cache = ledger.get("scorecard_cache")
        assert isinstance(cache, dict)
        assert "codex_cli:reactivated:codex" not in cache
        evicted_keys = ledger.get("scorecard_evicted_keys")
        assert isinstance(evicted_keys, list)
        assert "codex_cli:reactivated:codex" in evicted_keys

        reactivated_payload = _payload(tmp_path, "reactivated") | {
            "event": "prompt",
            "prompt": "reactivated turn",
            "turn_id": "turn-reactivated-new",
            "seq": 2,
        }
        _ = apply_v2_event(ledger, reactivated_payload)
        reactivated = scorecard_store.new_transition(
            reactivated_payload,
            ReasonCode.STOP_VERIFICATION_MISSING,
            GateAction.BLOCK,
            event_id="reactivated-3",
            occurred_at=started + timedelta(minutes=3),
        )

        # When: the evicted session becomes active and records a third block.
        scorecard_store.record_gate_transition_locked(
            ledger, reactivated_payload, reactivated
        )

    # Then: the partial reconstruction is hidden instead of claiming one complete block.
    cache = ledger.get("scorecard_cache")
    assert isinstance(cache, dict)
    partial = cache.get("codex_cli:reactivated:codex")
    assert isinstance(partial, dict)
    assert partial["blocked_attempts"] == 1
    assert partial["complete"] is False
    assert scorecard_store.cached_session_summary(ledger, reactivated_payload) is None
    rebuilt = scorecard_store.build_scorecard_cache(
        (*original_transitions, *filler_transitions, reactivated), complete=True
    )
    truth = rebuilt.get("codex_cli:reactivated:codex")
    assert isinstance(truth, dict)
    assert truth["blocked_attempts"] == 3


def test_record_gate_transition_locked_when_journal_fails_marks_only_cache_incomplete(
    tmp_path: Path,
) -> None:
    # Given: a valid gate transition and an unwritable journal.
    ledger = default_v2_ledger()
    payload = _payload(tmp_path)

    # When: the append raises an OS boundary error.
    with patch(
        "core.scorecard_store._append_transition", side_effect=PermissionError
    ):
        scorecard_store.record_gate_transition_locked(
            ledger, payload, _block(tmp_path)
        )

    # Then: no exception changes the gate path and cache truth is incomplete.
    cache = ledger.get("scorecard_cache")
    assert isinstance(cache, dict)
    entry: JsonValue | None = cache.get("codex_cli:session-1:codex")
    assert isinstance(entry, dict)
    assert entry["complete"] is False
    assert scorecard_store.cached_session_summary(ledger, payload) is None


def test_v2_ledger_scorecard_cache_is_optional_bounded_and_validated() -> None:
    # Given: an existing v2 ledger without the optional cache.
    ledger = default_v2_ledger()
    ledger["prompt"] = "scorecard test"
    ledger["agent"] = "codex"

    # When/Then: no migration is required.
    assert validate_v2_ledger(ledger) is ledger

    # Given: a malformed cache with more than the bounded 64 sessions.
    ledger["scorecard_cache"] = {
        f"codex_cli:session-{index:02d}:codex": {
            "host": "codex_cli",
            "session_id": f"session-{index:02d}",
            "agent": "codex",
            "activated_at": "2026-07-13T12:00:00+00:00",
            "observed": True,
            "complete": True,
            "blocked_attempts": 0,
            "recovered_scopes": 0,
            "resolved_attempts": 0,
            "cap_allows": 0,
            "unresolved_block_ids": [],
            "latest_turn_id": "",
            "first_occurred_at": None,
            "last_occurred_at": None,
            "by_reason": [],
        }
        for index in range(65)
    }

    # When/Then: invalid persisted cache state is rejected at the ledger boundary.
    with pytest.raises(LedgerSchemaError, match="scorecard_cache"):
        _ = validate_v2_ledger(ledger)

    # Given: malformed derived eviction history exceeds the same 64-session bound.
    ledger["scorecard_cache"] = {}
    ledger["scorecard_evicted_keys"] = [
        f"codex_cli:evicted-{index:02d}:codex" for index in range(65)
    ]

    # When/Then: invalid persisted eviction state is rejected at the ledger boundary.
    with pytest.raises(LedgerSchemaError, match="scorecard_evicted_keys"):
        _ = validate_v2_ledger(ledger)

    ledger["scorecard_evicted_keys"] = ["codex_cli:duplicate:codex"] * 2
    with pytest.raises(LedgerSchemaError, match="duplicate sessions"):
        _ = validate_v2_ledger(ledger)


def test_append_after_partial_tail_preserves_valid_new_event(tmp_path: Path) -> None:
    # Given: a writer-crash tail after one valid block.
    first = _block(tmp_path, "block-1")
    path = scorecard_store.scorecard_journal_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(scorecard_store.transition_json(first)) + "\n{\"partial\":" ,
        encoding="utf-8",
    )

    # When: a later writer appends a new independent event.
    second = _block(tmp_path, "block-2")
    scorecard_store._append_transition(tmp_path, second)
    replay = scorecard_store.load_scorecard_journal(tmp_path)

    # Then: both valid records survive while completeness remains honest.
    assert [item.event_id for item in replay.transitions] == ["block-1", "block-2"]
    assert replay.complete is False
