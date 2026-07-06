from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import sys
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


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
    response = tool_response(payload)
    response_path = _string(response.get("filePath") or response.get("file_path") or response.get("path"))
    return [response_path] if response_path else []


def tool_command(payload: Mapping[str, JsonValue]) -> str:
    source = tool_input(payload)
    return _string(source.get("command") or payload.get("command"))


def tool_output(payload: Mapping[str, JsonValue]) -> str:
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
    response = tool_response(payload)
    if response.get("success") is True or payload.get("success") is True:
        return True
    if response.get("success") is False or payload.get("success") is False:
        return False
    for key in ("exit_code", "exitCode"):
        code = response.get(key)
        if code is not None:
            return code == 0
    if payload.get("exit_code") is not None:
        return payload.get("exit_code") == 0
    # exit_code/success 필드 미제공(nested headless claude 세션 등, E1b F4에서 관측) —
    # 성공/실패를 알 수 없을 때 stdout 텍스트로 보수적 폴백: 실패 신호가 하나라도 있으면 실패,
    # 없고 성공 신호가 있어야 성공. 둘 다 없으면(판정 불가) 실패로 둔다.
    text = tool_output(payload).lower()
    if not text:
        return False
    fail_signals = ("failed", "error", "traceback", "assertionerror", "exception", "fatal", "not ok")
    if any(s in text for s in fail_signals):
        return False
    ok_signals = ("passed", "verify_ok", "success", " ok\n", " ok ", "all tests", "✓")
    return any(s in text for s in ok_signals)


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
