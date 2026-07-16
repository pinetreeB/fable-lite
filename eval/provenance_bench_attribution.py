from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import time
from typing import Final
from unittest.mock import patch

from core.ledger import JsonObject, load_ledger, save_ledger
from core.ledger_storage import atomic_write_text
from core.ledger_v2 import apply_v2_event, default_v2_ledger
from core.verify_state import evaluate_stop

from .provenance_bench_metrics import PhaseMeasurement, summarize_phase
from .provenance_bench_models import (
    AttributionBenchResult,
    AttributionScenarioResult,
)

ATTRIBUTION_SCALES: Final = (0, 1_000, 10_000)
STOP_BUDGET_MS: Final = 6_000


def run_attribution_benchmark(
    root: Path,
    *,
    warmups: int = 5,
    measurements: int = 30,
    scales: tuple[int, ...] = ATTRIBUTION_SCALES,
) -> AttributionBenchResult:
    if warmups < 0 or measurements <= 0:
        raise ValueError("warmups must be non-negative and measurements positive")
    if any(scale < 0 or scale > 10_000 for scale in scales):
        raise ValueError("path attribution scales must be between 0 and 10,000")
    scenarios = tuple(
        _run_scenario(root, scale, peer_exemption, warmups, measurements)
        for scale in scales
        for peer_exemption in (False, True)
    )
    failures = _budget_failures(scenarios, measurements)
    return AttributionBenchResult(
        scenarios,
        STOP_BUDGET_MS,
        True,
        not failures,
        failures,
    )


def _run_scenario(
    root: Path,
    scale: int,
    peer_exemption: bool,
    warmups: int,
    measurements: int,
) -> AttributionScenarioResult:
    scenario_root = root / f"attribution-{scale}-peer-{'on' if peer_exemption else 'off'}"
    payload, template = _scenario_template(scenario_root, scale, peer_exemption)
    samples: list[PhaseMeasurement] = []
    decisions: Counter[str] = Counter()
    atomic_write_count = 0
    ledger_valid = True
    for index in range(warmups + measurements):
        if not save_ledger(payload, template):
            raise OSError(f"failed to restore benchmark ledger: {scenario_root}")
        with (
            patch.dict(os.environ, {"FABLE_LITE_SCORECARD": "0"}, clear=False),
            patch("core.ledger.atomic_write_text", wraps=atomic_write_text) as atomic_write,
        ):
            started = time.perf_counter_ns()
            decision = evaluate_stop(payload)
            elapsed = time.perf_counter_ns() - started
        if index < warmups:
            continue
        samples.append(PhaseMeasurement(elapsed, 0, 0, 0, 0, 0, False, False))
        decision_name = decision.get("decision")
        if isinstance(decision_name, str):
            decisions[decision_name] += 1
        atomic_write_count += atomic_write.call_count
        ledger_valid = ledger_valid and load_ledger(payload).get("schema_version") == 2
    return AttributionScenarioResult(
        scale,
        peer_exemption,
        warmups,
        measurements,
        summarize_phase(tuple(samples)),
        dict(decisions),
        atomic_write_count,
        ledger_valid,
    )


def _scenario_template(
    root: Path, scale: int, peer_exemption: bool
) -> tuple[JsonObject, JsonObject]:
    payload: JsonObject = {
        "project_root": str(root),
        "host": "bench-host",
        "session_id": "bench-session",
        "agent": "bench-caller",
        "turn_id": "bench-turn",
        "attribution": "exact",
    }
    ledger = default_v2_ledger()
    _ = apply_v2_event(
        ledger,
        payload
        | {
            "event": "prompt",
            "seq": 1,
            "prompt": "attribution Stop benchmark",
            "baseline_snapshot_id": "snapshot:base",
            "current_snapshot_id": "snapshot:base",
        },
    )
    _ = apply_v2_event(
        ledger,
        payload
        | {
            "event": "change",
            "seq": 2,
            "event_id": "bench-change",
            "manifest_generation": 1,
            "commit_state": "committed",
            "owner": None,
            "attribution_status": "exclusive",
            "observed_by": ["bench-caller"],
            "invocation_id": "bench-invocation",
            "current_snapshot_id": "snapshot:changed",
            "paths": [
                {
                    "change_id": "change:bench",
                    "path": "bench-change.py",
                    "kind": "code",
                    "before": "digest:base",
                    "after": "digest:changed",
                    "requires_verification": True,
                }
            ],
        },
    )
    ledger["path_attribution"] = {
        f"bench/path-{index:05d}.py": _attribution_entry(index)
        for index in range(scale)
    }
    turns = ledger.get("active_turns")
    if isinstance(turns, dict):
        turn = turns.get("bench-host:bench-session:bench-caller")
        if isinstance(turn, dict):
            revisions = turn.get("path_revisions")
            if isinstance(revisions, dict):
                revision = revisions.get("bench-change.py")
                if isinstance(revision, dict):
                    revision["attribution"] = (
                        "peer" if peer_exemption else "external"
                    )
    return payload, ledger


def _attribution_entry(index: int) -> JsonObject:
    return {
        "generation": 1,
        "status": "exclusive",
        "owners": [
            {
                "agent_key": "bench-host:peer-session:peer",
                "turn_id": "peer-turn",
                "revision_seq": index + 1,
                "manifest_generation": 1,
                "after_digest": f"digest:peer-{index}",
                "invocation_id": f"peer-invocation-{index}",
                "settled": False,
            }
        ],
    }


def _budget_failures(
    scenarios: tuple[AttributionScenarioResult, ...], measurements: int
) -> tuple[str, ...]:
    failures: list[str] = []
    ten_thousand = [item for item in scenarios if item.path_attribution == 10_000]
    if len(ten_thousand) != 2:
        failures.append("missing_10k_peer_matrix")
    for scenario in scenarios:
        suffix = "on" if scenario.peer_exemption else "off"
        key = f"{scenario.path_attribution}_peer_{suffix}"
        expected_decision = "allow" if scenario.peer_exemption else "block"
        if scenario.stop.sample_count != measurements:
            failures.append(f"{key}_measurements")
        if scenario.atomic_write_count != measurements:
            failures.append(f"{key}_atomic_writes")
        if scenario.decision_counts != {expected_decision: measurements}:
            failures.append(f"{key}_decision")
        if not scenario.ledger_valid:
            failures.append(f"{key}_ledger")
        if scenario.path_attribution == 10_000 and (
            scenario.stop.p95_ns > STOP_BUDGET_MS * 1_000_000
        ):
            failures.append(f"{key}_p95")
    return tuple(failures)
