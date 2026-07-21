from __future__ import annotations

import json
import os
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Final, TypeAlias

from .provenance_types import ProvenanceConfig, ProvenanceConfigError, ProvenancePathError
from .state_layout import (
    LEGACY_STATE_DIR_NAME,
    MIGRATION_LOCK_NAME,
    MIGRATION_RECEIPT_NAME,
    MIGRATION_RECEIPT_TEMP_PREFIX,
    MIGRATION_STAGING_PREFIX,
    STATE_DIR_NAME,
    is_protected_state_name,
)

HARD_EXCLUDES: Final = (
    ".codegraph/**",
    ".git/**",
    f"{LEGACY_STATE_DIR_NAME}/**",
    f"{STATE_DIR_NAME}/**",
    f"{MIGRATION_STAGING_PREFIX}*/**",
    MIGRATION_LOCK_NAME,
    MIGRATION_RECEIPT_NAME,
    f"{MIGRATION_RECEIPT_TEMP_PREFIX}*",
    ".fablize/**",
    ".hg/**",
    ".svn/**",
)
HARD_EXCLUDE_DIRS: Final = frozenset(
    (
        ".codegraph",
        ".git",
        LEGACY_STATE_DIR_NAME,
        STATE_DIR_NAME,
        ".fablize",
        ".hg",
        ".svn",
    )
)
SOFT_EXCLUDES: Final = (
    "node_modules/**",
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    ".pytest_cache/**",
    ".mypy_cache/**",
    ".ruff_cache/**",
    ".next/cache/**",
    ".turbo/**",
    ".parcel-cache/**",
    ".angular/cache/**",
    ".nuxt/**",
    ".svelte-kit/**",
)
SOFT_EXCLUDE_CHAINS: Final = tuple(
    tuple(pattern.removesuffix("/**").split("/")) for pattern in SOFT_EXCLUDES
)
CONFIG_RELATIVE_PATH: Final = ".fable-lite/provenance-config.json"
PROJECT_PATH_IN_ROOT: Final = "in_root"
PROJECT_PATH_OUT_OF_ROOT: Final = "out_of_root"
PROJECT_PATH_UNRESOLVABLE: Final = "unresolvable"
JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | Sequence["JsonValue"] | Mapping[str, "JsonValue"]


def normalize_relative_path(root: Path, path: Path) -> str:
    root_text = os.path.abspath(root)
    path_text = os.path.abspath(path)
    try:
        relative = os.path.relpath(path_text, root_text)
    except ValueError as exc:
        raise ProvenancePathError(path=str(path), root=str(root)) from exc
    normalized = relative.replace("\\", "/")
    if normalized == ".." or normalized.startswith("../") or normalized == ".":
        raise ProvenancePathError(path=str(path), root=str(root))
    return normalized


def canonical_manifest_key(path: str, windows: bool) -> str:
    normalized = path.replace("\\", "/")
    return normalized.casefold() if windows else normalized


def canonicalize_project_path(
    root: str | Path,
    target: str,
    *,
    windows: bool | None = None,
) -> tuple[str, str | None]:
    """Resolve one candidate to a project-relative canonical key.

    Out-of-root and unresolvable paths remain distinct so callers never use either as
    evidence that an in-project path is safe.
    """
    normalized = target.strip().strip("'\"").replace("\\", "/")
    if not normalized:
        return (PROJECT_PATH_UNRESOLVABLE, None)
    candidate = Path(normalized)
    try:
        base = Path(root).resolve()
        resolved = (candidate if candidate.is_absolute() else base / normalized).resolve()
    except OSError:
        return (PROJECT_PATH_UNRESOLVABLE, None)
    try:
        relative = str(resolved.relative_to(base)).replace("\\", "/")
    except ValueError:
        return (PROJECT_PATH_OUT_OF_ROOT, None)
    casefolded = os.name == "nt" if windows is None else windows
    return (PROJECT_PATH_IN_ROOT, canonical_manifest_key(relative, casefolded))


def load_provenance_config(root: Path) -> ProvenanceConfig:
    config_path = root / CONFIG_RELATIVE_PATH
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ProvenanceConfig()
    except OSError as exc:
        raise ProvenanceConfigError("config", f"cannot read: {exc.strerror or exc}") from exc
    try:
        raw: JsonValue = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ProvenanceConfigError("config", "must be valid JSON") from exc
    if not isinstance(raw, dict):
        raise ProvenanceConfigError("config", "must be an object")
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise ProvenanceConfigError("version", "must equal 1")
    return ProvenanceConfig(
        include=_patterns(raw.get("include"), "include"),
        exclude=_patterns(raw.get("exclude"), "exclude"),
        generated=_patterns(raw.get("generated"), "generated"),
    )


@lru_cache(maxsize=262_144)
def is_path_in_scope(path: str, config: ProvenanceConfig) -> bool:
    if path == CONFIG_RELATIVE_PATH:
        return True
    if is_hard_excluded(path):
        return False
    if _matches_any(path, config.include):
        return True
    if _matches_any(path, config.exclude):
        return False
    return not _has_soft_excluded_chain(path)


def is_user_config_excluded(path: str, config: ProvenanceConfig) -> bool:
    return (
        path != CONFIG_RELATIVE_PATH
        and not is_hard_excluded(path)
        and not _matches_any(path, config.include)
        and _matches_any(path, config.exclude)
    )


def is_hard_excluded(path: str) -> bool:
    return is_harness_state_path(path) or _first_segment(path) in {".git", ".hg", ".svn"}


def is_harness_state_path(path: str) -> bool:
    head = _first_segment(path)
    return head in {".codegraph", ".fablize"} or is_protected_state_name(head)


@lru_cache(maxsize=65_536)
def should_descend(path: str, config: ProvenanceConfig) -> bool:
    if path == CONFIG_RELATIVE_PATH.partition("/")[0]:
        return True
    if is_hard_excluded(path):
        return False
    if _matches_any(path, config.include):
        return True
    if _matches_any(path, config.exclude) or _has_soft_excluded_chain(path):
        return _has_included_descendant(path, config.include)
    return True


def _patterns(value: JsonValue | None, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProvenanceConfigError(field, "must be a list of relative patterns")
    patterns: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise ProvenanceConfigError(f"{field}[{index}]", "must be a non-empty string")
        normalized = item.replace("\\", "/")
        if normalized.startswith("/") or normalized.startswith("../") or ":" in normalized:
            raise ProvenanceConfigError(f"{field}[{index}]", "must be root-relative")
        patterns.append(normalized)
    return tuple(patterns)


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_matches(path, pattern) for pattern in patterns)


def _first_segment(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.partition("/")[0]


def _has_soft_excluded_chain(path: str) -> bool:
    segments = tuple(segment for segment in path.replace("\\", "/").split("/") if segment)
    return any(_contains_segment_chain(segments, chain) for chain in SOFT_EXCLUDE_CHAINS)


def _contains_segment_chain(
    segments: tuple[str, ...],
    chain: tuple[str, ...],
) -> bool:
    chain_length = len(chain)
    return any(
        segments[index : index + chain_length] == chain
        for index in range(len(segments) - chain_length + 1)
    )


def _matches(path: str, pattern: str) -> bool:
    directory = pattern.removesuffix("/**")
    return fnmatchcase(path, pattern) or path == directory


def _has_included_descendant(directory: str, patterns: tuple[str, ...]) -> bool:
    return any(_include_can_match_descendant(directory, pattern) for pattern in patterns)


def _include_can_match_descendant(directory: str, pattern: str) -> bool:
    fixed_prefix = _fixed_prefix(pattern)
    if not fixed_prefix:
        return True
    if fixed_prefix == directory or fixed_prefix.startswith(f"{directory}/"):
        return True
    return fixed_prefix != pattern and directory.startswith(f"{fixed_prefix}/")


def _fixed_prefix(pattern: str) -> str:
    segments = pattern.split("/")
    prefix: list[str] = []
    for segment in segments:
        if any(character in segment for character in "*?["):
            break
        prefix.append(segment)
    return "/".join(prefix)
