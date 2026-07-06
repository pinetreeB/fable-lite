from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import fnmatch
import json
from pathlib import Path
import posixpath
from typing import TypeAlias

from core.ledger import JsonObject, agent_log_path

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class TaskCard:
    path: Path
    slug: str
    owner: str
    allowed_paths: list[str]
    forbidden_paths: list[str]
    verify: str
    done_artifact: str
    sentinel: str

    def target(self) -> str:
        match self.owner:
            case "antigravity":
                return "agy"
            case "claude" | "claude-ultracode":
                return "claude"
            case "codex":
                return "codex"
            case _:
                return "codex"

    def completion_paths(self) -> list[str]:
        paths: list[str] = []
        for item in (self.done_artifact, self.sentinel):
            if item and item not in paths:
                paths.append(item)
        return paths


def load_task_card(path: Path) -> TaskCard:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"작업카드 JSON 객체가 아닙니다: {path}")
    return TaskCard(
        path=path.resolve(),
        slug=_string(raw, "slug"),
        owner=_string(raw, "owner"),
        allowed_paths=_string_list(raw, "allowed_paths"),
        forbidden_paths=_string_list(raw, "forbidden_paths"),
        verify=_string(raw, "verify"),
        done_artifact=_string(raw, "done_artifact"),
        sentinel=_string(raw, "sentinel"),
    )


def card_changed_excludes(root: Path, card: TaskCard | None) -> list[str]:
    if card is None:
        return []
    excluded = [_relative_to_root(root, card.path)]
    excluded.extend(_relative_to_root(root, _path_for(root, path)) for path in card.completion_paths())
    return [path for path in excluded if path]


def card_completion_findings(root: Path, card: TaskCard | None) -> list[str]:
    missing: list[str] = []
    if card is None:
        return missing
    for path in card.completion_paths():
        if path and not _path_for(root, path).exists():
            missing.append(f"{path}: done_artifact 파일이 없습니다")
    return missing


def card_validation_findings(card: TaskCard | None) -> list[str]:
    if card is None:
        return []
    findings: list[str] = []
    for field in ("slug", "owner", "verify", "done_artifact"):
        if not getattr(card, field):
            findings.append(f"{field}: 작업카드 필수 필드가 없습니다")
    if not card.allowed_paths:
        findings.append("allowed_paths: 작업카드 필수 필드가 없습니다")
    return findings


def card_forbidden_findings(changed_files: list[str], card: TaskCard | None) -> list[str]:
    if card is None:
        return []
    findings: list[str] = []
    for path in changed_files:
        if any(_matches_card_path(path, pattern) for pattern in card.forbidden_paths):
            findings.append(f"{path}: forbidden_paths 침범")
    return findings


def card_scope_findings(changed_files: list[str], card: TaskCard | None) -> list[str]:
    if card is None or not card.allowed_paths:
        return []
    findings: list[str] = []
    for path in changed_files:
        if not any(_matches_card_path(path, pattern) for pattern in card.allowed_paths):
            findings.append(f"{path}: allowed_paths 범위 밖")
    return findings


def card_verify_success(root: Path, agent: str, ledger: JsonObject, card: TaskCard | None, fallback: bool) -> bool:
    if card is None or not card.verify:
        return fallback if card is None else False
    if agent:
        path = agent_log_path(str(root), agent)
        if path.exists():
            return _agent_log_has_verify_after(path, card)
    return False


def _agent_log_has_verify_after(path: Path, card: TaskCard) -> bool:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        card_time = datetime.fromtimestamp(card.path.stat().st_mtime, UTC)
    except OSError:
        return False
    for line in lines:
        if _line_is_card_verify(line, card, card_time):
            return True
    return False


def _line_is_card_verify(line: str, card: TaskCard, card_time: datetime) -> bool:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return False
    if not isinstance(raw, dict):
        return False
    timestamp = _timestamp(raw)
    return (
        timestamp is not None
        and timestamp >= card_time
        and raw.get("event") == "verification"
        and raw.get("command") == card.verify
        and raw.get("success") is True
    )


def _timestamp(payload: dict[str, JsonValue]) -> datetime | None:
    value = payload.get("timestamp")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _path_for(root: Path, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return ""


def _matches_card_path(path: str, pattern: str) -> bool:
    normalized = _normalize_card_path(path)
    normalized_pattern = _normalize_card_path(pattern)
    return _match_segments(normalized.split("/"), normalized_pattern.split("/"))


def _normalize_card_path(value: str) -> str:
    normalized = posixpath.normpath(value.replace("\\", "/")).strip("/")
    return ("" if normalized == "." else normalized).casefold()


def _match_segments(path_parts: list[str], pattern_parts: list[str]) -> bool:
    if not pattern_parts:
        return not path_parts
    head, *tail = pattern_parts
    if head == "**":
        return not tail or any(_match_segments(path_parts[index:], tail) for index in range(len(path_parts) + 1))
    return bool(path_parts) and fnmatch.fnmatchcase(path_parts[0], head) and _match_segments(path_parts[1:], tail)


def _string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def _string_list(payload: dict[str, JsonValue], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
