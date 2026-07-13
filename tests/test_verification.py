from __future__ import annotations

from core.verification import is_verification_command, text_indicates_success


def test_is_verification_command_recognizes_python_pytest_and_inline_assert() -> None:
    assert is_verification_command("pytest tests/") is True
    assert is_verification_command("python -m pytest") is True
    assert is_verification_command('python -c "assert 1 == 1"') is True


def test_is_verification_command_recognizes_plain_script_reexecution() -> None:
    # E1c F1에서 관측: `python demo.py`로 수정 전후 검증했는데 미인식이었던 케이스.
    assert is_verification_command("python demo.py") is True
    assert is_verification_command("node app.test.js") is True


def test_is_verification_command_recognizes_shell_script_execution() -> None:
    # v1 릴리스 심사 H3: bash/sh가 인터프리터 목록에 없어 이 프로젝트가 직접 감시하는
    # SHELL_TOOLS(Bash)의 스크립트 재실행 자체를 못 잡던 구멍.
    assert is_verification_command("bash test.sh") is True
    assert is_verification_command("sh run_tests.sh") is True
    assert is_verification_command("./test.sh") is False  # 인터프리터 접두사 없는 직접실행은 범위 밖(문서화된 한계)


def test_is_verification_command_recognizes_compiled_language_runners() -> None:
    assert is_verification_command("make test") is True
    assert is_verification_command("make check") is True
    assert is_verification_command("ctest") is True
    assert is_verification_command("mvn test") is True
    assert is_verification_command("gradle test") is True
    assert is_verification_command("./gradlew test") is True
    assert is_verification_command("dotnet test") is True
    assert is_verification_command("tox") is True
    assert is_verification_command("rake test") is True
    assert is_verification_command("Invoke-Pester") is True


def test_is_verification_command_excludes_non_verify_commands() -> None:
    assert is_verification_command("python manage.py migrate") is False
    assert is_verification_command("npm run build") is False
    assert is_verification_command("npm run deploy") is False


def test_output_only_and_quoted_runner_names_are_not_verification_commands() -> None:
    commands = (
        "echo pytest",
        'printf "all tests passed"',
        "Write-Output pytest",
        'python -c "print(\'ok\')"',
        "# pytest",
        'sh -c "echo pytest"',
    )

    for command in commands:
        assert is_verification_command(command) is False, command


def test_shell_chains_cannot_claim_atomic_verification_evidence() -> None:
    # Given: shell operators can skip tests or conceal their failing exit status.
    commands = (
        "echo ok || pytest",
        "pytest || echo ok",
        "pytest && echo ok",
        "pytest ; echo ok",
        "pytest | cat",
        "pytest & echo ok",
    )

    # When/Then: a chained command is not atomic verification evidence.
    for command in commands:
        assert is_verification_command(command) is False, command


def test_multiline_shell_commands_cannot_claim_atomic_verification_evidence() -> None:
    commands = ("pytest -q\necho ok", "pytest -q\r\necho ok")

    for command in commands:
        assert is_verification_command(command) is False, repr(command)


def test_real_test_runners_and_explicit_inline_assertions_remain_verification() -> None:
    commands = (
        "python -m pytest tests/",
        "pytest -q",
        "npm test",
        "npm run test",
        "pnpm test",
        "go test ./...",
        "cargo test",
        "dotnet test",
        'python -c "assert add(2, 3) == 5"',
        'python -c "import pytest; raise SystemExit(pytest.main([\'-q\']))"',
        'python -c "import unittest; unittest.main()"',
        "python verify_script.py",
        'python3 -c "assert True"',
        '"C:\\Python312\\python.exe" -m pytest "tests\\unit tests"',
        'ssh deploy@example.com "python -m pytest tests/"',
        'ssh -o KexAlgorithms=curve25519-sha256 host "pytest -q"',
        'ssh -E local.log host "pytest -q"',
        'ssh -o ControlPath=local.sock host "pytest -q"',
    )

    for command in commands:
        assert is_verification_command(command) is True, command


def test_remote_output_only_command_is_not_promoted_to_verification() -> None:
    assert is_verification_command('ssh deploy@example.com "echo pytest"') is False
    assert (
        is_verification_command(
            'ssh deploy@example.com "touch marker && python -m pytest"'
        )
        is False
    )


def test_text_indicates_success_conservative_fallback() -> None:
    assert text_indicates_success("3 passed in 0.02s") is True
    assert text_indicates_success("VERIFY_OK") is True
    assert text_indicates_success("FAILED test_foo") is False
    assert text_indicates_success("Traceback (most recent call last):") is False
    assert text_indicates_success("") is False
    assert text_indicates_success("no signal either way") is False


def test_text_indicates_success_accepts_ok_word_boundary_cases_from_p8() -> None:
    assert text_indicates_success("ok add=5 multiply=6") is True
    assert text_indicates_success("OK: 모든 검증 통과") is True
    assert text_indicates_success("broken") is False
    assert text_indicates_success("not ok") is False


def test_text_indicates_success_keeps_value_dump_without_ok_conservative_from_p8() -> None:
    text = "add(2,3) = 5\r\nadd(-1,1) = 0\r\nmultiply(2,3) = 6"

    assert text_indicates_success(text) is False
