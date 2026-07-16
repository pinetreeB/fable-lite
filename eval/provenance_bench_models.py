from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .provenance_bench_metrics import PhaseStats, SloResult


@dataclass(frozen=True, slots=True)
class FileSpec:
    relative: Path
    size: int
    band: str
    internationalized: bool


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    git: bool
    dirty_files: int
    incomplete: bool
    content_read_bytes: int
    hash_calls: int


@dataclass(frozen=True, slots=True)
class ScaleResult:
    file_count: int
    total_bytes: int
    warmups: int
    measurements: int
    phases: dict[str, PhaseStats]
    scenarios: tuple[ScenarioResult, ...]
    ledger_valid: bool


@dataclass(frozen=True, slots=True)
class StressResult:
    requested: bool
    incomplete: bool
    reason: str
    ledger_valid: bool


@dataclass(frozen=True, slots=True)
class ScorecardBenchResult:
    warmups: int
    measurements: int
    phases: dict[str, dict[str, PhaseStats]]
    hard_gate: SloResult


@dataclass(frozen=True, slots=True)
class AttributionScenarioResult:
    path_attribution: int
    peer_exemption: bool
    warmups: int
    measurements: int
    stop: PhaseStats
    decision_counts: dict[str, int]
    atomic_write_count: int
    ledger_valid: bool


@dataclass(frozen=True, slots=True)
class AttributionBenchResult:
    scenarios: tuple[AttributionScenarioResult, ...]
    budget_ms: int
    measure_only: bool
    passed: bool
    failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BenchResult:
    scales: tuple[ScaleResult, ...]
    slo: SloResult
    scale_slos: dict[int, SloResult]
    stress: StressResult
    scorecard: ScorecardBenchResult | None = None
    attribution: AttributionBenchResult | None = None
