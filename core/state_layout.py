from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import threading
from typing import Final


STATE_DIR_NAME: Final = ".smtw"
LEGACY_STATE_DIR_NAME: Final = ".fable-lite"
PROJECT_CONFIG_NAME: Final = ".smtw.toml"
LEGACY_ACTIVATION_CONFIG_NAME: Final = "config.json"
PROVENANCE_CONFIG_NAME: Final = "provenance-config.json"

MIGRATION_LOCK_NAME: Final = ".smtw-migration.lock"
MIGRATION_RECEIPT_NAME: Final = ".smtw-migration-receipt.json"
MIGRATION_RECEIPT_TEMP_PREFIX: Final = f"{MIGRATION_RECEIPT_NAME}.tmp-"
MIGRATION_STAGING_PREFIX: Final = ".smtw.migrating-"
MIGRATION_MARKER_NAME: Final = ".smtw-migration.json"
MIGRATION_MARKER_SCHEMA_VERSION: Final = 1
MIGRATION_PUBLISHED_PHASE: Final = "published"
# The layout barrier serializes every state mutation on one migration lock, so
# a bare-default writer's budget must absorb multi-agent contention.  1.0s was
# too tight under CI load (unfair file lock, tail latency) and timed out on
# .smtw-migration.lock; 5.0s covers 8-way contention while an uncontended write
# still acquires immediately.  Latency-sensitive hooks pass wait_seconds=0.
DEFAULT_STATE_WRITE_WAIT_SECONDS: Final = 5.0

RUNTIME_STATE_DIR_NAMES: Final = frozenset(
    {STATE_DIR_NAME, LEGACY_STATE_DIR_NAME}
)
PROTECTED_STATE_NAMES: Final = frozenset(
    {
        *RUNTIME_STATE_DIR_NAMES,
        MIGRATION_LOCK_NAME,
        MIGRATION_RECEIPT_NAME,
    }
)

_TRANSIENT_FILE_NAMES: Final = frozenset({"ledger.guard", "ledger.lock"})
_TRANSIENT_SUFFIXES: Final = (".guard", ".lock", ".tmp")
_HEX_DIGEST_LENGTH: Final = 64


class StateLayout(str, Enum):
    EMPTY = "EMPTY"
    LEGACY = "LEGACY"
    NATIVE = "NATIVE"
    MIGRATED = "MIGRATED"
    MIGRATING = "MIGRATING"
    CONFLICT = "CONFLICT"


class StateLayoutError(RuntimeError):
    pass


class _StateWriteScopes(threading.local):
    def __init__(self) -> None:
        self.held: dict[Path, tuple[Path, int]] = {}


_STATE_WRITE_SCOPES = _StateWriteScopes()


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    entry_type: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class StateManifest:
    entries: tuple[ManifestEntry, ...]
    digest: str
    file_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class LayoutInspection:
    layout: StateLayout
    root: Path
    legacy: Path
    target: Path
    staging: tuple[Path, ...]
    reason: str = ""


def legacy_state_dir(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / LEGACY_STATE_DIR_NAME


def native_state_dir(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / STATE_DIR_NAME


def state_dir(project_root: str | Path) -> Path:
    """Select one authoritative state tree without creating or migrating it."""
    inspection = inspect_state_layout_details(project_root)
    if inspection.layout in {
        StateLayout.EMPTY,
        StateLayout.NATIVE,
        StateLayout.MIGRATED,
    }:
        return inspection.target
    if inspection.layout in {StateLayout.LEGACY, StateLayout.MIGRATING}:
        return inspection.legacy
    detail = f": {inspection.reason}" if inspection.reason else ""
    raise StateLayoutError(f"state layout has no single authority{detail}")


def migration_lock_path(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / MIGRATION_LOCK_NAME


def migration_receipt_path(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / MIGRATION_RECEIPT_NAME


@contextmanager
def state_write_scope(
    project_root: str | Path,
    *,
    wait_seconds: float = DEFAULT_STATE_WRITE_WAIT_SECONDS,
) -> Iterator[Path]:
    """Serialize project-state mutation with migration and yield its authority.

    The authority is selected only after the layout lock is acquired.  Nested
    state writers in the same thread reuse the outer scope so a ledger
    transaction can safely cover its journals, snapshots, and agent logs.
    """
    root = Path(project_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    held = _STATE_WRITE_SCOPES.held.get(root)
    if held is not None:
        authority, depth = held
        _STATE_WRITE_SCOPES.held[root] = (authority, depth + 1)
        try:
            yield authority
        finally:
            current = _STATE_WRITE_SCOPES.held.get(root)
            if current is not None:
                if current[1] <= 1:
                    _STATE_WRITE_SCOPES.held.pop(root, None)
                else:
                    _STATE_WRITE_SCOPES.held[root] = (current[0], current[1] - 1)
        return

    from core.file_lock import owner_lock

    with owner_lock(migration_lock_path(root), wait_seconds=wait_seconds):
        authority = state_dir(root)
        _STATE_WRITE_SCOPES.held[root] = (authority, 1)
        try:
            yield authority
        finally:
            _STATE_WRITE_SCOPES.held.pop(root, None)


def is_migration_staging_name(name: str) -> bool:
    return name.startswith(MIGRATION_STAGING_PREFIX)


def is_protected_state_name(name: str, *, windows: bool | None = None) -> bool:
    casefolded = os.name == "nt" if windows is None else windows
    candidate = name.casefold() if casefolded else name
    exact = {
        item.casefold() if casefolded else item for item in PROTECTED_STATE_NAMES
    }
    prefix = (
        MIGRATION_STAGING_PREFIX.casefold()
        if casefolded
        else MIGRATION_STAGING_PREFIX
    )
    receipt_temp_prefix = (
        MIGRATION_RECEIPT_TEMP_PREFIX.casefold()
        if casefolded
        else MIGRATION_RECEIPT_TEMP_PREFIX
    )
    return (
        candidate in exact
        or candidate.startswith(prefix)
        or candidate.startswith(receipt_temp_prefix)
    )


def inspect_state_layout(project_root: str | Path) -> StateLayout:
    return inspect_state_layout_details(project_root).layout


def inspect_state_layout_details(project_root: str | Path) -> LayoutInspection:
    root = Path(project_root).resolve()
    legacy = root / LEGACY_STATE_DIR_NAME
    target = root / STATE_DIR_NAME
    try:
        staging = tuple(
            sorted(
                (
                    entry
                    for entry in root.iterdir()
                    if is_migration_staging_name(entry.name)
                ),
                key=lambda entry: entry.name,
            )
        )
    except FileNotFoundError:
        staging = ()
    except OSError as exc:
        return LayoutInspection(
            StateLayout.CONFLICT,
            root,
            legacy,
            target,
            (),
            f"cannot inspect project root: {type(exc).__name__}",
        )

    invalid = _invalid_layout_path(target, "target")
    if invalid:
        return LayoutInspection(
            StateLayout.CONFLICT, root, legacy, target, staging, invalid
        )

    target_exists = target.is_dir()
    if target_exists:
        marker_path = target / MIGRATION_MARKER_NAME
        marker_exists = _lexists(marker_path)
        if marker_exists:
            valid, reason = validate_published_authority_marker(target)
            if not valid:
                return LayoutInspection(
                    StateLayout.CONFLICT,
                    root,
                    legacy,
                    target,
                    staging,
                    reason,
                )
            return LayoutInspection(
                StateLayout.MIGRATED, root, legacy, target, staging
            )

    invalid = _invalid_layout_path(legacy, "legacy")
    if invalid:
        return LayoutInspection(
            StateLayout.CONFLICT, root, legacy, target, staging, invalid
        )
    for candidate in staging:
        invalid = _invalid_layout_path(candidate, "staging")
        if invalid:
            return LayoutInspection(
                StateLayout.CONFLICT, root, legacy, target, staging, invalid
            )

    legacy_exists = legacy.is_dir()
    if target_exists:
        if legacy_exists or staging:
            return LayoutInspection(
                StateLayout.CONFLICT,
                root,
                legacy,
                target,
                staging,
                "markerless target conflicts with legacy or staging state",
            )
        return LayoutInspection(StateLayout.NATIVE, root, legacy, target, staging)
    if staging and legacy_exists:
        return LayoutInspection(StateLayout.MIGRATING, root, legacy, target, staging)
    if staging:
        return LayoutInspection(
            StateLayout.CONFLICT,
            root,
            legacy,
            target,
            staging,
            "migration staging has no authoritative legacy source",
        )
    if legacy_exists:
        return LayoutInspection(StateLayout.LEGACY, root, legacy, target, staging)
    return LayoutInspection(StateLayout.EMPTY, root, legacy, target, staging)


def build_state_manifest(
    directory: str | Path,
    *,
    windows: bool | None = None,
) -> StateManifest:
    root = _filesystem_path(Path(directory))
    _require_plain_directory(root, "manifest root")
    casefolded = os.name == "nt" if windows is None else windows
    entries: list[ManifestEntry] = []
    canonical_paths: dict[str, str] = {}
    _collect_manifest_entries(root, root, entries, canonical_paths, casefolded)
    ordered = tuple(sorted(entries, key=lambda entry: entry.path))
    digest = sha256()
    file_count = 0
    total_bytes = 0
    for entry in ordered:
        digest.update(
            (
                f"{entry.entry_type}\0{entry.path}\0{entry.size}\0"
                f"{entry.sha256}\n"
            ).encode("utf-8")
        )
        if entry.entry_type == "file":
            file_count += 1
            total_bytes += entry.size
    return StateManifest(ordered, digest.hexdigest(), file_count, total_bytes)


def read_migration_marker(target: str | Path) -> dict[str, object]:
    marker_path = Path(target) / MIGRATION_MARKER_NAME
    try:
        marker_info = marker_path.lstat()
        if marker_path.is_symlink() or _stat_is_reparse_point(marker_info):
            raise StateLayoutError("migration marker is a link or reparse point")
        if not stat.S_ISREG(marker_info.st_mode):
            raise StateLayoutError("migration marker is not a regular file")
        raw: object = json.loads(marker_path.read_text(encoding="utf-8"))
    except StateLayoutError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateLayoutError(
            f"migration marker cannot be read: {type(exc).__name__}"
        ) from exc
    if not isinstance(raw, dict):
        raise StateLayoutError("migration marker must be a JSON object")
    return raw


def validate_published_marker(target: str | Path) -> tuple[bool, str]:
    """Validate a just-published target while it is still pristine."""
    target_path = Path(target)
    valid, reason = validate_published_authority_marker(target_path)
    if not valid:
        return False, reason
    try:
        marker = read_migration_marker(target_path)
        manifest = build_state_manifest(target_path)
    except (OSError, StateLayoutError) as exc:
        return False, str(exc)
    expected = (
        marker["source_digest"],
        marker["source_file_count"],
        marker["source_total_bytes"],
    )
    actual = (manifest.digest, manifest.file_count, manifest.total_bytes)
    if actual != expected:
        return False, "published target does not match its migration marker manifest"
    return True, ""


def validate_published_authority_marker(
    target: str | Path,
) -> tuple[bool, str]:
    """Validate the immutable marker fields that seal target authority."""
    target_path = Path(target)
    try:
        _require_plain_directory(target_path, "target")
        marker = read_migration_marker(target_path)
        _validate_marker_fields(marker, target_path)
    except (OSError, StateLayoutError) as exc:
        return False, str(exc)
    return True, ""


def _validate_marker_fields(marker: dict[str, object], target: Path) -> None:
    schema = marker.get("schema_version")
    if isinstance(schema, bool) or schema != MIGRATION_MARKER_SCHEMA_VERSION:
        raise StateLayoutError("migration marker schema is unsupported")
    if marker.get("phase") != MIGRATION_PUBLISHED_PHASE:
        raise StateLayoutError("migration marker is not published")
    for field in (
        "migration_id",
        "root",
        "source",
        "target",
        "source_digest",
        "started_at",
        "completed_at",
        "tool_version",
    ):
        value = marker.get(field)
        if not isinstance(value, str) or not value:
            raise StateLayoutError(f"migration marker {field} must be non-empty")
    digest = marker["source_digest"]
    if not _is_sha256(digest):
        raise StateLayoutError("migration marker source_digest is invalid")
    for field in ("source_file_count", "source_total_bytes"):
        value = marker.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise StateLayoutError(f"migration marker {field} must be non-negative")
    recorded_target = Path(str(marker["target"]))
    if _canonical_path(recorded_target) != _canonical_path(target):
        raise StateLayoutError("migration marker target does not match its directory")
    recorded_root = Path(str(marker["root"]))
    if _canonical_path(recorded_root) != _canonical_path(target.parent):
        raise StateLayoutError("migration marker root does not match its project")
    recorded_source = Path(str(marker["source"]))
    if _canonical_path(recorded_source) != _canonical_path(
        target.parent / LEGACY_STATE_DIR_NAME
    ):
        raise StateLayoutError("migration marker source does not match legacy state")


def _collect_manifest_entries(
    root: Path,
    directory: Path,
    entries: list[ManifestEntry],
    canonical_paths: dict[str, str],
    windows: bool,
) -> None:
    try:
        with os.scandir(directory) as scanner:
            children = sorted(scanner, key=lambda entry: entry.name)
    except OSError as exc:
        raise StateLayoutError(f"cannot scan state tree: {directory}") from exc
    for child in children:
        relative = Path(child.path).relative_to(root).as_posix()
        try:
            info = child.stat(follow_symlinks=False)
        except OSError as exc:
            raise StateLayoutError(f"cannot stat state entry: {relative}") from exc
        if child.is_symlink() or _is_reparse_point(child, info):
            raise StateLayoutError(f"state entry is a link or reparse point: {relative}")
        if _is_transient_manifest_path(relative):
            if stat.S_ISREG(info.st_mode):
                continue
            if stat.S_ISDIR(info.st_mode) and relative.endswith(".tmp"):
                continue
            raise StateLayoutError(
                f"transient state entry has an invalid type: {relative}"
            )
        canonical = relative.casefold() if windows else relative
        previous = canonical_paths.setdefault(canonical, relative)
        if previous != relative:
            raise StateLayoutError(
                f"state entries collide after Windows casefold: {previous}, {relative}"
            )
        if stat.S_ISDIR(info.st_mode):
            entries.append(ManifestEntry(relative, "directory", 0, ""))
            _collect_manifest_entries(
                root, Path(child.path), entries, canonical_paths, windows
            )
            continue
        if not stat.S_ISREG(info.st_mode):
            raise StateLayoutError(f"state entry is a special file: {relative}")
        digest, final_info = _hash_regular_file(Path(child.path))
        if _file_identity(info) != _file_identity(final_info):
            raise StateLayoutError(f"state file changed while hashing: {relative}")
        entries.append(ManifestEntry(relative, "file", info.st_size, digest))


def _hash_regular_file(path: Path) -> tuple[str, os.stat_result]:
    digest = sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
            final_info = os.fstat(handle.fileno())
    except OSError as exc:
        raise StateLayoutError(f"cannot read state file: {path}") from exc
    return digest.hexdigest(), final_info


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    if os.name == "nt":
        # CPython's Windows DirEntry stat uses zero dev/inode values while fstat on
        # the same handle exposes synthesized values. Size and nanosecond mtime are
        # the stable cross-call mutation signals on that platform.
        return (info.st_size, info.st_mtime_ns)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _is_transient_manifest_path(relative: str) -> bool:
    if relative in {LEGACY_ACTIVATION_CONFIG_NAME, MIGRATION_MARKER_NAME}:
        return True
    name = relative.rsplit("/", 1)[-1]
    return name in _TRANSIENT_FILE_NAMES or name.endswith(_TRANSIENT_SUFFIXES)


def _invalid_layout_path(path: Path, label: str) -> str:
    if not _lexists(path):
        return ""
    try:
        _require_plain_directory(path, label)
    except StateLayoutError as exc:
        return str(exc)
    return ""


def _require_plain_directory(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise StateLayoutError(f"cannot inspect {label}: {type(exc).__name__}") from exc
    if path.is_symlink() or _stat_is_reparse_point(info):
        raise StateLayoutError(f"{label} is a link or reparse point")
    if not stat.S_ISDIR(info.st_mode):
        raise StateLayoutError(f"{label} is not a directory")


def _is_reparse_point(entry: os.DirEntry[str], info: os.stat_result) -> bool:
    is_junction = getattr(entry, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    return _stat_is_reparse_point(info)


def _stat_is_reparse_point(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _canonical_path(path: Path) -> str:
    normalized = os.path.normcase(str(path.resolve()))
    return normalized.casefold() if os.name == "nt" else normalized


def _filesystem_path(path: Path) -> Path:
    """Use Win32's extended-length form for recursive state-tree I/O."""
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path(f"\\\\?\\UNC\\{absolute.lstrip('\\')}")
    return Path(f"\\\\?\\{absolute}")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HEX_DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )
