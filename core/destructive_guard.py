from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Final, TypeAlias

from .ledger import JsonObject, JsonValue, load_ledger, state_dir
from .provenance_policy import canonical_manifest_key

_STATE_DIR_NAME: Final[str] = Path(state_dir(".")).name

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
WRAPPER_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {
        "bash",
        "cmd",
        "cmd.exe",
        "doas",
        "env",
        "eval",
        "ionice",
        "nice",
        "nohup",
        "powershell",
        "powershell.exe",
        "pwsh",
        "setsid",
        "sh",
        "stdbuf",
        "sudo",
        "time",
        "timeout",
        "xargs",
    }
)
OPAQUE_WRAPPER_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {"bash", "cmd", "cmd.exe", "eval", "powershell", "powershell.exe", "pwsh", "sh"}
)
PATH_FLAGS: Final[frozenset[str]] = frozenset({"-path", "-literalpath", "-filepath"})

CATEGORY_GIT: Final[str] = "git_destructive"
CATEGORY_REMOVE: Final[str] = "os_remove"
CATEGORY_TRUNCATE: Final[str] = "truncate_redirect"

_STRIP_RE = re.compile(r"[^A-Za-z0-9.-]+")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z-]*")
_NOISE_RE = re.compile(r"['\"\\()&$]")
_REDIRECT_RE = re.compile(r"^(?P<operator>(?:\d*)>(?:[|&])?|&>\|?)(?P<target>.*)$")
_APPEND_REDIRECT_RE = re.compile(r"^(?:\d*|&)>>")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
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


def _tokenize(command: str) -> list[str]:
    import shlex

    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return command.split()


def _command_name(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1].casefold()
    return name.removesuffix(".exe")


def _deobfuscated_name(token: str) -> str:
    stripped = _STRIP_RE.sub("", token)
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
    while index < len(rest):
        token = rest[index]
        if token in COMMAND_SEPARATORS:
            break
        if token == "--":
            index += 1
            continue
        if token.startswith("--pathspec-from-file"):
            return _blocked(CATEGORY_GIT, "parse_unable_pathspec_from_file")
        if token.startswith("-"):
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
        option_tokens = rest[: rest.index("--")] if "--" in rest else rest
        if any(token in {"-b", "-B"} for token in option_tokens):
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


def _detect_truncate_pipe_tee(tokens: list[str]) -> ParsedDestructiveCommand | None:
    if "|" not in tokens:
        return None
    pipe_idx = tokens.index("|")
    if pipe_idx + 1 < len(tokens) and _command_name(tokens[pipe_idx + 1]) == "tee":
        return _blocked(CATEGORY_TRUNCATE, "parse_unable_pipeline")
    return None


def _detect_wrapper_indirect(tokens: list[str]) -> ParsedDestructiveCommand | None:
    wrapper = _command_name(tokens[0])
    if wrapper not in WRAPPER_COMMAND_NAMES:
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
    wrapped = " ".join(_unquote(t) for t in payload_tokens)
    if ">" in wrapped:
        return _blocked(CATEGORY_TRUNCATE, "parse_unable_wrapped")
    words = {match.group(0).casefold() for match in _WORD_RE.finditer(wrapped)}
    if words & REMOVE_COMMAND_NAMES:
        return _blocked(CATEGORY_REMOVE, "parse_unable_wrapped")
    if "git" in words or (words & GIT_SUBCOMMAND_NAMES):
        return _blocked(CATEGORY_GIT, "parse_unable_wrapped")
    if words & TRUNCATE_COMMAND_NAMES:
        return _blocked(CATEGORY_TRUNCATE, "parse_unable_wrapped")
    return None


_WRAPPER_OPTIONS_WITH_VALUE: Final[dict[str, frozenset[str]]] = {
    "doas": frozenset({"-a", "-C", "-u"}),
    "env": frozenset({"-C", "-S", "-u", "--chdir", "--split-string", "--unset"}),
    "ionice": frozenset({"-c", "-n", "-p", "-P", "-u"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "stdbuf": frozenset({"-e", "-i", "-o", "--error", "--input", "--output"}),
    "sudo": frozenset({"-C", "-D", "-g", "-h", "-p", "-R", "-r", "-T", "-u"}),
    "timeout": frozenset({"-k", "-s", "--kill-after", "--signal"}),
    "xargs": frozenset({"-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s"}),
}


def _wrapper_payload_tokens(tokens: list[str], wrapper: str) -> list[str]:
    index = 1
    options_with_value = _WRAPPER_OPTIONS_WITH_VALUE.get(wrapper, frozenset())
    while index < len(tokens) and tokens[index].startswith(("-", "/")):
        option = tokens[index].split("=", 1)[0]
        index += 2 if option in options_with_value and "=" not in tokens[index] else 1
    if wrapper == "env":
        while index < len(tokens) and _ASSIGNMENT_RE.match(tokens[index]):
            index += 1
    if wrapper == "timeout" and index < len(tokens):
        index += 1
    return tokens[index:]


def _detect_dynamic_command_head(command: str) -> ParsedDestructiveCommand | None:
    for segment in _command_segments(command):
        segment_tokens = _tokenize(segment)
        index = 0
        while index < len(segment_tokens) and _ASSIGNMENT_RE.match(segment_tokens[index]):
            index += 1
        if index < len(segment_tokens) and _has_environment_reference(segment_tokens[index]):
            return _blocked(CATEGORY_REMOVE, "parse_unable_dynamic_command")
    return None


def _command_segments(command: str) -> list[str]:
    segments: list[str] = []
    start = 0
    quote = ""
    escaped = False
    index = 0
    while index < len(command):
        character = command[index]
        if escaped:
            escaped = False
        elif character == "\\" and quote != "'":
            escaped = True
        elif quote:
            if character == quote:
                quote = ""
        elif character in "'\"":
            quote = character
        elif character == ";" or character == "|" or (
            character == "&" and index + 1 < len(command) and command[index + 1] == "&"
        ):
            segments.append(command[start:index])
            index += 1 if command[index : index + 2] in {"&&", "||"} else 0
            start = index + 1
        index += 1
    segments.append(command[start:])
    return segments


_FALLBACK_SCAN_WINDOW: Final[int] = 6


def _detect_obfuscated_fallback(tokens: list[str]) -> ParsedDestructiveCommand | None:
    # 난독화된 파괴 명령은 corpus 전 사례에서 명령 앞부분(토큰 0~2)에 등장한다. 스캔
    # 창을 앞부분으로 제한해, python -c "<긴 스크립트 문자열>"처럼 뒤쪽 인자 안에 우연히
    # rm/git 같은 단어가 데이터로 섞여 있는 무관한 명령까지 오탐하지 않게 한다.
    for token in tokens[:_FALLBACK_SCAN_WINDOW]:
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
    _detect_truncate_pipe_tee,
    _detect_truncate_redirect,
    _detect_git,
    _detect_remove,
    _detect_truncate_cmdlet,
    _detect_wrapper_indirect,
)


def parse_destructive_command(command: str) -> ParsedDestructiveCommand | None:
    """command 문자열을 파괴 카테고리(§6-4)로 분류한다.

    반환 None = 파괴 카테고리 대상이 아님(R2 불개입). resolved=True인 경우에만
    targets가 신뢰 가능한 정적 경로 목록이다 — 그 외(파싱 불능·암시적 범위)는
    fail-closed로 처리해야 한다(호출자 책임).
    """
    tokens = _tokenize(command)
    if not tokens:
        return None
    dynamic = _detect_dynamic_command_head(command)
    if dynamic is not None:
        return dynamic
    for detector in _DETECTORS:
        result = detector(tokens)
        if result is not None:
            return result
    return _detect_obfuscated_fallback(tokens)


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


_CANON_IN_ROOT: Final[str] = "in_root"
_CANON_OUT_OF_ROOT: Final[str] = "out_of_root"
_CANON_UNRESOLVABLE: Final[str] = "unresolvable"


def _canonicalize_target(root: str, target: str) -> tuple[str, str | None]:
    # path_attribution 인덱스 키는 canonical_manifest_key 규약(Windows casefold)이다.
    # 세 경우를 구분해 반환한다(단일 None으로 뭉치면 안 됨 — agy 적대검토 실측):
    #   ("in_root", key)      루트 내부로 확정 → key로 attribution 조회·판정
    #   ("out_of_root", None) 루트 밖(relative_to ValueError) → 이 프로젝트 소유 아님, R2 무관 skip
    #   ("unresolvable", None) resolve 자체 실패(OSError: 순환 심볼릭·초장경로 등) → fail-closed 차단
    # OSError를 out_of_root와 뭉쳐 통과시키면, 루트 안 peer 파일을 고의 예외 유발로 우회할 수
    # 있다(High). traversal(`..`)은 resolve **후** relative_to로 판정하므로 src/../peer.py처럼
    # 루트로 되돌아오는 경로는 여전히 in_root 키로 잡혀 차단된다.
    normalized = target.strip().strip("'\"").replace("\\", "/")
    if not normalized:
        return (_CANON_UNRESOLVABLE, None)
    candidate = Path(normalized)
    try:
        base = Path(root).resolve()
        resolved = (candidate if candidate.is_absolute() else base / normalized).resolve()
    except OSError:
        return (_CANON_UNRESOLVABLE, None)
    try:
        rel = str(resolved.relative_to(base)).replace("\\", "/")
    except ValueError:
        return (_CANON_OUT_OF_ROOT, None)
    return (_CANON_IN_ROOT, canonical_manifest_key(rel, os.name == "nt"))


def _is_state_dir_key(canonical: str) -> bool:
    # provenance/감사 상태 디렉토리(.fable-lite)는 어느 에이전트도 직접 파괴하면 안 된다.
    # attribution 인덱스에 등재되지 않으므로 소유권 조회로는 안 잡힌다 → 소유권 무관 하드 차단.
    head = canonical.split("/", 1)[0]
    return head == canonical_manifest_key(_STATE_DIR_NAME, os.name == "nt")


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


def _peer_open_invocation_candidates(ledger: JsonObject, caller_agent_key: str) -> frozenset[str]:
    from .ledger_v2 import open_peer_invocation_candidates

    return frozenset(open_peer_invocation_candidates(ledger, caller_agent_key))


def _block(reason_code: str) -> Decision:
    return {
        "decision": "block",
        "reason": (
            f"[smtw] R2: 파괴 조치가 차단되었습니다({reason_code}). "
            "귀속을 확정할 수 없거나(파싱 불능/암시적 전체 범위) 타 에이전트의 미정산 변경 "
            f"대상이라 fail-closed로 차단합니다. {RESOLUTION_PROCEDURES}"
        ),
    }


def _allow(message: str) -> Decision:
    return {"decision": "allow", "message": message}


def evaluate_r2_destructive_gate(
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
    parsed = parse_destructive_command(command)
    if parsed is None:
        return _allow("not destructive-shaped")
    if not parsed.resolved:
        return _block(parsed.reason)

    root = payload.get("project_root")
    root = root if isinstance(root, str) and root else "."

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
    peer_candidates = _peer_open_invocation_candidates(ledger, caller_agent_key)
    for raw_target in parsed.targets:
        disposition, canonical = _canonicalize_target(root, raw_target)
        if disposition == _CANON_UNRESOLVABLE:
            # resolve 자체가 실패한 경로(순환 심볼릭·초장경로 등): 고의 예외 유발로 루트 안
            # peer 파일을 우회할 수 없도록 fail-closed 차단(agy 적대검토 High).
            return _block("canonicalization_unavailable")
        if disposition == _CANON_OUT_OF_ROOT or canonical is None:
            # 루트 밖 대상: 이 프로젝트 path_attribution에 없어 타 에이전트 미정산 변경일 수
            # 없다 → R2 무관, 이 타겟은 건너뛴다. 다른 프로젝트/시스템 파일 보호는 비범위(§9).
            continue
        if _is_state_dir_key(canonical):
            # provenance/감사 상태(.fable-lite): 소유권 무관 하드 차단(agy 적대검토 Critical).
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
