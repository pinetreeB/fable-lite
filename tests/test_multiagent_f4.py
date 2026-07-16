from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.contract import (
    evaluate_pretool_contract,
    evaluate_state_file_friction,
    namespaced_contract_path,
    record_contract_authored_event,
)
from core.destructive_guard import evaluate_r2_destructive_gate, parse_destructive_command
from core.ledger import record_event


# --- r2-corpus.md 전 항목 파서 고정 -----------------------------------------
# tmp/multiagent-gate/r2-corpus.md §1(Git) + §2(OS Remove) + §3(Truncate) 전 34개 시나리오.
# "대상 파싱 성공" = resolved True, "차단"(파싱 불능/암시적 범위) = resolved False.

GIT_CASES: list[tuple[str, bool]] = [
    ("git checkout -- src/main.py", True),
    ('git restore "tests/app test.js"', True),
    ("git checkout -- src/a.ts src/b.ts", True),
    ("git reset --hard HEAD", False),
    ("git clean -fdx", False),
    ("git stash push -u", False),
    ("git switch --discard-changes main", False),
    ("git restore --source=HEAD --staged --worktree .", False),
    ("git checkout -- $TARGET_FILE", False),
    ("git restore src/*.js", False),
    ("git restore --pathspec-from-file=list.txt", False),
    ("g\\it check\\out -- src/a.py", False),
    ("git --git-dir=.git restore src/a.py", False),
]

REMOVE_CASES: list[tuple[str, bool]] = [
    ("rm src/config.json", True),
    ("rm -rf ./build/", True),
    ("Remove-Item .\\temp.txt -Force", True),
    ("del /F /S /Q logs\\*.log", False),
    ("rmdir /S /Q node_modules", True),
    ("rm -rf $PWD/dist", False),
    ("rm -rf /", False),
    ("rm -rf *", False),
    ("r'm' -rf file.txt", False),
    ("& (Get-Command Remove-Item) file.txt", False),
    ('cmd /c "del file.txt"', False),
]

TRUNCATE_CASES: list[tuple[str, bool]] = [
    ("> src/app.js", True),
    ("cat /dev/null > src/index.js", True),
    ('echo "" > "test/a.py"', True),
    ('Set-Content -Path file.txt -Value ""', True),
    ("type NUL > C:\\Users\\rotat\\fable-lite\\test.js", True),
    ("echo \"old\" > $FILE", False),
    ("cat /dev/null | tee file.txt", False),
    ('eval "cat /dev/null > file.txt"', False),
    ('Out-File -FilePath (Join-Path $pwd "a.js")', False),
    ('Set-Content -Path "fi" + "le.txt" -Value ""', False),
]

ALL_CORPUS_CASES = GIT_CASES + REMOVE_CASES + TRUNCATE_CASES


@pytest.mark.parametrize("command,expect_resolved", ALL_CORPUS_CASES)
def test_r2_corpus_parser_matches_expected_verdict(command: str, expect_resolved: bool) -> None:
    parsed = parse_destructive_command(command)
    assert parsed is not None, command
    assert parsed.category is not None
    assert parsed.resolved == expect_resolved, (command, parsed)
    if expect_resolved:
        assert parsed.targets
    else:
        assert parsed.targets == ()


def test_r2_corpus_non_destructive_commands_are_not_matched() -> None:
    for benign in (
        "python -m pytest tests/",
        "git commit -m 'msg'",
        "git add .",
        "npm install",
        "ls -la",
        'bash -c "npm install"',
    ):
        assert parse_destructive_command(benign) is None, benign


# --- R2 게이트 통합: attribution 주입 -----------------------------------------


def _payload(tmp_path: Path, command: str, *, agent: str = "claude", session_id: str = "s1") -> dict:
    return {
        "project_root": str(tmp_path),
        "tool_name": "Bash",
        "command": command,
        "host": "claude_code",
        "agent": agent,
        "session_id": session_id,
    }


@pytest.mark.parametrize(
    "command",
    ["git checkout -b feat/x", "git checkout -B feat/x"],
)
def test_r2_allows_checkout_branch_creation_flags(
    tmp_path: Path,
    command: str,
) -> None:
    # Given: checkout explicitly creates or resets a branch instead of restoring a path.
    # When: R2 evaluates the shell command.
    result = evaluate_r2_destructive_gate(_payload(tmp_path, command))

    # Then: branch creation is outside the destructive path gate.
    assert result["decision"] == "allow"


@pytest.mark.parametrize(
    "command",
    ["git checkout -- .", "git checkout -- ."],
)
def test_r2_keeps_checkout_implicit_scope_blocked(
    tmp_path: Path,
    command: str,
) -> None:
    # Given: checkout targets the whole worktree (implicit scope `.`).
    # When: R2 evaluates the shell command.
    result = evaluate_r2_destructive_gate(_payload(tmp_path, command))

    # Then: implicit whole-tree scope is always fail-closed regardless of attribution.
    assert result["decision"] == "block"


@pytest.mark.parametrize(
    "command",
    ["git checkout main", "git checkout release/v2", "git checkout feature/x"],
)
def test_r2_allows_checkout_branch_switch(tmp_path: Path, command: str) -> None:
    # Given: checkout without `--` names a branch; the project root has no matching file.
    # When: R2 evaluates the shell command.
    result = evaluate_r2_destructive_gate(_payload(tmp_path, command))

    # Then: the argument resolves to an untracked in-root path (no attribution owner),
    # so branch switches pass. A real file path (git checkout src/app.py) is delegated to
    # attribution lookup and blocks only when peer-owned — see the peer-owned test below.
    assert result["decision"] == "allow"


def test_r2_blocks_checkout_path_owned_by_peer(tmp_path: Path) -> None:
    def peer_lookup(_ledger, _canonical):
        return {
            "generation": 1,
            "status": "exclusive",
            "owners": [{"agent_key": "codex_cli:other:codex", "settled": False}],
        }

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "git checkout src/app.py"),
        lookup_path_attribution=peer_lookup,
        attribution_health=lambda _l: {"degraded": False, "capacity_exceeded": False},
    )
    # checkout <path> restoring a peer-owned file is blocked via attribution.
    assert result["decision"] == "block"


def test_r2_allows_non_destructive_shell_commands(tmp_path: Path) -> None:
    result = evaluate_r2_destructive_gate(_payload(tmp_path, "python -m pytest tests/"))
    assert result["decision"] == "allow"


def test_r2_blocks_parse_unable_and_implicit_scope_without_touching_ledger(tmp_path: Path) -> None:
    # 대상이 파싱 불능/암시적 범위이면 귀속 조회 없이 즉시 차단(ledger가 없어도 차단되어야 함).
    result = evaluate_r2_destructive_gate(_payload(tmp_path, "git reset --hard HEAD"))
    assert result["decision"] == "block"
    assert "R2" in str(result["reason"])


def test_r2_allows_resolved_target_when_untracked(tmp_path: Path) -> None:
    def fake_lookup(ledger, canonical_path):
        return None

    def fake_health(ledger):
        return {"degraded": False, "capacity_exceeded": False}

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm src/config.json"),
        lookup_path_attribution=fake_lookup,
        attribution_health=fake_health,
    )
    assert result["decision"] == "allow"


def test_r2_allows_resolved_target_when_self_owned(tmp_path: Path) -> None:
    caller_key = "claude_code:s1:claude"

    def fake_lookup(ledger, canonical_path):
        return {
            "generation": 1,
            "status": "exclusive",
            "owners": [{"agent_key": caller_key, "settled": False}],
        }

    def fake_health(ledger):
        return {"degraded": False, "capacity_exceeded": False}

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm src/config.json"),
        lookup_path_attribution=fake_lookup,
        attribution_health=fake_health,
    )
    assert result["decision"] == "allow"


def test_r2_blocks_target_owned_by_unsettled_peer(tmp_path: Path) -> None:
    def fake_lookup(ledger, canonical_path):
        return {
            "generation": 5,
            "status": "exclusive",
            "owners": [{"agent_key": "codex_cli:peer-session:codex", "settled": False}],
        }

    def fake_health(ledger):
        return {"degraded": False, "capacity_exceeded": False}

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm src/config.json"),
        lookup_path_attribution=fake_lookup,
        attribution_health=fake_health,
    )
    assert result["decision"] == "block"


def test_r2_allows_target_owned_by_settled_peer(tmp_path: Path) -> None:
    def fake_lookup(ledger, canonical_path):
        return {
            "generation": 5,
            "status": "exclusive",
            "owners": [{"agent_key": "codex_cli:peer-session:codex", "settled": True}],
        }

    def fake_health(ledger):
        return {"degraded": False, "capacity_exceeded": False}

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm src/config.json"),
        lookup_path_attribution=fake_lookup,
        attribution_health=fake_health,
    )
    assert result["decision"] == "allow"


def test_r2_fail_closed_when_attribution_health_reports_degraded(tmp_path: Path) -> None:
    def fake_lookup(ledger, canonical_path):
        return None

    def fake_health(ledger):
        return {"degraded": True, "capacity_exceeded": False}

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm src/config.json"),
        lookup_path_attribution=fake_lookup,
        attribution_health=fake_health,
    )
    assert result["decision"] == "block"


def test_r2_fail_closed_when_attribution_capacity_exceeded(tmp_path: Path) -> None:
    def fake_lookup(ledger, canonical_path):
        return None

    def fake_health(ledger):
        return {"degraded": False, "capacity_exceeded": True}

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm src/config.json"),
        lookup_path_attribution=fake_lookup,
        attribution_health=fake_health,
    )
    assert result["decision"] == "block"


def test_r2_fail_closed_when_lookup_path_attribution_raises(tmp_path: Path) -> None:
    # 의존 인터페이스(F1, core/ledger_v2.lookup_path_attribution)가 없거나 예외를 던지면
    # destructive_guard가 이를 degraded로 흡수해 fail-closed로 차단해야 한다 — F1 병행
    # 개발 중에도(미완성이든 완성 후 변경 중이든) 안전해야 한다는 §6-3 요건의 직접 고정.
    def boom(ledger, canonical_path):
        raise ImportError("F1 dependency unavailable")

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm src/config.json"),
        lookup_path_attribution=boom,
        attribution_health=lambda ledger: {"degraded": False, "capacity_exceeded": False},
    )
    assert result["decision"] == "block"


def test_r2_fail_closed_when_attribution_health_raises(tmp_path: Path) -> None:
    def boom(ledger):
        raise RuntimeError("F1 dependency unavailable")

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm src/config.json"),
        lookup_path_attribution=lambda ledger, canonical_path: None,
        attribution_health=boom,
    )
    assert result["decision"] == "block"


# --- R2-first invariant: schema 예외 상태에서도 R2가 최초로 판정(mco-codex-r2.md RC1) ---


def _healthy_fakes() -> dict:
    # F1(core/ledger_v2)의 실제 동작과 무관하게 R2-first/durable-marker 로직만 고립
    # 검증하기 위해, 귀속 조회는 항상 "정상/미추적"으로 가정하는 fake를 주입한다.
    return {
        "lookup_path_attribution": lambda ledger, canonical_path: None,
        "attribution_health": lambda ledger: {"degraded": False, "capacity_exceeded": False},
    }


def test_r2_first_blocks_destructive_command_when_ledger_is_corrupt(tmp_path: Path) -> None:
    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir()
    (state_dir / "ledger.json").write_text("{not-json", encoding="utf-8")

    # "git clean"은 파싱 단계(암시적 범위)에서 이미 차단되어 ledger를 건드리지 않는다 —
    # ledger 손상 경로 자체를 태우려면 대상이 정적으로 resolve되는 명령이 필요하다.
    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm src/config.json"), **_healthy_fakes()
    )

    assert result["decision"] == "block"


def test_r2_first_durable_marker_keeps_blocking_after_ledger_self_heals(tmp_path: Path) -> None:
    # load_ledger()는 손상 파일을 예외 없이 삼키고 .corrupt-*.bak으로 옮긴 뒤 기본 ledger를
    # 반환한다 — 그 bak이 디스크에 남아있는 한 이후 모든 호출에서 degraded가 지속돼야
    # 한다(§6-3 durable marker, "첫 호출 뒤 소실 방지").
    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir()
    (state_dir / "ledger.json").write_text("{not-json", encoding="utf-8")

    first = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm src/config.json"), **_healthy_fakes()
    )
    assert first["decision"] == "block"
    assert any(state_dir.glob("*.corrupt-*.bak"))

    second = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm src/config.json"), **_healthy_fakes()
    )
    assert second["decision"] == "block"


def test_r2_first_does_not_block_non_destructive_commands_when_ledger_is_corrupt(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir()
    (state_dir / "ledger.json").write_text("{not-json", encoding="utf-8")

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "python -m pytest tests/"), **_healthy_fakes()
    )

    assert result["decision"] == "allow"


def test_r2_first_invocation_order_precedes_resolve_active_invocation(tmp_path: Path) -> None:
    # adapters/*/pre_tool_use.py의 실제 R2-first 배선을 서브프로세스로 재현: 손상된
    # ledger.json 상태에서도 파괴 명령이면 fail-open으로 새지 않고 R2가 차단해야 한다
    # (mco-codex-r2.md RC1: resolve_active_invocation()의 LedgerSchemaError가
    # 광역 except로 새어나가 fail-open되던 결함의 회귀 방지).
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir()
    (state_dir / "ledger.json").write_text("{not-json", encoding="utf-8")

    payload = {
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "git reset --hard HEAD"},
        "session_id": "rc1-repro",
    }
    proc = subprocess.run(
        [sys.executable, str(root / "adapters" / "claude_code" / "pre_tool_use.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0
    result = json.loads(proc.stdout)
    assert result.get("decision") == "block", result
    assert "R2" in str(result.get("reason", ""))


# --- 계약 네임스페이스(§5-1) ---------------------------------------------------


def _exact_payload(tmp_path: Path, *, session_id: str = "s1", agent: str = "claude") -> dict:
    return {
        "project_root": str(tmp_path),
        "tool_name": "Edit",
        "file_paths": ["migrations/001_init.sql"],
        "prompt": "high risk edit",
        "host": "claude_code",
        "session_id": session_id,
        "agent": agent,
        "attribution": "exact",
    }


def _write_valid_contract(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "restated_goal": "high risk edit",
                "acceptance": ["tests pass"],
                "evidence": ["python -m pytest tests/test_x.py"],
            }
        ),
        encoding="utf-8",
    )


def _authoring_event_payload(tmp_path: Path, session_id: str, agent: str, namespaced: Path) -> dict:
    return {
        "project_root": str(tmp_path),
        "file_paths": [str(namespaced)],
        "host": "claude_code",
        "session_id": session_id,
        "agent": agent,
        "attribution": "exact",
    }


def test_r1_blocks_exact_identity_without_any_contract(tmp_path: Path) -> None:
    result = evaluate_pretool_contract(_exact_payload(tmp_path))
    assert result["decision"] == "block"


def test_r1_allows_own_namespaced_contract_with_matching_authored_event(tmp_path: Path) -> None:
    payload = _exact_payload(tmp_path)
    agent_key = "claude_code:s1:claude"
    namespaced = namespaced_contract_path(str(tmp_path), agent_key)
    _write_valid_contract(namespaced)
    record_contract_authored_event(_authoring_event_payload(tmp_path, "s1", "claude", namespaced))

    result = evaluate_pretool_contract(payload)

    assert result["decision"] == "allow"


def test_r1_rejects_valid_looking_contract_without_matching_authored_event(tmp_path: Path) -> None:
    # 계약 파일 자체는 스키마상 유효해도 contract_authored 이벤트가 없으면(§6-5 교차 확인
    # 실패) 인정하지 않는다 — 타 identity 계약을 복사해 붙여넣는 시나리오의 재현 차단.
    payload = _exact_payload(tmp_path)
    agent_key = "claude_code:s1:claude"
    namespaced = namespaced_contract_path(str(tmp_path), agent_key)
    _write_valid_contract(namespaced)
    # 다른 identity가 동시 활성 상태 — legacy 폴백도 불가능하게 만든다.
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "host": "codex_cli",
            "session_id": "peer-session",
            "agent": "codex",
            "prompt": "peer working",
        }
    )

    result = evaluate_pretool_contract(payload)

    assert result["decision"] == "block"


def test_r1_copying_another_identitys_contract_file_does_not_validate(tmp_path: Path) -> None:
    # agy 시나리오 C 재현: victim의 유효+감사된 계약을 attacker identity의 namespaced
    # 경로로 그대로 복사해도, attacker 자신의 감사 로그에는 대응 이벤트가 없어 무익화된다.
    victim_key = "claude_code:victim-session:claude"
    attacker_key = "claude_code:attacker-session:claude"
    victim_path = namespaced_contract_path(str(tmp_path), victim_key)
    attacker_path = namespaced_contract_path(str(tmp_path), attacker_key)
    _write_valid_contract(victim_path)
    record_contract_authored_event(
        _authoring_event_payload(tmp_path, "victim-session", "claude", victim_path)
    )
    attacker_path.parent.mkdir(parents=True, exist_ok=True)
    attacker_path.write_text(victim_path.read_text(encoding="utf-8"), encoding="utf-8")
    # 두 identity 모두 활성 상태로 등록 — legacy 폴백 불가.
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "host": "claude_code",
            "session_id": "victim-session",
            "agent": "claude",
            "prompt": "victim turn",
        }
    )
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "host": "claude_code",
            "session_id": "attacker-session",
            "agent": "claude",
            "prompt": "attacker turn",
        }
    )

    attacker_payload = _exact_payload(tmp_path, session_id="attacker-session")
    result = evaluate_pretool_contract(attacker_payload)

    assert result["decision"] == "block"


def test_r1_legacy_fallback_allowed_when_caller_is_sole_active_exact_identity(
    tmp_path: Path,
) -> None:
    from core.contract import contract_path

    _write_valid_contract(contract_path(str(tmp_path)))
    payload = _exact_payload(tmp_path)

    result = evaluate_pretool_contract(payload)

    assert result["decision"] == "allow"


def test_r1_legacy_fallback_disabled_when_another_exact_identity_is_active(
    tmp_path: Path,
) -> None:
    from core.contract import contract_path

    _write_valid_contract(contract_path(str(tmp_path)))
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "host": "codex_cli",
            "session_id": "peer-session",
            "agent": "codex",
            "prompt": "peer working",
        }
    )
    payload = _exact_payload(tmp_path)

    result = evaluate_pretool_contract(payload)

    assert result["decision"] == "block"


def test_r1_legacy_synthetic_identity_keeps_legacy_path_unconditionally(tmp_path: Path) -> None:
    # legacy_default 세션(attribution 없음)은 항상 기존 legacy 경로를 쓴다(설계 §5-1 마지막 줄) —
    # 기존 테스트(test_core_contracts.py)와의 하위 호환을 명시적으로 재확인.
    from core.contract import contract_path

    _write_valid_contract(contract_path(str(tmp_path)))
    payload = {
        "project_root": str(tmp_path),
        "tool_name": "Edit",
        "file_paths": ["migrations/001_init.sql"],
        "prompt": "high risk edit",
    }

    result = evaluate_pretool_contract(payload)

    assert result["decision"] == "allow"


# --- 상태 파일 마찰 차단(§6-5) -------------------------------------------------


def test_state_file_friction_blocks_direct_ledger_edit(tmp_path: Path) -> None:
    result = evaluate_state_file_friction(
        {
            "project_root": str(tmp_path),
            "tool_name": "Edit",
            "file_paths": [str(tmp_path / ".fable-lite" / "ledger.json")],
        }
    )
    assert result["decision"] == "block"


def test_state_file_friction_blocks_shell_truncate_of_ledger(tmp_path: Path) -> None:
    result = evaluate_state_file_friction(
        {
            "project_root": str(tmp_path),
            "tool_name": "Bash",
            "command": "echo '{}' > .fable-lite/ledger.json",
        }
    )
    assert result["decision"] == "block"


def test_state_file_friction_allows_own_contract_authoring(tmp_path: Path) -> None:
    result = evaluate_state_file_friction(
        {
            "project_root": str(tmp_path),
            "tool_name": "Edit",
            "file_paths": [str(tmp_path / ".fable-lite" / "contract.json")],
        }
    )
    assert result["decision"] == "allow"


def test_state_file_friction_allows_unrelated_files(tmp_path: Path) -> None:
    result = evaluate_state_file_friction(
        {
            "project_root": str(tmp_path),
            "tool_name": "Edit",
            "file_paths": ["app.py"],
        }
    )
    assert result["decision"] == "allow"


def test_pretool_contract_wires_state_file_friction_before_r1(tmp_path: Path) -> None:
    result = evaluate_pretool_contract(
        {
            "project_root": str(tmp_path),
            "tool_name": "Edit",
            "file_paths": [str(tmp_path / ".fable-lite" / "ledger.json")],
            "prompt": "let me just patch the ledger directly",
        }
    )
    assert result["decision"] == "block"
    assert "R2-friction" in str(result["reason"])


# --- pre-attribution 창 보호(RC3) ---------------------------------------------


def test_r2_blocks_target_matching_peer_open_invocation_candidate(tmp_path: Path) -> None:
    # peer가 아직 PostTool 귀속(change event)을 남기기 전, 즉 lookup_path_attribution이
    # None(미추적)을 반환하는 창에서도 그 경로가 peer의 기록된 invocation candidate와
    # 일치하면 R2는 통과시키지 않는다(설계 §6-4 RC3 pre-attribution 창 보호).
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "host": "codex_cli",
            "session_id": "peer-session",
            "agent": "codex",
            "prompt": "peer working",
        }
    )
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "invocation",
            "host": "codex_cli",
            "session_id": "peer-session",
            "agent": "codex",
            "invocation_id": "inv-peer-1",
            "candidate_paths": ["peer-new.py"],
        }
    )

    def fake_lookup(ledger, canonical_path):
        return None

    def fake_health(ledger):
        return {"degraded": False, "capacity_exceeded": False}

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm peer-new.py"),
        lookup_path_attribution=fake_lookup,
        attribution_health=fake_health,
    )

    assert result["decision"] == "block"


def test_r2_allows_untracked_target_with_no_peer_activity(tmp_path: Path) -> None:
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "host": "codex_cli",
            "session_id": "peer-session",
            "agent": "codex",
            "prompt": "peer working",
        }
    )
    record_event(
        {
            "project_root": str(tmp_path),
            "event": "invocation",
            "host": "codex_cli",
            "session_id": "peer-session",
            "agent": "codex",
            "invocation_id": "inv-peer-1",
            "candidate_paths": ["some-other-file.py"],
        }
    )

    def fake_lookup(ledger, canonical_path):
        return None

    def fake_health(ledger):
        return {"degraded": False, "capacity_exceeded": False}

    result = evaluate_r2_destructive_gate(
        _payload(tmp_path, "rm unrelated.py"),
        lookup_path_attribution=fake_lookup,
        attribution_health=fake_health,
    )

    assert result["decision"] == "allow"
