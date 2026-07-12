from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path
from typing import override


class EntryKind(StrEnum):
    REGULAR = "regular"
    SYMLINK = "symlink"


class ChangeOperation(StrEnum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    TYPE_CHANGE = "type_change"
    MODE_CHANGE = "mode_change"


@dataclass(frozen=True, slots=True)
class ProvenanceConfigError(ValueError):
    field: str
    requirement: str

    @override
    def __str__(self) -> str:
        return f"invalid provenance config at {self.field}: {self.requirement}"


@dataclass(frozen=True, slots=True)
class ProvenancePathError(ValueError):
    path: str
    root: str

    @override
    def __str__(self) -> str:
        return f"path is outside provenance root: {self.path} (root: {self.root})"


@dataclass(frozen=True, slots=True)
class ProvenanceDeltaPolicyError(ValueError):
    canonical_key: str

    @override
    def __str__(self) -> str:
        return f"canonical key collision while reconciling delta: {self.canonical_key}"


@dataclass(frozen=True, slots=True)
class ProvenanceConfig:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    generated: tuple[str, ...] = ()

    def is_generated(self, path: str) -> bool:
        return any(fnmatchcase(path, pattern) for pattern in self.generated)


@dataclass(frozen=True, slots=True)
class SnapshotScanOptions:
    previous: Snapshot | None = None
    windows: bool | None = None
    force_paths: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    canonical_key: str
    file_type: EntryKind
    size: int
    mtime_ns: int
    mode: int
    digest: str


@dataclass(frozen=True, slots=True)
class ScanIssue:
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class ScanResult:
    entries: tuple[ManifestEntry, ...]
    reparse_observations: tuple[ManifestEntry, ...]
    issues: tuple[ScanIssue, ...]


@dataclass(frozen=True, slots=True)
class Snapshot:
    root: Path
    entries: tuple[ManifestEntry, ...]
    reparse_observations: tuple[ManifestEntry, ...]
    issues: tuple[ScanIssue, ...]
    snapshot_id: str
    scope_policy_id: str
    generated_patterns: tuple[str, ...]
    is_casefolded: bool = False
    platform: str = ""
    full_reconciled_at: str | None = None

    @property
    def incomplete(self) -> bool:
        return bool(self.issues)

    def is_generated(self, path: str) -> bool:
        return any(fnmatchcase(path, pattern) for pattern in self.generated_patterns)


@dataclass(frozen=True, slots=True)
class NetDelta:
    path: str
    canonical_key: str
    op: ChangeOperation
    before: ManifestEntry | None
    after: ManifestEntry | None
    mode_changed: bool = False
