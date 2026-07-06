from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import sys
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

PATCH_PATH_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$", re.MULTILINE)


class PayloadError(Exception):
    pass


def bootstrap_repo_root() -> None:
    root = Path(__file__).resolve().parents[2]
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)


def read_payload() -> JsonObject:
    text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    if not text.strip():
        return {}
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PayloadError(f"malformed JSON payload: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise PayloadError("hook payload must be a JSON object")
    return {str(key): value for key, value in raw.items() if _json_value(value)}


def project_root(payload: Mapping[str, JsonValue]) -> str:
    cwd = _string(payload.get("cwd")) or os.getcwd()
    root = _string(payload.get("project_root")) or cwd
    base = Path(cwd).resolve()
    candidate = Path(root)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve()
    if not _is_relative_to(resolved, base):
        return str(base)
    return str(resolved)


def emit(payload: Mapping[str, JsonValue]) -> int:
    data = json.dumps(dict(payload), ensure_ascii=False)
    _ = sys.stdout.buffer.write(data.encode("utf-8"))
    _ = sys.stdout.buffer.write(b"\n")
    return 0


def fail_open(message: str) -> int:
    return emit({"systemMessage": f"fable-lite fail-open: {message}"})


def tool_input(payload: Mapping[str, JsonValue]) -> JsonObject:
    return _object(payload.get("tool_input"))


def tool_response(payload: Mapping[str, JsonValue]) -> JsonObject:
    return _object(payload.get("tool_response"))


def tool_file_paths(payload: Mapping[str, JsonValue]) -> list[str]:
    source = tool_input(payload)
    paths = string_list(source.get("file_paths"))
    if paths:
        return paths
    for key in ("file_path", "path", "notebook_path"):
        value = source.get(key) or payload.get(key)
        text = _string(value)
        if text:
            return [text]
    patch_paths = _patch_paths(tool_command(payload))
    if patch_paths:
        return patch_paths
    response = tool_response(payload)
    response_path = _string(response.get("filePath") or response.get("file_path") or response.get("path"))
    if response_path:
        return [response_path]
    return _paths_from_response_text(tool_response_text(payload))


def tool_command(payload: Mapping[str, JsonValue]) -> str:
    source = tool_input(payload)
    return _string(source.get("command") or payload.get("command"))


def tool_output(payload: Mapping[str, JsonValue]) -> str:
    response_text = tool_response_text(payload)
    if response_text:
        marker = "Output:\n"
        if marker in response_text:
            return response_text.split(marker, 1)[1].strip()
        return response_text.strip()
    response = tool_response(payload)
    parts = [
        _string(response.get("stdout")),
        _string(response.get("stderr")),
        _string(response.get("output")),
        _string(response.get("result")),
        _string(payload.get("output")),
    ]
    return "\n".join(part for part in parts if part)


def tool_success(payload: Mapping[str, JsonValue]) -> bool:
    response_text = tool_response_text(payload)
    if response_text:
        return bool(re.search(r"^Exit code:\s*0\s*$", response_text, re.MULTILINE))
    response = tool_response(payload)
    if response.get("success") is True or payload.get("success") is True:
        return True
    for key in ("exit_code", "exitCode"):
        code = response.get(key)
        if code is not None:
            return code == 0
    return payload.get("exit_code") == 0


def tool_response_text(payload: Mapping[str, JsonValue]) -> str:
    response = payload.get("tool_response")
    if isinstance(response, str):
        return response
    return ""


def last_assistant_text(payload: Mapping[str, JsonValue]) -> str:
    direct = _string(payload.get("last_assistant_message") or payload.get("lastAssistantMessage"))
    if direct:
        return direct
    return transcript_last_assistant_text(payload)


def transcript_last_assistant_text(payload: Mapping[str, JsonValue]) -> str:
    transcript = _string(payload.get("transcript_path") or payload.get("transcriptPath"))
    if not transcript:
        return ""
    path = Path(transcript)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    last = ""
    for line in lines:
        if not line.strip():
            continue
        try:
            raw: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict) and _is_assistant_record(raw):
            last = _content_text(raw)
    return last


def string_list(value: JsonValue | object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _patch_paths(command: str) -> list[str]:
    found: list[str] = []
    for match in PATCH_PATH_RE.finditer(command):
        path = (match.group(1) or match.group(2) or "").strip()
        if path and path not in found:
            found.append(path)
    return found


def _paths_from_response_text(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^[AM]\s+.+", stripped):
            path = stripped[2:].strip()
            if path and path not in found:
                found.append(path)
    return found


def _json_value(value: object) -> bool:
    if isinstance(value, str | int | bool) or value is None:
        return True
    if isinstance(value, list):
        return all(_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_value(item) for key, item in value.items())
    return False


def _string(value: JsonValue | object) -> str:
    return value if isinstance(value, str) else ""


def _object(value: JsonValue | object) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if _json_value(item)}


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def _is_assistant_record(raw: Mapping[str, object]) -> bool:
    if raw.get("type") == "assistant" or raw.get("role") == "assistant":
        return True
    message = raw.get("message")
    return isinstance(message, dict) and message.get("role") == "assistant"


def _content_text(raw: Mapping[str, object]) -> str:
    message = raw.get("message")
    content: object = raw.get("content")
    if isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text") or item.get("content")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)
