from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from core.destructive_guard import evaluate_r2_destructive_gate
from core.ledger_schema import JsonObject
from core.ledger_storage import state_dir as live_state_dir
from core.provenance_policy import is_hard_excluded, is_harness_state_path
from core.state_layout import (
    LEGACY_STATE_DIR_NAME,
    MIGRATION_LOCK_NAME,
    MIGRATION_MARKER_NAME,
    MIGRATION_MARKER_SCHEMA_VERSION,
    MIGRATION_PUBLISHED_PHASE,
    MIGRATION_RECEIPT_NAME,
    MIGRATION_RECEIPT_TEMP_PREFIX,
    MIGRATION_STAGING_PREFIX,
    PROJECT_CONFIG_NAME,
    STATE_DIR_NAME,
    StateLayout,
    StateLayoutError,
    build_state_manifest,
    inspect_state_layout,
    inspect_state_layout_details,
    is_protected_state_name,
)


def _write_published_marker(target: Path, source: Path) -> None:
    manifest = build_state_manifest(target)
    now = datetime.now(UTC).isoformat()
    marker = {
        "schema_version": MIGRATION_MARKER_SCHEMA_VERSION,
        "migration_id": "test-migration",
        "root": str(target.parent.resolve()),
        "source": str(source.resolve()),
        "target": str(target.resolve()),
        "phase": MIGRATION_PUBLISHED_PHASE,
        "source_digest": manifest.digest,
        "source_file_count": manifest.file_count,
        "source_total_bytes": manifest.total_bytes,
        "started_at": now,
        "completed_at": now,
        "tool_version": "test",
    }
    (target / MIGRATION_MARKER_NAME).write_text(
        json.dumps(marker), encoding="utf-8"
    )


def _r2_payload(root: Path, command: str) -> JsonObject:
    return {
        "project_root": str(root),
        "tool_name": "Bash",
        "command": command,
        "host": "codex_cli",
        "agent": "codex",
        "session_id": "state-layout",
    }


def _evaluate_r2(root: Path, command: str) -> JsonObject:
    return evaluate_r2_destructive_gate(
        _r2_payload(root, command),
        lookup_path_attribution=lambda _ledger, _canonical: None,
        attribution_health=lambda _ledger: {
            "degraded": False,
            "capacity_exceeded": False,
        },
    )


def test_layout_inspection_and_live_state_facade_have_no_write_side_effect(
    tmp_path: Path,
) -> None:
    root = tmp_path / "not-created"

    assert live_state_dir(str(root)) == root.resolve() / LEGACY_STATE_DIR_NAME
    assert inspect_state_layout(root) is StateLayout.EMPTY
    assert root.exists() is False


def test_layout_inspector_classifies_legacy_native_and_migrating(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "legacy"
    (legacy_root / LEGACY_STATE_DIR_NAME).mkdir(parents=True)
    native_root = tmp_path / "native"
    (native_root / STATE_DIR_NAME).mkdir(parents=True)
    migrating_root = tmp_path / "migrating"
    (migrating_root / LEGACY_STATE_DIR_NAME).mkdir(parents=True)
    (migrating_root / f"{MIGRATION_STAGING_PREFIX}123-dead").mkdir()

    assert inspect_state_layout(legacy_root) is StateLayout.LEGACY
    assert inspect_state_layout(native_root) is StateLayout.NATIVE
    assert inspect_state_layout(migrating_root) is StateLayout.MIGRATING


def test_markerless_target_and_legacy_are_a_conflict(tmp_path: Path) -> None:
    (tmp_path / LEGACY_STATE_DIR_NAME).mkdir()
    (tmp_path / STATE_DIR_NAME).mkdir()

    inspection = inspect_state_layout_details(tmp_path)

    assert inspection.layout is StateLayout.CONFLICT
    assert "markerless target" in inspection.reason


def test_valid_published_target_is_authoritative_even_with_legacy_and_orphan_stage(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / LEGACY_STATE_DIR_NAME
    target = tmp_path / STATE_DIR_NAME
    legacy.mkdir()
    target.mkdir()
    (target / "ledger.json").write_text('{"seq": 1}', encoding="utf-8")
    _write_published_marker(target, legacy)
    (tmp_path / f"{MIGRATION_STAGING_PREFIX}999-orphan").mkdir()

    assert inspect_state_layout(tmp_path) is StateLayout.MIGRATED


def test_tampered_or_malformed_published_target_is_a_conflict(tmp_path: Path) -> None:
    legacy = tmp_path / LEGACY_STATE_DIR_NAME
    target = tmp_path / STATE_DIR_NAME
    legacy.mkdir()
    target.mkdir()
    ledger = target / "ledger.json"
    ledger.write_text("before", encoding="utf-8")
    _write_published_marker(target, legacy)
    ledger.write_text("after", encoding="utf-8")

    assert inspect_state_layout(tmp_path) is StateLayout.CONFLICT

    ledger.write_text("before", encoding="utf-8")
    (target / MIGRATION_MARKER_NAME).write_text("not-json", encoding="utf-8")
    assert inspect_state_layout(tmp_path) is StateLayout.CONFLICT


def test_non_directory_layout_path_is_a_conflict(tmp_path: Path) -> None:
    (tmp_path / STATE_DIR_NAME).write_text("not a directory", encoding="utf-8")

    inspection = inspect_state_layout_details(tmp_path)

    assert inspection.layout is StateLayout.CONFLICT
    assert "not a directory" in inspection.reason


def test_manifest_excludes_activation_marker_and_transient_lock_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "ledger.json").write_text("ledger", encoding="utf-8")
    (tmp_path / "config.json").write_text("activation", encoding="utf-8")
    (tmp_path / "ledger.lock").write_text("owner", encoding="utf-8")
    (tmp_path / "copy.tmp").write_text("partial", encoding="utf-8")
    (tmp_path / MIGRATION_MARKER_NAME).write_text("marker", encoding="utf-8")

    manifest = build_state_manifest(tmp_path)

    assert [entry.path for entry in manifest.entries] == ["ledger.json"]
    assert manifest.file_count == 1
    assert manifest.total_bytes == len("ledger")


def test_manifest_rejects_windows_casefold_collisions(tmp_path: Path) -> None:
    (tmp_path / "Straße").write_text("one", encoding="utf-8")
    try:
        (tmp_path / "STRASSE").write_text("two", encoding="utf-8")
    except OSError as exc:
        pytest.skip(f"filesystem cannot create the casefold fixture: {exc}")

    with pytest.raises(StateLayoutError, match="casefold"):
        build_state_manifest(tmp_path, windows=True)


@pytest.mark.parametrize(
    "name",
    [
        STATE_DIR_NAME,
        LEGACY_STATE_DIR_NAME,
        f"{MIGRATION_STAGING_PREFIX}123-token",
        MIGRATION_LOCK_NAME,
        MIGRATION_RECEIPT_NAME,
        f"{MIGRATION_RECEIPT_TEMP_PREFIX}token",
    ],
)
def test_r2_hard_blocks_every_state_generation_and_migration_control_path(
    tmp_path: Path,
    name: str,
) -> None:
    result = _evaluate_r2(tmp_path, f"rm -rf {name}")

    assert result["decision"] == "block"
    assert "state_dir_protected" in str(result["reason"])


def test_state_prefix_lookalikes_and_tracked_config_are_not_r2_state_paths(
    tmp_path: Path,
) -> None:
    result = _evaluate_r2(tmp_path, f"rm {PROJECT_CONFIG_NAME}")

    assert result["decision"] == "allow"
    assert is_protected_state_name(".smtw-user") is False


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"{STATE_DIR_NAME}/ledger.json", True),
        (f"{LEGACY_STATE_DIR_NAME}/ledger.json", True),
        (f"{MIGRATION_STAGING_PREFIX}1-token/ledger.json", True),
        (MIGRATION_LOCK_NAME, True),
        (MIGRATION_RECEIPT_NAME, True),
        (f"{MIGRATION_RECEIPT_TEMP_PREFIX}token", True),
        (PROJECT_CONFIG_NAME, False),
        ("nested/.smtw/ledger.json", False),
    ],
)
def test_provenance_excludes_runtime_and_migration_paths_but_tracks_config(
    path: str,
    expected: bool,
) -> None:
    assert is_harness_state_path(path) is expected
    assert is_hard_excluded(path) is expected
