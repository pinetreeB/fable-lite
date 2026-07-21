from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
from unittest.mock import patch

import pytest

from core.state_layout import STATE_DIR_NAME, StateLayout, inspect_state_layout
from core.state_migration import MigrationStatus, migrate_state


def _source(root: Path) -> Path:
    source = root / ".fable-lite"
    source.mkdir(parents=True)
    (source / "ledger.json").write_text(
        '{"schema_version":2,"active_turns":{}}', encoding="utf-8"
    )
    return source


def test_link_or_reparse_source_entry_is_rejected_without_publish(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    external = tmp_path / "external.txt"
    external.write_text("external", encoding="utf-8")
    try:
        (source / "linked.txt").symlink_to(external)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    result = migrate_state(tmp_path, lock_wait_seconds=0)

    assert result.status is MigrationStatus.FAILED
    assert "link or reparse" in result.detail
    assert (tmp_path / STATE_DIR_NAME).exists() is False
    assert external.read_text(encoding="utf-8") == "external"


def test_readonly_file_and_bounded_long_path_copy_without_source_mutation(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    nested = source
    index = 0
    while True:
        candidate = nested / f"segment-{index}-abcdefghijklmnop"
        if len(str(candidate / "readonly-payload.txt")) > 235:
            break
        nested = candidate
        nested.mkdir()
        index += 1
    payload = nested / "readonly-payload.txt"
    payload.write_text("preserved", encoding="utf-8")
    original_mode = stat.S_IMODE(payload.stat().st_mode)
    payload.chmod(stat.S_IREAD)
    try:
        result = migrate_state(tmp_path, lock_wait_seconds=0)
        copied = tmp_path / STATE_DIR_NAME / payload.relative_to(source)

        assert result.status is MigrationStatus.MIGRATED
        assert copied.read_text(encoding="utf-8") == "preserved"
        assert payload.read_text(encoding="utf-8") == "preserved"
    finally:
        payload.chmod(original_mode | stat.S_IWRITE)


@pytest.mark.skipif(os.name != "nt", reason="Windows open-handle copy contract")
def test_windows_open_source_handle_does_not_require_legacy_rename(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    payload = source / "open.txt"
    payload.write_text("open", encoding="utf-8")

    with payload.open("rb") as handle:
        result = migrate_state(tmp_path, lock_wait_seconds=0)
        assert handle.read() == b"open"

    assert result.status is MigrationStatus.MIGRATED
    assert inspect_state_layout(tmp_path) is StateLayout.MIGRATED


@pytest.mark.skipif(os.name != "nt", reason="Windows junction/reparse contract")
def test_windows_junction_is_rejected_without_following_external_tree(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    external = tmp_path / "external-directory"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("outside", encoding="utf-8")
    junction = source / "junction"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(external)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation is unavailable: {created.stderr}")
    try:
        result = migrate_state(tmp_path, lock_wait_seconds=0)

        assert result.status is MigrationStatus.FAILED
        assert "link or reparse" in result.detail
        assert sentinel.read_text(encoding="utf-8") == "outside"
        assert (tmp_path / STATE_DIR_NAME).exists() is False
    finally:
        os.rmdir(junction)


def test_copy_permission_error_preserves_legacy_authority(tmp_path: Path) -> None:
    source = _source(tmp_path)
    payload = source / "payload.txt"
    payload.write_text("source", encoding="utf-8")

    with patch(
        "core.state_migration.shutil.copy2",
        side_effect=PermissionError("antivirus denied copy"),
    ):
        result = migrate_state(tmp_path, lock_wait_seconds=0)

    assert result.status is MigrationStatus.FAILED
    assert result.published is False
    assert payload.read_text(encoding="utf-8") == "source"
    assert inspect_state_layout(tmp_path) is StateLayout.LEGACY


@pytest.mark.skipif(os.name != "nt", reason="Windows casefold collision contract")
def test_windows_casefold_collision_is_rejected(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    (source / "Straße").write_text("one", encoding="utf-8")
    try:
        (source / "STRASSE").write_text("two", encoding="utf-8")
    except OSError as exc:
        pytest.skip(f"filesystem cannot create the casefold fixture: {exc}")

    result = migrate_state(tmp_path, lock_wait_seconds=0)

    assert result.status is MigrationStatus.FAILED
    assert "casefold" in result.detail
    assert (tmp_path / STATE_DIR_NAME).exists() is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode preservation")
def test_posix_file_mode_is_preserved(tmp_path: Path) -> None:
    source = _source(tmp_path)
    executable = source / "tool.sh"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o751)

    result = migrate_state(tmp_path, lock_wait_seconds=0)

    copied = tmp_path / STATE_DIR_NAME / "tool.sh"
    assert result.status is MigrationStatus.MIGRATED
    assert stat.S_IMODE(copied.stat().st_mode) == 0o751
