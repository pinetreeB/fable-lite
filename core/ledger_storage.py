from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time


REPLACE_RETRY_DELAY_SECONDS = 0.01


def state_dir(project_root: str) -> Path:
    return Path(project_root).resolve() / ".fable-lite"


def ledger_path(project_root: str) -> Path:
    return state_dir(project_root) / "ledger.json"


def atomic_write_text(destination: Path, serialized: str, prefix: str = "ledger-") -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=destination.parent,
        prefix=prefix,
        suffix=".tmp",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            _ = handle.write(serialized)
        _replace_with_one_retry(temporary, destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_bytes(destination: Path, content: bytes, prefix: str = "ledger-") -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "wb",
        delete=False,
        dir=destination.parent,
        prefix=prefix,
        suffix=".tmp",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            _ = handle.write(content)
        _replace_with_one_retry(temporary, destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def _replace_with_one_retry(source: Path, destination: Path) -> None:
    try:
        os.replace(source, destination)
    except PermissionError as first_error:
        time.sleep(REPLACE_RETRY_DELAY_SECONDS)
        try:
            os.replace(source, destination)
        except OSError:
            raise first_error
