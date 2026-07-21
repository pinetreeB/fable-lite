from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Final, TypeAlias, cast

from .runtime_env import DESIGN_GATE, canonical_env_key, smtw_env


JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
DESIGN_GATE_ENV: Final = canonical_env_key(DESIGN_GATE)
DESIGN_CONFIG_PATH: Final = Path("design/gate.config")
UI_EXTENSIONS: Final = frozenset(
    {".css", ".scss", ".sass", ".less", ".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".svg"}
)
UI_WORD_RE: Final = re.compile(r"(?<![A-Za-z0-9_])(?:ui|ux)(?![A-Za-z0-9_])", re.IGNORECASE)
UI_TERMS: Final = (
    "디자인",
    "화면",
    "페이지",
    "컴포넌트",
    "stylesheet",
    "tailwind",
    "style sheet",
)
CREATION_TERMS: Final = ("생성", "만들", "create", "generate", "build")


@dataclass(frozen=True, slots=True)
class DesignAllowlistEntry:
    path: str
    rule_id: str
    reason: str
    expires: date

    def matches(self, path: str, rule_id: str, today: date) -> bool:
        return today <= self.expires and self.rule_id == rule_id and fnmatchcase(path, self.path)


@dataclass(frozen=True, slots=True)
class DesignGateConfig:
    present: bool
    enabled: bool | None
    allowlist: tuple[DesignAllowlistEntry, ...]


def load_design_gate_config(root: Path) -> DesignGateConfig:
    path = root.resolve() / DESIGN_CONFIG_PATH
    try:
        raw = cast(JsonValue, json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return DesignGateConfig(False, None, ())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return DesignGateConfig(True, None, ())
    if not isinstance(raw, dict):
        return DesignGateConfig(True, None, ())
    enabled_value = raw.get("enabled")
    enabled = enabled_value if isinstance(enabled_value, bool) else None
    return DesignGateConfig(True, enabled, _allowlist(raw.get("allowlist")))


def design_gate_enabled(root: Path) -> bool:
    config = load_design_gate_config(root)
    if config.present:
        return config.enabled is True
    return smtw_env(DESIGN_GATE) == "1"


def design_domain(prompt: str, requested_paths: list[str]) -> str:
    lowered = f" {prompt.casefold()} "
    if (
        any(is_ui_path(path) for path in requested_paths)
        or UI_WORD_RE.search(prompt) is not None
        or any(term in lowered for term in UI_TERMS)
    ):
        return "UI"
    if any(term in lowered for term in CREATION_TERMS):
        return "CREATION"
    return "GENERAL"


def is_ui_path(path: str) -> bool:
    return Path(path.replace("\\", "/")).suffix.casefold() in UI_EXTENSIONS


def git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root.resolve()), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip() if result.returncode == 0 else "HEAD"


def dirty_ui_line_baseline(root: Path) -> dict[str, JsonValue]:
    paths = _git_lines(root, ("diff", "--name-only", "HEAD", "--"))
    paths.extend(_git_lines(root, ("ls-files", "--others", "--exclude-standard")))
    baseline: dict[str, JsonValue] = {}
    for path in sorted(set(paths)):
        normalized = path.replace("\\", "/")
        target = root.resolve() / normalized
        if is_ui_path(normalized) and target.is_file():
            baseline[normalized] = [_line_hash(line) for line in target.read_text(encoding="utf-8").splitlines()]
    return baseline


def _git_lines(root: Path, arguments: tuple[str, ...]) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root.resolve()), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.splitlines() if result.returncode == 0 else []


def _line_hash(line: str) -> str:
    return hashlib.blake2b(line.encode("utf-8"), digest_size=16).hexdigest()


def _allowlist(value: JsonValue | None) -> tuple[DesignAllowlistEntry, ...]:
    if not isinstance(value, list):
        return ()
    entries: list[DesignAllowlistEntry] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        entry = _allowlist_entry(item)
        if entry is not None:
            entries.append(entry)
    return tuple(entries)


def _allowlist_entry(value: dict[str, JsonValue]) -> DesignAllowlistEntry | None:
    path = value.get("path")
    rule_id = value.get("rule_id")
    reason = value.get("reason")
    expires = value.get("expires")
    if not isinstance(path, str) or not path:
        return None
    if not isinstance(rule_id, str) or not rule_id:
        return None
    if not isinstance(reason, str) or not reason:
        return None
    if not isinstance(expires, str) or not expires:
        return None
    try:
        expiry = date.fromisoformat(expires)
    except ValueError:
        return None
    return DesignAllowlistEntry(path, rule_id, reason, expiry)
