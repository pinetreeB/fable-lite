from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Final


PROTECTION_WINDOW: Final = timedelta(minutes=5)
MCP_COMMAND_RE: Final = re.compile(
    "|".join(
        (
            r"context7-mcp",
            r"codegraph",
            r"chrome-devtools-mcp",
            r"@modelcontextprotocol[\\/]server-memory",
            r"mcp-bundle[\\/]index\.js",
            r"lsp-daemon",
            r"git-bash-mcp",
            r"sisyphuslabs[\\/]omo",
        )
    ),
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ProcessRecord:
    pid: int
    parent_pid: int
    name: str
    command_line: str
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class ReapDecision:
    session_pid: int | None
    protect_until: datetime | None
    scoped_candidate_pids: tuple[int, ...]
    protected_pids: tuple[int, ...]
    target_pids: tuple[int, ...]
    termination_pids: tuple[int, ...]
    outside_scope_candidate_pids: tuple[int, ...]


def is_reaper_candidate(process: ProcessRecord) -> bool:
    name = process.name.casefold()
    return name == "node_repl.exe" or (
        name == "node.exe" and MCP_COMMAND_RE.search(process.command_line) is not None
    )


def find_nearest_codex_pid(
    processes: tuple[ProcessRecord, ...],
    start_pid: int,
) -> int | None:
    by_pid = {process.pid: process for process in processes}
    current_pid = start_pid
    visited: set[int] = set()
    while current_pid not in visited:
        visited.add(current_pid)
        current = by_pid.get(current_pid)
        if current is None:
            return None
        if current.name.casefold() == "codex.exe":
            return current.pid
        current_pid = current.parent_pid
    return None


def _belongs_to_session(
    process: ProcessRecord,
    session_pid: int,
    by_pid: dict[int, ProcessRecord],
) -> bool:
    current_pid = process.parent_pid
    visited: set[int] = set()
    while current_pid not in visited:
        if current_pid == session_pid:
            return True
        visited.add(current_pid)
        current = by_pid.get(current_pid)
        if current is None:
            return False
        current_pid = current.parent_pid
    return False


def _termination_roots(
    targets: tuple[ProcessRecord, ...],
    by_pid: dict[int, ProcessRecord],
) -> tuple[int, ...]:
    target_pids = {process.pid for process in targets}
    roots: list[int] = []
    for process in targets:
        current_pid = process.parent_pid
        visited: set[int] = set()
        has_target_ancestor = False
        while current_pid not in visited:
            if current_pid in target_pids:
                has_target_ancestor = True
                break
            visited.add(current_pid)
            current = by_pid.get(current_pid)
            if current is None:
                break
            current_pid = current.parent_pid
        if not has_target_ancestor:
            roots.append(process.pid)
    return tuple(sorted(roots))


def select_reap_decision(
    processes: tuple[ProcessRecord, ...],
    hook_pid: int,
    protection_window: timedelta = PROTECTION_WINDOW,
) -> ReapDecision:
    session_pid = find_nearest_codex_pid(processes, hook_pid)
    candidates = tuple(process for process in processes if is_reaper_candidate(process))
    if session_pid is None:
        return ReapDecision(
            None, None, (), (), (), (), tuple(sorted(p.pid for p in candidates))
        )

    by_pid = {process.pid: process for process in processes}
    # Ownership invariant: targets must descend from this hook's nearest codex.exe.
    # Other Codex/Claude/agy panes have different parent roots, so they are structurally untargetable.
    scoped = tuple(
        process
        for process in candidates
        if _belongs_to_session(process, session_pid, by_pid)
    )
    scoped_ids = {process.pid for process in scoped}
    outside = tuple(
        sorted(process.pid for process in candidates if process.pid not in scoped_ids)
    )
    known_creation = tuple(
        process.created_at for process in scoped if process.created_at is not None
    )
    if not known_creation:
        protected = scoped
        targets: tuple[ProcessRecord, ...] = ()
        protect_until = None
    else:
        protect_until = min(known_creation) + protection_window
        protected = tuple(
            process
            for process in scoped
            if process.created_at is None or process.created_at <= protect_until
        )
        protected_ids = {process.pid for process in protected}
        targets = tuple(
            process for process in scoped if process.pid not in protected_ids
        )

    return ReapDecision(
        session_pid=session_pid,
        protect_until=protect_until,
        scoped_candidate_pids=tuple(sorted(process.pid for process in scoped)),
        protected_pids=tuple(sorted(process.pid for process in protected)),
        target_pids=tuple(sorted(process.pid for process in targets)),
        termination_pids=_termination_roots(targets, by_pid),
        outside_scope_candidate_pids=outside,
    )
