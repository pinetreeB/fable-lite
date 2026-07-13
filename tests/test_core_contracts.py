from __future__ import annotations

import json
from pathlib import Path

from core.classify import classify_prompt
from core.compliance import check_investigation_compliance
from core.contract import evaluate_pretool_contract
from core.ledger import load_ledger, record_event
from core.scope_guard import evaluate_scope
from core.verify_state import evaluate_stop


LSV_BOOT_PROMPT = """[부팅] 너는 wmux 4-pane 팀의 우하 pane = 구현보조 담당 Claude Code(Sonnet 5, ultracode 모드)다 (메인 PC 로컬). 프로젝트: KMC 골프카트/LSV 미국수출 사업 (GM-KMC 투자유치) — 문서·리서치·재무모델·IM 작업, 현재 디렉토리가 작업폴더다. 부팅 절차: C:\\Users\\rotat\\.claude\\projects\\C--Users-rotat\\memory\\MEMORY.md 와 같은 폴더의 kmc\\golfcart-gm-im-model-review.md 를 읽고 프로젝트 상태를 파악하라. 운영 규칙: (1) 미션모드 — 산출물 95점 기준·치명결함 0 하드게이트 (2) 비가역 작업(외부발송·계약·지출·배포)은 자동 진행 금지, 좌상 Claude 오케스트레이터 경유 사용자 승인 필수 (3) 산출물은 작업폴더에 파일로 저장하되 다른 AI의 파일을 덮어쓰지 말 것 (4) 작업 완료 시 지정된 sentinel 파일 생성. 파악 완료되면 READY-SONNET 한 줄만 출력하고 대기하라."""
FABLE_BOOT_PROMPT = """세션 부팅: (1) C:\\Users\\rotat\\.claude\\projects\\C--Users-rotat\\memory\\MEMORY.md 를 읽어 사용자·운영규칙을 파악하고 (2) C:\\Users\\rotat\\.claude\\projects\\C--Users-rotat\\memory\\fable-lite\\project.md 로 프로젝트 상태를 파악해라. 너의 역할: 우하 pane 다차원·병렬 구현 보조(Claude Sonnet 5 max, ultracode 모드 — 다차원 병렬 작업 전용, 단일 작업은 좌상이 직접 함). 현재 프로젝트 fable-lite(C:\\Users\\rotat\\fable-lite, v1.1.0 릴리스 완료). 규칙: 미션모드 검토=평균 95점+치명결함 0 하드게이트 / 비가역 작업은 사용자 명시 OK 필수 / 위임받은 파일 영역만 수정. 읽기 완료 후 "부팅 완료" 보고하고 대기해라."""


def requested_paths_for(prompt: str) -> list[str]:
    result = classify_prompt({"prompt": prompt})
    paths = result["requested_paths"]
    assert isinstance(paths, list)
    return paths


def test_classify_prompt_routes_korean_debug_and_page_requests() -> None:
    debug_result = classify_prompt({"prompt": "버그 고쳐줘 안돼 에러가 나요"})
    page_result = classify_prompt({"prompt": "관리자 페이지 만들어줘"})
    debug_packs = debug_result["packs"]
    page_packs = page_result["packs"]

    assert debug_result["mode"] == "deep"
    assert isinstance(debug_packs, list)
    assert "investigation" in debug_packs
    assert page_result["mode"] == "normal"
    assert isinstance(page_packs, list)
    assert "verification-grounding" in page_packs


def test_classify_prompt_requires_goals_for_multi_story_work() -> None:
    result = classify_prompt({"prompt": "로그인 고치고 결제 페이지도 만들어줘"})

    assert result["needs_goals"] is True
    assert result["mode"] == "deep"


def test_classify_prompt_suppresses_artifact_and_goals_for_lsv_boot_briefing() -> None:
    result = classify_prompt({"prompt": LSV_BOOT_PROMPT})
    packs = result["packs"]

    assert result["needs_goals"] is False
    assert result["briefing"] is True
    assert isinstance(packs, list)
    assert "completion" not in packs
    assert "verification-grounding" not in packs


def test_classify_prompt_suppresses_goals_for_fable_boot_briefing_with_rule_phrase() -> None:
    result = classify_prompt({"prompt": FABLE_BOOT_PROMPT})

    assert result["needs_goals"] is False
    assert result["briefing"] is True


def test_classify_prompt_cancels_boot_briefing_when_imperative_action_is_present() -> None:
    result = classify_prompt({"prompt": "[부팅] MEMORY.md 읽고 login.py랑 auth.py 고쳐줘"})

    assert result["briefing"] is False
    assert result["needs_goals"] is True


def test_classify_prompt_keeps_goals_for_two_paths_with_imperative_action_verb() -> None:
    result = classify_prompt({"prompt": "auth.py 와 pay.py 수정해줘"})

    assert result["needs_goals"] is True
    assert result["briefing"] is False


def test_classify_prompt_keeps_risk_flags_inside_boot_briefing() -> None:
    result = classify_prompt({"prompt": "[부팅] 정리를 위해 rm -rf tmp/ 실행해라"})
    risks = result["risk_flags"]

    assert result["briefing"] is True
    assert isinstance(risks, list)
    assert risks


def test_classify_prompt_does_not_treat_waiting_after_real_work_as_briefing() -> None:
    result = classify_prompt({"prompt": "이슈 3개 고치고 끝나면 대기해"})

    assert result["briefing"] is False
    assert result["needs_goals"] is True


def test_mentioned_paths_excludes_versions_and_domains_but_keeps_real_paths() -> None:
    assert requested_paths_for("v1.1.0 릴리스") == []
    assert requested_paths_for("a.md b.py 봐줘") == ["a.md", "b.py"]
    assert requested_paths_for("google.com 참고") == []


def test_classify_prompt_matches_and_only_as_a_word_boundary() -> None:
    shell_summary = classify_prompt({"prompt": "ran 2 shell commands"})
    real_multi = classify_prompt({"prompt": "fix login and payment"})

    assert shell_summary["needs_goals"] is False
    assert shell_summary["briefing"] is False
    assert real_multi["needs_goals"] is True


def test_classify_prompt_handles_korean_multi_boundary_terms_precisely() -> None:
    greeting = classify_prompt({"prompt": "여러분 안녕하세요"})
    multi_files = classify_prompt({"prompt": "여러 파일 고쳐줘"})
    installment = classify_prompt({"prompt": "12개월 할부 표시"})

    assert greeting["needs_goals"] is False
    assert multi_files["needs_goals"] is True
    assert installment["needs_goals"] is False


def test_classify_prompt_keeps_action_noun_rule_phrase_as_briefing() -> None:
    result = classify_prompt({"prompt": "코드 수정 규칙을 파악한 뒤 대기해라"})

    assert result["briefing"] is True
    assert result["needs_goals"] is False


def test_classify_prompt_restores_plain_gochigo_multi_story_enumeration() -> None:
    result = classify_prompt({"prompt": "A 버그 고치고 B 기능 만들어줘"})

    assert result["needs_goals"] is True


def test_classify_prompt_cancels_boot_briefing_for_polite_imperative_action() -> None:
    result = classify_prompt(
        {"prompt": r"[부팅] MEMORY.md 와 kmc\x.md 읽고 auth.py pay.py 연동 좀 부탁해"}
    )

    assert result["briefing"] is False
    assert result["needs_goals"] is True


def test_classify_prompt_requires_boot_marker_at_prompt_start() -> None:
    result = classify_prompt({"prompt": "문서 정리했고 [부팅] 마커는 중간에 있음"})

    assert result["briefing"] is False


def test_mentioned_paths_keeps_ai_app_and_co_filelike_tokens() -> None:
    assert requested_paths_for("logo.ai Calculator.app vendor.co 확인") == [
        "logo.ai",
        "Calculator.app",
        "vendor.co",
    ]


def test_classify_prompt_uses_normal_mode_floor_for_briefings() -> None:
    result = classify_prompt({"prompt": "세션 부팅: MEMORY.md 읽고 대기해라"})

    assert result["briefing"] is True
    assert result["mode"] == "normal"


def test_classify_prompt_covers_korean_negative_forms_without_standalone_particle_multi_story() -> None:
    spaced = classify_prompt({"prompt": "왜 안 돼"})
    conjugated = classify_prompt({"prompt": "안되는데요"})
    single_story = classify_prompt({"prompt": "버튼 눌러도 반응이 없어요"})
    packs = single_story["packs"]

    assert spaced["mode"] == "deep"
    assert conjugated["mode"] == "deep"
    assert single_story["needs_goals"] is False
    assert isinstance(packs, list)
    assert "completion" not in packs


def test_investigation_compliance_requires_three_hypotheses_rejection_and_evidence() -> None:
    compliant = check_investigation_compliance(
        {
            "text": "\n".join(
                [
                    "가설 1: 라우터 문제",
                    "가설 2: 상태 초기화 문제",
                    "가설 3: API 응답 문제",
                    "기각: 라우터 문제는 재현되지 않음",
                    "증거: pytest tests/test_app.py 통과",
                ]
            )
        }
    )
    incomplete = check_investigation_compliance({"text": "가설 1: 하나뿐\n증거: 로그"})

    assert compliant["compliant"] is True
    assert compliant["hypothesis_count"] == 3
    assert incomplete["compliant"] is False
    missing = incomplete["missing"]
    assert isinstance(missing, list)
    assert "hypotheses" in missing
    assert "rejection" in missing


def test_investigation_compliance_accepts_english_markers() -> None:
    result = check_investigation_compliance(
        {
            "text": "\n".join(
                [
                    "Hypothesis 1: router regression",
                    "Hypothesis 2: state initialization",
                    "Hypothesis 3: API response shape",
                    "Rejected: Hypothesis 1 because route logs are clean",
                    "Evidence: tests/test_app.py passed",
                ]
            )
        }
    )

    assert result["compliant"] is True
    assert result["hypothesis_count"] == 3


def test_ledger_records_only_under_project_fable_lite_directory(tmp_path: Path) -> None:
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "버그 고쳐줘",
        }
    )
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "change",
            "path": "app.py",
            "kind": "code",
        }
    )
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "verification",
            "command": "python -m pytest",
            "success": True,
            "evidence": "1 passed",
        }
    )

    ledger = load_ledger({"project_root": str(tmp_path)})
    assert (tmp_path / ".fable-lite" / "ledger.json").exists()
    assert ledger["task_mode"] == "deep"
    assert ledger["changed_files_seen"] == ["app.py"]
    results = ledger["verification_results"]
    assert isinstance(results, list)
    first_result = results[0]
    assert isinstance(first_result, dict)
    assert first_result["success"] is True


def test_ledger_preserves_corrupted_json_as_backup_before_regenerating(tmp_path: Path) -> None:
    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir()
    ledger_file = state_dir / "ledger.json"
    _ = ledger_file.write_text("{broken", encoding="utf-8")

    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "버그 고쳐줘",
        }
    )
    ledger = load_ledger({"project_root": str(tmp_path)})

    assert next(state_dir.glob("ledger.json.corrupt-*.bak")).read_text(encoding="utf-8") == "{broken"
    assert ledger["prompt"] == "버그 고쳐줘"


def test_stop_gate_blocks_changed_unverified_work_at_most_twice(tmp_path: Path) -> None:
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "버그 고쳐줘",
        }
    )
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "change",
            "path": "app.py",
            "kind": "code",
        }
    )

    first = evaluate_stop({"project_root": str(tmp_path)})
    second = evaluate_stop({"project_root": str(tmp_path)})
    third = evaluate_stop({"project_root": str(tmp_path)})

    assert first["decision"] == "block"
    assert second["decision"] == "block"
    assert third["decision"] == "allow"
    message = third["message"]
    assert isinstance(message, str)
    assert "최대 2회" in message


def test_stop_gate_still_blocks_twice_when_stop_hook_active_is_true(tmp_path: Path) -> None:
    # v1 릴리스 심사 B2 회귀 테스트: 실제 Claude Code는 훅이 한 번 block을 반환하면
    # 그 강제 연속 응답에서의 다음 Stop 시도에 stop_hook_active=true를 실어 보낸다.
    # 이전 버그는 이 신호만 보고 재검사 없이 무조건 allow해서 MAX_STOP_BLOCKS=2가
    # 사실상 도달 불가능했다(p5b·e1·e1b·e1c 전체에서 stop_blocks가 항상 1이었던 원인).
    # stop_hook_active=True인 채로도 실제 미검증 상태가 계속되면 2회까지는 반드시 차단돼야 한다.
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "prompt",
            "task_mode": "deep",
            "prompt": "버그 고쳐줘",
        }
    )
    _ = record_event(
        {
            "project_root": str(tmp_path),
            "event": "change",
            "path": "app.py",
            "kind": "code",
        }
    )

    first = evaluate_stop({"project_root": str(tmp_path), "stop_hook_active": False})
    second = evaluate_stop({"project_root": str(tmp_path), "stop_hook_active": True})
    third = evaluate_stop({"project_root": str(tmp_path), "stop_hook_active": True})

    assert first["decision"] == "block"
    assert second["decision"] == "block"
    assert third["decision"] == "allow"
    ledger = load_ledger({"project_root": str(tmp_path)})
    assert ledger["stop_blocks"] == 2


def test_stop_block_counter_resets_on_new_prompt(tmp_path: Path) -> None:
    _ = record_event({"project_root": str(tmp_path), "event": "prompt", "task_mode": "deep", "prompt": "첫 작업"})
    _ = record_event({"project_root": str(tmp_path), "event": "change", "path": "app.py", "kind": "code"})
    assert evaluate_stop({"project_root": str(tmp_path)})["decision"] == "block"
    assert evaluate_stop({"project_root": str(tmp_path)})["decision"] == "block"

    _ = record_event({"project_root": str(tmp_path), "event": "prompt", "task_mode": "deep", "prompt": "새 작업"})
    ledger = load_ledger({"project_root": str(tmp_path)})

    assert ledger["stop_blocks"] == 0


def test_scope_guard_warns_when_changed_file_is_outside_requested_scope() -> None:
    result = evaluate_scope(
        {
            "prompt": "app.py만 수정해줘",
            "requested_paths": ["app.py"],
            "changed_files": ["app.py", "settings.py"],
        }
    )

    assert result["decision"] == "warn"
    assert result["out_of_scope"] == ["settings.py"]


def test_scope_guard_allows_casefolded_absolute_path_under_requested_relative_path(tmp_path: Path) -> None:
    changed = tmp_path / "APP.py"
    result = evaluate_scope(
        {
            "project_root": str(tmp_path),
            "prompt": "app.py만 수정해줘",
            "requested_paths": ["app.py"],
            "changed_files": [str(changed)],
        }
    )

    assert result["decision"] == "allow"


def test_scope_guard_skips_when_prompt_has_no_extractable_file_pattern() -> None:
    # p5b·E1에서 반복 확인된 허위 경고: "이 add가 왜 이상해?"처럼 파일명 없이
    # 대명사·심볼명으로만 지칭하면 요청 범위 자체가 특정되지 않은 것이라 경고가 무근거하다.
    result = evaluate_scope(
        {
            "prompt": "이 add가 왜 이상해? 고쳐줘",
            "requested_paths": [],
            "changed_files": ["C:/proj/calc.py"],
        }
    )

    assert result["decision"] == "allow"
    assert result["out_of_scope"] == []


def test_scope_guard_still_warns_when_prompt_names_a_different_file_explicitly() -> None:
    # 위 skip이 과잉 적용되지 않는지 확인: requested_paths가 비어 있어도
    # 프롬프트가 실제로 다른 파일명을 명시하면 기존 폴백(_prompt_mentions) 경고는 유지돼야 한다.
    result = evaluate_scope(
        {
            "prompt": "calc.py 고쳐줘",
            "requested_paths": [],
            "changed_files": ["/proj/settings.py"],
        }
    )

    assert result["decision"] == "warn"
    assert result["out_of_scope"] == ["/proj/settings.py"]


def test_high_risk_contract_blocks_edit_until_valid_contract_exists(tmp_path: Path) -> None:
    payload = {"project_root": str(tmp_path), "tool_name": "Edit", "file_paths": ["migrations/001_init.sql"], "prompt": "DB 마이그 수정"}
    blocked = evaluate_pretool_contract(payload)
    state_dir = tmp_path / ".fable-lite"
    state_dir.mkdir(exist_ok=True)
    _ = (state_dir / "contract.json").write_text(
        json.dumps({"restated_goal": "DB 마이그레이션 수정", "acceptance": ["python -m pytest tests/test_migration.py"], "evidence": ["test will be run before done"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    allowed = evaluate_pretool_contract(payload)

    assert blocked["decision"] == "block"
    assert allowed["decision"] == "allow"


def test_high_risk_contract_blocks_shell_commands_without_valid_contract(tmp_path: Path) -> None:
    result = evaluate_pretool_contract({"project_root": str(tmp_path), "tool_name": "Bash", "command": "python manage.py migrate && psql -c 'DROP TABLE users'"})

    assert result["decision"] == "block"


def test_r1_rm_refines_file_delete_risk(tmp_path: Path) -> None:
    (tmp_path / "node_modules").mkdir()
    _ = (tmp_path / "test.py").write_text("print('ok')\n", encoding="utf-8")
    nested = tmp_path / "tmp" / "x.txt"
    nested.parent.mkdir()
    _ = nested.write_text("ok\n", encoding="utf-8")
    cases = {
        "rm -rf /": "block", "rm -rf *": "block", "rm -rf node_modules": "block",
        "rm -rf ~user/file.txt": "block",
        "rm -rf C:foo.txt": "block",
        "rm -rf $HOME/file.txt": "block", "rm -rf ${HOME}/file.txt": "block",
        "rm -rf ./tmp/[ab].txt": "block", "rm -rf ./tmp/{a,b}.txt": "block",
        "rm test.py": "allow", "rm -rf ./tmp/x.txt": "allow",
        "Remove-Item -Recurse -Force node_modules": "block", "Remove-Item -Re -Force node_modules": "block",
        "Remove-Item -Path node_modules -Re -Force": "block",
        "Remove-Item -Recurse -Force $env:USERPROFILE": "block",
        "Remove-Item -Path ./tmp/x.txt,C:/Users/rotat -Force": "block", "Remove-Item -Path:./tmp/x.txt,/ -Force": "block",
        "Remove-Item -Path:C:/Users/rotat -Recurse -Force": "block",
        "Remove-Item -Path:C:foo.txt -Force": "block",
        'Remove-Item -Path:"C:/Users/rotat/file.txt" -Force': "block",
        "Remove-Item -LiteralPath:'C:/Users/rotat/file.txt' -Force": "block",
        "Remove-Item '": "block", "Remove-Item -Recurse -Force ./tmp/x.txt": "allow",
    }
    for command, expected in cases.items():
        result = evaluate_pretool_contract({"project_root": str(tmp_path), "tool_name": "Bash", "command": command})

        assert result["decision"] == expected, command


def test_classify_enumeration_is_multi_story_but_progressive_is_not() -> None:
    # 회귀 고정: "하고" 제거(발견B) 후에도 쉼표 열거형은 다중 스토리로 잡혀야 한다 (v2 러너 P-N2 프로브)
    enumeration = classify_prompt(
        {"prompt": "사용자 프로필 페이지를 만들고, API 연동도 하고, 데이터베이스 스키마도 업데이트해줘."}
    )
    assert enumeration["needs_goals"] is True

    # 진행형("~하고 있어")은 단일 수정 요청 — 다중 스토리 오분류 금지 (발견B)
    progressive = classify_prompt({"prompt": "add 함수가 뺄셈을 하고 있어 고쳐줘"})
    assert progressive["needs_goals"] is False
