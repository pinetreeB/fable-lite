from __future__ import annotations

import re
import shlex
from typing import Final


COMMAND_SEPARATORS: Final = frozenset({"&&", "||", ";", "|", "&"})
_SSH_SAFE_FLAGS: Final = frozenset(
    {
        "-4",
        "-6",
        "-A",
        "-C",
        "-K",
        "-T",
        "-X",
        "-Y",
        "-a",
        "-f",
        "-g",
        "-k",
        "-n",
        "-q",
        "-s",
        "-t",
        "-v",
        "-x",
    }
)
_SSH_VALUE_OPTIONS: Final = frozenset(
    {
        "-B",
        "-D",
        "-J",
        "-L",
        "-R",
        "-b",
        "-c",
        "-e",
        "-i",
        "-l",
        "-m",
        "-o",
        "-p",
    }
)
_SSH_SAFE_CONFIG_OPTIONS: Final = frozenset(
    {
        "batchmode",
        "bindaddress",
        "bindinterface",
        "ciphers",
        "compression",
        "connectionattempts",
        "connecttimeout",
        "escapechar",
        "forwardagent",
        "hostkeyalgorithms",
        "hostname",
        "identitiesonly",
        "identityfile",
        "loglevel",
        "macs",
        "port",
        "preferredauthentications",
        "proxyjump",
        "requesttty",
        "serveralivecountmax",
        "serveraliveinterval",
        "stricthostkeychecking",
        "user",
    }
)
_SCP_SAFE_FLAGS: Final = frozenset(
    {"-3", "-4", "-6", "-A", "-B", "-C", "-O", "-R", "-T", "-p", "-q", "-r", "-v"}
)
_SCP_VALUE_OPTIONS: Final = frozenset(
    {"-J", "-P", "-X", "-c", "-i", "-l", "-o"}
)
_SSH_ALL_FLAGS: Final = frozenset(
    f"-{flag}" for flag in "46AaCfGgKkMNnqsTtVvXxYy"
)
_SSH_ALL_VALUE_OPTIONS: Final = frozenset(
    {
        "-B",
        "-D",
        "-E",
        "-F",
        "-I",
        "-J",
        "-L",
        "-O",
        "-P",
        "-Q",
        "-R",
        "-S",
        "-W",
        "-b",
        "-c",
        "-e",
        "-i",
        "-l",
        "-m",
        "-o",
        "-p",
        "-w",
    }
)
_SSH_NO_REMOTE_FLAGS: Final = frozenset({"-G", "-N", "-V"})
_SSH_NO_REMOTE_VALUE_OPTIONS: Final = frozenset({"-O", "-Q", "-W"})
_SCP_ALL_FLAGS: Final = frozenset(f"-{flag}" for flag in "346ABCOpqRrsTv")
_SCP_ALL_VALUE_OPTIONS: Final = frozenset(
    {"-D", "-F", "-J", "-P", "-S", "-X", "-c", "-i", "-l", "-o"}
)
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


def is_remote_mutation_command(command: str) -> bool:
    for segment in command_segments(command):
        tokens = without_environment_assignments(without_shell_redirections(segment))
        if not tokens:
            continue
        name = command_name(tokens[0])
        if name == "ssh" and _is_ssh_remote_mutation(tokens[1:]):
            return True
        if name == "scp" and _is_scp_upload_with_options(
            tokens[1:],
            _SCP_ALL_FLAGS,
            _SCP_ALL_VALUE_OPTIONS,
            validate_values=False,
        ):
            return True
    return False


def without_shell_redirections(tokens: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _is_redirection_operator(token):
            if normalized and _is_stream_selector(normalized[-1]):
                _ = normalized.pop()
            index += 1
            if index < len(tokens) and not _is_redirection_operator(tokens[index]):
                index += 1
            continue
        normalized.append(token)
        index += 1
    return tuple(normalized)


def _is_redirection_operator(token: str) -> bool:
    return bool(
        token
        and ({"<", ">"} & set(token))
        and set(token) <= {"<", ">", "&", "|"}
    )


def _is_stream_selector(token: str) -> bool:
    return token.isdigit() or token == "*"


def remote_ssh_command(command: str) -> str | None:
    segments = command_segments(command)
    if len(segments) != 1:
        return None
    tokens = without_environment_assignments(
        without_shell_redirections(segments[0])
    )
    if not tokens:
        return None
    if command_name(tokens[0]) != "ssh":
        return None
    return remote_ssh_command_tokens(tokens)


def remote_ssh_command_tokens(tokens: tuple[str, ...]) -> str | None:
    tokens = without_shell_redirections(tokens)
    if not tokens or command_name(tokens[0]) != "ssh":
        return None
    arguments = tokens[1:]
    if _ssh_options_disable_remote_command(arguments):
        return None
    operands = _operands(
        arguments,
        _SSH_ALL_FLAGS,
        _SSH_ALL_VALUE_OPTIONS,
        validate_values=False,
    )
    if operands is None or len(operands) < 2:
        return ""
    return " ".join(operands[1:])


def _is_direct_ssh(arguments: tuple[str, ...]) -> bool:
    operands = _operands(arguments, _SSH_SAFE_FLAGS, _SSH_VALUE_OPTIONS)
    return operands is not None and bool(operands) and not operands[0].startswith("-")


def _is_ssh_remote_mutation(arguments: tuple[str, ...]) -> bool:
    if _ssh_options_disable_remote_command(arguments):
        return False
    operands = _operands(
        arguments,
        _SSH_ALL_FLAGS,
        _SSH_ALL_VALUE_OPTIONS,
        validate_values=False,
    )
    return operands is not None and bool(operands) and not operands[0].startswith("-")


def _is_scp_upload(arguments: tuple[str, ...]) -> bool:
    return _is_scp_upload_with_options(
        arguments, _SCP_SAFE_FLAGS, _SCP_VALUE_OPTIONS, validate_values=True
    )


def _is_scp_upload_with_options(
    arguments: tuple[str, ...],
    flags: frozenset[str],
    value_options: frozenset[str],
    *,
    validate_values: bool,
) -> bool:
    operands = _operands(
        arguments,
        flags,
        value_options,
        validate_values=validate_values,
    )
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
    *,
    validate_values: bool = True,
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
            if validate_values and not _is_safe_option_value(
                argument, arguments[index + 1]
            ):
                return None
            index += 2
            continue
        if argument.startswith("-"):
            if argument not in safe_flags and not _is_valid_option_cluster(
                argument,
                safe_flags,
                value_options,
                validate_values=validate_values,
            ):
                return None
            index += 1
            continue
        operands.append(argument)
        options_done = True
        index += 1
    return tuple(operands)


def _ssh_options_disable_remote_command(arguments: tuple[str, ...]) -> bool:
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--" or not argument.startswith("-"):
            return False
        if argument in _SSH_ALL_VALUE_OPTIONS:
            if index + 1 >= len(arguments):
                return True
            if argument in _SSH_NO_REMOTE_VALUE_OPTIONS:
                return True
            if argument == "-o" and _ssh_config_disables_remote_command(
                arguments[index + 1]
            ):
                return True
            index += 2
            continue
        if argument in _SSH_NO_REMOTE_FLAGS or _ssh_option_cluster_disables_remote(
            argument
        ):
            return True
        index += 1
    return True


def _ssh_config_disables_remote_command(value: str) -> bool:
    cleaned = clean_token(value)
    if "=" in cleaned:
        name, raw_value = cleaned.split("=", 1)
    else:
        name, _, raw_value = cleaned.partition(" ")
    return name.casefold() == "sessiontype" and raw_value.strip().casefold() == "none"


def _is_valid_option_cluster(
    argument: str,
    flags: frozenset[str],
    value_options: frozenset[str],
    *,
    validate_values: bool,
) -> bool:
    body = argument[1:]
    index = 0
    while index < len(body):
        option = f"-{body[index]}"
        if option in flags:
            index += 1
            continue
        if option in value_options:
            value = body[index + 1 :]
            return bool(value) and (
                not validate_values or _is_safe_option_value(option, value)
            )
        return False
    return bool(body)


def _ssh_option_cluster_disables_remote(argument: str) -> bool:
    if not argument.startswith("-"):
        return False
    body = argument[1:]
    index = 0
    while index < len(body):
        option = f"-{body[index]}"
        if option in _SSH_NO_REMOTE_FLAGS:
            return True
        if option in _SSH_ALL_VALUE_OPTIONS:
            value = body[index + 1 :]
            return option in _SSH_NO_REMOTE_VALUE_OPTIONS or (
                option == "-o" and _ssh_config_disables_remote_command(value)
            )
        index += 1
    return False


def _is_safe_option_value(option: str, value: str) -> bool:
    if option == "-o":
        return _is_safe_ssh_config(value)
    if option == "-L":
        return _is_tcp_local_forward(value)
    return True


def _is_tcp_local_forward(value: str) -> bool:
    cleaned = clean_token(value)
    if cleaned.startswith("["):
        closing = cleaned.find("]:")
        if closing < 0:
            return False
        port, separator, _ = cleaned[closing + 2 :].partition(":")
        return bool(separator and port.isdigit())
    first, separator, remainder = cleaned.partition(":")
    if not separator:
        return False
    if first.isdigit():
        return True
    second, separator, _ = remainder.partition(":")
    return bool(
        separator
        and second.isdigit()
        and "/" not in first
        and "\\" not in first
    )


def _is_safe_ssh_config(value: str) -> bool:
    cleaned = clean_token(value)
    if "=" in cleaned:
        name, raw_value = cleaned.split("=", 1)
    else:
        name, separator, raw_value = cleaned.partition(" ")
        if not separator:
            raw_value = ""
    normalized_name = name.casefold()
    if normalized_name == "stricthostkeychecking":
        return raw_value.strip().casefold() == "yes"
    return normalized_name in _SSH_SAFE_CONFIG_OPTIONS


def _is_remote_spec(value: str) -> bool:
    if _WINDOWS_DRIVE_RE.match(value):
        return False
    host, separator, path = value.partition(":")
    return bool(separator and host and path and "/" not in host and "\\" not in host)
