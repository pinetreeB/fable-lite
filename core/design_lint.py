from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
from pathlib import Path
import re
import subprocess
from typing import Final, TypeAlias

from .design_gate import DesignAllowlistEntry, is_ui_path, load_design_gate_config


JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
RAW_COLOR: Final = "design/raw-color"
RAW_SPACING: Final = "design/raw-spacing"
TAILWIND_ARBITRARY: Final = "design/tailwind-arbitrary"
COLOR_LITERAL_PATTERN: Final = (
    r"(?:#[0-9A-Fa-f]{3,8}\b|(?:rgba?|hsla?)\([^)]*\))"
)
COLOR_LITERAL_RE: Final = re.compile(COLOR_LITERAL_PATTERN, re.IGNORECASE)
TAILWIND_RE: Final = re.compile(r"\[(?:#[0-9A-Fa-f]{3,8}|-?\d+(?:\.\d+)?px)\]")
CHART_DATA_START_RE: Final = re.compile(
    r"\b(?:chartData|chart_data|datasets?|series)\b\s*(?:=|:)\s*(?P<open>[\[{])",
    re.IGNORECASE,
)
CHART_COLOR_RE: Final = re.compile(
    rf"\b(?:color|backgroundColor)\s*:\s*[\"']?{COLOR_LITERAL_PATTERN}",
    re.IGNORECASE,
)
COLOR_PROPERTY_RE: Final = re.compile(
    rf"(?:^|[;{{\s])(?:color|background(?:-color)?|border(?:-[\w-]+)?-color|outline-color|fill|stroke|box-shadow|text-shadow|--[\w-]+)\s*:\s*[^;}}]*?{COLOR_LITERAL_PATTERN}",
    re.IGNORECASE,
)
SPACING_PROPERTY_RE: Final = re.compile(
    r"(?:^|[;{\s])(?:margin(?:-[\w-]+)?|padding(?:-[\w-]+)?|gap|row-gap|column-gap|inset(?:-[\w-]+)?|top|right|bottom|left)\s*:\s*(?P<value>[^;}]+)",
    re.IGNORECASE,
)
JS_SPACING_RE: Final = re.compile(
    r"(?:margin\w*|padding\w*|gap|rowGap|columnGap|inset|top|right|bottom|left)\s*:\s*(?P<value>[\"'][^\"']*[\"']|-?\d+(?:\.\d+)?(?:px)?)",
)
PX_LITERAL_RE: Final = re.compile(r"(?P<number>-?\d+(?:\.\d+)?)px\b", re.IGNORECASE)
NUMBER_LITERAL_RE: Final = re.compile(r"^-?\d+(?:\.\d+)?$")
SCRIPT_EXTENSIONS: Final = frozenset({".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"})


@dataclass(frozen=True, slots=True)
class DesignViolation:
    file: str
    line: int
    rule_id: str
    message: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "file": self.file,
            "line": self.line,
            "rule_id": self.rule_id,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class DesignLintResult:
    violations: tuple[DesignViolation, ...]
    checked_files: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.violations

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "passed": self.passed,
            "checked_files": list(self.checked_files),
            "violations": [violation.to_json() for violation in self.violations],
        }


@dataclass(frozen=True, slots=True)
class DesignLintBaseline:
    revision: str = "HEAD"
    dirty_lines: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def hashes_for(self, path: str) -> tuple[str, ...] | None:
        return next((hashes for candidate, hashes in self.dirty_lines if candidate == path), None)


def run_design_lint(
    root: Path,
    paths: list[str] | None = None,
    baseline: DesignLintBaseline | None = None,
) -> DesignLintResult:
    absolute_root = root.resolve()
    active_baseline = baseline or DesignLintBaseline()
    candidates = sorted(set(paths or _changed_paths(absolute_root, active_baseline.revision)))
    allowlist = load_design_gate_config(absolute_root).allowlist
    violations: list[DesignViolation] = []
    checked: list[str] = []
    for relative in candidates:
        normalized = relative.replace("\\", "/")
        if not is_ui_path(normalized):
            continue
        path = absolute_root / normalized
        if not path.is_file():
            continue
        checked.append(normalized)
        changed_lines = _changed_lines(absolute_root, normalized, active_baseline)
        violations.extend(_lint_file(path, normalized, changed_lines, allowlist))
    return DesignLintResult(tuple(violations), tuple(checked))


def _lint_file(
    path: Path,
    relative: str,
    changed_lines: set[int],
    allowlist: tuple[DesignAllowlistEntry, ...],
) -> list[DesignViolation]:
    if path.name.casefold().startswith("tokens."):
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    suffix = path.suffix.casefold()
    violations: list[DesignViolation] = []
    for line_number in sorted(changed_lines):
        if line_number < 1 or line_number > len(lines):
            continue
        line = lines[line_number - 1]
        rules = _rules_for_line(line, suffix, "\n".join(lines[:line_number]))
        for rule_id in rules:
            if _allowed(relative, rule_id, allowlist):
                continue
            violations.append(
                DesignViolation(relative, line_number, rule_id, _message(rule_id))
            )
    return violations


def _rules_for_line(line: str, suffix: str, context: str) -> tuple[str, ...]:
    rules: list[str] = []
    if TAILWIND_RE.search(line):
        rules.append(TAILWIND_ARBITRARY)
    if suffix != ".svg" and _has_raw_color(line, suffix, context):
        rules.append(RAW_COLOR)
    if _has_raw_spacing(line, suffix):
        rules.append(RAW_SPACING)
    return tuple(rules)


def _has_raw_color(line: str, suffix: str, context: str) -> bool:
    if suffix in SCRIPT_EXTENSIONS:
        return COLOR_LITERAL_RE.search(line) is not None and not _chart_data_color(context)
    return COLOR_PROPERTY_RE.search(line) is not None


def _chart_data_color(context: str) -> bool:
    current_line_start = context.rfind("\n") + 1
    if CHART_COLOR_RE.search(context[current_line_start:]) is None:
        return False
    starts = tuple(CHART_DATA_START_RE.finditer(context))
    if not starts:
        return False
    start = starts[-1]
    if start.start() >= current_line_start:
        return True
    opener = start.group("open")
    closer = "]" if opener == "[" else "}"
    preceding = context[start.end() - 1 : current_line_start]
    return preceding.count(opener) > preceding.count(closer)


def _has_raw_spacing(line: str, suffix: str) -> bool:
    pattern = JS_SPACING_RE if suffix in SCRIPT_EXTENSIONS else SPACING_PROPERTY_RE
    for match in pattern.finditer(line):
        value = match.group("value").strip().strip("\"'")
        numbers = [float(item.group("number")) for item in PX_LITERAL_RE.finditer(value)]
        if suffix in SCRIPT_EXTENSIONS and not numbers and NUMBER_LITERAL_RE.fullmatch(value):
            numbers.append(float(value))
        if any(abs(number) > 1 for number in numbers):
            return True
    return False


def _allowed(
    path: str,
    rule_id: str,
    allowlist: tuple[DesignAllowlistEntry, ...],
) -> bool:
    today = date.today()
    return any(entry.matches(path, rule_id, today) for entry in allowlist)


def _message(rule_id: str) -> str:
    if rule_id == RAW_COLOR:
        return "raw color literal must use a design token"
    if rule_id == RAW_SPACING:
        return "raw spacing literal must use a design token"
    return "Tailwind arbitrary design literal must use a token"


def _changed_paths(root: Path, baseline_revision: str) -> list[str]:
    tracked = _git(root, ("diff", "--name-only", baseline_revision, "--"))
    untracked = _git(root, ("ls-files", "--others", "--exclude-standard"))
    return [line for line in (*tracked, *untracked) if line]


def _changed_lines(root: Path, path: str, baseline: DesignLintBaseline) -> set[int]:
    dirty_hashes = baseline.hashes_for(path)
    if dirty_hashes is not None:
        return _changed_from_hashes(root / path, dirty_hashes)
    if not _tracked_at(root, path, baseline.revision):
        return set(range(1, _line_count(root / path) + 1))
    patch = _git(root, ("diff", "--unified=0", baseline.revision, "--", path))
    changed: set[int] = set()
    for line in patch:
        match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if match is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        changed.update(range(start, start + count))
    return changed


def _changed_from_hashes(path: Path, baseline_hashes: tuple[str, ...]) -> set[int]:
    current_hashes = tuple(
        hashlib.blake2b(line.encode("utf-8"), digest_size=16).hexdigest()
        for line in path.read_text(encoding="utf-8").splitlines()
    )
    return {
        index + 1
        for index, digest in enumerate(current_hashes)
        if index >= len(baseline_hashes) or digest != baseline_hashes[index]
    }


def _tracked_at(root: Path, path: str, revision: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{revision}:{path}"],
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _git(root: Path, arguments: tuple[str, ...]) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.splitlines() if result.returncode == 0 else []
