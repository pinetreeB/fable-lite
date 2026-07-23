"""DOC-02 README 일관성 자동검증 (PR I 편입용).

지시서 §8.4·§8.1 기준:

- (a) stale-literal: README 본문에 현재 구현과 어긋나는 일반 설명이 남아 있으면 실패.
  대상은 "single state dir" + ".fable-lite" 조합 서술, "every hook fail-open"류
  전면 fail-open 주장, "public alias ... design stage", 고정 경로
  ``.fable-lite/contract.json`` 서술이다. compat/history/changelog 인용 섹션만
  섹션 헤더 기반으로 좁게 예외로 둔다.
- (b) version-badge: README 배지 버전이 ``.claude-plugin/plugin.json`` 버전과 일치.
- (c) command smoke: README 코드블록의 ``smtw`` 명령을 추출하고, 대상 서브커맨드
  (doctor/status/init/migrate/goals/quarantine/scorecard)가 ``--help``로 exit 0.
  아직 이 체크아웃에 없는 서브커맨드(PR H 미머지)는 xfail(strict=False)로 표시한다.

현 README(stale 문구 잔존) 기준 (a)는 RED, (b)·(c)는 green/xfail이어야 한다.
PR I의 README 정합화 완료 후 (a)가 GREEN으로 전환되는지 확인하는 회귀 게이트다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
README_NAMES = ("README.md", "README.ko.md")
TARGET_SUBCOMMANDS = (
    "doctor",
    "status",
    "init",
    "migrate",
    "goals",
    "quarantine",
    "scorecard",
)

# compat/history/changelog 인용 섹션만 예외로 둔다 (섹션 헤더 기반, 좁게).
EXEMPT_HEADER_KEYWORDS = (
    "compat",
    "history",
    "changelog",
    "호환",
    "히스토리",
    "변경 이력",
    "변경이력",
)

_HEADER_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_SMTW_CMD_RE = re.compile(r"\bsmtw\s+(?P<sub>[a-z][a-z0-9_-]*)")
_BADGE_VERSION_RE = re.compile(r"version-(?P<version>\d+\.\d+\.\d+)-brightgreen\.svg")
_BLANKET_FAIL_OPEN_RE = re.compile(
    r"(every hook|all hooks?)\s+fail-?open|모든 훅.{0,12}fail-?open",
    re.IGNORECASE,
)


def _iter_body_lines(readme_name: str) -> Iterator[tuple[int, str, str]]:
    """(줄 번호, 줄, 현재 섹션 헤더)를 양식으로 README 본문을 순회한다."""
    text = (ROOT / readme_name).read_text(encoding="utf-8")
    section = ""
    for lineno, line in enumerate(text.splitlines(), start=1):
        header = _HEADER_RE.match(line)
        if header:
            section = header.group("title")
            continue
        yield lineno, line, section


def _is_exempt_section(section: str) -> bool:
    lowered = section.lower()
    return any(keyword in lowered for keyword in EXEMPT_HEADER_KEYWORDS)


def _violations(readme_name: str, predicate: object) -> list[str]:
    found: list[str] = []
    for lineno, line, section in _iter_body_lines(readme_name):
        if _is_exempt_section(section):
            continue
        if predicate(line):  # type: ignore[operator]
            found.append(f"  {readme_name}:{lineno}: {line.strip()}")
    return found


def _has_single_state_dir_claim(line: str) -> bool:
    lowered = line.lower()
    return "single state dir" in lowered and ".fable-lite" in lowered


def _has_blanket_fail_open_claim(line: str) -> bool:
    return _BLANKET_FAIL_OPEN_RE.search(line) is not None


def _has_public_alias_design_stage(line: str) -> bool:
    lowered = line.lower()
    if "public alias" in lowered and "design stage" in lowered:
        return True
    return "공개 별칭" in line and "설계 단계" in line


def _has_fixed_legacy_contract_path(line: str) -> bool:
    return ".fable-lite/contract.json" in line.lower()


# ---------------------------------------------------------------------------
# (a) stale-literal 검사
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("readme_name", README_NAMES)
def test_no_stale_single_state_dir_claim(readme_name: str) -> None:
    found = _violations(readme_name, _has_single_state_dir_claim)
    assert not found, (
        f"{readme_name}: 'single state dir' + '.fable-lite' 조합 서술은 stale입니다 "
        "(DOC-02 §8.1 — canonical runtime state는 `.smtw/`, `.fable-lite/`는 미이관 "
        "legacy 경로로 분리 서술):\n" + "\n".join(found)
    )


@pytest.mark.parametrize("readme_name", README_NAMES)
def test_no_blanket_fail_open_claim(readme_name: str) -> None:
    found = _violations(readme_name, _has_blanket_fail_open_claim)
    assert not found, (
        f"{readme_name}: 'every hook fail-open'류 전면 fail-open 주장은 거짓입니다 "
        "(DOC-02 §8.1 — R2 destructive ambiguity, canonical/legacy env conflict는 "
        "fail-closed. 경계별 정책 표로 서술):\n" + "\n".join(found)
    )


@pytest.mark.parametrize("readme_name", README_NAMES)
def test_no_public_alias_design_stage_claim(readme_name: str) -> None:
    found = _violations(readme_name, _has_public_alias_design_stage)
    assert not found, (
        f"{readme_name}: 'public alias ... design stage' 서술은 stale입니다 "
        "(DOC-02 §8.1 — 공개 별칭은 이미 구현됨. 해당 문구를 삭제):\n"
        + "\n".join(found)
    )


@pytest.mark.parametrize("readme_name", README_NAMES)
def test_no_fixed_legacy_contract_path(readme_name: str) -> None:
    found = _violations(readme_name, _has_fixed_legacy_contract_path)
    assert not found, (
        f"{readme_name}: 고정 경로 `.fable-lite/contract.json` 서술은 stale입니다 "
        "(DOC-02 §8.1 — contract는 authoritative state tree 아래 identity-namespaced "
        "경로. 내부 path를 본문에서 과도하게 고정하지 말 것. compat/history/changelog "
        "인용은 해당 섹션 헤더 아래에서만 예외):\n" + "\n".join(found)
    )


# ---------------------------------------------------------------------------
# (b) version-badge 정합
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("readme_name", README_NAMES)
def test_readme_badge_matches_plugin_version(readme_name: str) -> None:
    plugin = json.loads(
        (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    expected = plugin["version"]

    text = (ROOT / readme_name).read_text(encoding="utf-8")
    match = _BADGE_VERSION_RE.search(text)
    assert match is not None, f"{readme_name}: version 배지를 찾지 못했습니다"
    assert match.group("version") == expected, (
        f"{readme_name}: 배지 버전 {match.group('version')}이 plugin.json 버전 "
        f"{expected}와 일치하지 않습니다"
    )


# ---------------------------------------------------------------------------
# (c) command smoke
# ---------------------------------------------------------------------------


def _run_smtw(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "smtw", *args],
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _readme_code_block_lines() -> Iterator[str]:
    for readme_name in README_NAMES:
        text = (ROOT / readme_name).read_text(encoding="utf-8")
        in_fence = False
        for line in text.splitlines():
            if _FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                yield line


def _readme_smtw_references() -> dict[str, list[str]]:
    refs: dict[str, list[str]] = {}
    for line in _readme_code_block_lines():
        for match in _SMTW_CMD_RE.finditer(line):
            refs.setdefault(match.group("sub"), []).append(line.strip())
    return refs


@pytest.mark.parametrize("sub", TARGET_SUBCOMMANDS)
def test_target_subcommand_help_smoke(sub: str) -> None:
    examples = _readme_smtw_references().get(sub, [])
    result = _run_smtw(sub, "--help")
    if result.returncode != 0:
        pytest.xfail(
            f"`smtw {sub}`는 이 체크아웃에 아직 없습니다 "
            f"(PR H 미머지 — main 부재. README 코드블록 예시 {len(examples)}건)"
        )
    assert "usage" in result.stdout.lower(), result.stdout


def test_readme_documented_commands_respond_to_help() -> None:
    refs = _readme_smtw_references()
    if not refs:
        pytest.skip("README 코드블록에 문서화된 `smtw` 명령이 아직 없습니다")

    failures: list[str] = []
    for sub, lines in sorted(refs.items()):
        if sub in TARGET_SUBCOMMANDS:
            continue  # test_target_subcommand_help_smoke에서 담당
        result = _run_smtw(sub, "--help")
        if result.returncode != 0:
            failures.append(
                f"`smtw {sub}` (README 코드블록 {len(lines)}건 문서화) "
                f"--help exit {result.returncode}: {result.stderr.strip()}"
            )
    assert not failures, (
        "README가 문서화한 smtw 명령이 --help에 응답하지 않습니다:\n"
        + "\n".join(failures)
    )
