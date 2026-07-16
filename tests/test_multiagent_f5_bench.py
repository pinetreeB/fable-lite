from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from core.ledger_schema import JsonObject
from eval.provenance_bench_attribution import run_attribution_benchmark
from eval.provenance_bench_attribution_receipt import write_attribution_receipt
from eval.provenance_bench_metrics import SloResult
from eval.provenance_bench_models import BenchResult, StressResult
from eval.provenance_bench_receipt import write_receipt


def test_attribution_stop_benchmark_covers_matrix_and_atomic_writes(
    tmp_path: Path,
) -> None:
    # Given: a shortened attribution benchmark with two ledger scales.
    result = run_attribution_benchmark(
        tmp_path,
        warmups=0,
        measurements=2,
        scales=(0, 3),
    )

    # When: every path-attribution scale runs with peer exemption off and on.
    scenarios = {
        (scenario.path_attribution, scenario.peer_exemption): scenario
        for scenario in result.scenarios
    }

    # Then: every Stop sample includes one atomic ledger write and raw percentiles.
    assert set(scenarios) == {(0, False), (0, True), (3, False), (3, True)}
    assert all(scenario.stop.sample_count == 2 for scenario in scenarios.values())
    assert all(scenario.stop.p95_ns > 0 for scenario in scenarios.values())
    assert all(scenario.stop.p99_ns > 0 for scenario in scenarios.values())
    assert all(scenario.atomic_write_count == 2 for scenario in scenarios.values())
    assert all(scenario.ledger_valid for scenario in scenarios.values())
    assert scenarios[(0, False)].decision_counts == {"block": 2}
    assert scenarios[(0, True)].decision_counts == {"allow": 2}


def test_attribution_receipt_records_measure_only_budget_verdict(
    tmp_path: Path,
) -> None:
    # Given: a one-sample 10k-equivalent Stop benchmark result.
    result = run_attribution_benchmark(
        tmp_path / "bench",
        warmups=0,
        measurements=1,
        scales=(10_000,),
    )
    output = tmp_path / "bench-attribution-latest.json"

    # When: the W10-family receipt is persisted.
    write_attribution_receipt(output, result)
    receipt = cast(JsonObject, json.loads(output.read_text(encoding="utf-8")))

    # Then: it exposes p95/p99, 6s budget, and a non-hard-gate conclusion.
    assert receipt["schema_version"] == 1
    assert receipt["measure_only"] is True
    assert receipt["budget_ms"] == 6_000
    assert receipt["verdict"] in {"PASS", "FAIL"}
    scenarios = receipt["scenarios"]
    assert isinstance(scenarios, list)
    assert len(scenarios) == 2
    stops = [item["stop"] for item in scenarios if isinstance(item, dict)]
    assert all(isinstance(stop, dict) and "p95_ns" in stop for stop in stops)
    assert all(isinstance(stop, dict) and "p99_ns" in stop for stop in stops)


def test_w10_release_receipt_embeds_attribution_measurements(tmp_path: Path) -> None:
    # Given: the W10 release receipt receives a completed attribution benchmark.
    attribution = run_attribution_benchmark(
        tmp_path / "bench",
        warmups=0,
        measurements=1,
        scales=(10_000,),
    )
    result = BenchResult(
        (),
        SloResult(True, ()),
        {},
        StressResult(False, False, "not_requested", True),
        attribution=attribution,
    )
    output = tmp_path / "bench-latest.json"

    # When: the existing release receipt writer persists the result.
    write_receipt(output, result, seed=20260716)
    receipt = cast(JsonObject, json.loads(output.read_text(encoding="utf-8")))

    # Then: the bench-latest family carries the same measure-only verdict and raw scenarios.
    embedded = receipt["attribution"]
    assert isinstance(embedded, dict)
    assert embedded["measure_only"] is True
    assert embedded["budget_ms"] == 6_000
    assert embedded["verdict"] in {"PASS", "FAIL"}
    assert isinstance(embedded["scenarios"], list)
