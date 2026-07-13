from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Literal, assert_never
from unittest.mock import patch

import core.scorecard_store as scorecard_store
import core.verify_state as verify_state
from core.contract import evaluate_pretool_contract
from core.ledger import JsonObject, JsonValue, load_ledger, record_event
from core.verification_covers import active_turn
from core.verify_state import evaluate_stop


ROOT = Path(__file__).resolve().parents[1]
GateName = Literal["stop", "goals", "intent", "r1"]
STOP_WORKER = (
    "import json,sys; from core.verify_state import evaluate_stop; "
    "print(json.dumps(evaluate_stop(json.loads(sys.argv[1])), ensure_ascii=False))"
)


def _identity(root: Path, gate: str, index: int = 0) -> JsonObject:
    return {
        "project_root": str(root),
        "host": "codex_cli",
        "session_id": f"{gate}-session-{index}",
        "agent": f"codex-{gate}-{index}",
        "turn_id": f"{gate}-turn-{index}",
    }


def _seed_gate(root: Path, gate: GateName) -> JsonObject:
    identity = _identity(root, gate)
    prompt: JsonObject = identity | {"event": "prompt", "prompt": f"exercise {gate} gate"}
    match gate:
        case "stop":
            prompt["task_mode"] = "deep"
        case "goals":
            prompt.update({"task_mode": "normal", "needs_goals": True})
        case "intent":
            prompt.update({"task_mode": "normal", "intent_required": True})
        case "r1":
            prompt["task_mode"] = "normal"
        case unreachable:
            assert_never(unreachable)
    _ = record_event(prompt)
    match gate:
        case "stop":
            _ = record_event(
                identity | {"event": "change", "path": "app.py", "kind": "code"}
            )
            return identity
        case "r1":
            return identity | {
                "tool_name": "Edit",
                "file_paths": ["migrations/001_init.sql"],
                "prompt": "DB migration change",
            }
        case "goals" | "intent":
            return identity | {"tool_name": "Edit", "file_paths": ["app.py"]}
        case unreachable:
            assert_never(unreachable)


def _decide(gate: GateName, payload: JsonObject) -> JsonObject:
    match gate:
        case "stop":
            return evaluate_stop(payload)
        case "goals" | "intent" | "r1":
            return evaluate_pretool_contract(payload)
        case unreachable:
            assert_never(unreachable)


def _recover(root: Path, gate: GateName, payload: JsonObject) -> None:
    state = root / ".fable-lite"
    match gate:
        case "stop":
            _ = record_event(
                payload
                | {
                    "event": "verification",
                    "command": "python -m pytest tests/test_app.py",
                    "success": True,
                    "evidence": "1 passed",
                }
            )
        case "goals":
            _ = (state / "goals.json").write_text("{}\n", encoding="utf-8")
        case "intent":
            _ = (state / "intent.json").write_text("{}\n", encoding="utf-8")
        case "r1":
            _ = (state / "contract.json").write_text(
                json.dumps(
                    {
                        "restated_goal": "apply the migration safely",
                        "acceptance": ["migration test passes"],
                        "evidence": ["migration test passed"],
                    }
                ),
                encoding="utf-8",
            )
        case unreachable:
            assert_never(unreachable)


def _journal_events(root: Path) -> list[JsonObject]:
    path = root / ".fable-lite" / "scorecard" / "gates.jsonl"
    assert path.exists(), "S2 gate wiring did not create the scorecard journal"
    raw_events: list[JsonValue] = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert all(isinstance(event, dict) for event in raw_events)
    return [event for event in raw_events if isinstance(event, dict)]


def _string(event: JsonObject, field: str) -> str:
    assert isinstance(value := event.get(field), str)
    return value


def _strings(event: JsonObject, field: str) -> list[str]:
    assert isinstance(value := event.get(field), list)
    assert all(isinstance(item, str) for item in value)
    return [item for item in value if isinstance(item, str)]


def _assert_recovery(root: Path, gate: GateName, reason_code: str, resolution: str) -> None:
    # Given: a canonical session is missing the gate-specific checkpoint.
    payload = _seed_gate(root, gate)

    # When: the gate blocks, its real recovery condition is fulfilled, and it retries.
    blocked = _decide(gate, payload)
    _recover(root, gate, payload)
    allowed = _decide(gate, payload)

    # Then: the allow explicitly resolves the prior block without changing decisions.
    assert blocked["decision"] == "block"
    assert allowed["decision"] == "allow"
    events = _journal_events(root)
    assert [_string(event, "action") for event in events] == ["block", "recover"]
    assert [_string(event, "reason_code") for event in events] == [reason_code] * 2
    assert _string(events[1], "resolution") == resolution
    assert _strings(events[1], "resolves") == [_string(events[0], "event_id")]


def _assert_cap(root: Path, gate: GateName, reason_code: str) -> None:
    # Given: one capped gate remains unresolved through both allowed blocks.
    payload = _seed_gate(root, gate)

    # When: the same gate is attempted three times.
    decisions = [_decide(gate, payload)["decision"] for _ in range(3)]

    # Then: the third attempt is a distinct unresolved cap passage.
    assert decisions == ["block", "block", "allow"]
    events = _journal_events(root)
    assert [_string(event, "action") for event in events] == ["block", "block", "cap_allow"]
    assert {_string(event, "reason_code") for event in events} == {reason_code}
    block_ids = {_string(event, "event_id") for event in events[:2]}
    assert set(_strings(events[2], "resolves")) == block_ids
    assert all(_string(event, "action") != "recover" for event in events)


def test_stop_records_block_then_recovery(tmp_path: Path) -> None:
    _assert_recovery(tmp_path, "stop", "stop.verification_missing", "verification")


def test_goals_records_block_then_recovery(tmp_path: Path) -> None:
    _assert_recovery(tmp_path, "goals", "pretool.goals_missing", "goals_checkpoint")


def test_intent_records_block_then_recovery(tmp_path: Path) -> None:
    _assert_recovery(tmp_path, "intent", "pretool.intent_missing", "intent_checkpoint")


def test_r1_records_block_then_recovery(tmp_path: Path) -> None:
    _assert_recovery(tmp_path, "r1", "pretool.contract_missing", "contract")


def test_stop_records_cap_allow_without_recovery(tmp_path: Path) -> None:
    _assert_cap(tmp_path, "stop", "stop.verification_missing")


def test_stop_reason_shift_records_cap_allow_for_all_unresolved_blocks(
    tmp_path: Path,
) -> None:
    # Given: two turn-wide Stop blocks were consumed by incomplete provenance.
    payload = _seed_gate(tmp_path, "stop")
    _ = record_event(
        payload
        | {
            "event": "observation",
            "provenance_incomplete": True,
            "provenance_mutation_capable": True,
        }
    )
    first = evaluate_stop(payload)
    second = evaluate_stop(payload)

    # When: provenance recovers but verification is still missing on the capped retry.
    _ = record_event(
        payload
        | {
            "event": "observation",
            "provenance_incomplete": False,
            "provenance_mutation_capable": True,
        }
    )
    capped = evaluate_stop(payload | {"attribution": "legacy_default"})

    # Then: the reason shift cannot silently drop the required cap_allow record.
    assert [first["decision"], second["decision"], capped["decision"]] == [
        "block",
        "block",
        "allow",
    ]
    events = _journal_events(tmp_path)
    assert [_string(event, "action") for event in events] == [
        "block",
        "block",
        "cap_allow",
    ]
    assert [_string(event, "reason_code") for event in events] == [
        "stop.provenance_incomplete",
        "stop.provenance_incomplete",
        "stop.verification_missing",
    ]
    assert set(_strings(events[2], "resolves")) == {
        _string(events[0], "event_id"),
        _string(events[1], "event_id"),
    }
    assert _string(events[2], "attribution") == "legacy_default"
    assert all(_string(event, "action") != "recover" for event in events)


def test_goals_records_cap_allow_without_recovery(tmp_path: Path) -> None:
    _assert_cap(tmp_path, "goals", "pretool.goals_missing")


def test_intent_records_cap_allow_without_recovery(tmp_path: Path) -> None:
    _assert_cap(tmp_path, "intent", "pretool.intent_missing")


def test_journal_failure_preserves_exact_stop_decision_and_marks_cache_incomplete(
    tmp_path: Path,
) -> None:
    # Given: equivalent control and faulted unverified Stop sessions.
    control_payload = _seed_gate(tmp_path / "control", "stop")
    fault_payload = _seed_gate(tmp_path / "fault", "stop")
    control = evaluate_stop(control_payload)

    # When: only the scorecard journal append fails.
    with patch.object(
        scorecard_store,
        "_append_transition",
        side_effect=PermissionError("injected journal failure"),
        create=True,
    ):
        faulted = evaluate_stop(fault_payload)

    # Then: the gate decision is byte-for-byte equivalent and cache truth is incomplete.
    assert faulted == control
    ledger = load_ledger(fault_payload)
    cache = ledger.get("scorecard_cache")
    assert isinstance(cache, dict)
    entry = cache.get("codex_cli:stop-session-0:codex-stop-0")
    assert isinstance(entry, dict)
    assert entry["complete"] is False


def test_scorecard_boundary_failure_preserves_exact_stop_decision(tmp_path: Path) -> None:
    # Given: equivalent control and faulted unverified Stop sessions.
    control_payload = _seed_gate(tmp_path / "control", "stop")
    fault_payload = _seed_gate(tmp_path / "fault", "stop")
    control = evaluate_stop(control_payload)

    # When: the scorecard cache/journal boundary itself raises.
    failure = PermissionError("injected cache failure")
    with ExitStack() as stack:
        module_writer = stack.enter_context(
            patch.object(
                scorecard_store,
                "record_gate_transition_locked",
                side_effect=failure,
                create=True,
            )
        )
        caller_writer = stack.enter_context(
            patch.object(
                verify_state,
                "record_gate_transition_locked",
                side_effect=failure,
                create=True,
            )
        )
        faulted = evaluate_stop(fault_payload)

    # Then: observability failure cannot alter or replace the gate Decision.
    assert faulted == control
    assert module_writer.call_count + caller_writer.call_count == 1


def _subprocess_stop(payload: JsonObject) -> str:
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    completed = subprocess.run(
        [sys.executable, "-c", STOP_WORKER, json.dumps(payload)],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": python_path,
            "FABLE_LITE_TEST_LOCK_WAIT_SECONDS": "45",
        },
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    raw: JsonValue = json.loads(completed.stdout)
    assert isinstance(raw, dict)
    decision = raw.get("decision")
    assert isinstance(decision, str)
    return decision


def _assert_subprocess_writers(tmp_path: Path, writer_count: int) -> None:
    # Given: independent canonical Stop turns sharing one ledger and journal.
    payloads = [_identity(tmp_path, "writer", index) for index in range(writer_count)]
    for payload in payloads:
        _ = record_event(
            payload
            | {
                "event": "prompt",
                "task_mode": "deep",
                "prompt": "concurrent scorecard writer",
            }
        )
        _ = record_event(
            payload | {"event": "change", "path": "app.py", "kind": "code"}
        )

    seeded = load_ledger(payloads[0])
    assert seeded["event_seq"] == writer_count * 2
    for payload in payloads:
        turn = active_turn(seeded, payload)
        assert isinstance(turn, dict)
        assert turn["task_mode"] == "deep"
        assert turn["changed_files_seen"] == ["app.py"]

    # When: Windows-compatible child interpreters write from concurrent threads.
    with ThreadPoolExecutor(max_workers=min(writer_count, 8)) as executor:
        decisions = list(executor.map(_subprocess_stop, payloads))

    # Then: JSONL and bounded cache contain every event exactly once.
    assert decisions == ["block"] * writer_count
    events = _journal_events(tmp_path)
    event_ids = [_string(event, "event_id") for event in events]
    assert len(events) == writer_count
    assert len(set(event_ids)) == writer_count
    sessions = {_string(event, "session_id") for event in events}
    expected_sessions = {_string(payload, "session_id") for payload in payloads}
    assert sessions == expected_sessions
    cache = load_ledger(payloads[0]).get("scorecard_cache")
    assert isinstance(cache, dict) and len(cache) == writer_count


def test_eight_subprocess_gate_writers_preserve_unique_valid_jsonl(tmp_path: Path) -> None:
    _assert_subprocess_writers(tmp_path, 8)


def test_thirty_two_subprocess_gate_writers_preserve_unique_valid_jsonl(
    tmp_path: Path,
) -> None:
    _assert_subprocess_writers(tmp_path, 32)
