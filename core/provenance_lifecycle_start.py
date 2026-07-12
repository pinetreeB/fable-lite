from __future__ import annotations

from pathlib import Path

from .provenance import normalize_relative_path, workspace_scope_policy_id
from .provenance_types import Snapshot


def can_fast_start(
    current: Snapshot | None,
    current_is_stop_full: bool,
    incomplete: bool,
    root: Path,
) -> bool:
    return (
        current is not None
        and current_is_stop_full
        and not incomplete
        and not current.incomplete
        and current.scope_policy_id == workspace_scope_policy_id(root)
    )


def candidate_paths(root: Path, candidates: tuple[str, ...]) -> frozenset[str]:
    normalized: set[str] = set()
    for candidate in candidates:
        path = Path(candidate)
        absolute = path if path.is_absolute() else root / path
        try:
            normalized.add(normalize_relative_path(root, absolute))
        except ValueError:
            continue
    return frozenset(normalized)
