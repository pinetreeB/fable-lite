from __future__ import annotations

from pathlib import Path

from .provenance_lifecycle_types import TurnState
from .provenance_store import load_turn_baseline


def load_resumed_turn(
    root: Path,
    agent: str,
    turn_id: str,
    event_seq: int,
    mutation_capable: bool,
) -> TurnState:
    baseline = load_turn_baseline(root, agent, turn_id)
    if baseline is None:
        raise KeyError((agent, turn_id))
    return TurnState(agent, turn_id, baseline, event_seq, mutation_capable)
