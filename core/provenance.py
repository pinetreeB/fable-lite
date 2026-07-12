from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Final

from .provenance_capture import (
    HASH_CHUNK_BYTES,
    CaptureRequest,
    CapturedPath,
    capture_regular,
    capture_symlink,
)
from .provenance_delta import calculate_net_delta
from .provenance_policy import (
    canonical_manifest_key,
    is_path_in_scope,
    load_provenance_config,
    normalize_relative_path,
    should_descend,
)
from .provenance_snapshot import SnapshotBuildContext, build_snapshot, scope_policy_id
from .provenance_types import (
    ChangeOperation,
    EntryKind,
    ManifestEntry,
    NetDelta,
    ProvenanceConfig,
    ScanIssue,
    ScanResult,
    Snapshot,
    SnapshotScanOptions,
)

REPARSE_ATTRIBUTE: Final = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
__all__ = (
    "ChangeOperation",
    "EntryKind",
    "HASH_CHUNK_BYTES",
    "ManifestEntry",
    "NetDelta",
    "ProvenanceConfig",
    "ScanIssue",
    "ScanResult",
    "Snapshot",
    "SnapshotScanOptions",
    "calculate_net_delta",
    "canonical_manifest_key",
    "load_provenance_config",
    "normalize_relative_path",
    "snapshot_workspace",
    "snapshot_workspace_with_options",
    "workspace_scope_policy_id",
)


@dataclass(frozen=True, slots=True)
class _ScanContext:
    root: Path
    config: ProvenanceConfig
    windows: bool
    force_paths: frozenset[str]
    previous_entries: dict[str, ManifestEntry]
    previous_reparse_observations: dict[str, ManifestEntry]


@dataclass(frozen=True, slots=True)
class _PathInfo:
    path: Path
    relative: str
    key: str
    metadata: os.stat_result
    is_link: bool


class _ScanState:
    """Accumulates one directory walk because discovery order requires mutable work lists."""

    directories: list[Path]
    entries: list[ManifestEntry]
    observations: list[ManifestEntry]
    issues: list[ScanIssue]

    def __init__(self, root: Path) -> None:
        self.directories = [root]
        self.entries = []
        self.observations = []
        self.issues = []


def snapshot_workspace(
    root: Path,
    previous: Snapshot | None = None,
    windows: bool | None = None,
) -> Snapshot:
    return snapshot_workspace_with_options(
        root,
        SnapshotScanOptions(previous=previous, windows=windows),
    )


def snapshot_workspace_with_options(root: Path, options: SnapshotScanOptions) -> Snapshot:
    absolute_root = Path(os.path.abspath(root))
    previous_entries, previous_reparse_observations = _previous_state(
        options.previous,
        options.windows,
    )
    context = _ScanContext(
        root=absolute_root,
        config=load_provenance_config(absolute_root),
        windows=os.name == "nt" if options.windows is None else options.windows,
        force_paths=options.force_paths,
        previous_entries=previous_entries,
        previous_reparse_observations=previous_reparse_observations,
    )
    return build_snapshot(
        SnapshotBuildContext(context.root, context.config, context.windows, os.name),
        _scan(context),
    )


def workspace_scope_policy_id(root: Path, windows: bool | None = None) -> str:
    absolute_root = Path(os.path.abspath(root))
    casefolded = os.name == "nt" if windows is None else windows
    context = SnapshotBuildContext(
        absolute_root,
        load_provenance_config(absolute_root),
        casefolded,
        os.name,
    )
    return scope_policy_id(context)


def _scan(context: _ScanContext) -> ScanResult:
    state = _ScanState(context.root)
    while state.directories:
        directory = state.directories.pop()
        try:
            with os.scandir(directory) as scan:
                children = sorted(scan, key=lambda entry: entry.name)
        except OSError:
            relative = normalize_relative_path(context.root, directory)
            state.issues.append(ScanIssue(relative, "unreadable_directory"))
            continue
        for child in children:
            _visit_path(child, context, state)
    return ScanResult(tuple(state.entries), tuple(state.observations), tuple(state.issues))


def _visit_path(
    child: os.DirEntry[str],
    context: _ScanContext,
    state: _ScanState,
) -> None:
    relative = normalize_relative_path(context.root, Path(child.path))
    try:
        metadata = child.stat(follow_symlinks=False)
        is_link = child.is_symlink()
    except OSError:
        state.issues.append(ScanIssue(relative, "unreadable_path"))
        return
    info = _PathInfo(
        Path(child.path),
        relative,
        canonical_manifest_key(relative, context.windows),
        metadata,
        is_link,
    )
    if info.is_link:
        if not is_path_in_scope(relative, context.config):
            return
        _append_capture(capture_symlink(_capture_request(info)), state)
        return
    if _is_non_symlink_reparse(metadata, context.windows):
        _visit_reparse(info, context, state)
        return
    if stat.S_ISDIR(metadata.st_mode):
        if should_descend(relative, context.config):
            state.directories.append(info.path)
        return
    if not is_path_in_scope(relative, context.config):
        return
    if stat.S_ISREG(metadata.st_mode):
        previous = (
            None
            if info.relative in context.force_paths
            else context.previous_entries.get(info.key)
        )
        _append_capture(capture_regular(_capture_request(info, previous)), state)
        return
    state.issues.append(ScanIssue(relative, "special_file"))


def _visit_reparse(
    info: _PathInfo,
    context: _ScanContext,
    state: _ScanState,
) -> None:
    if stat.S_ISDIR(info.metadata.st_mode):
        state.issues.append(ScanIssue(info.relative, "unstable_reparse"))
        return
    if not is_path_in_scope(info.relative, context.config):
        return
    captured = capture_regular(_capture_request(info))
    if captured.entry is None:
        _append_capture(captured, state)
        return
    state.observations.append(captured.entry)
    previous = context.previous_reparse_observations.get(info.key)
    if previous is not None and previous.digest == captured.entry.digest:
        state.entries.append(captured.entry)
        return
    state.issues.append(ScanIssue(info.relative, "unstable_reparse"))


def _append_capture(
    captured: CapturedPath,
    state: _ScanState,
) -> None:
    if captured.entry is not None:
        state.entries.append(captured.entry)
    if captured.issue is not None:
        state.issues.append(captured.issue)


def _capture_request(
    info: _PathInfo,
    previous: ManifestEntry | None = None,
) -> CaptureRequest:
    return CaptureRequest(info.path, info.relative, info.key, previous)


def _previous_state(
    snapshot: Snapshot | None,
    windows: bool | None,
) -> tuple[dict[str, ManifestEntry], dict[str, ManifestEntry]]:
    casefolded = os.name == "nt" if windows is None else windows
    if snapshot is None or snapshot.is_casefolded != casefolded:
        return {}, {}
    return (
        {entry.canonical_key: entry for entry in snapshot.entries},
        {entry.canonical_key: entry for entry in snapshot.reparse_observations},
    )


def _is_non_symlink_reparse(metadata: os.stat_result, windows: bool) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    return windows and bool(attributes & REPARSE_ATTRIBUTE)
