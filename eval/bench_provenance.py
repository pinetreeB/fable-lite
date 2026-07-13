from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
from typing import Final

from core.ledger import ledger_path, load_ledger, record_event
from core.provenance_lifecycle import ProvenanceLifecycle

from .provenance_bench_metrics import MIB, PhaseMeasurement, PhaseStats, SloResult, evaluate_slo, measure, measure_memory, summarize_phase
from .provenance_bench_models import BenchResult, FileSpec, ScaleResult, ScenarioResult, StressResult
from .provenance_bench_receipt import write_receipt
from .provenance_bench_scorecard import run_scorecard_benchmark

SCALE_TARGET_BYTES: Final = {1_000: 256 * MIB // 10, 10_000: 256 * MIB, 50_000: 2 * 1024 * MIB}
DEFAULT_OUTPUT: Final = Path(__file__).resolve().parent / "results" / "bench-latest.json"
BENCH_TMP_DIR: Final = Path(__file__).resolve().parent.parent / "tmp"


def synthetic_plan(file_count: int, target_bytes: int, seed: int) -> tuple[FileSpec, ...]:
    bands = (("small", file_count * 90 // 100, 1_024, 8 * 1_024), ("medium", file_count * 9 // 100, 64 * 1_024, 256 * 1_024))
    large_count = file_count - sum(count for _, count, _, _ in bands)
    limits = bands + (("large", large_count, MIB, 8 * MIB),)
    minimum = sum(count * low for _, count, low, _ in limits)
    capacity = sum(count * (high - low) for _, count, low, high in limits)
    if not minimum <= target_bytes <= minimum + capacity:
        raise ValueError("target bytes are outside the configured distribution")
    sizes = [low for _, count, low, _ in limits for _ in range(count)]
    headroom = [high - low for _, count, low, high in limits for _ in range(count)]
    remaining = target_bytes - minimum
    for index, room in enumerate(headroom):
        allocated = remaining * room // capacity
        sizes[index] += allocated
        remaining -= allocated
        capacity -= room
    randomizer = random.Random(seed)
    candidates = [index for index, room in enumerate(headroom) if sizes[index] < sizes[index] + room]
    randomizer.shuffle(candidates)
    for index in candidates[:remaining]:
        sizes[index] += 1
    expanded_bands = [name for name, count, _, _ in limits for _ in range(count)]
    return tuple(_file_spec(index, sizes[index], expanded_bands[index]) for index in range(file_count))


def run_benchmark(stress: bool, seed: int) -> BenchResult:
    BENCH_TMP_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fable-provenance-bench-", dir=BENCH_TMP_DIR) as raw:
        root = Path(raw)
        scorecard = run_scorecard_benchmark(root / "scorecard")
        small = _run_scale(root / "1k", 1_000, 5, 30, seed, False)
        standard = _run_scale(root / "10k", 10_000, 5, 30, seed, True)
        scale_slos = {
            scale.file_count: evaluate_slo(
                scale.phases,
                max(stats.rss_peak_bytes for stats in scale.phases.values()),
                scale.file_count,
            )
            for scale in (small, standard)
        }
        failures = tuple(f"{count}:{failure}" for count, result in scale_slos.items() for failure in result.failures)
        slo = SloResult(all(result.passed for result in scale_slos.values()), failures)
        stress_result = _run_stress(root / "50k", seed) if stress else StressResult(False, False, "not_requested", True)
    return BenchResult((small, standard), slo, scale_slos, stress_result, scorecard)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark provenance lifecycle SLOs.")
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    options = parser.parse_args(argv)
    result = run_benchmark(options.stress, options.seed)
    write_receipt(options.output, result, options.seed)
    scorecard_passed = result.scorecard is not None and result.scorecard.hard_gate.passed
    passed = result.slo.passed and scorecard_passed
    print(
        f"provenance bench slo={'PASS' if result.slo.passed else 'FAIL'} "
        f"scorecard={'PASS' if scorecard_passed else 'FAIL'} stress={result.stress.reason}"
    )
    return 0 if passed else 1


def _file_spec(index: int, size: int, band: str) -> FileSpec:
    internationalized = index % 10 == 0
    depth = 1 + index % 8
    route = index % 8
    segments = [f"d{level}-{route}" for level in range(depth)]
    if internationalized:
        segments[0] = "공백 경로"
        segments[-1] = "유니코드-Δ"
        name = f"한글 파일 {index}.bin"
    else:
        name = f"file-{index}.bin"
    return FileSpec(Path(*segments) / name, size, band, internationalized)


def _run_scale(root: Path, file_count: int, warmups: int, measurements: int, seed: int, scenarios: bool) -> ScaleResult:
    plan = synthetic_plan(file_count, SCALE_TARGET_BYTES[file_count], seed)
    cold_root = root.with_name(f"{root.name}-cold")
    record_event({"project_root": str(root), "event": "scope_warning", "message": "benchmark"})
    _populate(root, plan)
    record_event({"project_root": str(cold_root), "event": "scope_warning", "message": "benchmark"})
    _populate(cold_root, plan)
    phases: dict[str, list[PhaseMeasurement]] = {"fast_path": [], "cold_start": [], "post_tool": [], "stop": []}
    lifecycle = _stopped_lifecycle(root, "initial")
    for index in range(warmups + measurements):
        _drop_snapshots(cold_root)
        cold = ProvenanceLifecycle(cold_root)
        _, cold_measurement = measure(lambda: cold.start_turn("bench", f"cold-{index}"))
        _, fast = measure(lambda: lifecycle.start_turn("bench", f"turn-{index}"))
        invocation = lifecycle.begin_invocation("bench", f"turn-{index}", f"invoke-{index}", ())
        _, post = measure(lambda: lifecycle.post_tool(invocation))
        _, stop = measure(lambda: lifecycle.finish_turn("bench", f"turn-{index}"))
        if index >= warmups:
            phases["cold_start"].append(cold_measurement)
            phases["fast_path"].append(fast)
            phases["post_tool"].append(post)
            phases["stop"].append(stop)
    coverage = _scenario_coverage(root, plan) if scenarios else ()
    return ScaleResult(
        file_count,
        sum(spec.size for spec in plan),
        warmups,
        measurements,
        _with_memory_probe(root, cold_root, phases),
        coverage,
        _ledger_valid(root),
    )


def _with_memory_probe(root: Path, cold_root: Path, phases: dict[str, list[PhaseMeasurement]]) -> dict[str, PhaseStats]:
    _drop_snapshots(cold_root)
    cold = ProvenanceLifecycle(cold_root)
    _, cold_peak, cold_rss = measure_memory(lambda: cold.start_turn("bench", "memory-cold"))
    lifecycle = _stopped_lifecycle(root, "memory-seed")
    _, fast_peak, fast_rss = measure_memory(lambda: lifecycle.start_turn("bench", "memory-turn"))
    invocation = lifecycle.begin_invocation("bench", "memory-turn", "memory-invoke", ())
    _, post_peak, post_rss = measure_memory(lambda: lifecycle.post_tool(invocation))
    _, stop_peak, stop_rss = measure_memory(lambda: lifecycle.finish_turn("bench", "memory-turn"))
    peaks = {
        "cold_start": (cold_peak, cold_rss),
        "fast_path": (fast_peak, fast_rss),
        "post_tool": (post_peak, post_rss),
        "stop": (stop_peak, stop_rss),
    }
    return {
        name: replace(summarize_phase(tuple(items)), tracemalloc_peak_bytes=peaks[name][0], rss_peak_bytes=max(summarize_phase(tuple(items)).rss_peak_bytes, peaks[name][1]))
        for name, items in phases.items()
    }


def _stopped_lifecycle(root: Path, suffix: str) -> ProvenanceLifecycle:
    _drop_snapshots(root)
    lifecycle = ProvenanceLifecycle(root)
    _ = lifecycle.start_turn("bench", suffix)
    _ = lifecycle.finish_turn("bench", suffix)
    return lifecycle


def _scenario_coverage(root: Path, plan: tuple[FileSpec, ...]) -> tuple[ScenarioResult, ...]:
    results: list[ScenarioResult] = []
    for git in (False, True):
        if git:
            subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True, text=True)
        for dirty_files in (0, 1, 100):
            lifecycle = _stopped_lifecycle(root, f"coverage-{git}-{dirty_files}")
            changed = _dirty(root, plan[:dirty_files])
            result, measurement = measure(lambda: lifecycle.start_turn("bench", f"coverage-turn-{git}-{dirty_files}"))
            _ = lifecycle.finish_turn("bench", f"coverage-turn-{git}-{dirty_files}")
            _restore(root, changed)
            results.append(ScenarioResult(git, dirty_files, result.incomplete, measurement.content_read_bytes, measurement.hash_calls))
    return tuple(results)


def _run_stress(root: Path, seed: int) -> StressResult:
    try:
        result = _run_scale(root, 50_000, 0, 1, seed, False)
    except (MemoryError, OSError) as exc:
        return StressResult(True, True, type(exc).__name__, _ledger_valid(root))
    deadline = any(stats.max_ns > 2_000_000_000 for stats in result.phases.values())
    return StressResult(True, deadline, "deadline_exceeded" if deadline else "complete", result.ledger_valid)


def _populate(root: Path, plan: tuple[FileSpec, ...]) -> None:
    for index, spec in enumerate(plan):
        path = root / spec.relative
        path.parent.mkdir(parents=True, exist_ok=True)
        block = bytes([index % 251]) * min(spec.size, MIB)
        with path.open("wb") as handle:
            remaining = spec.size
            while remaining:
                count = min(remaining, len(block))
                _ = handle.write(block[:count])
                remaining -= count


def _dirty(root: Path, specs: tuple[FileSpec, ...]) -> tuple[FileSpec, ...]:
    for index, spec in enumerate(specs):
        with (root / spec.relative).open("r+b") as handle:
            _ = handle.write(bytes([(index + 1) % 251]))
    return specs


def _restore(root: Path, specs: tuple[FileSpec, ...]) -> None:
    for index, spec in enumerate(specs):
        with (root / spec.relative).open("r+b") as handle:
            _ = handle.write(bytes([index % 251]))


def _drop_snapshots(root: Path) -> None:
    shutil.rmtree(root / ".fable-lite" / "snapshots", ignore_errors=True)


def _ledger_valid(root: Path) -> bool:
    path = ledger_path(str(root))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(raw, dict) and raw.get("schema_version") == 2 and load_ledger({"project_root": str(root)}).get("schema_version") == 2


if __name__ == "__main__":
    raise SystemExit(main())
