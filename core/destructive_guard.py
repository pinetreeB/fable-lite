from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum, auto
import os
from pathlib import Path
import re
from typing import Final, TypeAlias

from .ledger import JsonObject, JsonValue, load_ledger, state_dir
from .provenance_policy import (
    PROJECT_PATH_OUT_OF_ROOT as _CANON_OUT_OF_ROOT,
    PROJECT_PATH_UNRESOLVABLE as _CANON_UNRESOLVABLE,
    canonical_manifest_key,
    canonicalize_project_path,
)
from .scorecard_coordination import CoordinationReason
from .state_layout import is_protected_state_name

Decision: TypeAlias = dict[str, JsonValue]

SHELL_TOOLS: Final[frozenset[str]] = frozenset({"Bash", "PowerShell"})
COMMAND_SEPARATORS: Final[frozenset[str]] = frozenset({"&&", "||", ";", "|"})

GIT_SUBCOMMANDS_ALWAYS_IMPLICIT: Final[frozenset[str]] = frozenset({"reset", "clean", "stash", "read-tree"})
GIT_SUBCOMMANDS_PATHSPEC: Final[frozenset[str]] = frozenset({"checkout", "restore"})
GIT_SWITCH_DISCARD_FLAGS: Final[frozenset[str]] = frozenset({"--discard-changes"})
GIT_SUBCOMMAND_NAMES: Final[frozenset[str]] = (
    GIT_SUBCOMMANDS_ALWAYS_IMPLICIT | GIT_SUBCOMMANDS_PATHSPEC | frozenset({"switch"})
)

REMOVE_COMMAND_NAMES: Final[frozenset[str]] = frozenset({"rm", "remove-item", "del", "rd", "rmdir"})
TRUNCATE_COMMAND_NAMES: Final[frozenset[str]] = frozenset({"set-content", "out-file"})
SHELL_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {
        "ash",
        "bash",
        "csh",
        "dash",
        "fish",
        "ksh",
        "sh",
        "tcsh",
        "zsh",
    }
)
WRAPPER_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {
        *SHELL_COMMAND_NAMES,
        "busybox",
        "builtin",
        "cmd",
        "cmd.exe",
        "command",
        "doas",
        "env",
        "eval",
        "exec",
        "ionice",
        "nice",
        "nohup",
        "powershell",
        "powershell.exe",
        "pwsh",
        "setsid",
        "stdbuf",
        "sudo",
        "time",
        "timeout",
        "toybox",
        "xargs",
    }
)
OPAQUE_WRAPPER_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {
        *SHELL_COMMAND_NAMES,
        "cmd",
        "cmd.exe",
        "eval",
        "powershell",
        "powershell.exe",
        "pwsh",
    }
)
PATH_FLAGS: Final[frozenset[str]] = frozenset({"-path", "-literalpath", "-filepath"})
FIND_TESTS_WITH_VALUE: Final[frozenset[str]] = frozenset(
    {
        "-name",
        "-iname",
        "-path",
        "-ipath",
        "-wholename",
        "-iwholename",
        "-lname",
        "-ilname",
        "-regex",
        "-iregex",
        "-type",
        "-xtype",
        "-user",
        "-group",
        "-uid",
        "-gid",
        "-size",
        "-mtime",
        "-mmin",
        "-atime",
        "-amin",
        "-ctime",
        "-cmin",
        "-newer",
    }
)

CATEGORY_GIT: Final[str] = "git_destructive"
CATEGORY_REMOVE: Final[str] = "os_remove"
CATEGORY_TRUNCATE: Final[str] = "truncate_redirect"

R2_COORDINATION_REASON_MAP: Final[dict[str, CoordinationReason]] = {
    "ledger_degraded": CoordinationReason.ATTRIBUTION_DEGRADED,
    "attribution_health_unavailable": CoordinationReason.ATTRIBUTION_DEGRADED,
    "attribution_degraded_or_capacity_exceeded": CoordinationReason.ATTRIBUTION_DEGRADED,
    "canonicalization_unavailable": CoordinationReason.UNRESOLVABLE_TARGET,
    "state_dir_protected": CoordinationReason.STATE_DIR_PROTECTED,
    "attribution_lookup_unavailable": CoordinationReason.ATTRIBUTION_DEGRADED,
    "peer_unsettled_revision": CoordinationReason.PEER_UNSETTLED,
    "peer_open_invocation_candidate": CoordinationReason.PEER_UNSETTLED,
    "parse_unable_dynamic_command": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_dynamic_expression": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_missing_path_flag": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_missing_target": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_missing_value": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_obfuscated": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_pathspec_from_file": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_pipeline": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_shell_syntax": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_subcommand": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_target": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
    "parse_unable_wrapped": CoordinationReason.COMMAND_PARSE_UNAVAILABLE,
}

_STRIP_RE = re.compile(r"[^A-Za-z0-9.-]+")
_NOISE_RE = re.compile(r"['\"\\()&$\[\]]")
_REDIRECT_RE = re.compile(r"^(?P<operator>(?:\d*)>(?:[|&])?|&>\|?)(?P<target>.*)$")
_APPEND_REDIRECT_RE = re.compile(r"^(?:\d*|&)>>")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_ATTACHED_HERE_STRING_RE = re.compile(
    rf"(?m)(?P<prefix>^|[;&|][ \t]*)(?P<shell>(?:[^\s;&|]+/)?"
    rf"(?:{'|'.join(sorted(SHELL_COMMAND_NAMES))}))<<<"
)
_FUNCTION_CALL_RE = re.compile(
    r"(?ms)(?:^|[;&]\s*)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\(\s*\)\s*\{(?P<body>.*?)\}\s*;\s*(?P=name)(?:\s|$)"
)
_CASE_BLOCK_RE = re.compile(r"(?ms)(?:^|[;&]\s*)case\b.*?\bin\b(?P<body>.*?)\besac\b")
_CASE_CLAUSE_RE = re.compile(r"(?ms)(?:^|;;)\s*[^)]*\)\s*(?P<body>.*?)(?=;;|$)")
_AMBIGUOUS_NESTED_BACKTICK_RE = re.compile(r"`[^`\r\n]*`[^`\r\n]+``")


class _ShellScanState(Enum):
    EXPECT_COMMAND = auto()
    IN_ARGUMENTS = auto()
    AFTER_SEPARATOR = auto()
    AFTER_CONTROL_WORD = auto()
    IN_SINGLE_QUOTE = auto()
    IN_DOUBLE_QUOTE = auto()
    ESCAPED = auto()


_COMMAND_POSITION_CONTROL_WORDS: Final[frozenset[str]] = frozenset(
    {
        "if",
        "while",
        "until",
        "for",
        "select",
        "then",
        "do",
        "else",
        "elif",
        "coproc",
        "{",
        "(",
        "!",
        "&",
    }
)
_COMMAND_TERMINATORS: Final[frozenset[str]] = frozenset(
    {"fi", "done", "esac", "}", ")"}
)
# 07-16 사고 재발 방지 3절차(설계 §6-4) — 원복 자가발급 표면을 두지 않는 대신 안내만 한다.
RESOLUTION_PROCEDURES: Final[str] = (
    "파괴 조치는 오케스트레이터 전결입니다. 해소 절차: "
    "① 소유 에이전트 본인이 자기 세션에서 실행 "
    "② 소유 에이전트가 검증/정리로 settlement 후 실행 "
    "③ 사용자(인간)가 직접 실행(훅 밖)."
)


@dataclass(frozen=True, slots=True)
class ParsedDestructiveCommand:
    category: str
    resolved: bool
    targets: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class _HeredocDeclaration:
    delimiter: str
    strip_tabs: bool
    quoted: bool
    consumer: str


def _tokenize(command: str) -> list[str]:
    import shlex

    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return command.split()


def _normalize_attached_here_strings(command: str) -> str:
    return _ATTACHED_HERE_STRING_RE.sub(
        lambda match: f"{match.group('prefix')}{match.group('shell')} <<<",
        command,
    )


def _command_name(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return name.removesuffix(".exe")


def _deobfuscated_name(token: str) -> str:
    stripped = _STRIP_RE.sub("", token.rsplit("/", 1)[-1])
    return stripped.casefold().removesuffix(".exe")


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[1:-1]
    return token


def _has_glob(target: str) -> bool:
    return any(ch in target for ch in ("*", "?", "[", "]", "{", "}"))


def _has_environment_reference(target: str) -> bool:
    return "$" in target or "%" in target


def _is_root_or_home(target: str) -> bool:
    clean = target.strip()
    return clean in {"/", "\\", "~", ".", "./", ".\\"} or clean.startswith("~")


def _validate_target(target: str) -> str:
    if not target:
        return "parse_unable"
    if target.startswith(":"):
        return "pathspec_magic"
    if _has_glob(target) or _has_environment_reference(target):
        return "parse_unable"
    if _is_root_or_home(target):
        return "implicit_scope"
    return "ok"




def _blocked(category: str, reason: str) -> ParsedDestructiveCommand:
    return ParsedDestructiveCommand(category, False, (), reason)


def _parse_git_pathspec(subcommand: str, rest: list[str]) -> ParsedDestructiveCommand | None:
    positionals: list[str] = []
    index = 0
    after_double_dash = False
    while index < len(rest):
        token = rest[index]
        if token in COMMAND_SEPARATORS:
            break
        if not after_double_dash and token == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and token.startswith("--pathspec-from-file"):
            return _blocked(CATEGORY_GIT, "parse_unable_pathspec_from_file")
        if not after_double_dash and token.startswith("-"):
            index += 1
            continue
        positionals.append(token)
        index += 1
    if not positionals:
        # `git checkout`/`git restore` 인자 없음: 대상 pathspec이 없어 특정 파일을 복원하지
        # 않는다(checkout 단독=상태 표시). R2 대상 아님.
        return None
    # `--` 유무와 무관하게 위치 인자를 pathspec 대상으로 attribution 판정에 위임한다.
    # 브랜치명(git checkout main·release/v2)은 루트에 매칭 파일이 없어 미추적으로 통과하고,
    # 실제 파일 경로(src/app.py)는 canonical 키로 조회돼 타 에이전트 미정산 소유면 차단된다.
    # 정적으로 브랜치명/파일명을 구분하려던 기존 로직은 release/v2 같은 슬래시 브랜치명을
    # 오탐했다 — 판정을 attribution 조회에 넘겨 오탐을 제거한다.
    targets: list[str] = []
    for raw in positionals:
        target = _unquote(raw)
        validity = _validate_target(target)
        if validity == "ok":
            targets.append(target)
        elif validity == "implicit_scope":
            return _blocked(CATEGORY_GIT, "implicit_scope")
        else:
            return _blocked(CATEGORY_GIT, "parse_unable_target")
    return ParsedDestructiveCommand(CATEGORY_GIT, True, tuple(targets), "")


_CHECKOUT_CLUSTER_FLAGS: Final[frozenset[str]] = frozenset(
    {"f", "q", "l", "m", "d", "t", "p"}
)


def _parse_checkout_options(rest: list[str]) -> tuple[bool, bool]:
    """Return effective force and branch-creation states before ``--``."""
    force = False
    branch_creation = False
    index = 0
    while index < len(rest):
        token = rest[index]
        if token == "--":
            break
        if token == "--force":
            force = True
        elif token == "--no-force":
            force = False
        elif token in {"-b", "-B"}:
            branch_creation = True
            index += 1
        elif token.startswith(("-b", "-B")) and len(token) > 2:
            branch_creation = True
            suffix = token[2:]
            # Attached branch values remain data.  An all-flag suffix, such
            # as the contracted ``-Bf`` case, remains part of the cluster.
            if suffix and set(suffix) <= _CHECKOUT_CLUSTER_FLAGS:
                force = force or "f" in suffix
        elif token.startswith("-") and not token.startswith("--"):
            cluster = token[1:]
            cluster_index = 0
            while cluster_index < len(cluster):
                flag = cluster[cluster_index]
                if flag == "f":
                    force = True
                if flag in {"b", "B"}:
                    branch_creation = True
                    suffix = cluster[cluster_index + 1 :]
                    if suffix and set(suffix) <= _CHECKOUT_CLUSTER_FLAGS:
                        force = force or "f" in suffix
                    break
                cluster_index += 1
        index += 1
    return force, branch_creation


def _detect_git(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if _command_name(tokens[0]) != "git":
        return None
    if len(tokens) < 2:
        return None
    candidate = tokens[1]
    subcommand = candidate.casefold()
    if subcommand not in GIT_SUBCOMMAND_NAMES:
        if candidate.startswith("-"):
            # 서브커맨드 앞에 global 옵션(예: --git-dir=...)이 와서 어떤 서브커맨드인지
            # 확정할 수 없다 — 파괴 서브커맨드일 가능성을 배제할 수 없으므로 보수적으로
            # parse_unable 처리한다(§6-4). global 옵션 파싱은 구현하지 않는다(문서 허용).
            return _blocked(CATEGORY_GIT, "parse_unable_subcommand")
        # commit/add/push/log 등 무관한 서브커맨드 — R2 대상이 아니다.
        return None
    if subcommand in GIT_SUBCOMMANDS_ALWAYS_IMPLICIT:
        return _blocked(CATEGORY_GIT, "implicit_scope")
    if subcommand == "switch":
        flags = {t.casefold() for t in tokens[2:]}
        if flags & GIT_SWITCH_DISCARD_FLAGS:
            return _blocked(CATEGORY_GIT, "implicit_scope")
        return None
    rest = tokens[2:]
    if subcommand == "checkout":
        force, branch_creation = _parse_checkout_options(rest)
        if force:
            return _blocked(CATEGORY_GIT, "implicit_scope")
        if branch_creation:
            return None
    return _parse_git_pathspec(subcommand, rest)


def _detect_remove(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if _command_name(tokens[0]) not in REMOVE_COMMAND_NAMES:
        return None
    positionals: list[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token in COMMAND_SEPARATORS:
            break
        if token.startswith("-") or token.startswith("/"):
            index += 1
            continue
        positionals.append(token)
        index += 1
    if not positionals:
        return _blocked(CATEGORY_REMOVE, "implicit_scope")
    targets: list[str] = []
    for raw in positionals:
        target = _unquote(raw)
        validity = _validate_target(target)
        if validity == "ok":
            targets.append(target)
        elif validity == "implicit_scope":
            return _blocked(CATEGORY_REMOVE, "implicit_scope")
        else:
            return _blocked(CATEGORY_REMOVE, "parse_unable_target")
    return ParsedDestructiveCommand(CATEGORY_REMOVE, True, tuple(targets), "")


def _detect_truncate_cmdlet(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if _command_name(tokens[0]) not in TRUNCATE_COMMAND_NAMES:
        return None
    index = 1
    while index < len(tokens):
        if tokens[index].casefold() in PATH_FLAGS:
            if index + 1 >= len(tokens):
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_value")
            value_token = tokens[index + 1]
            if value_token.startswith("(") or "$" in value_token:
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_dynamic_expression")
            if index + 2 < len(tokens) and tokens[index + 2] == "+":
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_dynamic_expression")
            target = _unquote(value_token)
            validity = _validate_target(target)
            if validity == "ok":
                return ParsedDestructiveCommand(CATEGORY_TRUNCATE, True, (target,), "")
            if validity == "implicit_scope":
                return _blocked(CATEGORY_TRUNCATE, "implicit_scope")
            return _blocked(CATEGORY_TRUNCATE, "parse_unable_target")
        index += 1
    return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_path_flag")


def _detect_truncate_redirect(tokens: list[str]) -> ParsedDestructiveCommand | None:
    for index, raw_token in enumerate(tokens):
        if _unquote(raw_token) != raw_token:
            continue
        token = _unquote(raw_token)
        if token.startswith(("<(", ">(")):
            continue
        if _APPEND_REDIRECT_RE.match(token):
            continue
        match = _REDIRECT_RE.match(token)
        if match is None:
            continue
        target = match.group("target")
        if not target:
            if index + 1 >= len(tokens):
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_missing_target")
            target = _unquote(tokens[index + 1])
        if match.group("operator").endswith(">&") and (target == "-" or target.isdigit()):
            continue
        validity = _validate_target(target)
        if validity == "ok":
            return ParsedDestructiveCommand(CATEGORY_TRUNCATE, True, (target,), "")
        if validity == "implicit_scope":
            return _blocked(CATEGORY_TRUNCATE, "implicit_scope")
        return _blocked(CATEGORY_TRUNCATE, "parse_unable_target")
    return None


def _detect_tee(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if _command_name(tokens[0]) != "tee":
        return None
    append = False
    targets: list[str] = []
    after_double_dash = False
    for raw_token in tokens[1:]:
        token = _unquote(raw_token)
        if not after_double_dash and token == "--":
            after_double_dash = True
            continue
        if not after_double_dash and token in {"--help", "--version"}:
            return None
        if not after_double_dash and token == "--append":
            append = True
            continue
        if not after_double_dash and (
            token in {"--ignore-interrupts", "-p"}
            or token.startswith("--output-error=")
        ):
            continue
        if not after_double_dash and token.startswith("--"):
            return _blocked(CATEGORY_TRUNCATE, "parse_unable_pipeline")
        if not after_double_dash and token.startswith("-") and token != "-":
            short_flags = token[1:]
            if not short_flags or not set(short_flags) <= {"a", "i", "p"}:
                return _blocked(CATEGORY_TRUNCATE, "parse_unable_pipeline")
            append = append or "a" in short_flags
            continue
        if token != "-":
            targets.append(token)
    if append or not targets:
        return None
    validated: list[str] = []
    for target in targets:
        validity = _validate_target(target)
        if validity == "ok":
            validated.append(target)
        elif validity == "implicit_scope":
            return _blocked(CATEGORY_TRUNCATE, "implicit_scope")
        else:
            return _blocked(CATEGORY_TRUNCATE, "parse_unable_target")
    return ParsedDestructiveCommand(CATEGORY_TRUNCATE, True, tuple(validated), "")


def _detect_wrapper_indirect(tokens: list[str]) -> ParsedDestructiveCommand | None:
    wrapper = _command_name(tokens[0])
    if wrapper not in WRAPPER_COMMAND_NAMES:
        return None
    if wrapper in SHELL_COMMAND_NAMES:
        here_string = _shell_here_string_payload(tokens)
        if here_string is not None:
            if not here_string:
                return _blocked(CATEGORY_REMOVE, "parse_unable_wrapped")
            nested = parse_destructive_command(here_string)
            if nested is not None:
                return _blocked(nested.category, "parse_unable_wrapped")
            return None
        if any(_unquote(token).startswith("<<") for token in tokens[1:]):
            return None
    payload_tokens = _wrapper_payload_tokens(tokens, wrapper)
    if not payload_tokens:
        return None
    nested = parse_destructive_command(" ".join(_unquote(token) for token in payload_tokens))
    if nested is not None:
        if wrapper in OPAQUE_WRAPPER_COMMAND_NAMES:
            return _blocked(nested.category, "parse_unable_wrapped")
        return nested
    if wrapper not in OPAQUE_WRAPPER_COMMAND_NAMES:
        return None
    return None


def _shell_here_string_payload(tokens: list[str]) -> str | None:
    for index, raw_token in enumerate(tokens[1:], start=1):
        token = _unquote(raw_token)
        if token in {"-c", "--command"}:
            return None
        if token == "<<<":
            if index + 1 >= len(tokens):
                return ""
            return _unquote(tokens[index + 1])
        if token.startswith("<<<"):
            return _unquote(token[3:])
    return None


_WRAPPER_OPTIONS_WITH_VALUE: Final[dict[str, frozenset[str]]] = {
    "doas": frozenset({"-a", "-C", "-u"}),
    "env": frozenset({"-C", "-S", "-u", "--chdir", "--split-string", "--unset"}),
    "exec": frozenset({"-a"}),
    "ionice": frozenset({"-c", "-n", "-p", "-P", "-u"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "stdbuf": frozenset({"-e", "-i", "-o", "--error", "--input", "--output"}),
    "sudo": frozenset(
        {
            "-C",
            "-D",
            "-g",
            "-h",
            "-p",
            "-R",
            "-r",
            "-T",
            "-u",
            "--chdir",
            "--chroot",
            "--close-from",
            "--command-timeout",
            "--group",
            "--host",
            "--prompt",
            "--role",
            "--type",
            "--user",
        }
    ),
    "timeout": frozenset({"-k", "-s", "--kill-after", "--signal"}),
    "xargs": frozenset({"-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s"}),
}


def _joined_option_value(tokens: list[str], index: int) -> tuple[str, int]:
    value = tokens[index].split("=", 1)[1]
    quote = value[0] if value.startswith(("'", '"')) else ""
    cursor = index + 1
    pieces = [value]
    while quote and not pieces[-1].endswith(quote) and cursor < len(tokens):
        pieces.append(tokens[cursor])
        cursor += 1
    return _unquote(" ".join(pieces)), cursor


def _wrapper_payload_tokens(tokens: list[str], wrapper: str) -> list[str]:
    if wrapper == "command" and any(token in {"-v", "-V"} for token in tokens[1:]):
        return []
    index = 1
    options_with_value = _WRAPPER_OPTIONS_WITH_VALUE.get(wrapper, frozenset())
    while index < len(tokens) and tokens[index].startswith(("-", "/")):
        option = tokens[index].split("=", 1)[0]
        if wrapper == "env" and option in {"-S", "--split-string"}:
            if "=" in tokens[index]:
                split_payload, next_index = _joined_option_value(tokens, index)
                return [split_payload, *tokens[next_index:]]
            return tokens[index + 1 :]
        index += 2 if option in options_with_value and "=" not in tokens[index] else 1
    if wrapper == "env":
        while index < len(tokens) and _ASSIGNMENT_RE.match(tokens[index]):
            index += 1
    if wrapper == "timeout" and index < len(tokens):
        index += 1
    return tokens[index:]


def _detect_dynamic_command_head(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if tokens and _has_environment_reference(_unquote(tokens[0])):
        return _blocked(CATEGORY_REMOVE, "parse_unable_dynamic_command")
    return None


def _detect_coproc(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if not tokens or _unquote(tokens[0]).casefold() != "coproc":
        return None
    candidates = [tokens[1:]]
    if len(tokens) >= 3 and _unquote(tokens[1]).isidentifier():
        candidates.append(tokens[2:])
    for candidate in candidates:
        if not candidate:
            continue
        nested = parse_destructive_command(
            " ".join(_unquote(token) for token in candidate)
        )
        if nested is not None:
            return nested
    return None


def _detect_find_exec(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if _command_name(tokens[0]) != "find":
        return None
    index = 1
    while index < len(tokens):
        token = _unquote(tokens[index]).casefold()
        if token in FIND_TESTS_WITH_VALUE:
            index += 2
            continue
        if token == "-delete":
            return _blocked(CATEGORY_REMOVE, "implicit_scope")
        if token not in {"-exec", "-execdir", "-ok", "-okdir"}:
            index += 1
            continue
        payload_start = index + 1
        terminator = payload_start
        while terminator < len(tokens) and _unquote(tokens[terminator]) not in {
            ";",
            r"\;",
            "+",
        }:
            terminator += 1
        if terminator == len(tokens) or terminator == payload_start:
            return _blocked(CATEGORY_REMOVE, "parse_unable_shell_syntax")
        nested = parse_destructive_command(
            " ".join(_unquote(item) for item in tokens[payload_start:terminator])
        )
        if nested is not None:
            return nested
        index = terminator + 1
    return None


def _find_arithmetic_end(line: str, opening: int) -> int | None:
    depth = 1
    index = opening + 3
    while index < len(line) - 1:
        if line[index] == "\\":
            index += 2
            continue
        if line[index : index + 2] == "((":
            depth += 1
            index += 2
            continue
        if line[index : index + 2] == "))":
            depth -= 1
            if depth == 0:
                return index
            index += 2
            continue
        index += 1
    return None


def _heredoc_consumer(line: str, operator: int) -> str:
    """Return the effective command receiving one heredoc redirect."""
    tokens = _tokenize(line[:operator])
    last_boundary = -1
    for index, token in enumerate(tokens):
        if _unquote(token) in {"&&", "||", ";", "|", "&"}:
            last_boundary = index
    tokens = _command_position_tokens(tokens[last_boundary + 1 :])
    while tokens and _command_name(tokens[0]) in {
        "builtin",
        "command",
        "env",
        "exec",
    }:
        wrapper = _command_name(tokens[0])
        payload = _wrapper_payload_tokens(tokens, wrapper)
        if not payload or payload == tokens:
            return ""
        tokens = _command_position_tokens(payload)
    return _command_name(tokens[0]) if tokens else ""


def _heredoc_declarations(
    line: str,
) -> tuple[list[_HeredocDeclaration], bool]:
    declarations: list[_HeredocDeclaration] = []
    quote = ""
    escaped = False
    index = 0
    while index < len(line):
        character = line[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if character == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if quote:
            if character == quote:
                quote = ""
            index += 1
            continue
        if character in "'\"":
            quote = character
            index += 1
            continue
        if line[index : index + 3] == "$((":
            arithmetic_end = _find_arithmetic_end(line, index)
            if arithmetic_end is None:
                return declarations, True
            index = arithmetic_end + 2
            continue
        if (
            line[index : index + 2] != "<<"
            or line[index : index + 3] == "<<<"
            or (index > 0 and line[index - 1] == "<")
        ):
            index += 1
            continue

        cursor = index + 2
        strip_tabs = cursor < len(line) and line[cursor] == "-"
        if strip_tabs:
            cursor += 1
        while cursor < len(line) and line[cursor] in " \t":
            cursor += 1
        if cursor >= len(line):
            return declarations, True

        delimiter: list[str] = []
        word_quote = ""
        quoted = False
        while cursor < len(line):
            character = line[cursor]
            if word_quote:
                if character == word_quote:
                    word_quote = ""
                elif character == "\\" and word_quote == '"' and cursor + 1 < len(line):
                    cursor += 1
                    delimiter.append(line[cursor])
                else:
                    delimiter.append(character)
                cursor += 1
                continue
            if character in "'\"":
                quoted = True
                word_quote = character
                cursor += 1
                continue
            if character == "\\" and cursor + 1 < len(line):
                quoted = True
                cursor += 1
                delimiter.append(line[cursor])
                cursor += 1
                continue
            if character.isspace() or character in ";|&<>()":
                break
            delimiter.append(character)
            cursor += 1
        if word_quote or not delimiter:
            return declarations, True
        declarations.append(
            _HeredocDeclaration(
                delimiter="".join(delimiter),
                strip_tabs=strip_tabs,
                quoted=quoted,
                consumer=_heredoc_consumer(line, index),
            )
        )
        index = cursor
    return declarations, False


def _shell_stdin_is_script(tokens: list[str]) -> bool:
    """Classify whether a shell takes its script from standard input.

    ``-c``/``--command`` and a positional script file are separate payload
    sources. Other options do not make stdin data, and unknown options stay on
    the fail-closed side when a heredoc body contains a destructive command.
    """
    stdin_forced = False
    index = 1
    while index < len(tokens):
        token = _unquote(tokens[index])
        if token in {"-c", "--command"} or token.startswith("--command="):
            return False
        if token in {"--help", "--version"}:
            return False
        if token == "--":
            return stdin_forced or index + 1 >= len(tokens)
        if token in {"-", "-s", "--stdin"}:
            stdin_forced = True
            index += 1
            continue
        if token in {"-o", "+o", "-O", "--init-file", "--rcfile"}:
            if index + 1 >= len(tokens):
                return True
            index += 2
            continue
        if token.startswith(("-", "+")) and token not in {"-", "+"}:
            short_flags = token[1:] if not token.startswith("--") else ""
            if "c" in short_flags:
                return False
            if token.startswith("-") and "s" in short_flags:
                stdin_forced = True
            index += 1
            continue
        return stdin_forced
    return True


def _shell_reads_heredoc(line: str, declaration: _HeredocDeclaration) -> bool:
    if declaration.consumer not in SHELL_COMMAND_NAMES:
        return False
    operator = line.find("<<")
    if operator < 0:
        return False
    tokens = _tokenize(line[:operator])
    last_boundary = -1
    for index, token in enumerate(tokens):
        if _unquote(token) in {"&&", "||", ";", "|", "&"}:
            last_boundary = index
    tokens = _command_position_tokens(tokens[last_boundary + 1 :])
    while tokens and _command_name(tokens[0]) in {
        "builtin",
        "command",
        "env",
        "exec",
    }:
        payload = _wrapper_payload_tokens(tokens, _command_name(tokens[0]))
        if not payload or payload == tokens:
            return True
        tokens = _command_position_tokens(payload)
    if not tokens or _command_name(tokens[0]) not in SHELL_COMMAND_NAMES:
        return False
    return _shell_stdin_is_script(tokens)


def _heredoc_expansion_commands(body: str) -> tuple[list[str], bool]:
    """Extract commands expanded by the outer shell in an unquoted heredoc."""
    commands: list[str] = []
    malformed = _has_ambiguous_nested_backticks(body)
    index = 0
    while index < len(body):
        character = body[index]
        if character == "\\":
            index += 2
            continue
        if body[index : index + 2] == "$(":
            closing = _find_substitution_end(body, index + 1)
            if closing is None:
                return commands, True
            commands.append(body[index + 2 : closing])
            index = closing + 1
            continue
        if character == "`":
            closing = _find_backtick_end(body, index)
            if closing is None:
                return commands, True
            commands.append(body[index + 1 : closing])
            index = closing + 1
            continue
        index += 1
    return commands, malformed


def _without_heredoc_bodies(command: str) -> tuple[str, bool, list[str]]:
    pending: list[tuple[_HeredocDeclaration, bool, list[str]]] = []
    retained: list[str] = []
    executable_bodies: list[str] = []
    malformed = False
    for line in command.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        line_ending = line[len(content) :]
        if pending:
            declaration, executable, body = pending[0]
            candidate = (
                content.lstrip("\t") if declaration.strip_tabs else content
            )
            retained.append(line_ending)
            if candidate == declaration.delimiter:
                pending.pop(0)
                body_text = "".join(body)
                if executable:
                    executable_bodies.append(body_text)
                elif not declaration.quoted:
                    expansions, invalid = _heredoc_expansion_commands(body_text)
                    executable_bodies.extend(expansions)
                    malformed = malformed or invalid
            else:
                body.append(line)
            continue
        retained.append(line)
        declarations, invalid = _heredoc_declarations(content)
        pending.extend(
            (
                declaration,
                _shell_reads_heredoc(content, declaration),
                [],
            )
            for declaration in declarations
        )
        malformed = malformed or invalid
    return "".join(retained), malformed or bool(pending), executable_bodies


def _find_backtick_end(command: str, opening: int) -> int | None:
    index = opening + 1
    while index < len(command):
        if command[index] == "\\":
            index += 2
            continue
        if command[index] == "`":
            return index
        index += 1
    return None


def _find_substitution_end(command: str, open_paren: int) -> int | None:
    """Find the balanced end of a ``$(`` command substitution."""
    depth = 1
    state = _ShellScanState.EXPECT_COMMAND
    return_state = _ShellScanState.EXPECT_COMMAND
    index = open_paren + 1
    while index < len(command):
        character = command[index]
        if state is _ShellScanState.ESCAPED:
            state = return_state
            index += 1
            continue
        if state is _ShellScanState.IN_SINGLE_QUOTE:
            if character == "'":
                state = _ShellScanState.IN_ARGUMENTS
            index += 1
            continue
        if state is _ShellScanState.IN_DOUBLE_QUOTE:
            if character == "\\":
                return_state = state
                state = _ShellScanState.ESCAPED
            elif character == '"':
                state = _ShellScanState.IN_ARGUMENTS
            elif character == "`":
                nested_end = _find_backtick_end(command, index)
                if nested_end is None:
                    return None
                index = nested_end + 1
                continue
            elif command[index : index + 2] == "$(":
                nested_end = _find_substitution_end(command, index + 1)
                if nested_end is None:
                    return None
                index = nested_end + 1
                continue
            index += 1
            continue
        if character == "\\":
            return_state = state
            state = _ShellScanState.ESCAPED
        elif character == "'":
            state = _ShellScanState.IN_SINGLE_QUOTE
        elif character == '"':
            state = _ShellScanState.IN_DOUBLE_QUOTE
        elif character == "`":
            nested_end = _find_backtick_end(command, index)
            if nested_end is None:
                return None
            index = nested_end + 1
            continue
        elif command[index : index + 2] == "$(":
            nested_end = _find_substitution_end(command, index + 1)
            if nested_end is None:
                return None
            index = nested_end + 1
            continue
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _starts_case_construct(segment: str) -> bool:
    tokens = [_unquote(token).casefold() for token in _tokenize(segment)]
    return bool(tokens) and tokens[0] == "case" and "in" in tokens[1:]


def _structured_execution_bodies(command: str) -> list[str]:
    bodies = [match.group("body") for match in _FUNCTION_CALL_RE.finditer(command)]
    for case_match in _CASE_BLOCK_RE.finditer(command):
        clauses = case_match.group("body")
        bodies.extend(
            match.group("body") for match in _CASE_CLAUSE_RE.finditer(clauses)
        )
    return bodies


def _has_ambiguous_nested_backticks(command: str) -> bool:
    visible: list[str] = []
    in_single_quote = False
    escaped = False
    for character in command:
        if escaped:
            visible.append(" ")
            escaped = False
            continue
        if character == "\\" and not in_single_quote:
            visible.append(" ")
            escaped = True
            continue
        if character == "'":
            in_single_quote = not in_single_quote
            visible.append(" ")
            continue
        visible.append(" " if in_single_quote else character)
    return _AMBIGUOUS_NESTED_BACKTICK_RE.search("".join(visible)) is not None


def _command_segments(command: str) -> tuple[list[str], bool]:
    """Split shell text only at executable command boundaries.

    Quoted and escaped operators remain arguments.  Outside those lexical
    states, ``&`` and physical newlines open a command position just like the
    separators handled by the original parser.
    """
    segments: list[str] = []
    nested_segments: list[str] = []
    malformed = _has_ambiguous_nested_backticks(command)
    start = 0
    state = _ShellScanState.EXPECT_COMMAND
    return_state = _ShellScanState.EXPECT_COMMAND
    case_active = False
    case_expects_pattern = False
    index = 0
    while index < len(command):
        character = command[index]
        if state is _ShellScanState.ESCAPED:
            state = return_state
            index += 1
            continue
        if state is _ShellScanState.IN_SINGLE_QUOTE:
            if character == "'":
                state = _ShellScanState.IN_ARGUMENTS
            index += 1
            continue
        if state is _ShellScanState.IN_DOUBLE_QUOTE:
            if character == "\\":
                return_state = state
                state = _ShellScanState.ESCAPED
            elif character == '"':
                state = _ShellScanState.IN_ARGUMENTS
            elif command[index : index + 2] == "$(":
                closing = _find_substitution_end(command, index + 1)
                if closing is None:
                    malformed = True
                    index += 2
                    continue
                nested, nested_malformed = _command_segments(
                    command[index + 2 : closing]
                )
                nested_segments.extend(nested)
                malformed = malformed or nested_malformed
                index = closing + 1
                continue
            elif character == "`":
                closing = _find_backtick_end(command, index)
                if closing is None:
                    malformed = True
                    index += 1
                    continue
                nested, nested_malformed = _command_segments(
                    command[index + 1 : closing]
                )
                nested_segments.extend(nested)
                malformed = malformed or nested_malformed
                index = closing + 1
                continue
            index += 1
            continue
        if character == "\\":
            return_state = state
            state = _ShellScanState.ESCAPED
            index += 1
            continue
        if character == "'":
            state = _ShellScanState.IN_SINGLE_QUOTE
            index += 1
            continue
        if character == '"':
            state = _ShellScanState.IN_DOUBLE_QUOTE
            index += 1
            continue
        if command[index : index + 2] == "$(":
            closing = _find_substitution_end(command, index + 1)
            if closing is None:
                malformed = True
                index += 2
                continue
            nested, nested_malformed = _command_segments(command[index + 2 : closing])
            nested_segments.extend(nested)
            malformed = malformed or nested_malformed
            index = closing + 1
            continue
        if command[index : index + 2] in {"<(", ">("}:
            closing = _find_substitution_end(command, index + 1)
            if closing is None:
                malformed = True
                index += 2
                continue
            nested, nested_malformed = _command_segments(command[index + 2 : closing])
            nested_segments.extend(nested)
            malformed = malformed or nested_malformed
            index = closing + 1
            continue
        if character == "`":
            closing = _find_backtick_end(command, index)
            if closing is None:
                malformed = True
                index += 1
                continue
            nested, nested_malformed = _command_segments(command[index + 1 : closing])
            nested_segments.extend(nested)
            malformed = malformed or nested_malformed
            index = closing + 1
            continue

        separator_width = 0
        if character in "\r\n":
            separator_width = 2 if command[index : index + 2] == "\r\n" else 1
        elif character == ";":
            if command[index : index + 3] == ";;&":
                separator_width = 3
            elif command[index : index + 2] in {";;", ";&"}:
                separator_width = 2
            else:
                separator_width = 1
        elif character == "|" and (index == 0 or command[index - 1] != ">"):
            separator_width = 2 if command[index : index + 2] in {"||", "|&"} else 1
        elif character == "&" and not (
            (index > 0 and command[index - 1] == ">")
            or (index + 1 < len(command) and command[index + 1] == ">")
        ):
            if command[start:index].strip():
                separator_width = 2 if command[index : index + 2] == "&&" else 1

        if separator_width:
            segment = command[start:index]
            if _starts_case_construct(segment):
                case_active = True
            if case_expects_pattern and segment.strip():
                segment = f"case _ in {segment}"
                case_expects_pattern = False
            segments.append(segment)
            separator = command[index : index + separator_width]
            if case_active and separator in {";;", ";&", ";;&"}:
                case_expects_pattern = True
            index += separator_width
            start = index
            state = _ShellScanState.AFTER_SEPARATOR
            continue
        if not character.isspace():
            state = _ShellScanState.IN_ARGUMENTS
        index += 1
    final_segment = command[start:]
    if case_expects_pattern and final_segment.strip():
        final_segment = f"case _ in {final_segment}"
    segments.append(final_segment)
    return segments + nested_segments, malformed


_FALLBACK_SCAN_WINDOW: Final[int] = 6


def _detect_obfuscated_fallback(tokens: list[str]) -> ParsedDestructiveCommand | None:
    # 난독화된 파괴 명령은 corpus 전 사례에서 명령 앞부분(토큰 0~2)에 등장한다. 스캔
    # 창을 앞부분으로 제한해, python -c "<긴 스크립트 문자열>"처럼 뒤쪽 인자 안에 우연히
    # rm/git 같은 단어가 데이터로 섞여 있는 무관한 명령까지 오탐하지 않게 한다.
    scan_window = (
        tokens[:_FALLBACK_SCAN_WINDOW]
        if tokens and _NOISE_RE.search(tokens[0])
        else tokens[:1]
    )
    for token in scan_window:
        if not _NOISE_RE.search(token):
            # 노이즈 문자(따옴표/백슬래시/괄호/&/$)가 전혀 없는 깨끗한 토큰은 이미
            # 전용 탐지기가 정확히 평가했다 — 여기서 재매치하면 "git commit"처럼
            # 무관한 정상 서브커맨드까지 오탐한다.
            continue
        name = _deobfuscated_name(token)
        if not name:
            continue
        if name == "git":
            return _blocked(CATEGORY_GIT, "parse_unable_obfuscated")
        if name in REMOVE_COMMAND_NAMES:
            return _blocked(CATEGORY_REMOVE, "parse_unable_obfuscated")
        if name in TRUNCATE_COMMAND_NAMES:
            return _blocked(CATEGORY_TRUNCATE, "parse_unable_obfuscated")
    return None


_DETECTORS: Final[tuple[Callable[[list[str]], ParsedDestructiveCommand | None], ...]] = (
    _detect_tee,
    _detect_truncate_redirect,
    _detect_git,
    _detect_remove,
    _detect_truncate_cmdlet,
    _detect_find_exec,
    _detect_wrapper_indirect,
)


def _command_position_tokens(tokens: list[str]) -> list[str]:
    if (
        len(tokens) >= 3
        and _unquote(tokens[0]).casefold() == "coproc"
        and _unquote(tokens[1]).isidentifier()
        and _unquote(tokens[1]).upper() == _unquote(tokens[1])
    ):
        tokens = [tokens[0], *tokens[2:]]
    if tokens and _unquote(tokens[0]).casefold() == "case":
        in_index = next(
            (
                index
                for index, token in enumerate(tokens[1:], start=1)
                if _unquote(token).casefold() == "in"
            ),
            None,
        )
        if in_index is None:
            return []
        pattern_end = next(
            (
                index
                for index, token in enumerate(tokens[in_index + 1 :], start=in_index + 1)
                if ")" in _unquote(token)
            ),
            None,
        )
        if pattern_end is None:
            return []
        pattern_token = _unquote(tokens[pattern_end])
        _pattern, _closing, remainder = pattern_token.partition(")")
        tokens = ([remainder] if remainder else []) + tokens[pattern_end + 1 :]

    state = _ShellScanState.EXPECT_COMMAND
    index = 0
    closing_stack: list[str] = []
    command_states = {
        _ShellScanState.EXPECT_COMMAND,
        _ShellScanState.AFTER_CONTROL_WORD,
    }
    while index < len(tokens) and state in command_states:
        token = _unquote(tokens[index])
        if token.casefold() in _COMMAND_POSITION_CONTROL_WORDS:
            if token == "(":
                closing_stack.append(")")
            elif token == "{":
                closing_stack.append("}")
            state = _ShellScanState.AFTER_CONTROL_WORD
            index += 1
            continue
        if _ASSIGNMENT_RE.match(token):
            state = _ShellScanState.EXPECT_COMMAND
            index += 1
            continue
        state = _ShellScanState.IN_ARGUMENTS
    normalized = tokens[index:]
    if normalized and all(
        _unquote(token).casefold() in _COMMAND_TERMINATORS for token in normalized
    ):
        return []
    while (
        normalized
        and closing_stack
        and _unquote(normalized[-1]) == closing_stack[-1]
    ):
        normalized = normalized[:-1]
        closing_stack.pop()
    return normalized


_EXECUTABLE_PREFIX_WRAPPERS: Final[frozenset[str]] = frozenset(
    {"builtin", "command", "env", "exec"}
)


def _r2_command_segments(command: str) -> tuple[list[str], bool]:
    """Return the executable segments consumed by the R2 parser."""
    normalized_command = _normalize_attached_here_strings(command)
    executable_text, heredoc_malformed, heredoc_bodies = _without_heredoc_bodies(
        normalized_command
    )
    segments, substitution_malformed = _command_segments(executable_text)
    nested_malformed = False
    for body in [*heredoc_bodies, *_structured_execution_bodies(executable_text)]:
        nested_segments, body_malformed = _command_segments(body)
        segments.extend(nested_segments)
        nested_malformed = nested_malformed or body_malformed
    return (
        segments,
        heredoc_malformed or substitution_malformed or nested_malformed,
    )


def executable_command_positions(command: str) -> tuple[tuple[str, ...], ...]:
    """Expose R2's command-position normalization for advisory consumers.

    The returned tokens identify the effective executable after lexical
    assignments and the transparent ``env``/``command``/``exec`` prefixes R2
    already understands.  This is parser reuse only; callers must not treat the
    result as an authorization decision.
    """
    positions: list[tuple[str, ...]] = []
    segments, _malformed = _r2_command_segments(command)
    for segment in segments:
        tokens = _command_position_tokens(_tokenize(segment))
        while tokens and _command_name(tokens[0]) in _EXECUTABLE_PREFIX_WRAPPERS:
            wrapper = _command_name(tokens[0])
            payload = _wrapper_payload_tokens(tokens, wrapper)
            if not payload or payload == tokens:
                tokens = []
                break
            tokens = _command_position_tokens(payload)
        if tokens:
            positions.append(tuple(_unquote(token) for token in tokens))
    return tuple(positions)


def _parse_destructive_segment(command: str) -> ParsedDestructiveCommand | None:
    """연산자 경계가 제거된 단일 shell segment를 분류한다."""
    raw_tokens = _tokenize(command)
    coproc = _detect_coproc(raw_tokens)
    if coproc is not None:
        return coproc
    tokens = _command_position_tokens(raw_tokens)
    if not tokens:
        return None
    dynamic = _detect_dynamic_command_head(tokens)
    if dynamic is not None:
        return dynamic
    for detector in _DETECTORS:
        result = detector(tokens)
        if result is not None:
            return result
    return _detect_obfuscated_fallback(tokens)


def parse_destructive_commands(command: str) -> tuple[ParsedDestructiveCommand, ...]:
    """모든 shell segment의 파괴 분류 결과를 원래 순서대로 반환한다.

    quote 또는 escape 안의 연산자는 _command_segments()가 경계로 취급하지 않는다.
    non-destructive segment는 제외한다. resolved=False 결과가 하나라도 있으면 호출자는
    전체 command를 fail-closed로 처리해야 한다.
    """
    segments, malformed = _r2_command_segments(command)
    parsed: list[ParsedDestructiveCommand] = []
    if malformed:
        parsed.append(_blocked(CATEGORY_REMOVE, "parse_unable_shell_syntax"))
    for segment in segments:
        result = _parse_destructive_segment(segment)
        if result is not None and result not in parsed:
            parsed.append(result)
    return tuple(parsed)


def parse_destructive_command(command: str) -> ParsedDestructiveCommand | None:
    """첫 파괴 segment를 반환하는 기존 단수 API 호환 래퍼다.

    복수 segment를 정책 판정할 때는 parse_destructive_commands()를 사용해야 한다.
    """
    parsed = parse_destructive_commands(command)
    return parsed[0] if parsed else None


# --- §6-3/§6-4 게이트 판정 ---------------------------------------------------


def _default_lookup_path_attribution(ledger: JsonObject, canonical_path: str) -> JsonObject | None:
    # F1(core/ledger_v2.py) 동결 계약 — 병행 구현 중이라 아직 없을 수 있다. 지연 임포트로
    # ImportError/AttributeError를 호출부(evaluate_r2_destructive_gate)의 try에서
    # "귀속 신뢰 불가 = degraded"로 흡수시킨다(§6-3 fail-closed).
    from .ledger_v2 import lookup_path_attribution  # type: ignore[attr-defined]

    return lookup_path_attribution(ledger, canonical_path)


def _default_attribution_health(ledger: JsonObject) -> JsonObject:
    from .ledger_v2 import attribution_health  # type: ignore[attr-defined]

    return attribution_health(ledger)


def _canonicalize_target(root: str, target: str) -> tuple[str, str | None]:
    # Candidate recording and R2 target evaluation must use the exact same path rules.
    return canonicalize_project_path(root, target)


def _canonicalize_lexical_target(root: str, target: str) -> str | None:
    """Return an in-project key without following target symlinks."""
    normalized = target.strip().strip("'\"").replace("\\", "/")
    if not normalized:
        return None
    base = os.path.abspath(root)
    candidate = Path(normalized)
    absolute = os.path.abspath(
        normalized if candidate.is_absolute() else Path(base) / candidate
    )
    try:
        relative = os.path.relpath(absolute, base).replace("\\", "/")
    except ValueError:
        return None
    if relative == "." or relative == ".." or relative.startswith("../"):
        return None
    return canonical_manifest_key(relative, os.name == "nt")


def _is_state_dir_key(canonical: str) -> bool:
    # Runtime state, its preserved legacy copy, and migration control paths never
    # enter attribution. Protect all generations lexically and after resolution.
    head = canonical.split("/", 1)[0]
    return is_protected_state_name(head)


def _has_durable_corrupt_marker(root: str) -> bool:
    try:
        return any(state_dir(root).glob("*.corrupt-*.bak"))
    except OSError:
        return False


def _load_ledger_for_r2(root: str) -> tuple[JsonObject, bool]:
    # R2-first invariant(§6-3): 이 함수가 어댑터 전체에서 최초의 상태 접근이어야 한다.
    # schema 예외 포함 어떤 예외도 여기서 흡수해 degraded로 전환한다 — 이후 단계의
    # 광역 except fail-open이 R2 판정을 되돌리지 못하게 하기 위함(mco-codex-r2.md RC1).
    # marker 확인은 load 이후에 한다: 손상 JSON은 load_ledger()가 내부에서 삼키고(예외를
    # 던지지 않고) .corrupt-*.bak을 이번 호출에서 막 생성하므로, load 전에만 확인하면
    # 그 첫 호출의 신호를 놓친다(§6-3 "첫 호출 뒤 소실 방지").
    try:
        ledger = load_ledger({"project_root": root})
    except Exception:  # noqa: BLE001
        return {}, True
    return ledger, _has_durable_corrupt_marker(root)


def _identity_agent_key(payload: Mapping[str, JsonValue]) -> str:
    host = payload.get("host")
    session_id = payload.get("session_id")
    agent = payload.get("agent")
    return ":".join(
        value if isinstance(value, str) and value else "unknown"
        for value in (host, session_id, agent)
    )


def _owned_by_unsettled_peer(record: JsonObject | None, caller_agent_key: str) -> bool:
    if not isinstance(record, dict):
        return False
    owners = record.get("owners")
    if not isinstance(owners, list):
        return False
    for owner in owners:
        if not isinstance(owner, dict):
            continue
        if owner.get("agent_key") == caller_agent_key:
            continue
        if owner.get("settled") is not True:
            return True
    return False


def _peer_open_invocation_candidates(
    ledger: JsonObject, caller_agent_key: str, root: str
) -> frozenset[str]:
    from .ledger_v2 import open_peer_invocation_candidates

    return frozenset(open_peer_invocation_candidates(ledger, caller_agent_key, root))


def _block(reason_code: str) -> Decision:
    return {
        "decision": "block",
        "coordination_reason_code": _coordination_reason_for_block(
            reason_code
        ).value,
        "reason": (
            f"[smtw] R2: 파괴 조치가 차단되었습니다({reason_code}). "
            f"{_block_explanation(reason_code)} {RESOLUTION_PROCEDURES}"
        ),
    }


def _block_explanation(reason_code: str) -> str:
    if reason_code == "parse_unable_dynamic_command":
        return (
            "셸 구간 선두의 런타임 변수 또는 동적 명령을 정적으로 해석할 수 없어 "
            "파괴 명령 가능성을 배제할 수 없으므로 fail-closed로 차단합니다."
        )
    if reason_code == "parse_unable_dynamic_expression":
        return (
            "동적 경로 표현식을 정적으로 해석할 수 없어 파괴 대상을 확정할 수 없으므로 "
            "fail-closed로 차단합니다."
        )
    if reason_code.startswith("parse_unable_"):
        return (
            "파괴 명령 또는 대상을 정적으로 해석할 수 없어 파괴 가능성을 배제할 수 "
            "없으므로 fail-closed로 차단합니다."
        )
    return (
        "귀속을 확정할 수 없거나(파싱 불능/암시적 전체 범위) 타 에이전트의 미정산 변경 "
        "대상이라 fail-closed로 차단합니다."
    )


def _coordination_reason_for_block(reason_code: str) -> CoordinationReason:
    return R2_COORDINATION_REASON_MAP.get(
        reason_code,
        CoordinationReason.UNRESOLVABLE_TARGET,
    )


def _allow(message: str) -> Decision:
    return {"decision": "allow", "message": message}


def evaluate_r2_destructive_gate(
    payload: Mapping[str, JsonValue],
    *,
    lookup_path_attribution: Callable[[JsonObject, str], JsonObject | None] = _default_lookup_path_attribution,
    attribution_health: Callable[[JsonObject], JsonObject] = _default_attribution_health,
) -> Decision:
    # 판정 로직 자체는 _decide_r2에 그대로 있다 — 이 래퍼는 block 판정에 한해
    # quarantine 백업(B1 Q-1~Q-4)을 best-effort로 덧붙일 뿐, decision/
    # coordination_reason_code는 절대 바꾸지 않는다(백업 성패와 무관).
    decision = _decide_r2(
        payload,
        lookup_path_attribution=lookup_path_attribution,
        attribution_health=attribution_health,
    )
    if decision.get("decision") == "block":
        decision = _apply_quarantine_backup(payload, decision)
    return decision


def _apply_quarantine_backup(
    payload: Mapping[str, JsonValue], decision: Decision
) -> Decision:
    try:
        command = payload.get("command")
        if not isinstance(command, str) or not command.strip():
            return decision
        root = payload.get("project_root")
        root = root if isinstance(root, str) and root else "."
        reason_code = decision.get("coordination_reason_code")
        reason_code_text = reason_code if isinstance(reason_code, str) else "unknown"

        from .quarantine import backup_blocked_command

        saved = backup_blocked_command(
            root,
            command=command,
            agent_key=_identity_agent_key(payload),
            reason_code=reason_code_text,
            target=_display_targets(command),
        )
        if saved is None:
            return _append_quarantine_reason(decision, _quarantine_failure_note())
        existing_reason = decision.get("reason")
        reason_text = existing_reason if isinstance(existing_reason, str) else ""
        merged = dict(decision)
        merged["reason"] = reason_text + _quarantine_note(saved)
        return merged
    except Exception:  # noqa: BLE001 - quarantine backup must never affect the deny decision.
        return _append_quarantine_reason(decision, _quarantine_failure_note())


def _display_targets(command: str) -> str:
    try:
        parsed_commands = parse_destructive_commands(command)
    except Exception:  # noqa: BLE001 - purely informational, never fatal.
        return ""
    targets = [target for parsed in parsed_commands for target in parsed.targets]
    return ", ".join(targets)


def _quarantine_note(path: Path) -> str:
    from .quarantine import read_record

    record = read_record(path)
    if record is None:
        return _quarantine_failure_note()
    if record.truncated is True or record.record_status == "incomplete":
        return (
            " 작성하려던 내용은 일부만 보관됐습니다. 완전한 명령으로 적용하지 마세요. "
            f"quarantine 경로: `{path}`. 오케스트레이터에게 "
            "회수(smtw quarantine show/list)를 요청하세요. / "
            "Blocked content was only partially preserved; do not apply as a "
            f"complete command. Quarantine path: {path}."
        )
    if record.truncated is False and record.record_status == "complete":
        return (
            " 작성하려던 내용은 완전히 보관됐습니다. "
            f"quarantine 경로: `{path}`. 오케스트레이터에게 "
            "회수(smtw quarantine show/list)를 요청하세요. / "
            f"Blocked content preserved completely. Quarantine path: {path}."
        )
    return (
        " quarantine 파일의 완전성 메타데이터를 확인할 수 없습니다. 완전한 "
        "명령으로 적용하지 마세요. / Quarantine completeness metadata is "
        "unavailable; do not apply as a complete command."
    )


def _quarantine_failure_note() -> str:
    return (
        " 작성하려던 내용을 quarantine에 보관하지 못했습니다. R2 차단은 그대로 "
        "유지됩니다. / Blocked content could not be preserved in quarantine; "
        "the R2 block remains in effect."
    )


def _append_quarantine_reason(decision: Decision, note: str) -> Decision:
    existing_reason = decision.get("reason")
    reason_text = existing_reason if isinstance(existing_reason, str) else ""
    merged = dict(decision)
    merged["reason"] = reason_text + note
    return merged


def _decide_r2(
    payload: Mapping[str, JsonValue],
    *,
    lookup_path_attribution: Callable[[JsonObject, str], JsonObject | None] = _default_lookup_path_attribution,
    attribution_health: Callable[[JsonObject], JsonObject] = _default_attribution_health,
) -> Decision:
    tool = payload.get("tool_name")
    if tool not in SHELL_TOOLS:
        return _allow("not a shell command")
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        return _allow("no command")
    parsed_commands = parse_destructive_commands(command)
    if not parsed_commands:
        return _allow("not destructive-shaped")
    for parsed in parsed_commands:
        if not parsed.resolved:
            return _block(parsed.reason)

    root = payload.get("project_root")
    root = root if isinstance(root, str) and root else "."

    # Lexical state-dir protection must run before ledger I/O and physical resolve:
    # `.fable-lite` may itself be a symlink that resolves outside the project.
    for parsed in parsed_commands:
        for raw_target in parsed.targets:
            lexical = _canonicalize_lexical_target(root, raw_target)
            if lexical is not None and _is_state_dir_key(lexical):
                return _block("state_dir_protected")

    # R2-first: 이 load가 어댑터 전체에서 최초의 상태 접근이다(§6-3).
    ledger, degraded = _load_ledger_for_r2(root)
    if degraded:
        return _block("ledger_degraded")

    try:
        health = attribution_health(ledger)
    except Exception:  # noqa: BLE001
        return _block("attribution_health_unavailable")
    if health.get("degraded") or health.get("capacity_exceeded"):
        return _block("attribution_degraded_or_capacity_exceeded")

    caller_agent_key = _identity_agent_key(payload)
    peer_candidates = _peer_open_invocation_candidates(ledger, caller_agent_key, root)
    for parsed in parsed_commands:
        for raw_target in parsed.targets:
            disposition, canonical = _canonicalize_target(root, raw_target)
            if disposition == _CANON_UNRESOLVABLE:
                # resolve 자체가 실패한 경로(순환 심볼릭·초장경로 등): 고의 예외 유발로 루트 안
                # peer 파일을 우회할 수 없도록 fail-closed 차단(agy High).
                return _block("canonicalization_unavailable")
            if disposition == _CANON_OUT_OF_ROOT or canonical is None:
                # 루트 밖 대상: 이 프로젝트 path_attribution에 없어 타 에이전트 미정산 변경일 수
                # 없다 → R2 무관, 이 타겟은 건너뛴다. 다른 프로젝트/시스템 파일 보호는 비범위(§9).
                continue
            if _is_state_dir_key(canonical):
                # provenance/감사 상태(.fable-lite): 소유권 무관 하드 차단(agy Critical).
                return _block("state_dir_protected")
            try:
                record = lookup_path_attribution(ledger, canonical)
            except Exception:  # noqa: BLE001
                return _block("attribution_lookup_unavailable")
            if _owned_by_unsettled_peer(record, caller_agent_key):
                return _block("peer_unsettled_revision")
            if canonical in peer_candidates:
                return _block("peer_open_invocation_candidate")
    return _allow("r2 pass")
