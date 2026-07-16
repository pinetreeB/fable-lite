from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from core.ledger_schema import JsonValue

from .provenance_bench_metrics import MIB, budgets_for_scale
from .provenance_bench_attribution_receipt import attribution_value
from .provenance_bench_models import BenchResult, ScaleResult, ScorecardBenchResult


def write_receipt(path: Path, result: BenchResult, seed: int) -> None:
    encoded = json.dumps(_receipt(result, seed), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", delete=False, dir=path.parent) as handle:
        _ = handle.write(encoded)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _receipt(result: BenchResult, seed: int) -> dict[str, JsonValue]:
    scale_slos = {
        _scale_key(scale.file_count): _slo_value(result, scale)
        for scale in result.scales
    }
    receipt: dict[str, JsonValue] = {
        "schema_version": 2,
        "platform": os.name,
        "seed": seed,
        "scales": [_scale_value(result, scale) for scale in result.scales],
        "slo": {"passed": result.slo.passed, "failures": list(result.slo.failures), "scales": scale_slos},
        "stress": asdict(result.stress),
        "hard_gate": {
            "passed": result.slo.passed
            and all(scale.ledger_valid for scale in result.scales)
            and (result.scorecard is None or result.scorecard.hard_gate.passed)
        },
    }
    if result.scorecard is not None:
        receipt["scorecard"] = _scorecard_value(result.scorecard)
    if result.attribution is not None:
        receipt["attribution"] = attribution_value(result.attribution)
    return receipt


def _scorecard_value(result: ScorecardBenchResult) -> dict[str, JsonValue]:
    return {
        "warmups": result.warmups,
        "measurements": result.measurements,
        "phases": {
            phase_name: {arm: asdict(stats) for arm, stats in arms.items()}
            for phase_name, arms in result.phases.items()
        },
        "hard_gate": {
            "passed": result.hard_gate.passed,
            "failures": list(result.hard_gate.failures),
        },
    }


def _scale_value(result: BenchResult, scale: ScaleResult) -> dict[str, JsonValue]:
    return {
        "files": scale.file_count,
        "total_bytes": scale.total_bytes,
        "warmups": scale.warmups,
        "measurements": scale.measurements,
        "ledger_valid": scale.ledger_valid,
        "phases": {name: asdict(stats) for name, stats in scale.phases.items()},
        "scenario_coverage": [asdict(item) for item in scale.scenarios],
        "slo": _slo_value(result, scale),
    }


def _slo_value(result: BenchResult, scale: ScaleResult) -> dict[str, JsonValue]:
    slo = result.scale_slos[scale.file_count]
    rss_peak = max(stats.rss_peak_bytes for stats in scale.phases.values())
    return {
        "passed": slo.passed,
        "failures": list(slo.failures),
        "budgets_ms": budgets_for_scale(scale.file_count),
        "rss_peak_bytes": rss_peak,
        "rss_budget_bytes": 80 * MIB,
    }


def _scale_key(file_count: int) -> str:
    return f"{file_count // 1_000}k"
