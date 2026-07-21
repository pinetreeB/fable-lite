from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import cast
from unittest.mock import patch

import pytest

import core.state_migration as state_migration
from core.file_lock import owner_lock
from core.ledger_storage import state_dir as live_state_dir
from core.state_layout import (
    LEGACY_STATE_DIR_NAME,
    MIGRATION_LOCK_NAME,
    MIGRATION_MARKER_NAME,
    MIGRATION_RECEIPT_NAME,
    MIGRATION_STAGING_PREFIX,
    STATE_DIR_NAME,
    StateLayout,
    build_state_manifest,
    inspect_state_layout,
    validate_published_marker,
)
from core.state_migration import (
    MigrationStatus,
    RollbackSafety,
    assess_rollback,
    migrate_state,
    prepare_state_layout,
)


ROOT = Path(__file__).resolve().parents[1]


def _legacy(root: Path, *, active: object | None = None) -> Path:
    source = root / LEGACY_STATE_DIR_NAME
    source.mkdir(parents=True)
    ledger = {
        "schema_version": 2,
        "active_turns": {} if active is None else active,
    }
    (source / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    (source / "nested").mkdir()
    (source / "nested" / "payload.bin").write_bytes(b"\x00wave-one\xff")
    (source / "config.json").write_text(
        '{"schema_version":1,"supervision":true}', encoding="utf-8"
    )
    return source


def _file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _staging(root: Path) -> tuple[Path, ...]:
    return tuple(root.glob(f"{MIGRATION_STAGING_PREFIX}*"))


def _run_cli(root: Path) -> subprocess.CompletedProcess[str]:
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-m", "fable_lite", "migrate", "--root", str(root)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": python_path, "PYTHONIOENCODING": "utf-8"},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _orphan_marker(
    root: Path,
    *,
    pid: int = 2_147_483_647,
    migration_id: str = "orphan",
    recorded_root: Path | None = None,
) -> Path:
    staging = root / f"{MIGRATION_STAGING_PREFIX}{pid}-{migration_id}"
    staging.mkdir()
    payload = {
        "schema_version": 1,
        "migration_id": migration_id,
        "owner_pid": pid,
        "root": str((recorded_root or root).resolve()),
        "source": str((root / LEGACY_STATE_DIR_NAME).resolve()),
        "target": str((root / STATE_DIR_NAME).resolve()),
        "phase": "copying",
    }
    (staging / MIGRATION_MARKER_NAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    os.utime(staging, (0, 0))
    return staging


def test_migrate_copy_verifies_publishes_preserves_source_and_is_idempotent(
    tmp_path: Path,
) -> None:
    source = _legacy(tmp_path)
    (source / "ledger.guard").write_text("transient", encoding="utf-8")
    (source / "abandoned.tmp").write_text("partial", encoding="utf-8")
    before = _file_snapshot(source)

    first = migrate_state(tmp_path, lock_wait_seconds=0)
    target = tmp_path / STATE_DIR_NAME

    assert first.status is MigrationStatus.MIGRATED
    assert first.published is True
    assert inspect_state_layout(tmp_path) is StateLayout.MIGRATED
    assert validate_published_marker(target) == (True, "")
    assert build_state_manifest(source) == build_state_manifest(target)
    assert _file_snapshot(source) == before
    assert (target / "config.json").exists() is False
    assert (target / "ledger.guard").exists() is False
    assert (target / "abandoned.tmp").exists() is False
    assert live_state_dir(str(tmp_path)) == source
    assert assess_rollback(tmp_path) is RollbackSafety.SAFE_UNCHANGED
    assert (tmp_path / MIGRATION_RECEIPT_NAME).is_file()
    marker_before = (target / MIGRATION_MARKER_NAME).read_bytes()
    target_before = _file_snapshot(target)

    second = migrate_state(tmp_path, lock_wait_seconds=0)

    assert second.status is MigrationStatus.ALREADY_MIGRATED
    assert second.migration_id == first.migration_id
    assert (target / MIGRATION_MARKER_NAME).read_bytes() == marker_before
    assert _file_snapshot(target) == target_before
    assert (tmp_path / MIGRATION_LOCK_NAME).exists() is False
    assert _staging(tmp_path) == ()


def test_prepare_is_read_only_and_never_performs_automatic_migration(
    tmp_path: Path,
) -> None:
    source = _legacy(tmp_path)
    before = _file_snapshot(source)

    preparation = prepare_state_layout(tmp_path, True)

    assert preparation.migration_allowed is True
    assert preparation.inspection.layout is StateLayout.LEGACY
    assert _file_snapshot(source) == before
    assert (tmp_path / STATE_DIR_NAME).exists() is False
    assert (tmp_path / MIGRATION_LOCK_NAME).exists() is False
    assert (tmp_path / MIGRATION_RECEIPT_NAME).exists() is False


def test_inactive_and_exact_home_checks_happen_before_layout_access(
    tmp_path: Path,
) -> None:
    root = tmp_path / "must-not-exist"
    with patch(
        "core.state_migration.inspect_state_layout_details",
        side_effect=AssertionError("layout must not be read"),
    ):
        inactive = migrate_state(root, activation=False)
    assert inactive.status is MigrationStatus.INACTIVE
    assert root.exists() is False

    with (
        patch("core.state_migration.is_user_home_root", return_value=True),
        patch(
            "core.state_migration.inspect_state_layout_details",
            side_effect=AssertionError("home layout must not be read"),
        ),
    ):
        home = migrate_state(root, activation=True)
    assert home.status is MigrationStatus.HOME_REFUSED
    assert root.exists() is False


def test_real_exact_home_leaves_every_migration_artifact_unchanged() -> None:
    home = Path.home().resolve()

    def artifacts() -> tuple[tuple[str, int, int], ...]:
        candidates = [
            home / STATE_DIR_NAME,
            home / MIGRATION_LOCK_NAME,
            home / MIGRATION_RECEIPT_NAME,
            *home.glob(f"{MIGRATION_STAGING_PREFIX}*"),
        ]
        records: list[tuple[str, int, int]] = []
        for path in candidates:
            try:
                info = path.lstat()
            except OSError:
                continue
            records.append((path.name, info.st_size, info.st_mtime_ns))
        return tuple(sorted(records))

    before = artifacts()
    result = migrate_state(home, activation=True)

    assert result.status is MigrationStatus.HOME_REFUSED
    assert artifacts() == before


@pytest.mark.parametrize(
    ("active", "reason"),
    [
        ({"agent": {"turn_id": "open"}}, "active_turn"),
        (
            {
                "agent": {
                    "turn_id": "open",
                    "invocations": {"one": {"status": "open"}},
                }
            },
            "open_invocation",
        ),
    ],
)
def test_active_turn_or_open_invocation_defers_with_zero_artifacts(
    tmp_path: Path,
    active: object,
    reason: str,
) -> None:
    source = _legacy(tmp_path, active=active)
    before = _file_snapshot(source)

    result = migrate_state(tmp_path, lock_wait_seconds=0)

    assert result.status is MigrationStatus.DEFERRED
    assert result.reason_code == reason
    assert _file_snapshot(source) == before
    assert (tmp_path / STATE_DIR_NAME).exists() is False
    assert (tmp_path / MIGRATION_LOCK_NAME).exists() is False
    assert (tmp_path / MIGRATION_RECEIPT_NAME).exists() is False
    assert _staging(tmp_path) == ()


@pytest.mark.parametrize(
    "failed_stage",
    [
        "before_manifest",
        "after_manifest",
        "after_staging_created",
        "before_file_copy",
        "after_file_copy",
        "after_copy",
        "after_copy_manifest",
        "after_source_manifest",
        "before_marker_write",
        "after_marker_write",
        "after_final_source_manifest",
        "before_publish",
    ],
)
def test_faults_before_publish_reuse_legacy_and_remove_owned_staging(
    tmp_path: Path,
    failed_stage: str,
) -> None:
    root = tmp_path / failed_stage
    source = _legacy(root)
    before = _file_snapshot(source)

    def fail(stage: str, _path: Path | None) -> None:
        if stage == failed_stage:
            raise OSError(f"injected {stage}")

    result = migrate_state(root, lock_wait_seconds=0, fault_injector=fail)

    assert result.status is MigrationStatus.FAILED
    assert result.published is False
    assert result.failed_stage == failed_stage
    assert inspect_state_layout(root) is StateLayout.LEGACY
    assert _file_snapshot(source) == before
    assert (root / STATE_DIR_NAME).exists() is False
    assert _staging(root) == ()
    assert (root / MIGRATION_RECEIPT_NAME).is_file()


@pytest.mark.parametrize(
    "failed_stage",
    ["after_publish", "before_marker_reread", "after_marker_reread"],
)
def test_faults_after_publish_never_fall_back_or_remove_valid_target(
    tmp_path: Path,
    failed_stage: str,
) -> None:
    root = tmp_path / failed_stage
    _legacy(root)

    def fail(stage: str, _path: Path | None) -> None:
        if stage == failed_stage:
            raise OSError(f"injected {stage}")

    result = migrate_state(root, lock_wait_seconds=0, fault_injector=fail)

    assert result.status is MigrationStatus.FAILED
    assert result.published is True
    assert result.failed_stage == failed_stage
    assert inspect_state_layout(root) is StateLayout.MIGRATED
    assert (root / STATE_DIR_NAME).is_dir()
    assert migrate_state(root).status is MigrationStatus.ALREADY_MIGRATED


def test_marker_atomic_write_and_rename_failures_do_not_publish(tmp_path: Path) -> None:
    marker_root = tmp_path / "marker"
    marker_source = _legacy(marker_root)
    marker_before = _file_snapshot(marker_source)
    real_write = state_migration._atomic_write_json

    def fail_published_marker(path: Path, payload: dict[str, object]) -> None:
        if path.name == MIGRATION_MARKER_NAME and payload.get("phase") == "published":
            raise PermissionError("injected marker replace denial")
        real_write(path, payload)

    with patch("core.state_migration._atomic_write_json", side_effect=fail_published_marker):
        marker_result = migrate_state(marker_root, lock_wait_seconds=0)
    assert marker_result.status is MigrationStatus.FAILED
    assert marker_result.published is False
    assert _file_snapshot(marker_source) == marker_before
    assert (marker_root / STATE_DIR_NAME).exists() is False

    rename_root = tmp_path / "rename"
    rename_source = _legacy(rename_root)
    rename_before = _file_snapshot(rename_source)
    with patch("core.state_migration.os.rename", side_effect=PermissionError("denied")):
        rename_result = migrate_state(rename_root, lock_wait_seconds=0)
    assert rename_result.status is MigrationStatus.FAILED
    assert rename_result.failed_stage == "before_publish"
    assert _file_snapshot(rename_source) == rename_before
    assert (rename_root / STATE_DIR_NAME).exists() is False


def test_publish_marker_reread_failure_keeps_target_for_crash_recovery(
    tmp_path: Path,
) -> None:
    _legacy(tmp_path)
    with patch(
        "core.state_migration.validate_published_marker",
        return_value=(False, "injected reread failure"),
    ):
        result = migrate_state(tmp_path, lock_wait_seconds=0)

    assert result.status is MigrationStatus.FAILED
    assert result.published is True
    assert result.failed_stage == "before_marker_reread"
    assert inspect_state_layout(tmp_path) is StateLayout.MIGRATED


def test_unlocked_source_mutation_is_detected_before_publish(tmp_path: Path) -> None:
    source = _legacy(tmp_path)
    payload = source / "nested" / "payload.bin"

    def mutate(stage: str, _path: Path | None) -> None:
        if stage == "after_copy":
            with payload.open("ab") as handle:
                _ = handle.write(b"mutated")

    result = migrate_state(tmp_path, lock_wait_seconds=0, fault_injector=mutate)

    assert result.status is MigrationStatus.FAILED
    assert result.published is False
    assert "source changed" in result.detail
    assert inspect_state_layout(tmp_path) is StateLayout.LEGACY
    assert (tmp_path / STATE_DIR_NAME).exists() is False


def test_source_mutation_at_final_publish_boundary_is_rejected(tmp_path: Path) -> None:
    source = _legacy(tmp_path)
    payload = source / "nested" / "payload.bin"

    def mutate(stage: str, _path: Path | None) -> None:
        if stage == "before_publish":
            with payload.open("ab") as handle:
                _ = handle.write(b"last-window-write")

    result = migrate_state(tmp_path, lock_wait_seconds=0, fault_injector=mutate)

    assert result.status is MigrationStatus.FAILED
    assert result.failed_stage == "before_publish"
    assert result.published is False
    assert "publish boundary" in result.detail
    assert (tmp_path / STATE_DIR_NAME).exists() is False


def test_fault_hook_observes_every_included_file_copy_and_no_excluded_file(
    tmp_path: Path,
) -> None:
    source = _legacy(tmp_path)
    (source / "ignored.lock").write_text("lock", encoding="utf-8")
    copied: list[str] = []

    def observe(stage: str, path: Path | None) -> None:
        if stage == "before_file_copy" and path is not None:
            copied.append(path.relative_to(source).as_posix())

    result = migrate_state(tmp_path, lock_wait_seconds=0, fault_injector=observe)

    assert result.status is MigrationStatus.MIGRATED
    assert copied == ["ledger.json", "nested/payload.bin"]


def test_true_crash_before_publish_leaves_orphan_and_after_publish_is_idempotent(
    tmp_path: Path,
) -> None:
    before_root = tmp_path / "before"
    _legacy(before_root)

    def crash_before(stage: str, _path: Path | None) -> None:
        if stage == "after_marker_write":
            raise KeyboardInterrupt("simulated process death")

    with pytest.raises(KeyboardInterrupt):
        migrate_state(before_root, lock_wait_seconds=0, fault_injector=crash_before)
    assert inspect_state_layout(before_root) is StateLayout.MIGRATING
    assert (before_root / STATE_DIR_NAME).exists() is False
    assert len(_staging(before_root)) == 1

    after_root = tmp_path / "after"
    _legacy(after_root)

    def crash_after(stage: str, _path: Path | None) -> None:
        if stage == "after_publish":
            raise KeyboardInterrupt("simulated process death")

    with pytest.raises(KeyboardInterrupt):
        migrate_state(after_root, lock_wait_seconds=0, fault_injector=crash_after)
    assert inspect_state_layout(after_root) is StateLayout.MIGRATED
    assert migrate_state(after_root).status is MigrationStatus.ALREADY_MIGRATED


def test_only_trusted_dead_old_orphan_is_collected(tmp_path: Path) -> None:
    trusted_root = tmp_path / "trusted"
    _legacy(trusted_root)
    trusted = _orphan_marker(trusted_root)

    trusted_result = migrate_state(
        trusted_root, lock_wait_seconds=0, orphan_min_age_seconds=0
    )

    assert trusted_result.status is MigrationStatus.MIGRATED
    assert trusted.exists() is False

    untrusted_root = tmp_path / "untrusted"
    _legacy(untrusted_root)
    untrusted = _orphan_marker(
        untrusted_root, recorded_root=tmp_path / "different-root"
    )

    untrusted_result = migrate_state(
        untrusted_root, lock_wait_seconds=0, orphan_min_age_seconds=0
    )

    assert untrusted_result.status is MigrationStatus.MIGRATED
    assert untrusted.is_dir()

    live_root = tmp_path / "live"
    _legacy(live_root)
    live = _orphan_marker(live_root, pid=os.getpid())

    live_result = migrate_state(live_root, lock_wait_seconds=0, orphan_min_age_seconds=0)

    assert live_result.status is MigrationStatus.MIGRATED
    assert live.is_dir()


def test_markerless_target_conflict_is_never_overwritten(tmp_path: Path) -> None:
    _legacy(tmp_path)
    target = tmp_path / STATE_DIR_NAME
    target.mkdir()
    sentinel = target / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = migrate_state(tmp_path, lock_wait_seconds=0)

    assert result.status is MigrationStatus.CONFLICT
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert _staging(tmp_path) == ()


def test_target_created_in_publish_race_is_not_overwritten(tmp_path: Path) -> None:
    _legacy(tmp_path)
    real_rename = os.rename

    def create_competing_target(source: Path, target: Path) -> None:
        target_path = Path(target)
        target_path.mkdir()
        (target_path / "competitor.txt").write_text("keep", encoding="utf-8")
        real_rename(source, target)

    with patch("core.state_migration.os.rename", side_effect=create_competing_target):
        result = migrate_state(tmp_path, lock_wait_seconds=0)

    assert result.status is MigrationStatus.FAILED
    assert result.published is False
    assert (tmp_path / STATE_DIR_NAME / "competitor.txt").read_text(
        encoding="utf-8"
    ) == "keep"


def test_layout_lock_busy_is_deferred_and_dead_owner_is_recovered(
    tmp_path: Path,
) -> None:
    busy_root = tmp_path / "busy"
    _legacy(busy_root)
    layout_lock = busy_root / MIGRATION_LOCK_NAME
    with owner_lock(layout_lock, wait_seconds=0):
        busy = migrate_state(busy_root, lock_wait_seconds=0)
    assert busy.status is MigrationStatus.DEFERRED
    assert busy.reason_code == "layout_lock_busy"
    assert (busy_root / STATE_DIR_NAME).exists() is False

    stale_root = tmp_path / "stale"
    _legacy(stale_root)
    stale_lock = stale_root / MIGRATION_LOCK_NAME
    stale_lock.write_text("2147483647:dead-owner", encoding="ascii")

    recovered = migrate_state(stale_root, lock_wait_seconds=0)

    assert recovered.status is MigrationStatus.MIGRATED
    assert stale_lock.exists() is False


def test_concurrent_cli_migrators_have_exactly_one_publisher(tmp_path: Path) -> None:
    source = _legacy(tmp_path)
    (source / "large.bin").write_bytes(b"x" * (2 * 1024 * 1024))
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    command = [
        sys.executable,
        "-m",
        "fable_lite",
        "migrate",
        "--root",
        str(tmp_path),
    ]
    processes = [
        subprocess.Popen(
            command,
            cwd=ROOT,
            env={
                **os.environ,
                "PYTHONPATH": python_path,
                "PYTHONIOENCODING": "utf-8",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for _ in range(4)
    ]
    completed = [process.communicate(timeout=60) for process in processes]

    assert [process.returncode for process in processes] == [0, 0, 0, 0]
    payloads = [json.loads(stdout) for stdout, _stderr in completed]
    statuses = [payload["status"] for payload in payloads]
    assert statuses.count(MigrationStatus.MIGRATED.value) == 1
    assert statuses.count(MigrationStatus.ALREADY_MIGRATED.value) == 3
    assert inspect_state_layout(tmp_path) is StateLayout.MIGRATED
    assert (tmp_path / MIGRATION_LOCK_NAME).exists() is False
    assert _staging(tmp_path) == ()


def test_cli_smtw_migrate_contract_emits_machine_readable_result(tmp_path: Path) -> None:
    _legacy(tmp_path)

    completed = _run_cli(tmp_path)
    payload = cast(dict[str, object], json.loads(completed.stdout))

    assert completed.returncode == 0, completed.stderr
    assert payload["status"] == MigrationStatus.MIGRATED.value
    assert payload["layout"] == StateLayout.MIGRATED.value
    assert payload["ok"] is True


def test_rollback_assessment_refuses_any_post_publish_or_legacy_write(
    tmp_path: Path,
) -> None:
    target_root = tmp_path / "target-write"
    _legacy(target_root)
    assert migrate_state(target_root).status is MigrationStatus.MIGRATED
    (target_root / STATE_DIR_NAME / "new-event.json").write_text(
        "new", encoding="utf-8"
    )
    assert assess_rollback(target_root) is RollbackSafety.TARGET_DIVERGED
    assert (target_root / STATE_DIR_NAME).is_dir()

    legacy_root = tmp_path / "legacy-write"
    source = _legacy(legacy_root)
    assert migrate_state(legacy_root).status is MigrationStatus.MIGRATED
    (source / "late-v2-write.json").write_text("late", encoding="utf-8")
    assert assess_rollback(legacy_root) is RollbackSafety.LEGACY_DIVERGED
    assert inspect_state_layout(legacy_root) is StateLayout.MIGRATED
    observed = migrate_state(legacy_root)
    assert observed.status is MigrationStatus.ALREADY_MIGRATED
    assert observed.reason_code == "legacy_diverged"


def test_receipt_distinguishes_failed_stage_and_published_state(tmp_path: Path) -> None:
    _legacy(tmp_path)

    def fail(stage: str, _path: Path | None) -> None:
        if stage == "after_copy":
            raise PermissionError("antivirus-style denial")

    result = migrate_state(tmp_path, lock_wait_seconds=0, fault_injector=fail)
    receipt = json.loads((tmp_path / MIGRATION_RECEIPT_NAME).read_text(encoding="utf-8"))

    assert result.status is MigrationStatus.FAILED
    assert receipt["status"] == MigrationStatus.FAILED.value
    assert receipt["failed_stage"] == "after_copy"
    assert receipt["published"] is False
    assert receipt["error_type"] == "PermissionError"


def test_fixture_rehearsal_preserves_ledger_bytes_hash_count_and_size(
    tmp_path: Path,
) -> None:
    source = tmp_path / LEGACY_STATE_DIR_NAME
    source.mkdir()
    fixture = ROOT / "tests" / "fixtures" / "v2-provenance" / "ledger.json"
    ledger_bytes = fixture.read_bytes()
    (source / "ledger.json").write_bytes(ledger_bytes)
    (source / "snapshots").mkdir()
    (source / "snapshots" / "baseline.json").write_bytes(b'{"fixture":true}')
    immutable = _file_snapshot(source)
    manifest = build_state_manifest(source)

    result = migrate_state(tmp_path, lock_wait_seconds=0)

    assert result.status is MigrationStatus.DEFERRED
    assert result.reason_code == "active_turn"
    assert _file_snapshot(source) == immutable
    assert build_state_manifest(source) == manifest

    ledger = json.loads((source / "ledger.json").read_text(encoding="utf-8"))
    ledger["active_turns"] = {}
    (source / "ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    immutable_quiesced = _file_snapshot(source)
    quiesced_manifest = build_state_manifest(source)

    migrated = migrate_state(tmp_path, lock_wait_seconds=0)

    assert migrated.status is MigrationStatus.MIGRATED
    assert migrated.source_digest == quiesced_manifest.digest
    assert migrated.file_count == quiesced_manifest.file_count
    assert migrated.total_bytes == quiesced_manifest.total_bytes
    assert _file_snapshot(source) == immutable_quiesced
    assert (tmp_path / STATE_DIR_NAME / "ledger.json").read_bytes() == (
        source / "ledger.json"
    ).read_bytes()
