from __future__ import annotations

import os
from unittest import mock, skipUnless

from contrib.codex_process_reaper import windows_runtime


@skipUnless(os.name == "nt", "Windows-only process handle query")
def test_live_process_ids_uses_native_handles_without_shelling_out() -> None:
    # Given: one live PID and one impossible PID are requested together.
    requested = (os.getpid(), 2_147_483_647)

    # When: the after-count snapshot queries their Windows process handles.
    with mock.patch("subprocess.run", side_effect=AssertionError("shell used")):
        live = windows_runtime.live_process_ids(requested)

    # Then: the live PID remains and no PowerShell subprocess is needed.
    assert live == {os.getpid()}
