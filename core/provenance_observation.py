from __future__ import annotations

import hashlib
import json

from .provenance_delta import calculate_net_delta
from .provenance_lifecycle_types import (
    LifecycleState,
    ObservationInput,
    ObservedChange,
    TurnState,
)
from .provenance_types import NetDelta


def record_deltas(
    state: LifecycleState,
    observation: ObservationInput,
) -> tuple[ObservedChange, ...]:
    added: list[ObservedChange] = []
    for delta in observation.deltas:
        change_id = change_id_for(delta)
        existing = state.changes.get(change_id)
        if existing is None:
            source = _source_for_delta(state, observation.source, delta.path)
            owner = observation.agent if observation.agent and source != "external" else None
            change = ObservedChange(
                change_id,
                delta.path,
                delta.canonical_key,
                delta.op,
                None if delta.before is None else delta.before.digest,
                None if delta.after is None else delta.after.digest,
                source,
                owner,
                "exclusive",
                (observation.agent,) if observation.agent else (),
            )
            state.changes[change_id] = change
            state.event_seq += 1
            added.append(change)
            continue
        observed_by = tuple(
            sorted(set(existing.observed_by) | ({observation.agent} if observation.agent else set()))
        )
        if existing.owner is not None and observation.agent and existing.owner != observation.agent:
            state.changes[change_id] = replace_observation(existing, observed_by, True)
        elif observed_by != existing.observed_by:
            state.changes[change_id] = replace_observation(existing, observed_by, False)
    return tuple(added)


def _source_for_delta(state: LifecycleState, source: str, path: str) -> str:
    current = state.current
    if source != "external" and current is not None and current.is_generated(path):
        return "generated"
    return source


def pending_change_ids(state: LifecycleState, turn: TurnState) -> tuple[str, ...]:
    if state.current is None:
        return ()
    return tuple(
        sorted(change_id_for(delta) for delta in calculate_net_delta(turn.baseline, state.current))
    )


def change_id_for(delta: NetDelta) -> str:
    encoded = json.dumps(
        (
            delta.canonical_key,
            delta.op.value,
            None if delta.before is None else delta.before.digest,
            None if delta.after is None else delta.after.digest,
        ),
        separators=(",", ":"),
    )
    digest = hashlib.blake2b(encoded.encode("utf-8"), digest_size=32)
    return f"blake2b-256:{digest.hexdigest()}"


def replace_observation(
    existing: ObservedChange,
    observed_by: tuple[str, ...],
    contended: bool,
) -> ObservedChange:
    if contended:
        return ObservedChange(
            existing.change_id,
            existing.path,
            existing.canonical_key,
            existing.op,
            existing.before_digest,
            existing.after_digest,
            "external",
            None,
            "contended",
            observed_by,
        )
    return ObservedChange(
        existing.change_id,
        existing.path,
        existing.canonical_key,
        existing.op,
        existing.before_digest,
        existing.after_digest,
        existing.source,
        existing.owner,
        existing.attribution_status,
        observed_by,
    )
