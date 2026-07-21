from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from .ledger_schema import JsonObject, JsonValue
from .runtime_env import AUTO_MIGRATION, canonical_env_key, smtw_env

DEFAULT_RECEIPTS_DIR: Final = Path(__file__).resolve().parent / "release_receipts"
AUTO_MIGRATION_ENV: Final = canonical_env_key(AUTO_MIGRATION)


def auto_migration_enabled(receipts_dir: Path | None = None) -> bool:
    """Ledger v1->v2 schema migration gate (unlocked in v2.0.0).

    Enabled whenever the packaged release receipts prove green — deliberately
    env-independent so a clean wheel install keeps schema migration on. Do NOT
    gate the opt-in invocation-status backfill on this; use
    ``status_backfill_enabled()`` for that.
    """
    directory = DEFAULT_RECEIPTS_DIR if receipts_dir is None else receipts_dir
    return _provenance_green(_load(directory / "provenance-latest.json")) and _benchmark_green(
        _load(directory / "bench-latest.json")
    )


def status_backfill_enabled() -> bool:
    """Opt-in gate for backfilling missing invocation statuses on legacy ledgers.

    Distinct from schema migration: this touches live ledgers and must stay off
    unless the operator explicitly sets ``SMTW_AUTO_MIGRATION=1`` (the v3
    legacy alias is resolved by :mod:`core.runtime_env`).
    """
    return smtw_env(AUTO_MIGRATION) == "1"


def _load(path: Path) -> JsonObject | None:
    try:
        raw: JsonValue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _provenance_green(receipt: JsonObject | None) -> bool:
    if receipt is None or not _passed(receipt):
        return False
    golden = _mapping(receipt.get("golden"))
    replay = _mapping(receipt.get("canonical_replay"))
    git = _mapping(receipt.get("git_non_git"))
    return (
        golden is not None
        and replay is not None
        and git is not None
        and _integer(golden.get("cases")) == 200
        and _integer(golden.get("false_negatives")) == 0
        and _integer(golden.get("false_positives")) == 0
        and _integer(replay.get("failures")) == 0
        and git.get("mismatches") == []
    )


def _benchmark_green(receipt: JsonObject | None) -> bool:
    if receipt is None or not _passed(receipt):
        return False
    slo = _mapping(receipt.get("slo"))
    if slo is None or slo.get("passed") is not True:
        return False
    scales = _mapping(slo.get("scales"))
    one_k = _mapping(scales.get("1k")) if scales is not None else None
    ten_k = _mapping(scales.get("10k")) if scales is not None else None
    return one_k is not None and ten_k is not None and one_k.get("passed") is True and ten_k.get("passed") is True


def _passed(receipt: JsonObject) -> bool:
    hard_gate = _mapping(receipt.get("hard_gate"))
    return hard_gate is not None and hard_gate.get("passed") is True


def _mapping(value: JsonValue | None) -> JsonObject | None:
    return value if isinstance(value, dict) else None


def _integer(value: JsonValue | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
