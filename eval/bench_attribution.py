from __future__ import annotations

import argparse
from pathlib import Path
import tempfile
from typing import Final

from .provenance_bench_attribution import run_attribution_benchmark
from .provenance_bench_attribution_receipt import write_attribution_receipt

DEFAULT_OUTPUT: Final = (
    Path(__file__).resolve().parent / "results" / "bench-attribution-latest.json"
)


class _Options(argparse.Namespace):
    output: Path = DEFAULT_OUTPUT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark multi-agent Stop attribution.")
    _ = parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    options = parser.parse_args(argv, namespace=_Options())
    with tempfile.TemporaryDirectory(prefix="fable-attribution-bench-") as raw:
        result = run_attribution_benchmark(Path(raw))
    write_attribution_receipt(options.output, result)
    verdict = "PASS" if result.passed else "FAIL"
    print(f"multiagent attribution Stop measure-only={verdict}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
