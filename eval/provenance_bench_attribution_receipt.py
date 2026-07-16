from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from core.ledger_schema import JsonObject, JsonValue

from .provenance_bench_models import (
    AttributionBenchResult,
    AttributionScenarioResult,
)


def attribution_value(result: AttributionBenchResult) -> JsonObject:
    return {
        "measure_only": result.measure_only,
        "budget_ms": result.budget_ms,
        "verdict": "PASS" if result.passed else "FAIL",
        "failures": list(result.failures),
        "scenarios": [_scenario_value(scenario) for scenario in result.scenarios],
    }


def write_attribution_receipt(path: Path, result: AttributionBenchResult) -> None:
    payload: JsonObject = {
        "schema_version": 1,
        "suite": "multiagent-attribution-stop",
        "platform": os.name,
    } | attribution_value(result)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", delete=False, dir=path.parent
    ) as handle:
        _ = handle.write(encoded)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _scenario_value(scenario: AttributionScenarioResult) -> dict[str, JsonValue]:
    return {
        "path_attribution": scenario.path_attribution,
        "peer_exemption": scenario.peer_exemption,
        "warmups": scenario.warmups,
        "measurements": scenario.measurements,
        "stop": asdict(scenario.stop),
        "decision_counts": scenario.decision_counts,
        "atomic_write_count": scenario.atomic_write_count,
        "ledger_valid": scenario.ledger_valid,
    }
