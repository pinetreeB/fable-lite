from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
from typing import Final, cast


ROOT_TOKEN: Final = "{SMTW_ROOT}"
EXPECTED_ROOT_TOKENS: Final = 8
EVENT_SCRIPTS: Final = {
    "UserPromptSubmit": "user_prompt_submit.py",
    "PreToolUse": "pre_tool_use.py",
    "PostToolUse": "post_tool_use.py",
    "Stop": "stop.py",
}
POWERSHELL: Final = "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install self-locating show-me-the-work hooks into a Codex project.",
    )
    _ = parser.add_argument("--target", required=True, type=Path, help="Target project directory")
    _ = parser.add_argument(
        "--upgrade",
        action="store_true",
        help="Atomically replace only smtw-owned hook entries in an existing manifest",
    )
    return parser


def _first_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, list) or not value or not isinstance(value[0], dict):
        return None
    return cast(dict[str, object], value[0])


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _posix_command(script: Path) -> str:
    return f"{shlex.quote(Path(sys.executable).as_posix())} {shlex.quote(script.as_posix())}"


def _windows_command(script: Path) -> str:
    executable = _powershell_literal(Path(sys.executable).as_posix())
    script_path = _powershell_literal(script.as_posix())
    return f'{POWERSHELL} -NoProfile -ExecutionPolicy Bypass -Command "& {executable} {script_path}"'


def render_hooks(repo_root: Path) -> str | None:
    template = Path(__file__).with_name("hooks.json").read_text(encoding="utf-8")
    if template.count(ROOT_TOKEN) != EXPECTED_ROOT_TOKENS:
        return None
    try:
        raw: object = json.loads(template)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    manifest = cast(dict[str, object], raw)
    hooks_value = manifest.get("hooks")
    if not isinstance(hooks_value, dict):
        return None
    hooks = cast(dict[str, object], hooks_value)

    for event, filename in EVENT_SCRIPTS.items():
        matcher = _first_object(hooks.get(event))
        command_entry = _first_object(matcher.get("hooks")) if matcher is not None else None
        if command_entry is None:
            return None
        command = command_entry.get("command")
        command_windows = command_entry.get("commandWindows")
        if not isinstance(command, str) or ROOT_TOKEN not in command:
            return None
        if not isinstance(command_windows, str) or ROOT_TOKEN not in command_windows:
            return None
        script = repo_root / "adapters" / "codex_cli" / filename
        command_entry["command"] = _posix_command(script)
        command_entry["commandWindows"] = _windows_command(script)

    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def _is_owned_hook(value: object, filename: str) -> bool:
    if not isinstance(value, dict):
        return False
    entry = cast(dict[str, object], value)
    fragment = f"/adapters/codex_cli/{filename}"
    return any(
        isinstance(command, str)
        and fragment in command.replace("\\", "/")
        for field in ("command", "commandWindows")
        if (command := entry.get(field)) is not None
    )


def merge_owned_hooks(
    existing: dict[str, object],
    rendered: dict[str, object],
) -> dict[str, object] | None:
    existing_hooks_value = existing.get("hooks")
    rendered_hooks_value = rendered.get("hooks")
    if not isinstance(existing_hooks_value, dict) or not isinstance(
        rendered_hooks_value, dict
    ):
        return None
    existing_hooks = cast(dict[str, object], existing_hooks_value)
    rendered_hooks = cast(dict[str, object], rendered_hooks_value)

    for event, filename in EVENT_SCRIPTS.items():
        current_value = existing_hooks.get(event, [])
        replacement_value = rendered_hooks.get(event)
        if not isinstance(current_value, list) or not isinstance(
            replacement_value, list
        ):
            return None
        replacement_matcher = _first_object(replacement_value)
        if replacement_matcher is None:
            return None

        retained: list[object] = []
        for raw_matcher in current_value:
            if not isinstance(raw_matcher, dict):
                retained.append(raw_matcher)
                continue
            matcher = cast(dict[str, object], raw_matcher)
            hooks_value = matcher.get("hooks")
            if not isinstance(hooks_value, list):
                retained.append(matcher)
                continue
            filtered = [
                entry
                for entry in hooks_value
                if not _is_owned_hook(entry, filename)
            ]
            if len(filtered) == len(hooks_value):
                retained.append(matcher)
            elif filtered:
                retained.append({**matcher, "hooks": filtered})

        existing_hooks[event] = [*retained, replacement_matcher]
    return existing


def _parsed_object(text: str) -> dict[str, object] | None:
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    return cast(dict[str, object], raw) if isinstance(raw, dict) else None


def _atomic_replace_text(destination: Path, content: str) -> None:
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=destination.parent,
        prefix=f"{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(handle.name)
    try:
        with handle:
            _ = handle.write(content)
        os.replace(temporary, destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    target_value = getattr(args, "target", None)
    if not isinstance(target_value, Path):
        parser.error("--target must be a project directory")

    repo_root = Path(__file__).resolve().parents[2]
    rendered = render_hooks(repo_root)
    if rendered is None:
        print("show-me-the-work Codex hook template is invalid; installation aborted.", file=sys.stderr)
        return 2

    destination = target_value.resolve() / ".codex" / "hooks.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    upgrade = getattr(args, "upgrade", False) is True
    try:
        with destination.open("x", encoding="utf-8", newline="\n") as handle:
            _ = handle.write(rendered)
    except FileExistsError:
        if not upgrade:
            print(f"Refusing to overwrite existing Codex hooks: {destination}", file=sys.stderr)
            return 1
        if destination.is_symlink():
            print(f"Refusing to upgrade symlinked Codex hooks: {destination}", file=sys.stderr)
            return 1
        try:
            existing_text = destination.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Cannot read existing Codex hooks: {exc}", file=sys.stderr)
            return 1
        existing = _parsed_object(existing_text)
        replacement = _parsed_object(rendered)
        if existing is None or replacement is None:
            print(f"Existing Codex hooks are not a JSON object: {destination}", file=sys.stderr)
            return 1
        merged = merge_owned_hooks(existing, replacement)
        if merged is None:
            print(f"Existing Codex hooks have an incompatible shape: {destination}", file=sys.stderr)
            return 1
        try:
            _atomic_replace_text(
                destination,
                json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            )
        except OSError as exc:
            print(f"Cannot atomically upgrade Codex hooks: {exc}", file=sys.stderr)
            return 1
        print(f"Upgraded smtw-owned Codex hooks: {destination}")
        return 0

    print(f"Installed show-me-the-work Codex hooks: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
