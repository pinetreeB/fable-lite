from __future__ import annotations

import re
import shlex
from collections.abc import Iterable


_REDIRECT = re.compile(r"(?:^|\s)(?:\d?>{1,2})\s*(?:'([^']+)'|\"([^\"]+)\"|([^\s;|&]+))")
# 인라인 스크립트(python -c / node -e)의 상태파일 쓰기 탐지. Path('x')는 read_text()·
# exists() 같은 읽기에도 붙으므로, 뒤에 **쓰기 메서드가 체이닝될 때만** 대상으로 잡는다
# (읽기 오탐 제거). 변수 경유(p = Path('x'); p.write_text())는 이 정규식이 못 잡으나 그건
# friction의 기존 한계이며 §6-5 이중 근거 교차 확인이 실질 방어다.
_INLINE = re.compile(
    r"fs\.(?:writeFileSync|appendFileSync|rmSync|unlinkSync)\(\s*(?:'([^']+)'|\"([^\"]+)\")"
    r"|Path\(\s*(?:'([^']+)'|\"([^\"]+)\")\s*\)\s*\.\s*"
    r"(?:write_text|write_bytes|unlink|touch|mkdir|rmdir|rename|replace|chmod"
    r"|symlink_to|hardlink_to|open\(\s*['\"][wax])"
)


def shell_candidate_paths(command: str) -> tuple[str, ...]:
    paths: list[str] = []
    paths.extend(_redirect_paths(command))
    paths.extend(_inline_paths(command))
    paths.extend(_token_paths(_tokens(command)))
    return tuple(dict.fromkeys(path for path in paths if _path(path)))


def _redirect_paths(command: str) -> Iterable[str]:
    for match in _REDIRECT.finditer(command):
        yield next(value for value in match.groups() if value is not None)


def _inline_paths(command: str) -> Iterable[str]:
    for match in _INLINE.finditer(command):
        yield next(value for value in match.groups() if value is not None)


def _tokens(command: str) -> tuple[str, ...]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";|&>")
        lexer.whitespace_split = True
        return tuple(lexer)
    except ValueError:
        return ()


def _token_paths(tokens: tuple[str, ...]) -> Iterable[str]:
    index = 0
    while index < len(tokens):
        command = tokens[index].lower()
        end = _command_end(tokens, index + 1)
        args = tokens[index + 1 : end]
        if command == "tee":
            yield from (arg for arg in args if not arg.startswith("-"))
        elif command in {"cp", "mv"}:
            values = [arg for arg in args if not arg.startswith("-")]
            if values:
                yield values[-1]
        elif command == "rm":
            yield from (arg for arg in args if not arg.startswith("-"))
        elif command == "sed" and any(arg.startswith("-i") for arg in args):
            values = [arg for arg in args if not arg.startswith("-")]
            yield from values[1:]
        elif command in {"set-content", "add-content", "out-content", "out-file"}:
            yield from _powershell_path(args)
        index = end + 1


def _command_end(tokens: tuple[str, ...], index: int) -> int:
    for end in range(index, len(tokens)):
        if tokens[end] in {";", "&&", "||", "|"}:
            return end
    return len(tokens)


def _powershell_path(args: tuple[str, ...]) -> Iterable[str]:
    switches = {"-path", "-literalpath", "-filepath"}
    for index, arg in enumerate(args[:-1]):
        if arg.lower() in switches:
            yield args[index + 1]


def _path(value: str) -> str:
    path = value.strip().strip("'\"").replace("\\", "/")
    return "" if not path or path.startswith("-") else path
