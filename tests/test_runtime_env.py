from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from adapters.codex_cli import stop as codex_stop
from core.agent_log import _lock_wait_seconds
from core.design_gate import design_gate_enabled
from core.release_gate import status_backfill_enabled
from core.runtime_env import (
    AUTO_MIGRATION,
    CODEX_REAPER,
    CODEX_REAPER_DRY_RUN,
    CODEX_REAPER_LOG,
    CODEX_REAPER_POWERSHELL,
    DESIGN_GATE,
    RUNTIME_ENV_SUFFIXES,
    SmtwEnvConflictError,
    TEST_LOCK_WAIT_SECONDS,
    canonical_env_key,
    legacy_env_key,
    resolve_smtw_env,
    smtw_env,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = ("core", "adapters", "fable_lite", "goals", "contrib", "scripts")


@pytest.mark.parametrize("suffix", sorted(RUNTIME_ENV_SUFFIXES))
def test_canonical_runtime_env_is_selected_by_presence(suffix: str) -> None:
    canonical = canonical_env_key(suffix)
    legacy = legacy_env_key(suffix)

    selected = resolve_smtw_env(
        suffix,
        {canonical: "", legacy: ""},
    )

    assert selected.value == ""
    assert selected.source == "canonical"
    assert selected.key == canonical
    assert selected.present is True


@pytest.mark.parametrize("suffix", sorted(RUNTIME_ENV_SUFFIXES))
def test_legacy_runtime_env_remains_a_v3_alias(suffix: str) -> None:
    legacy = legacy_env_key(suffix)

    selected = resolve_smtw_env(suffix, {legacy: "legacy-value"})

    assert selected.value == "legacy-value"
    assert selected.source == "legacy"
    assert selected.key == legacy


@pytest.mark.parametrize("suffix", sorted(RUNTIME_ENV_SUFFIXES))
def test_different_env_generations_fail_closed(suffix: str) -> None:
    canonical = canonical_env_key(suffix)
    legacy = legacy_env_key(suffix)

    with pytest.raises(SmtwEnvConflictError) as captured:
        _ = smtw_env(suffix, {canonical: "0", legacy: "1"})

    assert captured.value.canonical_key == canonical
    assert captured.value.legacy_key == legacy
    assert "0" not in str(captured.value)
    assert "1" not in str(captured.value)


def test_absent_runtime_env_has_no_source() -> None:
    selected = resolve_smtw_env(DESIGN_GATE, {})

    assert selected.value is None
    assert selected.source == "absent"
    assert selected.key is None
    assert selected.present is False


def test_zero_canonical_value_never_falls_back_to_enabled_legacy() -> None:
    with pytest.raises(SmtwEnvConflictError):
        _ = smtw_env(
            AUTO_MIGRATION,
            {
                canonical_env_key(AUTO_MIGRATION): "0",
                legacy_env_key(AUTO_MIGRATION): "1",
            },
        )


def test_core_callers_keep_their_existing_value_semantics(
    tmp_path: Path,
) -> None:
    with patch.dict(
        os.environ,
        {
            canonical_env_key(AUTO_MIGRATION): "0",
            canonical_env_key(DESIGN_GATE): "0",
            canonical_env_key(TEST_LOCK_WAIT_SECONDS): "45",
        },
        clear=True,
    ):
        assert status_backfill_enabled() is False
        assert design_gate_enabled(tmp_path) is False
        assert _lock_wait_seconds() == 45


def test_design_gate_surfaces_generation_conflict(
    tmp_path: Path,
) -> None:
    with patch.dict(
        os.environ,
        {
            canonical_env_key(DESIGN_GATE): "0",
            legacy_env_key(DESIGN_GATE): "1",
        },
        clear=True,
    ):
        with pytest.raises(SmtwEnvConflictError):
            _ = design_gate_enabled(tmp_path)


def test_reaper_parent_normalizes_all_child_controls_to_canonical(
    tmp_path: Path,
) -> None:
    values = {
        CODEX_REAPER: "1",
        CODEX_REAPER_LOG: str(tmp_path / "legacy.log"),
        CODEX_REAPER_DRY_RUN: "1",
        CODEX_REAPER_POWERSHELL: "pwsh.exe",
    }
    legacy_environment = {
        legacy_env_key(suffix): value for suffix, value in values.items()
    }
    completed = subprocess.CompletedProcess(args=[], returncode=0)

    with (
        patch.dict(os.environ, legacy_environment, clear=True),
        patch.object(codex_stop.os, "name", "nt"),
        patch.object(codex_stop.subprocess, "run", return_value=completed) as run,
    ):
        codex_stop._run_process_reaper(tmp_path, str(tmp_path))

    child_env = run.call_args.kwargs["env"]
    for suffix, value in values.items():
        assert legacy_env_key(suffix) not in child_env
        assert child_env[canonical_env_key(suffix)] == value


def test_reaper_parent_does_not_launch_on_conflicting_controls(
    tmp_path: Path,
) -> None:
    with (
        patch.dict(
            os.environ,
            {
                canonical_env_key(CODEX_REAPER): "0",
                legacy_env_key(CODEX_REAPER): "1",
            },
            clear=True,
        ),
        patch.object(codex_stop.os, "name", "nt"),
        patch.object(codex_stop.subprocess, "run") as run,
    ):
        with pytest.raises(SmtwEnvConflictError):
            codex_stop._run_process_reaper(tmp_path, str(tmp_path))

    run.assert_not_called()


def test_production_has_no_direct_legacy_semantic_env_key() -> None:
    legacy_keys = {legacy_env_key(suffix) for suffix in RUNTIME_ENV_SUFFIXES}
    violations: list[str] = []
    for root_name in PRODUCTION_ROOTS:
        for path in (REPO_ROOT / root_name).rglob("*.py"):
            relative = path.relative_to(REPO_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in legacy_keys
                ):
                    violations.append(f"{relative}:{node.lineno}:{node.value}")

    assert violations == []


def test_adapter_templates_use_only_the_canonical_root_token() -> None:
    template_paths = (
        REPO_ROOT / "adapters" / "codex_cli" / "hooks.json",
        REPO_ROOT / "adapters" / "antigravity" / "hooks.json",
    )

    for path in template_paths:
        content = path.read_text(encoding="utf-8")
        assert "{SMTW_ROOT}" in content
        assert "{FABLE_LITE_ROOT}" not in content
