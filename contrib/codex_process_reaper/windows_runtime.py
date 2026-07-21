from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
import os
import subprocess
from typing import Final, override

from core.runtime_env import (
    CODEX_REAPER_POWERSHELL,
    canonical_env_key,
    smtw_env,
)

from .decision import MCP_COMMAND_RE, ProcessRecord


POWERSHELL_ENV: Final = canonical_env_key(CODEX_REAPER_POWERSHELL)
DEFAULT_POWERSHELL: Final = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
SNAPSHOT_TIMEOUT_SECONDS: Final = 6
TASKKILL_TIMEOUT_SECONDS: Final = 6
PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
ERROR_ACCESS_DENIED: Final = 5
ERROR_INVALID_PARAMETER: Final = 87
PROCESS_SNAPSHOT_BODY: Final = "\n".join(
    (
        "$ErrorActionPreference = 'Stop'",
        "$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)",
        "$all = @(Get-CimInstance -Query 'SELECT ProcessId,ParentProcessId,Name,CommandLine,CreationDate FROM Win32_Process')",
        "$byPid = @{}",
        "foreach ($process in $all) { $byPid[[int]$process.ProcessId] = $process }",
        "$candidates = @($all | Where-Object { ($_.Name -eq 'node.exe' -and $_.CommandLine -match $rx) -or $_.Name -eq 'node_repl.exe' })",
        "$candidateIds = @{}",
        "foreach ($candidate in $candidates) { $candidateIds[[int]$candidate.ProcessId] = $true }",
        "$sessionPid = 0",
        "$current = if ($byPid.ContainsKey($hookPid)) { $byPid[$hookPid] } else { $null }",
        "$visited = @{}",
        "while ($current -and -not $visited.ContainsKey([int]$current.ProcessId)) {",
        "  $currentPid = [int]$current.ProcessId",
        "  $visited[$currentPid] = $true",
        "  if ($current.Name -eq 'codex.exe') { $sessionPid = $currentPid; break }",
        "  $parentPid = [int]$current.ParentProcessId",
        "  $current = if ($byPid.ContainsKey($parentPid)) { $byPid[$parentPid] } else { $null }",
        "}",
        "$scoped = @()",
        "$outsidePids = [Collections.Generic.List[int]]::new()",
        "foreach ($candidate in $candidates) {",
        "  $belongs = $false",
        "  $current = $candidate",
        "  $visited = @{}",
        "  while ($current -and -not $visited.ContainsKey([int]$current.ProcessId)) {",
        "    $currentPid = [int]$current.ProcessId",
        "    if ($currentPid -eq $sessionPid -and $sessionPid -ne 0) { $belongs = $true; break }",
        "    $visited[$currentPid] = $true",
        "    $parentPid = [int]$current.ParentProcessId",
        "    $current = if ($byPid.ContainsKey($parentPid)) { $byPid[$parentPid] } else { $null }",
        "  }",
        "  if ($belongs) { $scoped += $candidate } else { $outsidePids.Add([int]$candidate.ProcessId) }",
        "}",
        "$emit = @{}",
        "$seeds = @($scoped)",
        "if ($byPid.ContainsKey($hookPid)) { $seeds += $byPid[$hookPid] }",
        "foreach ($seed in $seeds) {",
        "  $current = $seed",
        "  $visited = @{}",
        "  while ($current -and -not $visited.ContainsKey([int]$current.ProcessId)) {",
        "    $currentPid = [int]$current.ProcessId",
        "    $visited[$currentPid] = $true",
        "    $emit[$currentPid] = $true",
        "    $parentPid = [int]$current.ParentProcessId",
        "    $current = if ($byPid.ContainsKey($parentPid)) { $byPid[$parentPid] } else { $null }",
        "  }",
        "}",
        'Write-Output ("META`t{0}" -f ($outsidePids -join ","))',
        "$all | Where-Object { $emit.ContainsKey([int]$_.ProcessId) } | ForEach-Object {",
        "  $created = if ($_.CreationDate) { $_.CreationDate.ToUniversalTime().ToString('o') } else { '' }",
        "  $command = if ($_.Name -eq 'node.exe' -and $candidateIds.ContainsKey([int]$_.ProcessId)) { 'Y29udGV4dDctbWNw' } else { '' }",
        '  "{0}`t{1}`t{2}`t{3}`t{4}" -f $_.ProcessId, $_.ParentProcessId, $_.Name, $created, $command',
        "}",
    )
)


@dataclass(frozen=True, slots=True)
class ProcessSnapshotError(Exception):
    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    records: tuple[ProcessRecord, ...]
    outside_candidate_pids: tuple[int, ...]


def _parse_process_line(line: str) -> ProcessRecord:
    fields = line.split("\t", maxsplit=4)
    if len(fields) != 5:
        raise ProcessSnapshotError(
            detail="process snapshot row has an invalid field count"
        )
    pid_text, parent_text, name, created_text, command_text = fields
    try:
        created_at = (
            datetime.fromisoformat(created_text.replace("Z", "+00:00"))
            if created_text
            else None
        )
        if created_at is not None and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return ProcessRecord(
            pid=int(pid_text),
            parent_pid=int(parent_text),
            name=name,
            command_line=base64.b64decode(command_text, validate=True).decode(
                "utf-8", errors="replace"
            ),
            created_at=created_at,
        )
    except (ValueError, binascii.Error) as exc:
        raise ProcessSnapshotError(
            detail="process snapshot row could not be parsed"
        ) from exc


def _snapshot_command(hook_pid: int) -> str:
    regex = MCP_COMMAND_RE.pattern.replace("'", "''")
    return "\n".join(
        (f"$hookPid = {hook_pid}", f"$rx = '{regex}'", PROCESS_SNAPSHOT_BODY)
    )


def snapshot_processes(hook_pid: int) -> ProcessSnapshot:
    configured_powershell = smtw_env(CODEX_REAPER_POWERSHELL)
    powershell = (
        DEFAULT_POWERSHELL
        if configured_powershell is None
        else configured_powershell
    )
    try:
        completed = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _snapshot_command(hook_pid),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SNAPSHOT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProcessSnapshotError(detail="process snapshot timed out") from exc
    except OSError as exc:
        raise ProcessSnapshotError(detail="process snapshot could not start") from exc
    if completed.returncode != 0:
        raise ProcessSnapshotError(
            detail=f"process snapshot failed with exit code {completed.returncode}"
        )
    lines = completed.stdout.splitlines()
    if not lines or not lines[0].startswith("META\t"):
        raise ProcessSnapshotError(detail="process snapshot metadata is missing")
    outside_text = lines[0].partition("\t")[2]
    try:
        outside = tuple(sorted(int(pid) for pid in outside_text.split(",") if pid))
    except ValueError as exc:
        raise ProcessSnapshotError(
            detail="outside process metadata is invalid"
        ) from exc
    return ProcessSnapshot(
        records=tuple(_parse_process_line(line) for line in lines[1:] if line.strip()),
        outside_candidate_pids=outside,
    )


def _taskkill_command(pids: tuple[int, ...]) -> list[str]:
    command = ["taskkill.exe"]
    for pid in pids:
        command.extend(("/PID", str(pid)))
    command.extend(("/T", "/F"))
    return command


def terminate_process_trees(pids: tuple[int, ...]) -> bool:
    if not pids:
        return True
    try:
        completed = subprocess.run(
            _taskkill_command(pids),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TASKKILL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def terminate_process_tree(pid: int) -> bool:
    return terminate_process_trees((pid,))


def live_process_ids(process_ids: tuple[int, ...]) -> set[int]:
    if not process_ids:
        return set()
    if os.name != "nt":
        raise ProcessSnapshotError(detail="after-count process query is Windows-only")
    import _winapi

    live: set[int] = set()
    for process_id in process_ids:
        try:
            handle = _winapi.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, process_id
            )
        except OSError as exc:
            if exc.winerror == ERROR_INVALID_PARAMETER:
                continue
            if exc.winerror == ERROR_ACCESS_DENIED:
                live.add(process_id)
                continue
            raise ProcessSnapshotError(
                detail=f"after-count process query failed with Windows error {exc.winerror}"
            ) from exc
        try:
            if _winapi.GetExitCodeProcess(handle) == _winapi.STILL_ACTIVE:
                live.add(process_id)
        finally:
            _winapi.CloseHandle(handle)
    return live
