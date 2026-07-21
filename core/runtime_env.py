from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from typing import Final, Literal


CANONICAL_ENV_PREFIX: Final = "SMTW_"
LEGACY_ENV_PREFIX: Final = "FABLE_LITE_"

AUTO_MIGRATION: Final = "AUTO_MIGRATION"
TEST_LOCK_WAIT_SECONDS: Final = "TEST_LOCK_WAIT_SECONDS"
DESIGN_GATE: Final = "DESIGN_GATE"
SCORECARD: Final = "SCORECARD"
CODEX_REAPER: Final = "CODEX_REAPER"
CODEX_REAPER_LOG: Final = "CODEX_REAPER_LOG"
CODEX_REAPER_DRY_RUN: Final = "CODEX_REAPER_DRY_RUN"
CODEX_REAPER_POWERSHELL: Final = "CODEX_REAPER_POWERSHELL"

RUNTIME_ENV_SUFFIXES: Final = frozenset(
    {
        AUTO_MIGRATION,
        TEST_LOCK_WAIT_SECONDS,
        DESIGN_GATE,
        SCORECARD,
        CODEX_REAPER,
        CODEX_REAPER_LOG,
        CODEX_REAPER_DRY_RUN,
        CODEX_REAPER_POWERSHELL,
    }
)

EnvSource = Literal["canonical", "legacy", "absent"]


class SmtwEnvConflictError(RuntimeError):
    """Raised when canonical and legacy controls disagree."""

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix
        self.canonical_key = canonical_env_key(suffix)
        self.legacy_key = legacy_env_key(suffix)
        super().__init__(
            "conflicting SMTW runtime environment controls: "
            f"{self.canonical_key} and {self.legacy_key} differ"
        )


@dataclass(frozen=True, slots=True)
class RuntimeEnvValue:
    value: str | None
    source: EnvSource
    key: str | None

    @property
    def present(self) -> bool:
        return self.key is not None


def canonical_env_key(suffix: str) -> str:
    return f"{CANONICAL_ENV_PREFIX}{suffix}"


def legacy_env_key(suffix: str) -> str:
    return f"{LEGACY_ENV_PREFIX}{suffix}"


def resolve_smtw_env(
    suffix: str,
    environ: Mapping[str, str] | None = None,
) -> RuntimeEnvValue:
    """Resolve one semantic key without applying caller-specific coercion.

    Presence, not truthiness, determines precedence. If both generations are
    present they must carry the exact same raw value; disagreement is an
    explicit fail-closed configuration error.
    """
    source = os.environ if environ is None else environ
    canonical_key = canonical_env_key(suffix)
    legacy_key = legacy_env_key(suffix)
    canonical_present = canonical_key in source
    legacy_present = legacy_key in source

    if canonical_present and legacy_present:
        canonical_value = source[canonical_key]
        if canonical_value != source[legacy_key]:
            raise SmtwEnvConflictError(suffix)
        return RuntimeEnvValue(canonical_value, "canonical", canonical_key)
    if canonical_present:
        return RuntimeEnvValue(source[canonical_key], "canonical", canonical_key)
    if legacy_present:
        return RuntimeEnvValue(source[legacy_key], "legacy", legacy_key)
    return RuntimeEnvValue(None, "absent", None)


def smtw_env(
    suffix: str,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    return resolve_smtw_env(suffix, environ).value
