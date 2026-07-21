from __future__ import annotations

from collections.abc import Callable, Iterator
from io import BufferedReader
import os
from pathlib import Path
import time
from typing import IO, cast
from unittest.mock import patch

import core.provenance_capture as capture
import core.contract as contract
import core.verify_state as verify_state
from core.agent_log import ledger_transaction
from core.ledger import load_ledger, record_event, save_ledger
from core.ledger_schema import JsonObject
from core.scorecard import GateAction, ReasonCode
from core.scorecard_store import new_transition, record_gate_transition_locked
from core.runtime_env import SCORECARD, canonical_env_key

from .provenance_bench_metrics import (
    PhaseMeasurement,
    PhaseStats,
    evaluate_scorecard_slo,
    summarize_phase,
)
from .provenance_bench_models import ScorecardBenchResult


WARMUPS = 5
MEASUREMENTS = 30
SCORECARD_ENV = canonical_env_key(SCORECARD)


def run_scorecard_benchmark(root: Path) -> ScorecardBenchResult:
    phases = {
        "stop_allow_scorecard": _stop_allow_phase(root / "stop-allow"),
        "gate_block_scorecard": _gate_block_phase(root / "gate-block"),
        "r1_block_scorecard": _r1_block_phase(root / "r1-block"),
    }
    return ScorecardBenchResult(
        WARMUPS,
        MEASUREMENTS,
        phases,
        evaluate_scorecard_slo(phases),
    )


def _stop_allow_phase(root: Path) -> dict[str, PhaseStats]:
    arm_measurements: dict[str, tuple[PhaseMeasurement, ...]] = {}
    for arm in ("off", "on"):
        payload = _identity(root / arm, "stop-allow", arm, 0)
        _seed_turn(payload, task_mode="quick", changed=False)
        with ledger_transaction(str(root / arm)):
            ledger = load_ledger(payload)
            transition = new_transition(
                payload, ReasonCode.STOP_VERIFICATION_MISSING, GateAction.BLOCK
            )
            record_gate_transition_locked(ledger, payload, transition)
            save_ledger(payload, ledger)
        samples: list[PhaseMeasurement] = []
        for index in range(WARMUPS + MEASUREMENTS):
            with patch.dict(
                os.environ,
                {SCORECARD_ENV: "0" if arm == "off" else "1"},
                clear=False,
            ):
                measurement = _measure_action(lambda: verify_state.evaluate_stop(payload))
            if index >= WARMUPS:
                samples.append(measurement)
        arm_measurements[arm] = tuple(samples)
    return {arm: summarize_phase(items) for arm, items in arm_measurements.items()}


def _gate_block_phase(root: Path) -> dict[str, PhaseStats]:
    arm_measurements: dict[str, tuple[PhaseMeasurement, ...]] = {}
    for arm in ("off", "on"):
        samples: list[PhaseMeasurement] = []
        for index in range(WARMUPS + MEASUREMENTS):
            payload = _identity(root / arm, "gate-block", arm, index)
            _seed_turn(payload, task_mode="deep", changed=True)
            patcher = patch.object(verify_state, "_record_scorecard", return_value=False)
            with patch.dict(os.environ, {SCORECARD_ENV: "1"}, clear=False):
                if arm == "off":
                    with patcher:
                        measurement = _measure_action(
                            lambda: verify_state.evaluate_stop(payload)
                        )
                else:
                    measurement = _measure_action(
                        lambda: verify_state.evaluate_stop(payload)
                    )
            if index >= WARMUPS:
                samples.append(measurement)
        arm_measurements[arm] = tuple(samples)
    return {arm: summarize_phase(items) for arm, items in arm_measurements.items()}


def _r1_block_phase(root: Path) -> dict[str, PhaseStats]:
    arm_measurements: dict[str, tuple[PhaseMeasurement, ...]] = {}
    for arm in ("off", "on"):
        samples: list[PhaseMeasurement] = []
        for index in range(WARMUPS + MEASUREMENTS):
            payload = _identity(root / arm, "r1-block", arm, index) | {
                "tool_name": "Edit",
                "file_paths": ["migrations/001_scorecard.sql"],
                "prompt": "DB migration change",
            }
            _seed_turn(payload, task_mode="deep", changed=False)
            with patch.dict(os.environ, {SCORECARD_ENV: "1"}, clear=False):
                measurement = _measure_action(
                    lambda: _r1_action(payload, enabled=arm == "on")
                )
            if index >= WARMUPS:
                samples.append(measurement)
        arm_measurements[arm] = tuple(samples)
    return {arm: summarize_phase(items) for arm, items in arm_measurements.items()}


def _r1_action(payload: JsonObject, *, enabled: bool) -> JsonObject:
    if enabled:
        return contract.evaluate_r1_contract_with_scorecard(payload)
    return contract.evaluate_r1_contract(payload)


def _identity(root: Path, phase: str, arm: str, index: int) -> JsonObject:
    return {
        "project_root": str(root),
        "host": "codex_cli",
        "session_id": f"bench-{phase}-{arm}-{index}",
        "agent": "codex",
        "turn_id": f"turn-{index}",
    }


def _seed_turn(payload: JsonObject, *, task_mode: str, changed: bool) -> None:
    _ = record_event(
        payload
        | {
            "event": "prompt",
            "task_mode": task_mode,
            "prompt": "scorecard benchmark",
        }
    )
    if changed:
        _ = record_event(
            payload | {"event": "change", "path": "app.py", "kind": "code"}
        )


def _measure_action(action: Callable[[], JsonObject]) -> PhaseMeasurement:
    stat_count = 0
    hash_calls = 0
    read_bytes = 0
    full_scans = 0
    journal_reads = 0
    original_stat = Path.stat
    original_glob = Path.glob
    original_rglob = Path.rglob
    original_read_text = Path.read_text
    original_open = Path.open
    original_digest: Callable[[BufferedReader, float | None], str | None] = (
        capture._digest_stream
    )

    def counting_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        nonlocal stat_count
        stat_count += 1
        return original_stat(path, follow_symlinks=follow_symlinks)

    def counting_glob(path: Path, pattern: str) -> Iterator[Path]:
        nonlocal full_scans
        full_scans += 1
        return original_glob(path, pattern)

    def counting_rglob(path: Path, pattern: str) -> Iterator[Path]:
        nonlocal full_scans
        full_scans += 1
        return original_rglob(path, pattern)

    def counting_read_text(
        path: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        nonlocal journal_reads
        if _is_scorecard_journal(path):
            journal_reads += 1
        return original_read_text(path, encoding=encoding, errors=errors)

    def counting_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> IO[str] | IO[bytes]:
        nonlocal journal_reads
        if _is_scorecard_journal(path) and ("r" in mode or "+" in mode):
            journal_reads += 1
        return cast(
            IO[str] | IO[bytes],
            original_open(path, mode, buffering, encoding, errors, newline),
        )

    def counting_digest(
        handle: BufferedReader,
        deadline: float | None,
    ) -> str | None:
        nonlocal hash_calls, read_bytes
        before = handle.tell()
        digest = original_digest(handle, deadline)
        hash_calls += 1
        read_bytes += handle.tell() - before
        return digest

    with (
        patch.object(Path, "stat", counting_stat),
        patch.object(Path, "glob", counting_glob),
        patch.object(Path, "rglob", counting_rglob),
        patch.object(Path, "read_text", counting_read_text),
        patch.object(Path, "open", counting_open),
        patch.object(capture, "_digest_stream", counting_digest),
    ):
        started = time.perf_counter_ns()
        _ = action()
        elapsed = time.perf_counter_ns() - started
    return PhaseMeasurement(
        elapsed,
        read_bytes,
        hash_calls,
        stat_count,
        0,
        0,
        False,
        full_scans > 0,
        journal_reads,
    )


def _is_scorecard_journal(path: Path) -> bool:
    return path.name == "gates.jsonl" and path.parent.name == "scorecard"
