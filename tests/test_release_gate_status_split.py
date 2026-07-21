"""Regression: schema-migration and status-backfill gates must stay independent.

A v2.4 B5 change gated `auto_migration_enabled()` on FABLE_LITE_AUTO_MIGRATION,
which silently turned off the (env-independent) ledger v1->v2 schema migration
unlocked in v2.0.0 and broke the wheel-smoke `assert auto_migration_enabled()`
CI step. These tests pin the two gates apart.
"""

from __future__ import annotations

import os
from unittest.mock import patch

from core.release_gate import auto_migration_enabled, status_backfill_enabled


def test_auto_migration_ignores_env_and_follows_receipts() -> None:
    # Receipts green -> enabled regardless of the opt-in env var (wheel smoke contract).
    with patch("core.release_gate._provenance_green", return_value=True), patch(
        "core.release_gate._benchmark_green", return_value=True
    ):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FABLE_LITE_AUTO_MIGRATION", None)
            assert auto_migration_enabled() is True
        with patch.dict(os.environ, {"FABLE_LITE_AUTO_MIGRATION": "1"}):
            assert auto_migration_enabled() is True


def test_auto_migration_disabled_when_receipts_not_green() -> None:
    with patch("core.release_gate._provenance_green", return_value=False), patch(
        "core.release_gate._benchmark_green", return_value=True
    ):
        assert auto_migration_enabled() is False


def test_status_backfill_is_opt_in_via_env() -> None:
    with patch.dict(os.environ, {"FABLE_LITE_AUTO_MIGRATION": "1"}):
        assert status_backfill_enabled() is True
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("FABLE_LITE_AUTO_MIGRATION", None)
        assert status_backfill_enabled() is False


def test_status_backfill_does_not_depend_on_receipts() -> None:
    # Even with green receipts, backfill stays off without the explicit env opt-in.
    with patch("core.release_gate._provenance_green", return_value=True), patch(
        "core.release_gate._benchmark_green", return_value=True
    ), patch.dict(os.environ, {}, clear=False):
        os.environ.pop("FABLE_LITE_AUTO_MIGRATION", None)
        assert status_backfill_enabled() is False
