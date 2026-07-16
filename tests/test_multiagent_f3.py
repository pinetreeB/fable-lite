from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.ledger import JsonObject, JsonValue, capture_verification_covers, load_ledger, record_event
from core.ledger_v2 import (
    apply_v2_event,
    default_v2_ledger,
    open_peer_invocation_candidates,
)
from core.verification_covers import active_turn
from core.verify_state import evaluate_stop, evaluate_without_io


def _event(root: Path, agent: str, event: str, **extra: JsonValue) -> JsonObject:
    return {
        "project_root": str(root),
        "event": event,
        "host": "host",
        "session_id": f"session-{agent}",
        "agent": agent,
        "turn_id": f"turn-{agent}",
        "attribution": "exact",
    } | extra


def _prompt(root: Path, agent: str, **extra: JsonValue) -> JsonObject:
    state: JsonObject = {
        "prompt": f"{agent} work",
        "baseline_snapshot_id": "snapshot:base",
        "current_snapshot_id": "snapshot:base",
    }
    state.update(extra)
    return record_event(
        _event(root, agent, "prompt") | state
    )


def test_open_invocation_closes_on_next_event_and_expires_after_lease(tmp_path: Path) -> None:
    # Given: beta has one current open invocation and one invocation older than the lease.
    ledger = default_v2_ledger()
    now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    _ = apply_v2_event(
        ledger,
        _event(tmp_path, "beta", "prompt", seq=1, timestamp=(now - timedelta(minutes=40)).isoformat()),
    )
    _ = apply_v2_event(
        ledger,
        _event(
            tmp_path,
            "beta",
            "invocation",
            seq=2,
            timestamp=(now - timedelta(minutes=31)).isoformat(),
            invocation_id="expired",
            candidate_paths=["expired.py"],
        ),
    )
    _ = apply_v2_event(
        ledger,
        _event(
            tmp_path,
            "beta",
            "invocation",
            seq=3,
            timestamp=now.isoformat(),
            invocation_id="current",
            candidate_paths=["current.py"],
        ),
    )

    # When: open peer candidates are queried, then beta emits its next event.
    before = open_peer_invocation_candidates(ledger, "host:session-alpha:alpha", now=now)
    _ = apply_v2_event(
        ledger,
        _event(
            tmp_path,
            "beta",
            "observation",
            seq=4,
            timestamp=(now + timedelta(seconds=1)).isoformat(),
            invocation_id="current",
            provenance_incomplete=False,
            provenance_status="complete",
            provenance_status_reason="",
        ),
    )
    after = open_peer_invocation_candidates(
        ledger,
        "host:session-alpha:alpha",
        now=now + timedelta(seconds=1),
    )

    # Then: only the leased current window was visible and it is durably closed by the event.
    assert set(before) == {"current.py"}
    assert after == {}
    turn = active_turn(ledger, _event(tmp_path, "beta", "stop"))
    assert turn is not None
    invocations = turn["invocations"]
    assert isinstance(invocations, dict)
    current = invocations["current"]
    assert isinstance(current, dict)
    assert current["status"] == "closed"
    assert current["completed_seq"] == 4


def test_finish_requested_keeps_blocked_turn_and_allow_finishes_it(tmp_path: Path) -> None:
    # Given: alpha has a changed turn and has requested finish without verification.
    _ = _prompt(tmp_path, "alpha")
    _ = record_event(
        _event(
            tmp_path,
            "alpha",
            "change",
            event_id="alpha-change",
            current_snapshot_id="snapshot:changed",
            paths=[
                {
                    "change_id": "change:alpha",
                    "path": "app.py",
                    "kind": "code",
                    "before": "digest:base",
                    "after": "digest:changed",
                    "requires_verification": True,
                }
            ],
        )
    )
    _ = record_event(_event(tmp_path, "alpha", "finish_requested", invocation_id="stop-1"))
    stop_payload = _event(tmp_path, "alpha", "stop")

    # When: Stop blocks once, then a matching successful verification is recorded.
    blocked = evaluate_stop(stop_payload)
    blocked_ledger = load_ledger(stop_payload)
    covers = capture_verification_covers(_event(tmp_path, "alpha", "verification"))
    _ = record_event(
        _event(
            tmp_path,
            "alpha",
            "verification",
            invocation_id="verify-alpha",
            command="python -m pytest tests/test_multiagent_f3.py -q",
            success=True,
            evidence="passed",
            covers=covers,
        )
    )
    allowed = evaluate_stop(stop_payload)
    allowed_ledger = load_ledger(stop_payload)

    # Then: block preserves active state, while allow commits turn_finished and removes it.
    assert blocked["decision"] == "block"
    assert active_turn(blocked_ledger, stop_payload) is not None
    assert allowed["decision"] == "allow"
    assert active_turn(allowed_ledger, stop_payload) is None


def test_next_transaction_garbage_collects_stale_turn_but_not_attribution() -> None:
    # Given: a turn and attribution record have been idle for more than 24 hours.
    ledger = default_v2_ledger()
    old = datetime(2026, 7, 14, tzinfo=UTC)
    now = datetime(2026, 7, 16, tzinfo=UTC)
    _ = apply_v2_event(
        ledger,
        _event(Path("."), "old", "prompt", seq=1, timestamp=old.isoformat(), prompt="old"),
    )
    ledger["path_attribution"] = {
        "app.py": {
            "generation": 1,
            "status": "exclusive",
            "owners": [
                {
                    "agent_key": "host:session-old:old",
                    "turn_id": "turn-old",
                    "revision_seq": 1,
                    "after_digest": "digest:old",
                    "invocation_id": "old",
                    "settled": False,
                }
            ],
        }
    }

    # When: another agent emits the next ledger transaction.
    _ = apply_v2_event(
        ledger,
        _event(Path("."), "fresh", "prompt", seq=2, timestamp=now.isoformat(), prompt="fresh"),
    )

    # Then: only the stale active turn is collected; attribution remains settlement-owned.
    turns = ledger["active_turns"]
    assert isinstance(turns, dict)
    assert "host:session-old:old" not in turns
    assert "app.py" in ledger["path_attribution"]


def test_degraded_turn_is_explicit_and_read_only_stop_is_informative(tmp_path: Path) -> None:
    # Given: prompt observation failed before a baseline could be persisted.
    ledger = _prompt(
        tmp_path,
        "reader",
        baseline_snapshot_id="snapshot:unavailable",
        current_snapshot_id="snapshot:unavailable",
        provenance_incomplete=True,
        provenance_status="incomplete",
        provenance_status_reason="observation_error",
    )
    payload = _event(tmp_path, "reader", "stop")

    # When: the exact read-only turn reaches Stop.
    decision = evaluate_without_io(ledger, payload)

    # Then: the turn is explicit and allowed only as a non-clean informational result.
    turn = active_turn(ledger, payload)
    assert turn is not None
    assert turn["baseline_status"] == "missing"
    assert turn["provenance_status_reason"] == "turn_not_started"
    assert decision["decision"] == "allow"
    assert "clean" in str(decision["message"])


def test_exact_missing_turn_does_not_consume_v1_projection_or_global_stop_counter() -> None:
    # Given: only stale v1 projection fields claim changes and a prior global stop block.
    ledger = default_v2_ledger()
    ledger["changed_files_seen"] = ["someone-else.py"]
    ledger["change_kinds"] = ["code"]
    ledger["stop_blocks"] = 1
    payload: JsonObject = {
        "project_root": ".",
        "host": "host",
        "session_id": "missing",
        "agent": "alpha",
        "turn_id": "missing-turn",
        "attribution": "exact",
    }

    # When: the exact identity without an active turn is evaluated.
    decision = evaluate_without_io(ledger, payload)

    # Then: gate state is turn_not_started rather than the global compatibility projection.
    assert decision["decision"] == "allow"
    assert "turn_not_started" in str(decision["message"])
    assert ledger["stop_blocks"] == 1
