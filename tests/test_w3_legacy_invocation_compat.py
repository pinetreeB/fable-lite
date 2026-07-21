from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import core.ledger_migration as migration_module
from core.destructive_guard import evaluate_r2_destructive_gate
from core.ledger import load_ledger, record_event, save_ledger
from core.ledger_schema import JsonObject, LedgerSchemaError, validate_v2_ledger
from core.ledger_storage import ledger_path
from core.ledger_v2 import (
    apply_v2_event,
    default_v2_ledger,
)


OS_REPLACE = os.replace
STATUS_ARCHIVE = "ledger.v2-invocation-status.json.bak"


def _legacy_status_ledger() -> tuple[JsonObject, JsonObject]:
    ledger = default_v2_ledger()
    _ = apply_v2_event(
        ledger,
        {
            "event": "prompt",
            "seq": 1,
            "host": "antigravity",
            "session_id": "default",
            "agent": "antigravity",
            "turn_id": "legacy-turn",
            "prompt": "legacy work",
        },
    )
    turns = ledger["active_turns"]
    assert isinstance(turns, dict)
    turn = turns["antigravity:default:antigravity"]
    assert isinstance(turn, dict)
    turn["invocations"] = {
        "tool:missing-status": {
            "candidate_paths": ["app.py"],
            "legacy_note": "preserve me",
            "started_seq": 1,
            "started_at": "2020-01-01T00:00:00+00:00",
        },
        "tool:open": {
            "candidate_paths": ["open.py"],
            "status": "open",
            "started_seq": 2,
            "started_at": "2026-07-21T00:00:00+00:00",
        },
        "tool:closed": {
            "candidate_paths": ["closed.py"],
            "status": "closed",
            "started_seq": 3,
            "completed_seq": 4,
        },
    }
    return ledger, turn


def _write_legacy_status_ledger(root: Path) -> tuple[Path, bytes]:
    ledger, _turn = _legacy_status_ledger()
    destination = ledger_path(str(root))
    destination.parent.mkdir(parents=True)
    original = json.dumps(ledger, ensure_ascii=False, indent=1).encode("utf-8")
    destination.write_bytes(original)
    return destination, original


def _turn_invocations(ledger: JsonObject) -> JsonObject:
    turns = ledger["active_turns"]
    assert isinstance(turns, dict)
    turn = turns["antigravity:default:antigravity"]
    assert isinstance(turn, dict)
    invocations = turn["invocations"]
    assert isinstance(invocations, dict)
    return invocations


def test_invocation_without_status_is_rejected_at_the_schema_boundary() -> None:
    # Given: an otherwise valid v2 ledger contains an invocation with no status.
    ledger = default_v2_ledger()
    _ = apply_v2_event(
        ledger,
        {
            "event": "prompt",
            "seq": 1,
            "host": "antigravity",
            "session_id": "default",
            "agent": "antigravity",
            "turn_id": "schema-turn",
            "prompt": "schema regression",
        },
    )
    turns = ledger["active_turns"]
    assert isinstance(turns, dict)
    turn = turns["antigravity:default:antigravity"]
    assert isinstance(turn, dict)
    turn["invocations"] = {
        "tool:missing-status": {"candidate_paths": ["src/app.py"]}
    }

    # When/Then: omission is invalid instead of being interpreted as a closed window.
    with pytest.raises(LedgerSchemaError, match=r"invocations.*status.*required"):
        _ = validate_v2_ledger(ledger)


def test_backfill_closes_only_missing_status_and_is_idempotent() -> None:
    # Given: one old fieldless invocation beside valid open and closed rows.
    legacy, _original_turn = _legacy_status_ledger()
    original_rows = _turn_invocations(legacy)

    # When: the migration transformation runs twice.
    migrated, changed = migration_module.backfill_invocation_statuses(legacy)
    repeated, repeated_changed = migration_module.backfill_invocation_statuses(migrated)

    # Then: only the omission becomes closed; inputs and existing rows are preserved.
    migrated_rows = _turn_invocations(migrated)
    assert changed == 1
    assert repeated_changed == 0
    assert repeated == migrated
    assert "status" not in original_rows["tool:missing-status"]
    assert migrated_rows["tool:missing-status"] == {
        "candidate_paths": ["app.py"],
        "legacy_note": "preserve me",
        "started_seq": 1,
        "started_at": "2020-01-01T00:00:00+00:00",
        "status": "closed",
    }
    assert migrated_rows["tool:open"] == original_rows["tool:open"]
    assert migrated_rows["tool:closed"] == original_rows["tool:closed"]
    assert validate_v2_ledger(migrated) is migrated


def test_backfill_keeps_the_exact_lease_boundary_open_and_closes_only_expired() -> None:
    # Given: status-less rows immediately inside, on, and beyond the 30-minute lease.
    observed_at = datetime(2026, 7, 21, 6, 0, tzinfo=UTC)
    ledger, _turn = _legacy_status_ledger()
    rows = _turn_invocations(ledger)
    rows["tool:inside-lease"] = {
        "candidate_paths": ["inside.py"],
        "started_seq": 10,
        "started_at": (observed_at - timedelta(minutes=29, seconds=59)).isoformat(),
    }
    rows["tool:lease-boundary"] = {
        "candidate_paths": ["boundary.py"],
        "started_seq": 11,
        "started_at": (observed_at - timedelta(minutes=30)).isoformat(),
    }
    rows["tool:expired"] = {
        "candidate_paths": ["expired.py"],
        "started_seq": 12,
        "started_at": (
            observed_at - timedelta(minutes=30, microseconds=1)
        ).isoformat(),
    }

    # When: classification uses the same strict-greater-than expiry as R2.
    migrated, _changed = migration_module.backfill_invocation_statuses(
        ledger,
        observed_at=observed_at,
    )
    migrated_rows = _turn_invocations(migrated)

    # Then: only the provably expired row is closed.
    assert migrated_rows["tool:inside-lease"]["status"] == "open"
    assert migrated_rows["tool:lease-boundary"]["status"] == "open"
    assert migrated_rows["tool:expired"]["status"] == "closed"


def test_recent_statusless_invocation_stays_open_and_blocks_r2_after_migration(
    tmp_path: Path,
) -> None:
    # Given: a mixed-version peer invocation started five minutes ago without status.
    ledger, _turn = _legacy_status_ledger()
    row = _turn_invocations(ledger)["tool:missing-status"]
    assert isinstance(row, dict)
    row["started_seq"] = 2
    row["started_at"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    destination = ledger_path(str(tmp_path))
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps(ledger), encoding="utf-8")

    # When: the real migration runs and R2 evaluates the peer candidate afterward.
    migrated = migration_module.migrate_v2_invocation_statuses(str(tmp_path))
    decision = evaluate_r2_destructive_gate(
        {
            "project_root": str(tmp_path),
            "host": "codex_cli",
            "session_id": "caller",
            "agent": "codex",
            "tool_name": "Bash",
            "command": "rm app.py",
        }
    )

    # Then: migration preserves the live window instead of creating a block-to-allow bypass.
    assert _turn_invocations(migrated)["tool:missing-status"]["status"] == "open"
    assert decision["decision"] == "block"
    assert "peer_open_invocation_candidate" in decision["reason"]


def test_unprovable_recent_statusless_invocation_stays_degraded_without_rewrite(
    tmp_path: Path,
) -> None:
    # Given: a recent status-less row lacks the sequence required by R2 open proof.
    ledger, _turn = _legacy_status_ledger()
    row = _turn_invocations(ledger)["tool:missing-status"]
    assert isinstance(row, dict)
    _ = row.pop("started_seq")
    row["started_at"] = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    destination = ledger_path(str(tmp_path))
    destination.parent.mkdir(parents=True)
    original = json.dumps(ledger, ensure_ascii=False).encode("utf-8")
    destination.write_bytes(original)

    # When: migration cannot prove that persisting open would preserve R2 protection.
    with pytest.raises(migration_module.LedgerMigrationError, match="classify"):
        _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    # Then: source stays untouched and the compatibility read remains fail-closed degraded.
    loaded = load_ledger({"project_root": str(tmp_path)})
    assert destination.read_bytes() == original
    assert not destination.with_name(STATUS_ARCHIVE).exists()
    assert loaded["attribution_degraded"] is True
    assert _turn_invocations(loaded)["tool:missing-status"]["status"] == "open"


def test_auto_migration_failure_logs_stage_and_detail_without_raising(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: migration is opted in but an immutable archive conflict makes it fail.
    destination, original = _write_legacy_status_ledger(tmp_path)
    destination.with_name(STATUS_ARCHIVE).write_bytes(b"conflicting archive")
    payload = {
        "project_root": str(tmp_path),
        "event": "scope_warning",
        "message": "migration failure must stay observable",
    }

    # When: the ordinary event path encounters the real migration error.
    with (
        patch("core.ledger.status_backfill_enabled", return_value=True),
        caplog.at_level(logging.WARNING, logger="core.ledger"),
    ):
        result = record_event(payload)

    # Then: the session remains alive/degraded and operators can distinguish ON failure.
    assert result["attribution_degraded"] is True
    assert destination.read_bytes() == original
    assert "automatic ledger migration failed" in caplog.text
    assert "stage=archive" in caplog.text
    assert "existing archive differs from ledger.json" in caplog.text


def test_auto_migration_off_has_no_failure_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: the same legacy shape with automatic migration deliberately disabled.
    destination, original = _write_legacy_status_ledger(tmp_path)

    # When: the normal event path runs in OFF mode.
    with (
        patch("core.ledger.status_backfill_enabled", return_value=False),
        caplog.at_level(logging.WARNING, logger="core.ledger"),
    ):
        result = record_event(
            {
                "project_root": str(tmp_path),
                "event": "scope_warning",
                "message": "migration intentionally disabled",
            }
        )

    # Then: it remains degraded without falsely reporting a migration failure.
    assert result["attribution_degraded"] is True
    assert destination.read_bytes() == original
    assert "automatic ledger migration failed" not in caplog.text


def test_migration_failure_logger_cannot_break_hook_fail_open(
    tmp_path: Path,
) -> None:
    # Given: both migration and its best-effort diagnostic sink fail.
    destination, original = _write_legacy_status_ledger(tmp_path)
    destination.with_name(STATUS_ARCHIVE).write_bytes(b"conflicting archive")

    # When: the normal event path catches the real migration error.
    with (
        patch("core.ledger.status_backfill_enabled", return_value=True),
        patch("core.ledger.LOGGER.warning", side_effect=RuntimeError("log sink down")),
    ):
        result = record_event(
            {
                "project_root": str(tmp_path),
                "event": "scope_warning",
                "message": "diagnostics must not kill the session",
            }
        )

    # Then: fail-open behavior and original bytes survive the diagnostic failure.
    assert result["attribution_degraded"] is True
    assert destination.read_bytes() == original


def test_auto_migration_off_preserves_legacy_bytes_and_degrades_fail_closed(
    tmp_path: Path,
) -> None:
    # Given: a legacy-status v2 ledger in a project without migration opt-in.
    destination, original = _write_legacy_status_ledger(tmp_path)
    payload = {
        "project_root": str(tmp_path),
        "event": "scope_warning",
        "message": "must not rewrite legacy status rows",
    }

    # When: an ordinary write path runs with automatic migration disabled.
    with patch("core.ledger.status_backfill_enabled", return_value=False):
        loaded = load_ledger(payload)
        result = record_event(payload)

    # Then: both reads fail closed in memory and the live bytes remain untouched.
    assert loaded["attribution_degraded"] is True
    assert result["attribution_degraded"] is True
    assert _turn_invocations(loaded)["tool:missing-status"]["status"] == "closed"
    assert destination.read_bytes() == original
    assert not destination.with_name(STATUS_ARCHIVE).exists()


def test_degraded_legacy_view_cannot_overwrite_a_concurrent_migration(
    tmp_path: Path,
) -> None:
    # Given: one process holds an OFF-mode degraded view of the old bytes.
    destination, _original = _write_legacy_status_ledger(tmp_path)
    payload = {"project_root": str(tmp_path)}
    degraded = load_ledger(payload)
    assert degraded["attribution_degraded"] is True

    # When: another process migrates first and the stale holder tries to save afterward.
    _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))
    migrated_bytes = destination.read_bytes()
    saved = save_ledger(payload, degraded)

    # Then: the non-persistable degraded view cannot clobber the newer strict ledger.
    assert saved is False
    assert destination.read_bytes() == migrated_bytes


def test_legacy_status_degradation_blocks_destructive_r2_before_attribution(
    tmp_path: Path,
) -> None:
    # Given: a missing-status row that would otherwise disappear from open-window proof.
    _destination, _original = _write_legacy_status_ledger(tmp_path)

    # When: a destructive command reaches the real R2 ledger boundary.
    decision = evaluate_r2_destructive_gate(
        {
            "project_root": str(tmp_path),
            "host": "codex_cli",
            "session_id": "caller",
            "agent": "codex",
            "tool_name": "Bash",
            "command": "rm app.py",
        }
    )

    # Then: explicit degradation blocks instead of treating omission as benign closure.
    assert decision["decision"] == "block"
    assert "attribution_degraded_or_capacity_exceeded" in decision["reason"]


def test_auto_migration_on_backfills_before_the_ordinary_event_write(
    tmp_path: Path,
) -> None:
    # Given: a legacy-status v2 ledger and an enabled migration gate.
    destination, original = _write_legacy_status_ledger(tmp_path)
    original_rows = _turn_invocations(json.loads(original.decode("utf-8")))

    # When: the ordinary event path performs its one-shot migration.
    with patch("core.ledger.status_backfill_enabled", return_value=True):
        result = record_event(
            {
                "project_root": str(tmp_path),
                "event": "scope_warning",
                "message": "after status migration",
            }
        )

    # Then: immutable source bytes are archived and the committed ledger is strict-valid.
    archive = destination.with_name(STATUS_ARCHIVE)
    persisted = json.loads(destination.read_text(encoding="utf-8"))
    persisted_rows = _turn_invocations(persisted)
    assert archive.read_bytes() == original
    assert persisted_rows["tool:missing-status"]["status"] == "closed"
    assert persisted_rows["tool:closed"] == original_rows["tool:closed"]
    assert result["attribution_degraded"] is False
    assert validate_v2_ledger(persisted) is persisted


def test_status_migration_is_idempotent_and_keeps_a_rollback_archive(
    tmp_path: Path,
) -> None:
    # Given: a byte-exact legacy-status ledger copy.
    destination, original = _write_legacy_status_ledger(tmp_path)

    # When: the explicit migration runs twice.
    first = migration_module.migrate_v2_invocation_statuses(str(tmp_path))
    first_bytes = destination.read_bytes()
    second = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    # Then: reruns are no-ops and the archive is sufficient for byte-exact rollback.
    archive = destination.with_name(STATUS_ARCHIVE)
    assert second == first
    assert destination.read_bytes() == first_bytes
    assert archive.read_bytes() == original
    destination.write_bytes(archive.read_bytes())
    assert destination.read_bytes() == original


def test_status_migration_restores_original_after_atomic_replace_failure(
    tmp_path: Path,
) -> None:
    # Given: a legacy-status ledger and a one-time destination replace failure.
    destination, original = _write_legacy_status_ledger(tmp_path)
    failed = False

    def fail_once(source: str, target: str | Path) -> None:
        nonlocal failed
        if Path(target) == destination and not failed:
            failed = True
            raise OSError("injected status migration replace failure")
        OS_REPLACE(source, target)

    # When: migration reaches the faulted atomic commit.
    with patch("core.ledger_storage.os.replace", side_effect=fail_once):
        with pytest.raises(migration_module.LedgerMigrationError, match="write"):
            _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    # Then: the immutable archive restores the original live bytes.
    assert failed is True
    assert destination.read_bytes() == original
    assert destination.with_name(STATUS_ARCHIVE).read_bytes() == original


def test_status_migration_rejects_other_malformed_state_without_any_rewrite(
    tmp_path: Path,
) -> None:
    # Given: status is missing, but an unrelated required ledger field is also absent.
    ledger, _turn = _legacy_status_ledger()
    _ = ledger.pop("task_mode")
    destination = ledger_path(str(tmp_path))
    destination.parent.mkdir(parents=True)
    original = json.dumps(ledger, ensure_ascii=False).encode("utf-8")
    destination.write_bytes(original)

    # When: the narrowly scoped backfill refuses to validate the rest of the ledger.
    with pytest.raises(migration_module.LedgerMigrationError, match="validate"):
        _ = migration_module.migrate_v2_invocation_statuses(str(tmp_path))

    # Then: neither live bytes nor a misleading rollback archive are created.
    assert destination.read_bytes() == original
    assert not destination.with_name(STATUS_ARCHIVE).exists()
