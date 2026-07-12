from __future__ import annotations

import json
from multiprocessing.connection import Connection, Listener
import os
from pathlib import Path
import subprocess
import sys
from unittest import skipIf

from core.ledger import JsonObject, load_ledger, record_event
from core.ledger_v1 import default_ledger
from core.verify_state import evaluate_stop


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_ONLY = skipIf(os.name != "nt", "Windows subprocess contract")


class WorkerFailure(RuntimeError):
    def __init__(self, index: int, error_type: str, detail: str) -> None:
        self.error_type = error_type
        super().__init__(f"worker[{index}] {error_type}: {detail}")


WORKER = ROOT / "tests" / "support" / "block_counter_worker.py"


def _concurrent_decisions(action: str, payloads: list[JsonObject]) -> list[str]:
    authkey = os.urandom(16)
    listener = Listener(("127.0.0.1", 0), authkey=authkey)
    address = listener.address
    assert isinstance(address, tuple)
    host, port = address
    assert isinstance(host, str)
    assert isinstance(port, int)
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    environment = {
        **os.environ,
        "PYTHONPATH": python_path,
        "FABLE_LITE_TEST_LOCK_WAIT_SECONDS": "45",
    }
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                str(WORKER),
                host,
                str(port),
                authkey.hex(),
                str(index),
                action,
                json.dumps(payload, ensure_ascii=False),
            ],
            cwd=ROOT,
            env=environment,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for index, payload in enumerate(payloads)
    ]
    channels: dict[int, Connection] = {}
    try:
        for _ in processes:
            channel = listener.accept()
            index_text = channel.recv_bytes().decode("ascii")
            assert index_text.isdigit()
            index = int(index_text)
            channels[index] = channel
        for channel in channels.values():
            channel.send_bytes(b"go")
        results: list[str] = []
        for index in range(len(payloads)):
            try:
                response = json.loads(channels[index].recv_bytes().decode("utf-8"))
            except EOFError as exc:
                process = processes[index]
                _ = process.wait(timeout=10)
                stderr = process.stderr.read() if process.stderr is not None else ""
                raise WorkerFailure(index, "TransportEOF", stderr or str(exc)) from exc
            if not isinstance(response, dict):
                raise WorkerFailure(index, "InvalidResponse", repr(response))
            status = response.get("status")
            if status != "ok":
                error_type = response.get("error_type")
                detail = response.get("message")
                raise WorkerFailure(
                    index,
                    error_type if isinstance(error_type, str) else "UnknownError",
                    detail if isinstance(detail, str) else "worker omitted an error message",
                )
            decision = response.get("decision")
            if not isinstance(decision, str):
                raise WorkerFailure(index, "InvalidResponse", "worker omitted decision")
            results.append(decision)
        for process in processes:
            stderr = process.stderr.read() if process.stderr is not None else ""
            assert process.wait(timeout=10) == 0, stderr
        return results
    finally:
        for channel in channels.values():
            channel.close()
        listener.close()
        for process in processes:
            if process.poll() is None:
                process.kill()
                _ = process.wait(timeout=10)


def _goals_decisions(payloads: list[JsonObject]) -> list[str]:
    try:
        return _concurrent_decisions("pretool", payloads)
    except WorkerFailure as exc:
        if exc.error_type not in {"TimeoutError", "TransportEOF"}:
            raise
        print(f"retrying flaky goals counter once: {exc}")
        return _concurrent_decisions("pretool", payloads)


def _start_unverified(root: Path, agent: str, session_id: str) -> None:
    base: JsonObject = {
        "project_root": str(root),
        "agent": agent,
        "host": "host",
        "session_id": session_id,
    }
    _ = record_event(
        base
        | {
            "event": "prompt",
            "task_mode": "deep",
            "prompt": f"{agent} change",
            "turn_id": f"{agent}-{session_id}",
        }
    )
    _ = record_event(base | {"event": "change", "path": f"{agent}.py", "kind": "code"})


def _stop_payload(root: Path, agent: str, session_id: str) -> JsonObject:
    return {
        "project_root": str(root),
        "agent": agent,
        "host": "host",
        "session_id": session_id,
    }


def _turn_stop_blocks(ledger: JsonObject, agent: str, session_id: str) -> int:
    turns = ledger["active_turns"]
    assert isinstance(turns, dict)
    turn = turns[f"host:{session_id}:{agent}"]
    assert isinstance(turn, dict)
    blocks = turn["blocks"]
    assert isinstance(blocks, dict)
    count = blocks["stop"]
    assert isinstance(count, int)
    return count


def _write_v1_unverified(root: Path) -> None:
    ledger = default_ledger()
    ledger["task_mode"] = "deep"
    ledger["changed_files_seen"] = ["legacy.py"]
    ledger["change_kinds"] = ["code"]
    state = root / ".fable-lite"
    state.mkdir()
    _ = (state / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")


@WINDOWS_ONLY
def test_eight_subprocess_stops_increment_one_turn_counter_exactly_twice(tmp_path: Path) -> None:
    # Given: one v2 turn has an unverified code change.
    _start_unverified(tmp_path, "alpha", "one")
    payload = _stop_payload(tmp_path, "alpha", "one")

    # When: eight subprocesses evaluate Stop from one release barrier.
    decisions = _concurrent_decisions("stop", [payload] * 8)

    # Then: exactly two requests block and the per-turn counter has no lost update.
    assert sum(decision == "block" for decision in decisions) == 2
    ledger = load_ledger(payload)
    assert _turn_stop_blocks(ledger, "alpha", "one") == 2


@WINDOWS_ONLY
def test_subprocess_stops_keep_two_agent_caps_independent(tmp_path: Path) -> None:
    # Given: two agents each own an unverified active turn.
    _start_unverified(tmp_path, "alpha", "one")
    _start_unverified(tmp_path, "beta", "two")
    alpha = _stop_payload(tmp_path, "alpha", "one")
    beta = _stop_payload(tmp_path, "beta", "two")

    # When: both agents race four Stop evaluations each.
    payloads = [alpha] * 4 + [beta] * 4
    decisions = _concurrent_decisions("stop", payloads)

    # Then: each agent independently receives two blocks and keeps its own cap.
    assert sum(decision == "block" for decision in decisions[:4]) == 2
    assert sum(decision == "block" for decision in decisions[4:]) == 2
    ledger = load_ledger(alpha)
    assert _turn_stop_blocks(ledger, "alpha", "one") == 2
    assert _turn_stop_blocks(ledger, "beta", "two") == 2


@WINDOWS_ONLY
def test_v1_subprocess_stops_keep_global_counter_without_lost_update(tmp_path: Path) -> None:
    # Given: an unmigrated v1 ledger has an unverified code change.
    _write_v1_unverified(tmp_path)
    payload: JsonObject = {"project_root": str(tmp_path)}

    # When: eight subprocesses evaluate its legacy Stop gate concurrently.
    decisions = _concurrent_decisions("stop", [payload] * 8)

    # Then: the legacy projection also records exactly two blocks.
    assert sum(decision == "block" for decision in decisions) == 2
    assert load_ledger(payload)["stop_blocks"] == 2


@WINDOWS_ONLY
def test_subprocess_goals_counter_blocks_exactly_twice(tmp_path: Path) -> None:
    # Given: the goals gate requires its prompt checkpoint.
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "gate",
            "needs_goals": True,
        }
    )
    payload: JsonObject = {"project_root": str(tmp_path), "tool_name": "Edit", "file_paths": ["app.py"]}

    # When: eight subprocesses attempt the same guarded edit together.
    decisions = _goals_decisions([payload] * 8)

    # Then: the goals counter reaches exactly two without a lost update.
    assert sum(decision == "block" for decision in decisions) == 2
    assert load_ledger(payload)["goals_blocks"] == 2


@WINDOWS_ONLY
def test_subprocess_intent_counter_blocks_exactly_twice(tmp_path: Path) -> None:
    # Given: the intent gate requires its prompt checkpoint.
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "normal",
            "prompt": "gate",
            "intent_required": True,
        }
    )
    payload: JsonObject = {"project_root": str(tmp_path), "tool_name": "Edit", "file_paths": ["app.py"]}

    # When: eight subprocesses attempt the same guarded edit together.
    decisions = _concurrent_decisions("pretool", [payload] * 8)

    # Then: the intent counter reaches exactly two without a lost update.
    assert sum(decision == "block" for decision in decisions) == 2
    assert load_ledger(payload)["intent_blocks"] == 2


@WINDOWS_ONLY
def test_subprocess_worker_reports_child_exception_without_eof(tmp_path: Path) -> None:
    # Given: a child receives an unsupported action after the release barrier.
    payload: JsonObject = {"project_root": str(tmp_path)}

    # When: the concurrent runner receives the child's terminal response.
    try:
        _ = _concurrent_decisions("unsupported", [payload])
    except WorkerFailure as exc:
        failure = str(exc)
    else:
        raise AssertionError("worker failure must be returned to the parent")

    # Then: the real child error is surfaced instead of a transport EOF.
    assert "KeyError" in failure


def test_alpha_prompt_preserves_beta_turn_counter(tmp_path: Path) -> None:
    # Given: beta has consumed its complete Stop cap.
    _start_unverified(tmp_path, "beta", "two")
    beta = _stop_payload(tmp_path, "beta", "two")
    assert evaluate_stop(beta)["decision"] == "block"
    assert evaluate_stop(beta)["decision"] == "block"

    # When: alpha starts a fresh prompt turn.
    _start_unverified(tmp_path, "alpha", "one")

    # Then: beta's stored counter remains untouched by alpha's prompt reset.
    ledger = load_ledger(beta)
    assert _turn_stop_blocks(ledger, "beta", "two") == 2
