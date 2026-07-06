from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypeAlias

from core.ledger import record_event


ROOT = Path(__file__).resolve().parents[1]

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def run_cli(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    python_path = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    return subprocess.run(
        [sys.executable, "-m", "fable_lite", *args],
        cwd=cwd or ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": python_path},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def git(project: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stderr


def init_repo(project: Path) -> None:
    git(project, "init")
    git(project, "config", "user.email", "test@example.com")
    git(project, "config", "user.name", "Test")
    (project / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    git(project, "add", ".")
    git(project, "commit", "-m", "init")


def read_json(path: Path) -> dict[str, JsonValue]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def write_card(
    path: Path,
    *,
    owner: str = "codex",
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    verify: str = "python -m pytest tests/",
    done_artifact: str = "tmp/.done-card",
    sentinel: str | None = None,
) -> None:
    allowed: list[JsonValue] = list(allowed_paths or ["app.py"])
    forbidden: list[JsonValue] = list(forbidden_paths or [])
    payload: dict[str, JsonValue] = {
        "slug": "card",
        "owner": owner,
        "allowed_paths": allowed,
        "forbidden_paths": forbidden,
        "verify": verify,
        "done_artifact": done_artifact,
        "risk": "L1",
    }
    if sentinel is not None:
        payload["sentinel"] = sentinel
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8", newline="\n")


def write_project_file(project: Path, relative: str, text: str = "changed\n") -> None:
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def record_card_work(project: Path, changed_path: str, verify: str, *, success: bool = True) -> None:
    base = {"project_root": str(project), "agent": "codex"}
    record_event({**base, "event": "prompt", "task_mode": "deep", "prompt": "카드 작업"})
    record_event({**base, "event": "change", "path": changed_path, "kind": "artifact"})
    record_event({**base, "event": "verification", "command": verify, "success": success, "evidence": "1 passed"})


def test_check_reports_green_when_changed_files_have_successful_verification(tmp_path: Path) -> None:
    init_repo(tmp_path)
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8", newline="\n")
    record_event({"project_root": str(tmp_path), "event": "prompt", "task_mode": "deep", "prompt": "app.py 수정", "agent": "codex"})
    record_event({"project_root": str(tmp_path), "event": "change", "path": "app.py", "kind": "code", "agent": "codex"})
    record_event({"project_root": str(tmp_path), "event": "verification", "command": "python -m pytest", "success": True, "evidence": "1 passed", "agent": "codex"})

    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex"])

    assert result.returncode == 0
    assert "GREEN" in result.stdout
    assert "app.py" in result.stdout


def test_check_reports_red_for_unverified_scope_drift_r1_and_missing_sentinel(tmp_path: Path) -> None:
    init_repo(tmp_path)
    marker = tmp_path / "marker.txt"
    marker.write_text("start\n", encoding="utf-8", newline="\n")
    (tmp_path / "settings.py").write_text("DROP TABLE users;\n", encoding="utf-8", newline="\n")
    record_event({"project_root": str(tmp_path), "event": "prompt", "task_mode": "deep", "prompt": "app.py만 수정하고 완료 후 빈 파일 tmp/.done-x sentinel 생성", "agent": "codex"})
    record_event({"project_root": str(tmp_path), "event": "change", "path": "settings.py", "kind": "code", "agent": "codex"})

    result = run_cli(["check", "--root", str(tmp_path), "--agent", "codex", "--since-file", str(marker)])

    assert result.returncode == 1
    assert "RED" in result.stdout
    assert "미검증 변경" in result.stdout
    assert "settings.py" in result.stdout
    assert "범위" in result.stdout
    assert "R1" in result.stdout
    assert "sentinel" in result.stdout


def test_record_event_writes_agent_jsonl_without_breaking_legacy_ledger(tmp_path: Path) -> None:
    record_event({"project_root": str(tmp_path), "event": "change", "path": "app.py", "kind": "code", "agent": "codex"})

    ledger = read_json(tmp_path / ".fable-lite" / "ledger.json")
    agent_log = tmp_path / ".fable-lite" / "agents" / "codex.jsonl"
    lines = agent_log.read_text(encoding="utf-8").splitlines()

    assert ledger["agent"] == "codex"
    assert ledger["changed_files_seen"] == ["app.py"]
    assert len(lines) == 1
    assert json.loads(lines[0])["agent"] == "codex"


def test_brief_prints_target_specific_delegation_rules() -> None:
    result = run_cli(
        ["brief", "--paths", "core/**,tests/**", "--verify-cmd", "python -m pytest tests/", "--sentinel", "tmp/.done-x", "--target", "agy"]
    )

    assert result.returncode == 0
    assert "allowed_paths" in result.stdout
    assert "core/**" in result.stdout
    assert "python -m pytest tests/" in result.stdout
    assert "tmp/.done-x" in result.stdout
    assert "사후 check" in result.stdout
    assert "상세 규율" in result.stdout


def test_check_with_card_reports_red_for_forbidden_touch_and_missing_verify(tmp_path: Path) -> None:
    init_repo(tmp_path)
    card = tmp_path / "card.json"
    write_card(card, allowed_paths=["app.py"], forbidden_paths=["secrets/**"], verify="python -m pytest tests/", done_artifact="tmp/.done-card")
    write_project_file(tmp_path, "secrets/token.txt")
    write_project_file(tmp_path, "tmp/.done-card", "")
    record_card_work(tmp_path, "secrets/token.txt", "python -m pytest other/")

    result = run_cli(["check", "--card", str(card)], cwd=tmp_path)

    assert result.returncode == 1
    assert all(token in result.stdout for token in ("RED", "forbidden", "secrets/token.txt", "verify", "python -m pytest tests/"))


def test_check_with_card_matches_forbidden_case_insensitively(tmp_path: Path) -> None:
    init_repo(tmp_path)
    card = tmp_path / "card.json"
    verify = "python -m pytest tests/"
    write_card(card, allowed_paths=["**"], forbidden_paths=["secrets/**"], verify=verify, done_artifact="tmp/.done-card")
    write_project_file(tmp_path, "Secrets/token.txt")
    write_project_file(tmp_path, "tmp/.done-card", "")
    record_card_work(tmp_path, "Secrets/token.txt", verify)

    result = run_cli(["check", "--card", str(card)], cwd=tmp_path)

    assert result.returncode == 1
    assert "forbidden" in result.stdout
    assert "Secrets/token.txt" in result.stdout


def test_check_with_card_enforces_allowed_glob_without_broadening(tmp_path: Path) -> None:
    init_repo(tmp_path)
    card = tmp_path / "card.json"
    verify = "python -m pytest tests/"
    write_card(card, allowed_paths=["src/*.py"], verify=verify, done_artifact="tmp/.done-card")
    write_project_file(tmp_path, "src/secrets.txt")
    write_project_file(tmp_path, "tmp/.done-card", "")
    record_card_work(tmp_path, "src/secrets.txt", verify)

    result = run_cli(["check", "--card", str(card)], cwd=tmp_path)

    assert result.returncode == 1
    assert "src/secrets.txt" in result.stdout
    assert "allowed_paths" in result.stdout


def test_check_with_card_rejects_pre_card_legacy_verify(tmp_path: Path) -> None:
    init_repo(tmp_path)
    verify = "python -m pytest tests/"
    record_event({"project_root": str(tmp_path), "event": "verification", "command": verify, "success": True, "evidence": "old pass"})
    card = tmp_path / "card.json"
    write_card(card, allowed_paths=["app.py"], verify=verify, done_artifact="tmp/.done-card")
    write_project_file(tmp_path, "app.py", "print('ok')\n")
    write_project_file(tmp_path, "tmp/.done-card", "")

    result = run_cli(["check", "--card", str(card)], cwd=tmp_path)

    assert result.returncode == 1
    assert "verify" in result.stdout
    assert verify in result.stdout


def test_check_with_card_reports_red_for_missing_required_fields(tmp_path: Path) -> None:
    init_repo(tmp_path)
    card = tmp_path / "card.json"
    card.write_text(json.dumps({"owner": "codex", "forbidden_paths": []}), encoding="utf-8", newline="\n")

    result = run_cli(["check", "--card", str(card)], cwd=tmp_path)

    assert result.returncode == 1
    assert all(token in result.stdout for token in ("RED", "작업카드 오류", "allowed_paths", "verify", "done_artifact"))


def test_brief_with_card_uses_card_fields(tmp_path: Path) -> None:
    card = tmp_path / "card.json"
    write_card(
        card,
        owner="antigravity",
        allowed_paths=["fable_lite/**", "tests/**"],
        forbidden_paths=["core/**"],
        verify="python -m pytest tests/test_fable_lite_cli.py",
        done_artifact="tmp/.done-card",
        sentinel="C:/Users/rotat/.claude/tmp/.done-card",
    )

    result = run_cli(["brief", "--card", str(card)])

    assert result.returncode == 0
    assert all(
        token in result.stdout
        for token in ("fable_lite/**", "core/**", "python -m pytest tests/test_fable_lite_cli.py", "tmp/.done-card", "C:/Users/rotat/.claude/tmp/.done-card", "antigravity")
    )
