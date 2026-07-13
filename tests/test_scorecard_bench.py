from __future__ import annotations

from dataclasses import dataclass, fields, replace
from contextlib import contextmanager
import json
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import pytest

from core.ledger import JsonObject
from eval.provenance_bench_metrics import PhaseStats, SloResult
from eval.provenance_bench_models import (
    BenchResult,
    ScaleResult,
    StressResult,
)
from eval.provenance_bench_receipt import write_receipt


REQUIRED_SCORECARD_PHASES = (
    "stop_allow_scorecard",
    "gate_block_scorecard",
    "r1_block_scorecard",
)
@dataclass(frozen=True, slots=True)
class _StatsSpec:
    sample_count: int = 30
    p50_ns: int = 50_000_000
    p95_ns: int = 90_000_000
    p99_ns: int = 200_000_000
    max_ns: int = 220_000_000
    content_read_bytes: int = 0
    hash_calls: int = 0
    stat_count: int = 0
    journal_read_count: int = 0
    full_scan_count: int = 0


def _stats(spec: _StatsSpec = _StatsSpec()) -> PhaseStats:
    return PhaseStats(
        sample_count=spec.sample_count,
        p50_ns=spec.p50_ns,
        p95_ns=spec.p95_ns,
        p99_ns=spec.p99_ns,
        max_ns=spec.max_ns,
        content_read_bytes=spec.content_read_bytes,
        hash_calls=spec.hash_calls,
        stat_count=spec.stat_count,
        journal_read_count=spec.journal_read_count,
        tracemalloc_peak_bytes=0,
        rss_peak_bytes=0,
        incomplete_count=0,
        full_scan_count=spec.full_scan_count,
    )


def _green_scorecard_phases() -> dict[str, dict[str, PhaseStats]]:
    stop_baseline = _stats(
        _StatsSpec(hash_calls=4, stat_count=12, full_scan_count=2)
    )
    return {
        "stop_allow_scorecard": {"off": stop_baseline, "on": stop_baseline},
        "gate_block_scorecard": {"off": _stats(), "on": _stats()},
        "r1_block_scorecard": {"off": _stats(), "on": _stats()},
    }


def test_scorecard_receipt_adds_ab_results_without_replacing_w10_phases_or_slo(
    tmp_path: Path,
) -> None:
    # Given: a green existing W10 scale plus green Scorecard A/B measurements.
    from eval.provenance_bench_models import ScorecardBenchResult

    old_phases = {
        "fast_path": _stats(),
        "cold_start": _stats(),
        "post_tool": _stats(),
        "stop": _stats(),
    }
    existing_slo = SloResult(True, ())
    scorecard_slo = SloResult(True, ())
    scorecard = ScorecardBenchResult(
        warmups=5,
        measurements=30,
        phases=_green_scorecard_phases(),
        hard_gate=scorecard_slo,
    )
    result = BenchResult(
        scales=(
            ScaleResult(
                file_count=1_000,
                total_bytes=1,
                warmups=5,
                measurements=30,
                phases=old_phases,
                scenarios=(),
                ledger_valid=True,
            ),
        ),
        slo=existing_slo,
        scale_slos={1_000: existing_slo},
        stress=StressResult(False, False, "not_requested", True),
        scorecard=scorecard,
    )

    # When: the public receipt writer serializes the combined benchmark result.
    receipt_path = tmp_path / "bench.json"
    write_receipt(receipt_path, result, seed=20260713)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    # Then: Scorecard has the fixed A/B shape while the existing W10 result survives.
    assert receipt["scorecard"]["warmups"] == 5
    assert receipt["scorecard"]["measurements"] == 30
    assert receipt["scorecard"]["hard_gate"] == {
        "passed": True,
        "failures": [],
    }
    assert set(receipt["scorecard"]["phases"]) == set(REQUIRED_SCORECARD_PHASES)
    summary_fields = {field.name for field in fields(PhaseStats)}
    for phase in receipt["scorecard"]["phases"].values():
        assert set(phase) == {"off", "on"}
        assert set(phase["off"]) == summary_fields
        assert set(phase["on"]) == summary_fields
        assert phase["off"]["sample_count"] == 30
        assert phase["on"]["sample_count"] == 30
    assert set(receipt["scales"][0]["phases"]) == set(old_phases)
    assert receipt["scales"][0]["phases"]["stop"]["p95_ns"] == 90_000_000
    assert receipt["slo"]["passed"] is True
    assert receipt["slo"]["failures"] == []
    assert receipt["slo"]["scales"]["1k"]["passed"] is True
    assert receipt["slo"]["scales"]["1k"]["budgets_ms"]["stop"] == 1_000
    assert receipt["hard_gate"]["passed"] is True


def test_scorecard_slo_accepts_exact_append_percentile_boundaries() -> None:
    # Given: 30 on/off samples whose journal append phases meet both boundaries.
    from eval.provenance_bench_metrics import evaluate_scorecard_slo

    phases = _green_scorecard_phases()
    baseline = _stats(
        _StatsSpec(p50_ns=0, p95_ns=0, p99_ns=0, max_ns=0)
    )
    boundary = _stats(
        _StatsSpec(p95_ns=100_000_000, p99_ns=250_000_000, max_ns=250_000_000)
    )
    for phase_name in ("gate_block_scorecard", "r1_block_scorecard"):
        phases[phase_name] = {"off": baseline, "on": boundary}

    # When: the public Scorecard hard gate evaluates the measurements.
    result = evaluate_scorecard_slo(phases)

    # Then: p95 <= 100ms and p99 <= 250ms are inclusive.
    assert result == SloResult(True, ())


@pytest.mark.parametrize(
    ("phase_name", "stats", "failure"),
    (
        (
            "gate_block_scorecard",
            _stats(_StatsSpec(p95_ns=100_000_001)),
            "gate_block_scorecard_p95",
        ),
        (
            "r1_block_scorecard",
            _stats(_StatsSpec(p99_ns=250_000_001, max_ns=250_000_001)),
            "r1_block_scorecard_p99",
        ),
    ),
)
def test_scorecard_slo_rejects_append_percentile_over_budget(
    phase_name: str,
    stats: PhaseStats,
    failure: str,
) -> None:
    # Given: one append phase exceeds one required percentile boundary.
    from eval.provenance_bench_metrics import evaluate_scorecard_slo

    phases = _green_scorecard_phases()
    phases[phase_name]["off"] = _stats(
        _StatsSpec(p50_ns=0, p95_ns=0, p99_ns=0, max_ns=0)
    )
    phases[phase_name]["on"] = stats

    # When: the public Scorecard hard gate evaluates the measurements.
    result = evaluate_scorecard_slo(phases)

    # Then: the precise percentile breach blocks the hard gate.
    assert result.passed is False
    assert result.failures == (failure,)


@pytest.mark.parametrize(
    ("changed", "failure"),
    (
        (
            _StatsSpec(hash_calls=5, stat_count=12, full_scan_count=2),
            "stop_allow_scorecard_new_hash",
        ),
        (
            _StatsSpec(hash_calls=4, stat_count=13, full_scan_count=2),
            "stop_allow_scorecard_new_stat",
        ),
        (
            _StatsSpec(hash_calls=4, stat_count=12, full_scan_count=3),
            "stop_allow_scorecard_new_scan",
        ),
    ),
)
def test_stop_allow_scorecard_slo_rejects_new_scan_stat_or_hash_work(
    changed: _StatsSpec,
    failure: str,
) -> None:
    # Given: Scorecard-on adds one forbidden operation over the same off baseline.
    from eval.provenance_bench_metrics import evaluate_scorecard_slo

    phases = _green_scorecard_phases()
    phases["stop_allow_scorecard"]["on"] = _stats(changed)

    # When: the public Scorecard hard gate compares the A/B pair.
    result = evaluate_scorecard_slo(phases)

    # Then: new scan/stat/hash work blocks the hard gate.
    assert result.passed is False
    assert result.failures == (failure,)


def test_stop_allow_scorecard_slo_rejects_any_enabled_journal_read() -> None:
    # Given: the enabled Stop allow arm reads the scorecard journal once.
    from eval.provenance_bench_metrics import evaluate_scorecard_slo

    phases = _green_scorecard_phases()
    phases["stop_allow_scorecard"]["on"] = replace(
        phases["stop_allow_scorecard"]["on"], journal_read_count=1
    )

    # When: the public Scorecard hard gate evaluates the on arm.
    result = evaluate_scorecard_slo(phases)

    # Then: any journal read blocks the Stop allow hard gate.
    assert result.passed is False
    assert result.failures == ("stop_allow_scorecard_new_journal_read",)


def test_scorecard_measurement_counts_read_text_and_open_journal_reads(
    tmp_path: Path,
) -> None:
    # Given: a scorecard journal read through each supported Path API.
    from eval import provenance_bench_scorecard as bench

    journal = tmp_path / ".fable-lite" / "scorecard" / "gates.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text("{}\n", encoding="utf-8")

    def read_with_open() -> JsonObject:
        with journal.open(encoding="utf-8") as handle:
            return {"content": handle.read()}

    # When: each read is measured independently.
    read_text = bench._measure_action(
        lambda: {"content": journal.read_text(encoding="utf-8")}
    )
    opened = bench._measure_action(read_with_open)

    # Then: both journal access paths are visible to the hard gate.
    assert read_text.journal_read_count > 0
    assert opened.journal_read_count > 0


def test_scorecard_slo_requires_all_three_phases_and_both_ab_arms() -> None:
    # Given: one required phase is absent and another lacks its on arm.
    from eval.provenance_bench_metrics import evaluate_scorecard_slo

    phases = _green_scorecard_phases()
    del phases["r1_block_scorecard"]
    del phases["gate_block_scorecard"]["on"]

    # When: the public Scorecard hard gate validates the result shape.
    result = evaluate_scorecard_slo(phases)

    # Then: incomplete phase and A/B evidence cannot pass.
    assert result.passed is False
    assert result.failures == (
        "gate_block_scorecard_missing_on",
        "missing_r1_block_scorecard",
    )


def test_scorecard_slo_requires_thirty_measurements_per_ab_arm() -> None:
    # Given: one A/B arm contains only 29 measured samples.
    from eval.provenance_bench_metrics import evaluate_scorecard_slo

    phases = _green_scorecard_phases()
    phases["gate_block_scorecard"]["on"] = replace(
        phases["gate_block_scorecard"]["on"], sample_count=29
    )

    # When: the public Scorecard hard gate validates the measurement count.
    result = evaluate_scorecard_slo(phases)

    # Then: warmups are excluded and exactly 30 measurements remain required.
    assert result.passed is False
    assert result.failures == ("gate_block_scorecard_on_measurements",)


def test_r1_benchmark_off_has_no_new_owner_lock_and_on_has_exactly_one(
    tmp_path: Path,
) -> None:
    from eval import provenance_bench_scorecard as bench

    payload = bench._identity(tmp_path, "r1-block", "test", 0) | {
        "tool_name": "Edit",
        "file_paths": ["migrations/001_scorecard.sql"],
        "prompt": "DB migration change",
    }
    acquisitions = 0

    @contextmanager
    def counted_transaction(_root: str) -> Iterator[None]:
        nonlocal acquisitions
        acquisitions += 1
        yield

    with (
        patch.object(bench.contract, "ledger_transaction", counted_transaction),
        patch.object(bench.contract, "_record_r1_scorecard", return_value=False),
    ):
        _ = bench._r1_action(payload, enabled=False)
        assert acquisitions == 0
        _ = bench._r1_action(payload, enabled=True)

    assert acquisitions == 1
