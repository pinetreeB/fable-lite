from __future__ import annotations

import json
from pathlib import Path

from core.ledger import load_ledger
from core.ledger_storage import ledger_path
from core.ledger_v2 import (
    apply_v2_event,
    default_v2_ledger,
    open_peer_invocation_candidates,
)


def test_legacy_invocation_without_window_fields_loads_conservatively(
    tmp_path: Path,
) -> None:
    # Given: a persisted v2 ledger contains the pre-W3 invocation shape.
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
        "tool:default:other": {
            "candidate_paths": ["app.py"],
        },
        "tool:partial:edit": {
            "candidate_paths": ["partial.py"],
            "status": "open",
            "started_at": "2026-07-16T06:00:00+00:00",
        },
    }
    destination = ledger_path(str(tmp_path))
    destination.parent.mkdir(parents=True)
    destination.write_text(json.dumps(ledger), encoding="utf-8")

    # When: the normal ledger boundary loads the legacy invocation record.
    loaded = load_ledger({"project_root": str(tmp_path)})

    # Then: loading stays healthy and the fieldless record is not an open-window proof.
    assert loaded["attribution_degraded"] is False
    assert open_peer_invocation_candidates(
        loaded,
        "codex_cli:caller:codex",
    ) == {}
