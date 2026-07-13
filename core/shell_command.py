from __future__ import annotations

import re
import shlex
from typing import Final


COMMAND_SEPARATORS: Final = frozenset({"&&", "||", ";", "|", "&"})
_SSH_SAFE_FLAGS: Final = frozenset({"-4", "-6", "-n", "-q", "-t", "-T", "-v"})
_SSH_VALUE_OPTIONS: Final = frozenset({"-J", "-i", "-l", "-o", "-p"})
_SSH_SAFE_CONFIG_OPTIONS: Final = frozenset(
    {
        "batchmode",
        "connectionattempts",
        "connecttimeout",
        "identitiesonly",
        "identityfile",
        "loglevel",
        "preferredauthentications",
        "requesttty",
        "serveralivecountmax",
        "serveraliveinterval",
        "stricthostkeychecking",
    }
)
_SCP_SAFE_FLAGS: Final = frozenset({"-4", "-6", "-C", "-p", "-q", "-r", "-v"})
_SCP_VALUE_OPTIONS: Final = frozenset({"-J", "-P", "-i", "-o"})
_ENV_ASSIGNMENT_RE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_WINDOWS_DRIVE_RE: Final = re.compile(r"^[A-Za-z]:[\\/]")


def command_segments(command: str) -> tuple[tuple[str, ...], ...]:
    tokens = _command_tokens(command, posix=False)
    if not tokens:
        return ()
    segments: list[tuple[str, ...]] = []
    current: list[str] = []
    for token in tokens:
        if token in COMMAND_SEPARATORS:
            if current:
                segments.append(tuple(current))
                current = []
            continue
        current.append(token)
    if current:
        segments.append(tuple(current))
    return tuple(segments)


def command_operators(command: str) -> frozenset[str]:
    return frozenset(
        token
        for token in _command_tokens(command, posix=True)
        if token in COMMAND_SEPARATORS
    )


def _command_tokens(command: str, *, posix: bool) -> tuple[str, ...]:
    try:
        lexer = shlex.shlex(command, posix=posix, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = "#"
        return tuple(clean_token(token) for token in lexer if token)
    except ValueError:
        return ()


def command_name(token: str) -> str:
    name = clean_token(token).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    for suffix in (".exe", ".cmd", ".bat"):
        name = name.removesuffix(suffix)
    return name


def clean_token(token: str) -> str:
    return token.strip("\"'")


def without_environment_assignments(tokens: tuple[str, ...]) -> tuple[str, ...]:
    index = 0
    while index < len(tokens) and _ENV_ASSIGNMENT_RE.fullmatch(tokens[index]):
        index += 1
    return tokens[index:]


def is_remote_only_mutation_command(command: str) -> bool:
    if any(marker in command for marker in ("\n", "\r", "`", "$(")):
        return False
    segments = command_segments(command)
    if len(segments) != 1:
        return False
    tokens = without_environment_assignments(segments[0])
    if not tokens or any(token and set(token) <= {"<", ">"} for token in tokens):
        return False
    name = command_name(tokens[0])
    if name == "ssh":
        return _is_direct_ssh(tokens[1:])
    if name == "scp":
        return _is_scp_upload(tokens[1:])
    return False


def remote_ssh_command(command: str) -> str | None:
    if not is_remote_only_mutation_command(command):
        return None
    segments = command_segments(command)
    tokens = without_environment_assignments(segments[0])
    if command_name(tokens[0]) != "ssh":
        return None
    return remote_ssh_command_tokens(tokens)


def remote_ssh_command_tokens(tokens: tuple[str, ...]) -> str | None:
    if not tokens or command_name(tokens[0]) != "ssh":
        return None
    if any(token and set(token) <= {"<", ">"} for token in tokens):
        return None
    operands = _operands(tokens[1:], _SSH_SAFE_FLAGS, _SSH_VALUE_OPTIONS)
    if operands is None or len(operands) < 2:
        return ""
    return " ".join(operands[1:])


def _is_direct_ssh(arguments: tuple[str, ...]) -> bool:
    operands = _operands(arguments, _SSH_SAFE_FLAGS, _SSH_VALUE_OPTIONS)
    return operands is not None and bool(operands) and not operands[0].startswith("-")


def _is_scp_upload(arguments: tuple[str, ...]) -> bool:
    operands = _operands(arguments, _SCP_SAFE_FLAGS, _SCP_VALUE_OPTIONS)
    if operands is None or len(operands) < 2:
        return False
    sources = operands[:-1]
    destination = operands[-1]
    return _is_remote_spec(destination) and all(
        not _is_remote_spec(source) for source in sources
    )


def _operands(
    arguments: tuple[str, ...],
    safe_flags: frozenset[str],
    value_options: frozenset[str],
) -> tuple[str, ...] | None:
    operands: list[str] = []
    index = 0
    options_done = False
    while index < len(arguments):
        argument = arguments[index]
        if options_done:
            operands.append(argument)
            index += 1
            continue
        if argument == "--":
            operands.extend(arguments[index + 1 :])
            break
        if argument in value_options:
            if index + 1 >= len(arguments):
                return None
            if argument == "-o" and not _is_safe_ssh_config(arguments[index + 1]):
                return None
            index += 2
            continue
        attached_option = next(
            (
                option
                for option in value_options
                if len(option) == 2 and argument.startswith(option)
            ),
            None,
        )
        if attached_option is not None:
            value = argument[len(attached_option) :]
            if attached_option == "-o" and not _is_safe_ssh_config(value):
                return None
            index += 1
            continue
        if argument.startswith("-"):
            if argument not in safe_flags and not _is_safe_flag_bundle(
                argument, safe_flags
            ):
                return None
            index += 1
            continue
        operands.append(argument)
        options_done = True
        index += 1
    return tuple(operands)


def _is_safe_flag_bundle(argument: str, safe_flags: frozenset[str]) -> bool:
    return len(argument) > 2 and all(
        f"-{flag}" in safe_flags for flag in argument[1:]
    )


def _is_safe_ssh_config(value: str) -> bool:
    name = clean_token(value).split("=", 1)[0].split(maxsplit=1)[0].casefold()
    return name in _SSH_SAFE_CONFIG_OPTIONS


def _is_remote_spec(value: str) -> bool:
    if _WINDOWS_DRIVE_RE.match(value):
        return False
    host, separator, path = value.partition(":")
    return bool(separator and host and path and "/" not in host and "\\" not in host)
