from __future__ import annotations

import ast
import re
from typing import Final

from .shell_command import (
    command_name,
    command_operators,
    command_segments,
    remote_ssh_command_tokens,
    without_environment_assignments,
    without_shell_redirections,
)


OUTPUT_ONLY_COMMANDS: Final = frozenset(
    {"echo", "printf", "write-output", "write-host", "out-string"}
)
DIRECT_TEST_RUNNERS: Final = frozenset(
    {
        "pytest",
        "unittest",
        "jest",
        "vitest",
        "rspec",
        "phpunit",
        "ctest",
        "tox",
        "invoke-pester",
        "node:test",
    }
)
SHELL_WRAPPERS: Final = frozenset({"bash", "sh", "zsh", "pwsh", "powershell"})

# 스크립트 재실행 패턴(E1c F1에서 관측: `python demo.py`로 수정 전후 검증했는데 미인식).
# 인터프리터 + 스크립트파일. bash/sh/zsh 추가(H3 — 이 프로젝트가 직접 감시하는
# SHELL_TOOLS의 한 축인 Bash 자체의 스크립트 재실행이 빠져 있던 것을 보강).
TEST_SCRIPT_RE = re.compile(
    r"\b(?:python3?|node|ruby|deno|bun|go run|php|bash|sh|zsh)\s+[^\s|;&]*\.\w+",
    re.IGNORECASE,
)

# 스크립트 재실행처럼 보여도 검증이 아닌 명령 — 이게 있으면 검증으로 인정하지 않는다.
NON_VERIFY_TERMS = (
    "migrate", "makemigrations", "install", "setup.py", "collectstatic",
    "build", "deploy", "runserver", "serve", "start", "manage.py",
)

# tool_output 텍스트에서 실패를 시사하는 신호. 하나라도 있으면 성공 신호 유무와
# 무관하게 실패로 판정한다(보수적 — 애매하면 실패 쪽으로 기운다).
FAIL_SIGNALS = ("failed", "error", "traceback", "assertionerror", "exception", "fatal", "not ok")

# 실패 신호가 없을 때만 확인하는 성공 신호. 값 덤프만 있는 출력은 성공 토큰 없이 판정하지 않는다.
OK_SIGNALS = ("passed", "verify_ok", "success", "all tests", "✓")
OK_WORD_RE = re.compile(r"\bok\b", re.IGNORECASE)


def is_verification_command(command: str) -> bool:
    """이 셸 명령이 검증(테스트/빌드확인) 명령으로 인정되는지 판정한다."""
    if "\n" in command or "\r" in command or command_operators(command):
        return False
    return any(
        _is_verification_invocation(without_shell_redirections(tokens))
        for tokens in command_segments(command)
    )


def _is_verification_invocation(tokens: tuple[str, ...]) -> bool:
    tokens = without_environment_assignments(tokens)
    if not tokens:
        return False
    command = command_name(tokens[0])
    arguments = tokens[1:]
    if command in OUTPUT_ONLY_COMMANDS:
        return False
    if command in SHELL_WRAPPERS and arguments and arguments[0].casefold() in {"-c", "-command"}:
        return len(arguments) > 1 and is_verification_command(arguments[1])
    if command == "env":
        return _is_verification_invocation(without_environment_assignments(arguments))
    if command == "uv" and arguments[:1] == ("run",):
        return _is_verification_invocation(arguments[1:])
    if command == "ssh":
        nested = remote_ssh_command_tokens(tokens)
        nested_segments = command_segments(nested) if nested else ()
        return len(nested_segments) == 1 and _is_verification_invocation(
            nested_segments[0]
        )
    if command in {"python", "python3"}:
        return _is_python_verification(arguments)
    if command in DIRECT_TEST_RUNNERS:
        return True
    if command in {"npm", "yarn", "pnpm"}:
        return _is_package_test(arguments)
    if command in {"go", "cargo", "dotnet", "mvn", "gradle", "gradlew", "rake"}:
        if bool(arguments) and arguments[0].casefold() == "test":
            return True
        return command == "go" and _is_script_reexecution(tokens)
    if command == "make":
        return bool(arguments) and arguments[0].casefold() in {"test", "check"}
    if command == "node" and arguments and arguments[0].casefold() in {"--test", "node:test"}:
        return True
    if command == "deno" and arguments and arguments[0].casefold() == "test":
        return True
    return _is_script_reexecution(tokens)


def _is_python_verification(arguments: tuple[str, ...]) -> bool:
    if len(arguments) >= 2 and arguments[0] == "-m":
        return arguments[1].casefold() in {"pytest", "unittest"}
    if len(arguments) >= 2 and arguments[0] == "-c":
        return _inline_python_has_assertion(arguments[1])
    return _is_script_reexecution(("python", *arguments))


def _inline_python_has_assertion(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(
        isinstance(node, ast.Assert)
        or isinstance(node, ast.Call)
        and _call_name(node.func) in {"pytest.main", "unittest.main"}
        for node in ast.walk(tree)
    )


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _is_package_test(arguments: tuple[str, ...]) -> bool:
    lowered = tuple(argument.casefold() for argument in arguments)
    return lowered[:1] == ("test",) or lowered[:2] == ("run", "test")


def _is_script_reexecution(tokens: tuple[str, ...]) -> bool:
    command_text = " ".join(tokens)
    if any(term in command_text.casefold() for term in NON_VERIFY_TERMS):
        return False
    return bool(TEST_SCRIPT_RE.search(command_text))


def text_indicates_success(text: str) -> bool:
    """exit_code/success 필드가 없을 때(nested headless 세션 등, E1b F4에서 관측)
    stdout/stderr 텍스트만으로 성공 여부를 보수적으로 판정하는 폴백.
    판정 불가(텍스트 없음, 성공/실패 신호 둘 다 없음)면 실패로 둔다."""
    lowered = text.lower()
    if not lowered:
        return False
    if any(signal in lowered for signal in FAIL_SIGNALS):
        return False
    return any(signal in lowered for signal in OK_SIGNALS) or bool(OK_WORD_RE.search(text))
