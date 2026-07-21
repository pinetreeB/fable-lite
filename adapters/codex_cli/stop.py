from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Final


REAPER_ENABLE_ENV: Final = "SMTW_CODEX_REAPER"
REAPER_LOG_ENV: Final = "SMTW_CODEX_REAPER_LOG"
REAPER_TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})
REAPER_TIMEOUT_SECONDS: Final = 8


def _fail_open(message: str) -> int:
    data = json.dumps(
        {"systemMessage": f"[smtw] fail-open: {message}"}, ensure_ascii=False
    )
    _ = sys.stdout.buffer.write(data.encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def _append_reaper_launcher_error(
    log_path: Path, error_type: str, message: str
) -> None:
    entry = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "event": "codex_process_reaper",
        "status": "error",
        "error_type": error_type,
        "error": message[:500],
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            _ = handle.write(
                json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
            )
    except OSError:
        return


def _run_process_reaper(repo_root: Path, project_root: str) -> None:
    if os.name != "nt":
        return
    from core.runtime_env import (
        CODEX_REAPER,
        CODEX_REAPER_DRY_RUN,
        CODEX_REAPER_LOG,
        CODEX_REAPER_POWERSHELL,
        canonical_env_key,
        legacy_env_key,
        resolve_smtw_env,
    )

    suffixes = (
        CODEX_REAPER,
        CODEX_REAPER_LOG,
        CODEX_REAPER_DRY_RUN,
        CODEX_REAPER_POWERSHELL,
    )
    resolved = {
        suffix: resolve_smtw_env(suffix)
        for suffix in suffixes
    }
    enabled = resolved[CODEX_REAPER].value
    if enabled is None or enabled.strip().casefold() not in REAPER_TRUE_VALUES:
        return
    configured_log = resolved[CODEX_REAPER_LOG].value
    if configured_log is not None:
        log_path = Path(configured_log)
    else:
        from core.state_layout import state_dir

        log_path = state_dir(project_root) / "codex-process-reaper.log"
    env = os.environ.copy()
    for suffix, value in resolved.items():
        canonical_key = canonical_env_key(suffix)
        env.pop(canonical_key, None)
        env.pop(legacy_env_key(suffix), None)
        if value.value is not None:
            env[canonical_key] = value.value
    env[canonical_env_key(CODEX_REAPER_LOG)] = str(log_path)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "contrib.codex_process_reaper.reaper"],
            check=False,
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=REAPER_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _append_reaper_launcher_error(log_path, type(exc).__name__, str(exc))
        return
    if completed.returncode != 0:
        _append_reaper_launcher_error(
            log_path,
            "ReaperExitError",
            f"reaper exited with code {completed.returncode}",
        )


def main() -> int:
    try:
        root = Path(__file__).resolve().parents[2]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from adapters.codex_cli.common import (
            canonical_invocation,
            emit,
            last_assistant_text,
            project_root,
            read_payload,
        )
        from core.adapter_observation import finish_turn, resolve_active_invocation, restart_blocked_turn
        from core.verify_state import evaluate_stop

        payload = read_payload()
        project_root_value = project_root(payload)
        invocation = canonical_invocation(payload, "stop", "other", [], "", True, "")
        invocation = resolve_active_invocation(Path(project_root_value), invocation)
        _ = finish_turn(Path(project_root_value), invocation)
        stop_payload = {
            "project_root": project_root_value,
            "stop_hook_active": payload.get("stop_hook_active") is True,
            "assistant_text": last_assistant_text(payload),
            "host": invocation.host,
            "agent": invocation.agent,
            "session_id": invocation.session_id,
            "turn_id": invocation.turn_id,
            "attribution": invocation.scorecard_attribution,
        }
        result = evaluate_stop(stop_payload)
        if result["decision"] == "block":
            restart_blocked_turn(Path(project_root_value), invocation)
            return emit({"decision": "block", "reason": str(result["reason"])})
        message = str(result.get("message", "[smtw] Stop gate allow."))
        _run_process_reaper(root, project_root_value)
        return emit({"systemMessage": message})
    except Exception as exc:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        return _fail_open(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
