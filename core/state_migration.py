from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from typing import Final
from uuid import uuid4

from .file_lock import owner_lock, pid_is_alive
from .project_root import is_user_home_root
from .state_layout import (
    LEGACY_STATE_DIR_NAME,
    MIGRATION_MARKER_NAME,
    MIGRATION_MARKER_SCHEMA_VERSION,
    MIGRATION_PUBLISHED_PHASE,
    MIGRATION_RECEIPT_NAME,
    MIGRATION_RECEIPT_TEMP_PREFIX,
    MIGRATION_STAGING_PREFIX,
    STATE_DIR_NAME,
    LayoutInspection,
    ManifestEntry,
    StateLayout,
    StateLayoutError,
    StateManifest,
    build_state_manifest,
    inspect_state_layout_details,
    migration_lock_path,
    migration_receipt_path,
    read_migration_marker,
    validate_published_marker,
)


DEFAULT_MIGRATION_LOCK_WAIT_SECONDS: Final = 15.0
DEFAULT_ORPHAN_MIN_AGE_SECONDS: Final = 300.0
MIGRATION_RECEIPT_SCHEMA_VERSION: Final = 1
_COPYING_PHASE: Final = "copying"
_REPLACE_RETRY_SECONDS: Final = 0.01

MigrationFaultInjector = Callable[[str, Path | None], None]


class _MigrationStageFailure(RuntimeError):
    def __init__(self, stage: str, error: Exception) -> None:
        super().__init__(str(error))
        self.stage = stage
        self.error = error


class MigrationStatus(str, Enum):
    MIGRATED = "migrated"
    ALREADY_MIGRATED = "already_migrated"
    NOT_NEEDED = "not_needed"
    INACTIVE = "inactive"
    HOME_REFUSED = "home_refused"
    DEFERRED = "deferred"
    CONFLICT = "conflict"
    FAILED = "failed"


class RollbackSafety(str, Enum):
    SAFE_UNCHANGED = "safe_unchanged"
    NOT_MIGRATED = "not_migrated"
    TARGET_DIVERGED = "target_diverged"
    LEGACY_DIVERGED = "legacy_diverged"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class StateLayoutPreparation:
    root: Path
    inspection: LayoutInspection
    migration_allowed: bool
    reason_code: str = ""


@dataclass(frozen=True, slots=True)
class MigrationResult:
    status: MigrationStatus
    layout: StateLayout
    root: str
    reason_code: str = ""
    detail: str = ""
    migration_id: str = ""
    source_digest: str = ""
    file_count: int = 0
    total_bytes: int = 0
    published: bool = False
    failed_stage: str = ""
    error_type: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {
            MigrationStatus.MIGRATED,
            MigrationStatus.ALREADY_MIGRATED,
            MigrationStatus.NOT_NEEDED,
        }

    @property
    def exit_code(self) -> int:
        if self.ok:
            return 0
        if self.status in {
            MigrationStatus.INACTIVE,
            MigrationStatus.HOME_REFUSED,
            MigrationStatus.DEFERRED,
        }:
            return 2
        return 1

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["layout"] = self.layout.value
        payload["ok"] = self.ok
        return payload


def prepare_state_layout(
    project_root: str | Path,
    activation: bool,
) -> StateLayoutPreparation:
    """Perform the write-free activation/home/layout phase of migration."""
    root = Path(project_root).expanduser().resolve()
    if not activation:
        return StateLayoutPreparation(
            root, _uninspected_layout(root), False, "inactive"
        )
    if is_user_home_root(root):
        return StateLayoutPreparation(
            root, _uninspected_layout(root), False, "exact_home"
        )
    inspection = inspect_state_layout_details(root)
    allowed = inspection.layout in {StateLayout.LEGACY, StateLayout.MIGRATING}
    reason = "" if allowed else _preparation_reason(inspection)
    return StateLayoutPreparation(root, inspection, allowed, reason)


def migrate_state(
    project_root: str | Path,
    *,
    activation: bool = True,
    lock_wait_seconds: float = DEFAULT_MIGRATION_LOCK_WAIT_SECONDS,
    orphan_min_age_seconds: float = DEFAULT_ORPHAN_MIN_AGE_SECONDS,
    fault_injector: MigrationFaultInjector | None = None,
) -> MigrationResult:
    """Copy one legacy state tree into a verified, atomically published target.

    This is the only Wave 1 cutover-capable entry point. Merely importing this
    module, inspecting layout, or calling state_dir() never invokes it.
    """
    wait = _nonnegative_seconds(lock_wait_seconds, "lock_wait_seconds")
    orphan_age = _nonnegative_seconds(
        orphan_min_age_seconds, "orphan_min_age_seconds"
    )
    preparation = prepare_state_layout(project_root, activation)
    root = preparation.root
    inspection = preparation.inspection
    if not activation:
        return _result(MigrationStatus.INACTIVE, inspection, "inactive")
    if preparation.reason_code == "exact_home":
        return _result(MigrationStatus.HOME_REFUSED, inspection, "exact_home")
    if inspection.layout is StateLayout.MIGRATED:
        return _already_migrated_result(inspection)
    if inspection.layout in {StateLayout.EMPTY, StateLayout.NATIVE}:
        return _result(MigrationStatus.NOT_NEEDED, inspection, "no_legacy_state")
    if inspection.layout is StateLayout.CONFLICT:
        return _result(
            MigrationStatus.CONFLICT,
            inspection,
            "layout_conflict",
            inspection.reason,
        )

    _fault(fault_injector, "after_activation", root)
    try:
        with owner_lock(migration_lock_path(root), wait_seconds=wait):
            return _migrate_with_layout_lock(
                root,
                wait_seconds=wait,
                orphan_min_age_seconds=orphan_age,
                fault_injector=fault_injector,
            )
    except TimeoutError as exc:
        current = inspect_state_layout_details(root)
        return _result(
            MigrationStatus.DEFERRED,
            current,
            "layout_lock_busy",
            str(exc),
        )
    except OSError as exc:
        current = inspect_state_layout_details(root)
        return _result(
            MigrationStatus.FAILED,
            current,
            "layout_lock_error",
            str(exc),
            error_type=type(exc).__name__,
        )


def assess_rollback(project_root: str | Path) -> RollbackSafety:
    """Read-only assessment; Wave 1 never performs automatic rollback."""
    root = Path(project_root).expanduser().resolve()
    target = root / STATE_DIR_NAME
    legacy = root / LEGACY_STATE_DIR_NAME
    if not _is_plain_directory(target):
        return RollbackSafety.NOT_MIGRATED
    try:
        marker = read_migration_marker(target)
        expected = _manifest_tuple_from_marker(marker)
        if marker.get("phase") != MIGRATION_PUBLISHED_PHASE:
            return RollbackSafety.CONFLICT
        target_manifest = build_state_manifest(target)
    except (OSError, StateLayoutError):
        return RollbackSafety.CONFLICT
    if _manifest_tuple(target_manifest) != expected:
        return RollbackSafety.TARGET_DIVERGED
    if not _is_plain_directory(legacy):
        return RollbackSafety.LEGACY_DIVERGED
    try:
        legacy_manifest = build_state_manifest(legacy)
    except (OSError, StateLayoutError):
        return RollbackSafety.LEGACY_DIVERGED
    if _manifest_tuple(legacy_manifest) != expected:
        return RollbackSafety.LEGACY_DIVERGED
    return RollbackSafety.SAFE_UNCHANGED


def _migrate_with_layout_lock(
    root: Path,
    *,
    wait_seconds: float,
    orphan_min_age_seconds: float,
    fault_injector: MigrationFaultInjector | None,
) -> MigrationResult:
    _recover_orphan_staging(root, orphan_min_age_seconds)
    inspection = inspect_state_layout_details(root)
    if inspection.layout is StateLayout.MIGRATED:
        return _already_migrated_result(inspection)
    if inspection.layout in {StateLayout.EMPTY, StateLayout.NATIVE}:
        return _result(MigrationStatus.NOT_NEEDED, inspection, "no_legacy_state")
    if inspection.layout is StateLayout.CONFLICT:
        return _result(
            MigrationStatus.CONFLICT,
            inspection,
            "layout_conflict",
            inspection.reason,
        )
    source = root / LEGACY_STATE_DIR_NAME
    if not _is_plain_directory(source):
        return _result(
            MigrationStatus.CONFLICT,
            inspection,
            "legacy_unavailable",
            "migration staging exists without a plain legacy source",
        )
    target = root / STATE_DIR_NAME
    _fault(fault_injector, "layout_locked", root)

    quiescence = _quiescence_reason(source)
    if quiescence:
        return _result(
            MigrationStatus.DEFERRED,
            inspection,
            quiescence,
            "legacy state has an active turn or open invocation",
        )

    try:
        with owner_lock(source / "ledger.lock", wait_seconds=wait_seconds):
            _fault(fault_injector, "source_lock_acquired", source)
            quiescence = _quiescence_reason(source)
            if quiescence:
                return _result(
                    MigrationStatus.DEFERRED,
                    inspection,
                    quiescence,
                    "legacy state became active before its lock was acquired",
                )
            return _copy_verify_publish(
                root,
                source,
                target,
                fault_injector=fault_injector,
            )
    except TimeoutError as exc:
        current = inspect_state_layout_details(root)
        return _result(
            MigrationStatus.DEFERRED,
            current,
            "legacy_lock_busy",
            str(exc),
        )
    except OSError as exc:
        current = inspect_state_layout_details(root)
        return _result(
            MigrationStatus.FAILED,
            current,
            "legacy_lock_error",
            str(exc),
            error_type=type(exc).__name__,
        )


def _copy_verify_publish(
    root: Path,
    source: Path,
    target: Path,
    *,
    fault_injector: MigrationFaultInjector | None,
) -> MigrationResult:
    migration_id = uuid4().hex
    started_at = _now()
    staging = root / f"{MIGRATION_STAGING_PREFIX}{os.getpid()}-{migration_id}"
    manifest = StateManifest((), "", 0, 0)
    stage = "before_manifest"
    published = False
    try:
        _fault(fault_injector, stage, source)
        manifest = build_state_manifest(source)
        stage = "after_manifest"
        _fault(fault_injector, stage, source)

        stage = "create_staging"
        staging.mkdir(mode=0o700)
        copying_marker = _marker_payload(
            root=root,
            source=source,
            target=target,
            migration_id=migration_id,
            manifest=manifest,
            phase=_COPYING_PHASE,
            started_at=started_at,
            completed_at="",
        )
        _atomic_write_json(staging / MIGRATION_MARKER_NAME, copying_marker)
        stage = "after_staging_created"
        _fault(fault_injector, stage, staging)

        stage = "copy_files"
        _copy_manifest_entries(
            source,
            staging,
            manifest.entries,
            fault_injector=fault_injector,
        )
        stage = "after_copy"
        _fault(fault_injector, stage, staging)

        stage = "copy_manifest"
        copied = build_state_manifest(staging)
        if copied != manifest:
            raise StateLayoutError("staging manifest does not match source manifest")
        stage = "after_copy_manifest"
        _fault(fault_injector, stage, staging)

        stage = "source_manifest_after_copy"
        source_after = build_state_manifest(source)
        if source_after != manifest:
            raise StateLayoutError("source changed while migration copy was in progress")
        stage = "after_source_manifest"
        _fault(fault_injector, stage, source)

        published_marker = _marker_payload(
            root=root,
            source=source,
            target=target,
            migration_id=migration_id,
            manifest=manifest,
            phase=MIGRATION_PUBLISHED_PHASE,
            started_at=started_at,
            completed_at=_now(),
        )
        stage = "before_marker_write"
        _fault(fault_injector, stage, staging / MIGRATION_MARKER_NAME)
        _atomic_write_json(staging / MIGRATION_MARKER_NAME, published_marker)
        stage = "after_marker_write"
        _fault(fault_injector, stage, staging / MIGRATION_MARKER_NAME)

        stage = "final_source_manifest"
        source_final = build_state_manifest(source)
        if source_final != manifest:
            raise StateLayoutError("source changed before migration publish")
        stage = "after_final_source_manifest"
        _fault(fault_injector, stage, source)

        stage = "before_publish"
        _fault(fault_injector, stage, target)
        source_at_publish = build_state_manifest(source)
        if source_at_publish != manifest:
            raise StateLayoutError("source changed at migration publish boundary")
        if _lexists(target):
            raise FileExistsError(f"migration target already exists: {target}")
        os.rename(staging, target)
        published = True
        _fsync_directory(root)
        stage = "after_publish"
        _fault(fault_injector, stage, target)

        stage = "before_marker_reread"
        _fault(fault_injector, stage, target / MIGRATION_MARKER_NAME)
        marker_valid, marker_reason = validate_published_marker(target)
        if not marker_valid:
            raise StateLayoutError(marker_reason)
        reread = read_migration_marker(target)
        if reread.get("migration_id") != migration_id:
            raise StateLayoutError("published migration id changed during reread")
        stage = "after_marker_reread"
        _fault(fault_injector, stage, target / MIGRATION_MARKER_NAME)

        inspection = inspect_state_layout_details(root)
        if inspection.layout is not StateLayout.MIGRATED:
            raise StateLayoutError(
                f"published layout was not authoritative: {inspection.reason}"
            )
        result = MigrationResult(
            MigrationStatus.MIGRATED,
            StateLayout.MIGRATED,
            str(root),
            migration_id=migration_id,
            source_digest=manifest.digest,
            file_count=manifest.file_count,
            total_bytes=manifest.total_bytes,
            published=True,
        )
        _write_receipt_best_effort(root, result)
        return result
    except Exception as exc:  # noqa: BLE001 - result preserves exact failed stage/type.
        if not published:
            _remove_owned_staging(root, staging, migration_id)
        inspection = inspect_state_layout_details(root)
        reason_code = "published_verification_failed" if published else "migration_failed"
        reported_stage = exc.stage if isinstance(exc, _MigrationStageFailure) else stage
        reported_error = exc.error if isinstance(exc, _MigrationStageFailure) else exc
        result = MigrationResult(
            MigrationStatus.FAILED,
            inspection.layout,
            str(root),
            reason_code=reason_code,
            detail=str(reported_error),
            migration_id=migration_id,
            source_digest=manifest.digest,
            file_count=manifest.file_count,
            total_bytes=manifest.total_bytes,
            published=published,
            failed_stage=reported_stage,
            error_type=type(reported_error).__name__,
        )
        _write_receipt_best_effort(root, result)
        return result


def _copy_manifest_entries(
    source: Path,
    staging: Path,
    entries: tuple[ManifestEntry, ...],
    *,
    fault_injector: MigrationFaultInjector | None,
) -> None:
    directories = [entry for entry in entries if entry.entry_type == "directory"]
    files = [entry for entry in entries if entry.entry_type == "file"]
    source_io = _filesystem_path(source)
    staging_io = _filesystem_path(staging)
    for entry in directories:
        (staging_io / entry.path).mkdir()
    for entry in files:
        source_path = source_io / entry.path
        target_path = staging_io / entry.path
        _require_plain_file(source_path, entry.path)
        try:
            _fault(fault_injector, "before_file_copy", source / entry.path)
        except Exception as exc:  # noqa: BLE001 - retain injected fault stage.
            raise _MigrationStageFailure("before_file_copy", exc) from exc
        _ = shutil.copy2(source_path, target_path, follow_symlinks=False)
        try:
            _fault(fault_injector, "after_file_copy", source / entry.path)
        except Exception as exc:  # noqa: BLE001 - retain injected fault stage.
            raise _MigrationStageFailure("after_file_copy", exc) from exc
    for entry in reversed(directories):
        shutil.copystat(
            source_io / entry.path,
            staging_io / entry.path,
            follow_symlinks=False,
        )


def _quiescence_reason(source: Path) -> str:
    ledger = source / "ledger.json"
    try:
        info = ledger.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            ledger.is_symlink()
            or attributes & reparse_flag
            or not stat.S_ISREG(info.st_mode)
        ):
            return "ledger_unreadable"
        raw: object = json.loads(ledger.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "ledger_unreadable"
    if not isinstance(raw, dict):
        return "ledger_unreadable"
    active = raw.get("active_turns")
    if not isinstance(active, dict) or not active:
        return ""
    for turn in active.values():
        if not isinstance(turn, dict):
            return "active_turn"
        invocations = turn.get("invocations")
        if isinstance(invocations, dict) and any(
            isinstance(invocation, dict) and invocation.get("status") == "open"
            for invocation in invocations.values()
        ):
            return "open_invocation"
    return "active_turn"


def _recover_orphan_staging(root: Path, minimum_age_seconds: float) -> None:
    inspection = inspect_state_layout_details(root)
    for staging in inspection.staging:
        if not _is_plain_directory(staging):
            continue
        marker = _read_json_object(staging / MIGRATION_MARKER_NAME)
        if marker is None or not _orphan_marker_matches(root, staging, marker):
            continue
        pid = marker.get("owner_pid")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            continue
        try:
            age = time.time() - staging.stat().st_mtime
        except OSError:
            continue
        if age < minimum_age_seconds or pid_is_alive(pid):
            continue
        migration_id = str(marker["migration_id"])
        _remove_owned_staging(root, staging, migration_id, owner_pid=pid)


def _orphan_marker_matches(
    root: Path,
    staging: Path,
    marker: Mapping[str, object],
) -> bool:
    schema = marker.get("schema_version")
    migration_id = marker.get("migration_id")
    pid = marker.get("owner_pid")
    phase = marker.get("phase")
    if (
        isinstance(schema, bool)
        or schema != MIGRATION_MARKER_SCHEMA_VERSION
        or not isinstance(migration_id, str)
        or not migration_id
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or phase not in {_COPYING_PHASE, MIGRATION_PUBLISHED_PHASE}
    ):
        return False
    if staging.name != f"{MIGRATION_STAGING_PREFIX}{pid}-{migration_id}":
        return False
    return (
        _canonical_path_value(marker.get("root")) == _canonical_path(root)
        and _canonical_path_value(marker.get("source"))
        == _canonical_path(root / LEGACY_STATE_DIR_NAME)
        and _canonical_path_value(marker.get("target"))
        == _canonical_path(root / STATE_DIR_NAME)
    )


def _marker_payload(
    *,
    root: Path,
    source: Path,
    target: Path,
    migration_id: str,
    manifest: StateManifest,
    phase: str,
    started_at: str,
    completed_at: str,
) -> dict[str, object]:
    return {
        "schema_version": MIGRATION_MARKER_SCHEMA_VERSION,
        "migration_id": migration_id,
        "owner_pid": os.getpid(),
        "root": str(root.resolve()),
        "source": str(source.resolve()),
        "target": str(target.resolve()),
        "phase": phase,
        "source_digest": manifest.digest,
        "source_file_count": manifest.file_count,
        "source_total_bytes": manifest.total_bytes,
        "started_at": started_at,
        "completed_at": completed_at,
        "tool_version": _tool_version(),
    }


def _already_migrated_result(inspection: LayoutInspection) -> MigrationResult:
    reason_code = ""
    detail = ""
    try:
        marker = read_migration_marker(inspection.target)
        migration_id = _nonempty_string(marker.get("migration_id"))
        digest = _nonempty_string(marker.get("source_digest"))
        count = _nonnegative_int(marker.get("source_file_count"))
        total = _nonnegative_int(marker.get("source_total_bytes"))
    except StateLayoutError:
        migration_id = ""
        digest = ""
        count = 0
        total = 0
    legacy = inspection.legacy
    if not _is_plain_directory(legacy):
        reason_code = "legacy_unavailable"
        detail = "published target remains authoritative; preserved legacy is unavailable"
    else:
        try:
            legacy_manifest = build_state_manifest(legacy)
        except (OSError, StateLayoutError):
            reason_code = "legacy_unverifiable"
            detail = "published target remains authoritative; legacy cannot be verified"
        else:
            if _manifest_tuple(legacy_manifest) != (digest, count, total):
                reason_code = "legacy_diverged"
                detail = "published target remains authoritative; legacy changed after publish"
    return MigrationResult(
        MigrationStatus.ALREADY_MIGRATED,
        StateLayout.MIGRATED,
        str(inspection.root),
        reason_code=reason_code,
        detail=detail,
        migration_id=migration_id,
        source_digest=digest,
        file_count=count,
        total_bytes=total,
        published=True,
    )


def _result(
    status: MigrationStatus,
    inspection: LayoutInspection,
    reason_code: str,
    detail: str = "",
    *,
    error_type: str = "",
) -> MigrationResult:
    return MigrationResult(
        status,
        inspection.layout,
        str(inspection.root),
        reason_code=reason_code,
        detail=detail,
        error_type=error_type,
    )


def _preparation_reason(inspection: LayoutInspection) -> str:
    if inspection.layout is StateLayout.MIGRATED:
        return "already_migrated"
    if inspection.layout is StateLayout.CONFLICT:
        return "layout_conflict"
    return "no_legacy_state"


def _uninspected_layout(root: Path) -> LayoutInspection:
    return LayoutInspection(
        StateLayout.EMPTY,
        root,
        root / LEGACY_STATE_DIR_NAME,
        root / STATE_DIR_NAME,
        (),
    )


def _manifest_tuple(manifest: StateManifest) -> tuple[str, int, int]:
    return manifest.digest, manifest.file_count, manifest.total_bytes


def _manifest_tuple_from_marker(marker: Mapping[str, object]) -> tuple[str, int, int]:
    schema = marker.get("schema_version")
    if isinstance(schema, bool) or schema != MIGRATION_MARKER_SCHEMA_VERSION:
        raise StateLayoutError("rollback marker schema is unsupported")
    return (
        _nonempty_string(marker.get("source_digest")),
        _nonnegative_int(marker.get("source_file_count")),
        _nonnegative_int(marker.get("source_total_bytes")),
    )


def _require_plain_file(path: Path, relative: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise StateLayoutError(f"source entry disappeared before copy: {relative}") from exc
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if path.is_symlink() or attributes & reparse_flag or not stat.S_ISREG(info.st_mode):
        raise StateLayoutError(f"source entry is no longer a plain file: {relative}")


def _is_plain_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return (
        not path.is_symlink()
        and not attributes & reparse_flag
        and stat.S_ISDIR(info.st_mode)
    )


def _remove_owned_staging(
    root: Path,
    staging: Path,
    migration_id: str,
    *,
    owner_pid: int | None = None,
) -> None:
    pid = os.getpid() if owner_pid is None else owner_pid
    expected = f"{MIGRATION_STAGING_PREFIX}{pid}-{migration_id}"
    if staging.name != expected or staging.parent.resolve() != root.resolve():
        return
    if not _lexists(staging) or not _is_plain_directory(staging):
        return

    def make_writable_and_retry(
        function: Callable[[str], object],
        path: str,
        _error: BaseException,
    ) -> None:
        os.chmod(path, os.stat(path).st_mode | stat.S_IWUSR)
        _ = function(path)

    try:
        shutil.rmtree(_filesystem_path(staging), onexc=make_writable_and_retry)
    except OSError:
        return


def _write_receipt_best_effort(root: Path, result: MigrationResult) -> None:
    payload = {
        "schema_version": MIGRATION_RECEIPT_SCHEMA_VERSION,
        "recorded_at": _now(),
        **result.as_dict(),
    }
    try:
        _atomic_write_json(migration_receipt_path(root), payload)
    except OSError:
        return


def _atomic_write_json(destination: Path, payload: Mapping[str, object]) -> None:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    prefix = (
        MIGRATION_RECEIPT_TEMP_PREFIX
        if destination.name == MIGRATION_RECEIPT_NAME
        else f".{destination.name}.tmp-"
    )
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=destination.parent,
        prefix=prefix,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            _ = handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, destination)
        except PermissionError as first_error:
            time.sleep(_REPLACE_RETRY_SECONDS)
            try:
                os.replace(temporary, destination)
            except OSError:
                raise first_error
        _fsync_directory(destination.parent)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _read_json_object(path: Path) -> dict[str, object] | None:
    try:
        info = path.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if path.is_symlink() or attributes & reparse_flag or not stat.S_ISREG(info.st_mode):
            return None
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _canonical_path_value(value: object) -> str:
    return _canonical_path(Path(value)) if isinstance(value, str) and value else ""


def _canonical_path(path: Path) -> str:
    normalized = os.path.normcase(str(path.resolve()))
    return normalized.casefold() if os.name == "nt" else normalized


def _filesystem_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path(f"\\\\?\\UNC\\{absolute.lstrip('\\')}")
    return Path(f"\\\\?\\{absolute}")


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _nonempty_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise StateLayoutError("migration marker string field is invalid")
    return value


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise StateLayoutError("migration marker integer field is invalid")
    return value


def _nonnegative_seconds(value: float, field: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return float(value)


def _fault(
    fault_injector: MigrationFaultInjector | None,
    stage: str,
    path: Path | None,
) -> None:
    if fault_injector is not None:
        fault_injector(stage, path)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _tool_version() -> str:
    try:
        return version("fable-lite")
    except PackageNotFoundError:
        return "unknown"
