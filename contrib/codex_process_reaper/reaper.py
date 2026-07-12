from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import time
from typing import Final, TypeAlias

from .decision import ReapDecision, select_reap_decision
from .windows_runtime import (
    live_process_ids,
    snapshot_processes,
    terminate_process_trees,
)


ENABLE_ENV: Final = "FABLE_LITE_CODEX_REAPER"
LOG_ENV: Final = "FABLE_LITE_CODEX_REAPER_LOG"
DRY_RUN_ENV: Final = "FABLE_LITE_CODEX_REAPER_DRY_RUN"
TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})
LogValue: TypeAlias = str | int | bool | None | list[int]


@dataclass(frozen=True, slots=True)
class ReaperRun:
    decision: ReapDecision
    outside_before_count: int
    after_count: int
    outside_after_count: int
    taskkill_succeeded: bool
    dry_run: bool
    elapsed_ms: int


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in TRUE_VALUES


def _log_path() -> Path:
    configured = os.environ.get(LOG_ENV)
    return (
        Path(configured)
        if configured
        else Path.cwd() / ".fable-lite" / "codex-process-reaper.log"
    )


def _append_log(fields: dict[str, LogValue]) -> None:
    entry: dict[str, LogValue] = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "event": "codex_process_reaper",
        **fields,
    }
    path = _log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            _ = handle.write(
                json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
            )
    except OSError:
        return


def run_reaper() -> ReaperRun:
    started = time.monotonic()
    before = snapshot_processes(os.getpid())
    decision = select_reap_decision(before.records, hook_pid=os.getpid())
    dry_run = _enabled(DRY_RUN_ENV)
    taskkill_succeeded = True
    if decision.session_pid is not None and decision.termination_pids and not dry_run:
        taskkill_succeeded = terminate_process_trees(decision.termination_pids)
    observed_pids = tuple(
        sorted(
            {
                *decision.scoped_candidate_pids,
                *before.outside_candidate_pids,
            }
        )
    )
    live_pids = live_process_ids(observed_pids)
    return ReaperRun(
        decision=decision,
        outside_before_count=len(before.outside_candidate_pids),
        after_count=sum(pid in live_pids for pid in decision.scoped_candidate_pids),
        outside_after_count=sum(
            pid in live_pids for pid in before.outside_candidate_pids
        ),
        taskkill_succeeded=taskkill_succeeded,
        dry_run=dry_run,
        elapsed_ms=round((time.monotonic() - started) * 1000),
    )


def _log_run(result: ReaperRun) -> None:
    decision = result.decision
    _append_log(
        {
            "status": "ok" if decision.session_pid is not None else "skipped",
            "reason": None
            if decision.session_pid is not None
            else "codex_parent_not_found",
            "session_pid": decision.session_pid,
            "before": len(decision.scoped_candidate_pids),
            "protected": len(decision.protected_pids),
            "targets": len(decision.target_pids),
            "termination_roots": list(decision.termination_pids),
            "after": result.after_count,
            "outside_before": result.outside_before_count,
            "outside_after": result.outside_after_count,
            "taskkill_succeeded": result.taskkill_succeeded,
            "dry_run": result.dry_run,
            "elapsed_ms": result.elapsed_ms,
        }
    )


def main() -> int:
    if not _enabled(ENABLE_ENV):
        return 0
    if os.name != "nt":
        _append_log({"status": "skipped", "reason": "windows_only"})
        return 0
    try:
        _log_run(run_reaper())
    except Exception as exc:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        _append_log(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            }
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
