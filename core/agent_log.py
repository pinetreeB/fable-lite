from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import re
import threading
import time

from . import file_lock as _file_lock
from .ledger_schema import JsonObject, JsonValue
from .runtime_env import (
    TEST_LOCK_WAIT_SECONDS,
    canonical_env_key,
    smtw_env,
)
from .state_layout import state_dir

LOCK_WAIT_SECONDS = 15.0
STALE_LOCK_SECONDS = _file_lock.STALE_LOCK_SECONDS
TEST_LOCK_WAIT_ENV = canonical_env_key(TEST_LOCK_WAIT_SECONDS)

# Keep the long-standing private names available for compatibility while the lock
# implementation itself lives in core.file_lock.
_owned_lock = _file_lock._owned_lock
_posix_guard = _file_lock._posix_guard
_read_lock_record = _file_lock._read_lock_record
_record_pid = _file_lock._record_pid
_pid_is_alive = _file_lock._pid_is_alive
_stale_record = _file_lock._stale_record
_unlink_matching_record = _file_lock._unlink_matching_record
_windows_pid_is_alive = _file_lock._windows_pid_is_alive


class _LedgerTransaction:
    """Opaque proof that the root ledger lock is active in this call stack."""

    __slots__ = ("_active", "_pid", "_root", "_thread_id")

    def __init__(self, root: Path) -> None:
        self._root = root
        self._active = True
        self._pid = os.getpid()
        self._thread_id = threading.get_ident()

    def assert_active_for(self, project_root: str) -> None:
        if not self._active:
            raise RuntimeError("ledger transaction is no longer active")
        if os.getpid() != self._pid or threading.get_ident() != self._thread_id:
            raise RuntimeError("ledger transaction cannot cross a process or thread boundary")
        if Path(project_root).resolve() != self._root:
            raise RuntimeError("ledger transaction belongs to a different project root")

    def _deactivate(self) -> None:
        self._active = False


def _safe_agent_name(agent: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", agent).strip(".-") or "agent"


def agent_log_path(project_root: str, agent: str) -> Path:
    return state_dir(project_root) / "agents" / f"{_safe_agent_name(agent)}.jsonl"


@contextmanager
def ledger_transaction(
    project_root: str,
    *,
    lock_wait_seconds: float | None = None,
    release_wait_seconds: float | None = None,
) -> Iterator[_LedgerTransaction]:
    """Hold the root ledger lock; zero wait makes one immediate acquire attempt."""
    root = Path(project_root).resolve()
    directory = state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "ledger.lock"
    wait_seconds = (
        _lock_wait_seconds()
        if lock_wait_seconds is None
        else _explicit_nonnegative_seconds(lock_wait_seconds, "lock_wait_seconds")
    )
    release_seconds = (
        1.0
        if release_wait_seconds is None
        else _explicit_nonnegative_seconds(
            release_wait_seconds,
            "release_wait_seconds",
        )
    )
    deadline = time.monotonic() + wait_seconds
    transaction = _LedgerTransaction(root)
    if os.name == "posix":
        with _posix_guard(directory / "ledger.guard", deadline):
            with _owned_lock(
                lock_path,
                deadline,
                release_wait_seconds=release_seconds,
            ):
                try:
                    yield transaction
                finally:
                    transaction._deactivate()
    else:
        with _owned_lock(
            lock_path,
            deadline,
            release_wait_seconds=release_seconds,
        ):
            try:
                yield transaction
            finally:
                transaction._deactivate()


def _lock_wait_seconds() -> float:
    test_value = smtw_env(TEST_LOCK_WAIT_SECONDS)
    if test_value is None:
        return LOCK_WAIT_SECONDS
    try:
        wait_seconds = float(test_value)
    except ValueError:
        return LOCK_WAIT_SECONDS
    return wait_seconds if wait_seconds > 0 else LOCK_WAIT_SECONDS


def _explicit_nonnegative_seconds(value: float, field: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return float(value)


def _json_safe(value: JsonValue) -> bool:
    if isinstance(value, str | int | float | bool) or value is None:
        return True
    if isinstance(value, list):
        return all(_json_safe(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_safe(item) for key, item in value.items())
    return False


def _json_value(value: JsonValue) -> JsonValue:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return None


def _event_payload(payload: Mapping[str, JsonValue]) -> JsonObject:
    return {
        key: _json_value(value)
        for key, value in payload.items()
        if _json_safe(value)
    }


def _normalize_agent_event(event: JsonObject) -> JsonObject:
    if event.get("schema_version") == 2:
        return event
    event["legacy_event"] = True
    return event


def append_agent_event(
    project_root: str,
    agent: str,
    payload: Mapping[str, JsonValue],
) -> None:
    if not agent:
        return
    path = agent_log_path(project_root, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = _event_payload(payload)
    event["agent"] = agent
    event["timestamp"] = datetime.now(UTC).isoformat()
    try:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            _ = handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
            _ = handle.write("\n")
    except OSError:
        return


def load_agent_events(project_root: str, agent: str) -> list[JsonObject] | None:
    path = agent_log_path(project_root, agent)
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    events: list[JsonObject] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            raw: JsonValue = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _json_safe(raw):
            event = _json_value(raw)
            if isinstance(event, dict):
                events.append(_normalize_agent_event(event))
    return events
