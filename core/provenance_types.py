from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatchcase
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Final, TypeAlias, override

JsonInput: TypeAlias = (
    str
    | int
    | float
    | bool
    | None
    | Sequence["JsonInput"]
    | Mapping[str, "JsonInput"]
)


DEFAULT_MAX_SCAN_ENTRIES: Final = 10_000
DEFAULT_MAX_SCAN_BYTES: Final = 256 * 1024 * 1024
DEFAULT_FULL_SCAN_SECONDS: Final = 8.0
DEFAULT_INCREMENTAL_SCAN_SECONDS: Final = 2.0


class EntryKind(StrEnum):
    REGULAR = "regular"
    SYMLINK = "symlink"


class ChangeOperation(StrEnum):
    CREATE = "create"
    MODIFY = "modify"
    DELETE = "delete"
    TYPE_CHANGE = "type_change"
    MODE_CHANGE = "mode_change"


class ProvenanceStatus(StrEnum):
    COMPLETE = "complete"
    COMPLETE_WITH_EXCLUSIONS = "complete_with_exclusions"
    INCOMPLETE = "incomplete"
    SCOPE_TOO_LARGE = "scope_too_large"
    UNSUPPORTED = "unsupported"


class ProvenanceReason(StrEnum):
    NONE = ""
    HOME_ROOT = "home_root"
    ENTRY_LIMIT = "entry_limit"
    BYTE_LIMIT = "byte_limit"
    DEADLINE = "deadline"
    SNAPSHOT_UNAVAILABLE = "snapshot_unavailable"
    STORE_READ_ERROR = "store_read_error"
    STORE_WRITE_ERROR = "store_write_error"
    OBSERVATION_ERROR = "observation_error"
    PEER_ACTIVITY = "peer_activity"
    TURN_NOT_STARTED = "turn_not_started"


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
    budget: ScanBudget | None = None


@dataclass(frozen=True, slots=True)
class ScanBudget:
    max_entries: int = DEFAULT_MAX_SCAN_ENTRIES
    max_bytes: int = DEFAULT_MAX_SCAN_BYTES
    max_seconds: float = DEFAULT_FULL_SCAN_SECONDS


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
class SnapshotExclusion:
    path: str
    reason: str
    peer_agent_key: str
    peer_turn_id: str
    invocation_id: str
    started_seq: int
    started_at: str
    observer_turn_id: str


@dataclass(frozen=True, slots=True)
class ScanBudgetPath:
    path: str
    total_bytes: int
    total_entries: int


@dataclass(frozen=True, slots=True)
class ScanResult:
    entries: tuple[ManifestEntry, ...]
    reparse_observations: tuple[ManifestEntry, ...]
    issues: tuple[ScanIssue, ...]
    status: ProvenanceStatus = ProvenanceStatus.COMPLETE
    status_reason: ProvenanceReason = ProvenanceReason.NONE
    budget_top_paths: tuple[ScanBudgetPath, ...] = ()
    budget_breach_path: str | None = None


@dataclass(frozen=True, slots=True)
class Snapshot:
    root: Path
    entries: tuple[ManifestEntry, ...]
    reparse_observations: tuple[ManifestEntry, ...]
    issues: tuple[ScanIssue, ...]
    snapshot_id: str
    scope_policy_id: str
    generated_patterns: tuple[str, ...]
    exclusions: tuple[SnapshotExclusion, ...] = ()
    is_casefolded: bool = False
    platform: str = ""
    full_reconciled_at: str | None = None
    status: ProvenanceStatus = ProvenanceStatus.COMPLETE
    status_reason: ProvenanceReason = ProvenanceReason.NONE
    budget_top_paths: tuple[ScanBudgetPath, ...] = ()
    budget_breach_path: str | None = None

    @property
    def incomplete(self) -> bool:
        return self.status is ProvenanceStatus.INCOMPLETE or bool(self.issues)

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


MAX_BUDGET_TOP_PATHS: Final = 3


def normalize_budget_top_paths(value: JsonInput) -> tuple[dict[str, str | int], ...]:
    """ledger에 저장된 provenance_budget_top_paths 원시값을 검증·정규화한다.

    타입이 안 맞거나(비-list), 항목이 dict가 아니거나, path가 빈 문자열이거나,
    bytes/entries가 음수 정수가 아니면 그 항목은 버린다. 최대 3개까지만 취한다.
    """
    if not isinstance(value, (list, tuple)):
        return ()
    normalized: list[dict[str, str | int]] = []
    for item in value:
        if len(normalized) >= MAX_BUDGET_TOP_PATHS:
            break
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        raw_bytes = item.get("bytes")
        raw_entries = item.get("entries")
        if not isinstance(path, str) or not path:
            continue
        if not isinstance(raw_bytes, int) or isinstance(raw_bytes, bool) or raw_bytes < 0:
            continue
        if not isinstance(raw_entries, int) or isinstance(raw_entries, bool) or raw_entries < 0:
            continue
        normalized.append({"path": path, "bytes": raw_bytes, "entries": raw_entries})
    return tuple(normalized)


def normalize_budget_breach_path(value: JsonInput) -> str | None:
    return value if isinstance(value, str) and value else None
