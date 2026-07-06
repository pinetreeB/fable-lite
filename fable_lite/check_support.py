from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import re
import subprocess

from core.ledger import classify_change_kind

SENTINEL_RE = re.compile(r"(?P<path>(?:[\w./\\-]+/)?\.done[\w_.-]*|tmp[/\\]\.done[\w_.-]*)", re.IGNORECASE)


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def parse_porcelain(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        value = line[3:]
        if " -> " in value:
            value = value.rsplit(" -> ", 1)[1]
        paths.append(value.replace("\\", "/"))
    return paths


def is_state_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized == ".fable-lite" or normalized.startswith(".fable-lite/")


def changed_since(root: Path, path: str, since_file: Path) -> bool:
    if not since_file.exists():
        return True
    target = root / path
    try:
        return not target.exists() or target.stat().st_mtime >= since_file.stat().st_mtime
    except OSError:
        return True


def relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""


def path_evidence(root: Path, path: str) -> str:
    target = root / path
    if target.exists() and target.is_file():
        try:
            return target.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            return ""
    diff = git(root, "diff", "--", path)
    return diff.stdout[:4000] if diff.returncode == 0 else ""


def sentinels(prompt: str) -> list[str]:
    found: list[str] = []
    for match in SENTINEL_RE.finditer(prompt):
        path = match.group("path").replace("\\", "/")
        if path not in found:
            found.append(path)
    return found


def has_successful_verification(ledger: Mapping[str, object]) -> bool:
    results = ledger.get("verification_results")
    if not isinstance(results, list):
        return False
    return any(isinstance(result, dict) and result.get("success") is True for result in results)


def non_docs(paths: list[str]) -> list[str]:
    return [path for path in paths if classify_change_kind(path) != "docs"]


def merge(first: list[str], second: list[str]) -> list[str]:
    merged = list(first)
    for item in second:
        if item and item not in merged:
            merged.append(item)
    return merged


def string(value: object) -> str:
    return value if isinstance(value, str) else ""


def string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
