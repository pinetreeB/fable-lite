from __future__ import annotations

import threading
import time
from pathlib import Path

from core.agent_log import ledger_transaction
from core.file_lock import owner_lock
from core.state_layout import migration_lock_path


def test_ledger_transaction_waits_past_legacy_one_second_layout_cap(
    tmp_path: Path,
) -> None:
    """A ledger transaction must honor its full lock-wait budget on the layout
    barrier, not the removed one-second cap.

    Regression for the CI flake where ``test_block_counter_atomic`` and
    ``test_scorecard_coordination`` intermittently raised
    ``TimeoutError: timed out waiting for owner lock: .smtw-migration.lock``.
    The old ``min(wait_seconds, DEFAULT_STATE_WRITE_WAIT_SECONDS)`` throttled the
    outer layout lock to 1s while the inner ledger lock waited far longer, so a
    holder that kept the migration lock past 1s starved concurrent writers even
    though their configured budget (tests use 45s) should have covered it.
    """
    root = tmp_path
    root.mkdir(parents=True, exist_ok=True)
    hold_seconds = 2.0
    holding = threading.Event()
    released = threading.Event()

    def _hold_migration_lock() -> None:
        with owner_lock(migration_lock_path(root), wait_seconds=5.0):
            holding.set()
            time.sleep(hold_seconds)
        released.set()

    holder = threading.Thread(target=_hold_migration_lock)
    holder.start()
    try:
        assert holding.wait(timeout=5.0)
        # Under the old 1s cap this raised TimeoutError while the holder kept
        # the lock for 2s.  The fix waits past 1s and then commits.
        start = time.monotonic()
        with ledger_transaction(root, lock_wait_seconds=15.0):
            waited = time.monotonic() - start
        assert waited >= hold_seconds - 0.5
        assert released.wait(timeout=5.0)
    finally:
        holder.join(timeout=10.0)
