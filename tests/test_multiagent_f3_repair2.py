from __future__ import annotations

from pathlib import Path

from core.adapter_observation import (
    CanonicalInvocation,
    begin_invocation,
    finish_turn,
    observe_post_tool,
    restart_blocked_turn,
    start_turn,
)
from core.ledger import record_event
from core.verify_state import evaluate_stop


def test_blocked_stop_preserves_baseline_for_followup_resume(tmp_path: Path) -> None:
    # Given: an exact mutation-capable turn has an unverified changed file.
    target = tmp_path / "app.py"
    _ = target.write_text("before", encoding="utf-8")
    turn = CanonicalInvocation(
        "claude_code",
        "claude",
        "session-1",
        "turn-1",
        "turn-start",
        "turn_start",
        "other",
        (),
        "",
        True,
        "",
    )
    baseline = start_turn(tmp_path, turn)
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "host": turn.host,
            "agent": turn.agent,
            "session_id": turn.session_id,
            "turn_id": turn.turn_id,
            "attribution": "exact",
            "prompt": "change app.py",
            "baseline_snapshot_id": baseline.baseline_snapshot_id,
            "current_snapshot_id": baseline.snapshot_id,
        }
    )
    edit = CanonicalInvocation(
        turn.host,
        turn.agent,
        turn.session_id,
        turn.turn_id,
        "edit-1",
        "pre_tool",
        "edit",
        ("app.py",),
        "",
        False,
        "",
    )
    _ = begin_invocation(tmp_path, edit)
    _ = target.write_text("after", encoding="utf-8")
    _ = observe_post_tool(
        tmp_path,
        CanonicalInvocation(
            turn.host,
            turn.agent,
            turn.session_id,
            turn.turn_id,
            "edit-1",
            "post_tool",
            "edit",
            ("app.py",),
            "",
            True,
            "written",
        ),
    )
    stop = CanonicalInvocation(
        turn.host,
        turn.agent,
        turn.session_id,
        turn.turn_id,
        "stop-1",
        "stop",
        "other",
        (),
        "",
        True,
        "",
    )

    # When: final reconciliation is followed by a blocking Stop and a follow-up tool.
    _ = finish_turn(tmp_path, stop)
    decision = evaluate_stop(
        {
            "project_root": str(tmp_path),
            "host": stop.host,
            "agent": stop.agent,
            "session_id": stop.session_id,
            "turn_id": stop.turn_id,
            "attribution": "exact",
        }
    )
    restart_blocked_turn(tmp_path, stop)
    resumed = begin_invocation(
        tmp_path,
        CanonicalInvocation(
            turn.host,
            turn.agent,
            turn.session_id,
            turn.turn_id,
            "verify-1",
            "pre_tool",
            "shell",
            (),
            "python -m pytest tests/test_multiagent_f3_repair2.py -q",
            False,
            "",
        ),
    )

    # Then: block keeps the turn resumable instead of losing its persisted baseline.
    assert decision["decision"] == "block"
    assert resumed.incomplete is False
