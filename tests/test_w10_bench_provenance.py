from __future__ import annotations

from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import time
from unittest.mock import patch

from eval.bench_provenance import (
    SCALE_TARGET_BYTES,
    synthetic_plan,
)
from eval.provenance_bench_metrics import (
    PhaseMeasurement,
    evaluate_slo,
    measure,
    summarize_phase,
)
from core.provenance_lifecycle import ProvenanceLifecycle
from core.provenance_progress import scan_progress
from core.provenance_store import save_workspace_current


def test_synthetic_plan_preserves_w10_distribution_and_path_mix() -> None:
    # Given: the fixed 1k W10 synthetic-repository target.
    plan = synthetic_plan(1_000, SCALE_TARGET_BYTES[1_000], seed=20260712)

    # When: the benchmark assigns file sizes and paths.
    bands = Counter(spec.band for spec in plan)

    # Then: exact count, byte budget, distribution, and 10% internationalized paths are retained.
    assert len(plan) == 1_000
    assert sum(spec.size for spec in plan) == SCALE_TARGET_BYTES[1_000]
    assert bands == {"small": 900, "medium": 90, "large": 10}
    assert sum(spec.internationalized for spec in plan) == 100


def test_slo_requires_clean_fast_path_zero_reads_and_budgeted_percentiles() -> None:
    # Given: four phase samples that meet the W10 p95 and memory budgets.
    phases = {
        "fast_path": summarize_phase((PhaseMeasurement(100_000_000, 0, 0, 30_000, 1, 1, False, False),) * 30),
        "cold_start": summarize_phase((PhaseMeasurement(900_000_000, 1, 1, 50_000, 1, 1, False, True),) * 30),
        "post_tool": summarize_phase((PhaseMeasurement(150_000_000, 0, 0, 30_000, 1, 1, False, False),) * 30),
        "stop": summarize_phase((PhaseMeasurement(900_000_000, 1, 1, 50_000, 1, 1, False, True),) * 30),
    }

    # When: SLO evaluation sees a clean fast path.
    green = evaluate_slo(phases, 79 * 1024 * 1024)
    dirty_fast = dict(phases)
    dirty_fast["fast_path"] = summarize_phase(
        (PhaseMeasurement(100_000_000, 1, 1, 30_000, 1, 1, False, False),) * 30
    )

    # Then: only the zero-read fast path is release-green.
    assert green.passed is True
    assert evaluate_slo(dirty_fast, 79 * 1024 * 1024).passed is False


def test_rev3_10k_slo_uses_extreme_scale_budgets() -> None:
    # Given: a 10k result inside the rev3 1s metadata and 6s full-scan budgets.
    phases = {
        "fast_path": summarize_phase((PhaseMeasurement(900_000_000, 0, 0, 10_000, 1, 1, False, False),) * 30),
        "cold_start": summarize_phase((PhaseMeasurement(5_500_000_000, 1, 1, 40_000, 1, 1, False, True),) * 30),
        "post_tool": summarize_phase((PhaseMeasurement(900_000_000, 0, 0, 10_000, 1, 1, False, False),) * 30),
        "stop": summarize_phase((PhaseMeasurement(5_500_000_000, 1, 1, 40_000, 1, 1, False, True),) * 30),
    }

    # Then: 10k passes while the same measurements remain invalid for representative 1k.
    assert evaluate_slo(phases, 79 * 1024 * 1024, 10_000).passed is True
    assert evaluate_slo(phases, 79 * 1024 * 1024, 1_000).passed is False


def test_scan_progress_uses_stderr_without_corrupting_hook_stdout() -> None:
    # Given: a scan that exceeds the 500ms production threshold at a shortened test delay.
    stdout = StringIO()
    stderr = StringIO()

    # When: the progress timer fires.
    with redirect_stdout(stdout), redirect_stderr(stderr), scan_progress(10_000, 0.01):
        time.sleep(0.03)

    # Then: hook stdout remains JSON-safe and the user sees a reassuring file-count message.
    assert stdout.getvalue() == ""
    assert "[smtw] 10,000개 파일 상태 검증 중" in stderr.getvalue()


def test_clean_turn_baseline_atomically_links_workspace_current(tmp_path: Path) -> None:
    # Given: a workspace-current snapshot that was just fully reconciled.
    (tmp_path / "app.py").write_text("print('v2')\n", encoding="utf-8")
    lifecycle = ProvenanceLifecycle(tmp_path)
    _ = lifecycle.start_turn("bench", "first")
    _ = lifecycle.finish_turn("bench", "first")

    # When: a clean fast-path turn starts.
    with patch("core.provenance_manifest.save_workspace_current", wraps=save_workspace_current) as save:
        result = lifecycle.start_turn("bench", "second")
    baseline = lifecycle.turn_baseline_path("bench", "second")

    # Then: its persisted baseline atomically reuses the unchanged current snapshot bytes.
    assert result.full_scan is False
    assert save.call_count == 0
    assert os.path.samefile(lifecycle.workspace_current_path, baseline)


def test_measure_wraps_deadline_aware_hashing(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("print('v2')\n", encoding="utf-8")
    lifecycle = ProvenanceLifecycle(tmp_path)

    result, measurement = measure(lambda: lifecycle.start_turn("bench", "deadline"))

    assert result.incomplete is False
    assert measurement.hash_calls == 1
    assert measurement.content_read_bytes > 0
