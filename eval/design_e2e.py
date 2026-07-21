"""디자인 게이트 라이브 E2E — 실제 Claude Code 훅 스키마 페이로드로 훅 체인을 구동해
   디자인 게이트가 **발동·차단·회복**하는지 종단 검증한다. 실제 CC 설치 없이 훅 command를
   그대로(user_prompt_submit / post_tool_use / pre_tool_use / stop) 실행한다. eval/e2e_smoke.py
   와 동일한 `run_hook(subprocess+CC 스키마)` 방식을 따른다.

   토글 ON = 프로젝트 `design/gate.config`의 `enabled: true` 또는 환경변수 `SMTW_DESIGN_GATE=1`.
   시나리오(ON, config 기반 발동 흐름):
     S1  UI 프롬프트(.css)        → classify domain=UI → ledger.design_required=true + design-review 팩
     S2  UI 파일 raw-hex Write    → PostToolUse provenance 관측 → ledger.design_touched=true
     S3  렌더검증(통과) + design_lint(미통과) → Stop 훅이 디자인 규칙 위반으로 차단(design block)
     S4  토큰(var()) 수정 + 렌더검증 + design_lint 통과 → Stop 통과(회복, fail-open 아님)
   토글 OFF(env 없음·config 없음):
     S5  위 전 과정 무발동 — 변경은 관측되나 design_* 미기록·design_touched 미발동·Stop 디자인 차단 없음
   토글 메커니즘 교차:
     S6  env-only ON 발동 · project config(false) 가 env(1) 을 이기는 우선순위
   안티게이밍·라이브니스(별도 blocked 턴 — MAX_DESIGN_BLOCKS=2 라 회복 arc 와 카운터 분리):
     S7  통과한 design_lint 가 후속 UI 편집으로 신선도 무효화(check_seq<change_seq) → 재차단(안티게이밍)
     S8  2회 차단(상한) 도달 → 다음 Stop 은 cap-allow(라이브니스, 무한잠금 방지)

   설계 노트(committed 구현 기준): Stop 디자인 차단은 ordinary 검증 게이트가 통과(또는 자체 2회 상한
   cap-allow)한 뒤에만 표면화된다(evaluate_stop 순서). 그래서 S3/S4 모두 렌더검증(공용 verification
   게이트=layer B)을 기록해 ordinary 게이트를 통과시키고, design_lint(layer A) 통과 여부로 차단/회복을
   가른다. committed evaluate_design_stop 은 layer A(design_lint 통과 AND check_seq>change_seq 신선도,
   상한 2회)만 직접 판정하며, layer B(렌더 tool 관측)는 별도 판정이 아니라 공용 verification 게이트로
   강제된다. allow/cap-allow 는 stop.py 가 `decision` 키를 안 내므로, 크래시성 fail-open 을 통과로
   오판하지 않도록 systemMessage 에 'fail-open' 부재를 함께 확인한다.

   실행: python eval/design_e2e.py   (repo 루트에서)
   종료코드 0 = 전부 통과.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import TypeAlias

reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(reconfigure):
    reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADAPT = ROOT / "adapters" / "claude_code"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ledger_storage import ledger_path  # noqa: E402
from core.runtime_env import DESIGN_GATE, canonical_env_key  # noqa: E402

DESIGN_ENV = canonical_env_key(DESIGN_GATE)

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
results: list[tuple[bool, str]] = []

UI_PROMPT = "src/App.css UI 화면을 디자인 수정해줘"
# raw hex 하드코딩(design/raw-color 위반) → 토큰(var()) 참조로 교정
BROKEN_CSS = ".hero {\n  color: #ff0000;\n}\n"
TOKEN_CSS = ".hero {\n  color: var(--brand);\n}\n"
# 렌더 검증 tool 대역: is_verification_command 이 스크립트 재실행으로 인정하는 명령
RENDER_CMD = "node playwright-render-check.mjs"


def _hook_env(design_on: bool) -> dict[str, str]:
    """훅 서브프로세스 env — 주변 env 누수를 막기 위해 토글을 항상 명시 제어한다."""
    env = dict(os.environ)
    env.pop(DESIGN_ENV, None)
    env["PYTHONIOENCODING"] = "utf-8"
    if design_on:
        env[DESIGN_ENV] = "1"
    return env


def run_hook(script: str, payload: JsonObject, *, cwd: str, design_on: bool = False) -> JsonObject:
    """실제 CC 훅 command 를 그대로 실행한다. cwd=프로젝트(실 CC 와 동일 — classify 가 Path.cwd() 사용)."""
    proc = subprocess.run(
        [sys.executable, str(ADAPT / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=_hook_env(design_on),
    )
    out = proc.stdout.strip()
    try:
        parsed = json.loads(out) if out else {}
    except json.JSONDecodeError:
        return {"_raw": out, "_returncode": proc.returncode}
    return parsed if isinstance(parsed, dict) else {"_raw": out}


def run_design_check(proj: str) -> tuple[int, JsonObject]:
    """fable_lite check --root <proj> --design (layer A 정적 린트 + Stop 재사용용 결과 기록)."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])
    proc = subprocess.run(
        [sys.executable, "-m", "fable_lite", "check", "--root", proj, "--design"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"_raw": proc.stdout, "_stderr": proc.stderr}
    return proc.returncode, payload if isinstance(payload, dict) else {"_raw": proc.stdout}


def check(name: str, cond: object, detail: str = "") -> None:
    results.append((bool(cond), f"{name}: {'PASS' if cond else 'FAIL'} {detail}"))


def rmtree(path: str) -> None:
    """Windows 견고 삭제 — git 이 만든 읽기전용 .git/objects 를 chmod 후 재삭제한다."""
    def _handler(func, target, _exc):  # type: ignore[no-untyped-def]
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass
    try:
        shutil.rmtree(path, onexc=_handler)      # Python 3.12+
    except TypeError:
        shutil.rmtree(path, onerror=_handler)    # Python <3.12


def ledger_of(proj: str) -> JsonObject:
    path = ledger_path(proj)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _violations(payload: JsonObject) -> list[JsonObject]:
    raw = payload.get("violations")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def git(proj: str, *args: str) -> None:
    subprocess.run(["git", "-C", proj, *args], check=False, capture_output=True, text=True, encoding="utf-8", errors="replace")


def init_repo(proj: str) -> None:
    git(proj, "init")
    git(proj, "config", "user.email", "e2e@example.com")
    git(proj, "config", "user.name", "design-e2e")
    pathlib.Path(proj, "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    git(proj, "add", ".")
    git(proj, "commit", "-m", "init")


def write_file(proj: str, rel: str, text: str) -> None:
    target = pathlib.Path(proj, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def enable_config(proj: str) -> None:
    write_file(proj, "design/gate.config", json.dumps({"enabled": True}))


def ui_write(proj: str, tool_name: str, rel: str, tool_use_id: str) -> JsonObject:
    """UI 파일 Edit/Write 를 PostToolUse 로 관측시킨다(파일은 사전에 디스크에 써 둔다).
    실제 CC 처럼 편집마다 고유 tool_use_id 를 부여한다 — 없으면 invocation_id 가 충돌해
    여러 편집-검증 사이클에서 covers/provenance 회계가 꼬인다(라이브 관측)."""
    return run_hook(
        "post_tool_use.py",
        {
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
            "cwd": proj,
            "tool_use_id": tool_use_id,
            "tool_input": {"file_path": rel},
            "tool_response": {"filePath": rel, "success": True},
        },
        cwd=proj,
    )


def render_verify(proj: str, tool_use_id: str) -> None:
    """렌더 검증 tool(playwright 대역) 호출을 Bash 검증 명령으로 pre→post 관측시킨다."""
    run_hook(
        "pre_tool_use.py",
        {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": proj,
         "tool_use_id": tool_use_id, "tool_input": {"command": RENDER_CMD}},
        cwd=proj,
    )
    run_hook(
        "post_tool_use.py",
        {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": proj,
         "tool_use_id": tool_use_id, "tool_input": {"command": RENDER_CMD},
         "tool_response": {"stdout": "1 passed", "exit_code": 0}},
        cwd=proj,
    )


def stop(proj: str) -> JsonObject:
    return run_hook("stop.py", {"hook_event_name": "Stop", "cwd": proj}, cwd=proj)


def scenario_on_arc() -> None:
    """S1~S4: 토글 ON(project config) 에서 발동 → 차단 → 회복."""
    proj = tempfile.mkdtemp(prefix="design-e2e-on-")
    try:
        init_repo(proj)
        enable_config(proj)  # 토글 ON: config.enabled=true (env 무관)

        # --- S1: UI 프롬프트 → design_required + design-review 팩 -------------------
        run_hook("user_prompt_submit.py",
                 {"hook_event_name": "UserPromptSubmit", "cwd": proj, "prompt": UI_PROMPT}, cwd=proj)
        led = ledger_of(proj)
        check("S1 UI 프롬프트 → design_required 기록", led.get("design_required") is True,
              f"(design_required={led.get('design_required')})")
        packs = led.get("packs") if isinstance(led.get("packs"), list) else []
        check("S1 design-review 팩 주입", "design-review" in packs, f"(packs={packs})")

        # --- S2: UI 파일 raw-hex Write → design_touched --------------------------
        write_file(proj, "src/App.css", BROKEN_CSS)
        ui_write(proj, "Write", "src/App.css", "s2-write")
        led = ledger_of(proj)
        check("S2 UI Write → design_touched 기록", led.get("design_touched") is True,
              f"(design_touched={led.get('design_touched')})")
        seen = led.get("changed_files_seen") if isinstance(led.get("changed_files_seen"), list) else []
        check("S2 UI 변경 원장 기록", any("App.css" in str(s) for s in seen), f"(seen={seen})")

        # --- S3: 렌더검증(통과) + design_lint(미통과) → Stop design block ----------
        render_verify(proj, "s3-render")  # ordinary 검증 게이트 충족(layer B)
        rc, dpayload = run_design_check(proj)  # layer A 정적 린트: raw-color 검출
        check("S3 design_lint 미통과(rc=1)", rc == 1, f"(rc={rc})")
        viols = _violations(dpayload)
        check("S3 raw-color 위반 검출", any(v.get("rule_id") == "design/raw-color" for v in viols),
              f"(violations={viols})")
        led = ledger_of(proj)
        # 비-공허: design_check_seq 는 record_design_result 실행 전엔 0(초기화). 실행 후에만
        # change_seq 를 초과 → 실패한 린트가 원장에 실제 기록됐음을 증명(단순 재확인 아님).
        check("S3 design_lint 결과 원장 기록(seq 갱신)",
              isinstance(led.get("design_check_seq"), int)
              and int(led.get("design_check_seq") or 0) > int(led.get("design_last_change_seq") or 0)
              and led.get("design_check_passed") is False,
              f"(check_seq={led.get('design_check_seq')}, change_seq={led.get('design_last_change_seq')}, passed={led.get('design_check_passed')})")
        rs = stop(proj)
        reason = rs.get("reason") if isinstance(rs.get("reason"), str) else ""
        check("S3 Stop 디자인 차단", rs.get("decision") == "block", f"(decision={rs.get('decision')})")
        check("S3 차단 사유=디자인 규칙 위반",
              "디자인 규칙 위반" in reason and "design/raw-color" in reason, f"(reason={reason[:90]})")
        led = ledger_of(proj)
        check("S3 design_blocks 독립 카운터 증가", led.get("design_blocks") == 1,
              f"(design_blocks={led.get('design_blocks')})")

        # --- S4: 토큰 수정 + 렌더검증 + design_lint 통과 → Stop 통과(회복) ---------
        write_file(proj, "src/App.css", TOKEN_CSS)
        ui_write(proj, "Edit", "src/App.css", "s4-edit")  # 재변경 → design_check 무효화
        render_verify(proj, "s4-render")       # 재검증(layer B)
        rc2, dpayload2 = run_design_check(proj)  # layer A: 이제 통과
        check("S4 design_lint 통과(rc=0)", rc2 == 0, f"(rc={rc2})")
        check("S4 위반 없음", _violations(dpayload2) == [], f"(violations={_violations(dpayload2)})")
        led = ledger_of(proj)
        check("S4 design_check_passed=True 기록", led.get("design_check_passed") is True,
              f"(design_check_passed={led.get('design_check_passed')})")
        rs2 = stop(proj)
        # allow/cap-allow 는 decision 키가 없다 → fail-open(크래시) 을 통과로 오판하지 않도록
        # systemMessage 존재 + 'fail-open' 부재를 함께 확인(양성 신호).
        msg2 = rs2.get("systemMessage") if isinstance(rs2.get("systemMessage"), str) else ""
        check("S4 Stop 통과(회복, fail-open 아님)",
              rs2.get("decision") != "block" and bool(msg2) and "fail-open" not in msg2,
              f"(decision={rs2.get('decision')}, systemMessage={msg2[:70]})")
        led = ledger_of(proj)
        check("S4 신규 차단 없음(design_blocks 유지=1)", led.get("design_blocks") == 1,
              f"(design_blocks={led.get('design_blocks')})")
    finally:
        rmtree(proj)


def scenario_off() -> None:
    """S5: 토글 OFF(env 없음·config 없음) → 전 과정 무발동."""
    proj = tempfile.mkdtemp(prefix="design-e2e-off-")
    try:
        init_repo(proj)  # config 미작성 + design_on=False(env 격리)

        run_hook("user_prompt_submit.py",
                 {"hook_event_name": "UserPromptSubmit", "cwd": proj, "prompt": UI_PROMPT}, cwd=proj)
        led = ledger_of(proj)
        check("S5 OFF design_required 미기록", led.get("design_required") is not True,
              f"(design_required={led.get('design_required')})")
        design_keys = [k for k in led if str(k).startswith("design_")]
        check("S5 OFF design_* 필드 부재", design_keys == [], f"(design_keys={design_keys})")

        write_file(proj, "src/App.css", BROKEN_CSS)
        ui_write(proj, "Write", "src/App.css", "s5-write")
        led = ledger_of(proj)
        # 양성대조: 관측 파이프라인(토글 무관)은 변경을 기록한다 → 아래 design_touched 부재가
        # "게이트가 관측된 변경을 억제"임을 뜻하게 만든다("아무 일도 안 일어남"과 구별).
        seen = led.get("changed_files_seen") if isinstance(led.get("changed_files_seen"), list) else []
        check("S5 OFF 변경 관측됨(파이프라인 가동 양성대조)",
              any("App.css" in str(s) for s in seen), f"(seen={seen})")
        check("S5 OFF 관측에도 design_touched 미발동", led.get("design_touched") is not True,
              f"(design_touched={led.get('design_touched')})")

        render_verify(proj, "s5-render")
        rs = stop(proj)
        reason = str(rs.get("reason", ""))
        msg = rs.get("systemMessage") if isinstance(rs.get("systemMessage"), str) else ""
        check("S5 OFF Stop 정상 통과(디자인 차단·fail-open 아님)",
              not (rs.get("decision") == "block" and "디자인" in reason)
              and bool(msg) and "fail-open" not in msg,
              f"(decision={rs.get('decision')}, systemMessage={msg[:70]})")
    finally:
        rmtree(proj)


def scenario_toggles() -> None:
    """S6: 토글 메커니즘 교차 — env-only ON 발동, config(false) > env(1) 우선순위."""
    # env-only ON (config 없음)
    proj = tempfile.mkdtemp(prefix="design-e2e-env-")
    try:
        init_repo(proj)
        run_hook("user_prompt_submit.py",
                 {"hook_event_name": "UserPromptSubmit", "cwd": proj, "prompt": UI_PROMPT},
                 cwd=proj, design_on=True)  # SMTW_DESIGN_GATE=1
        led = ledger_of(proj)
        check("S6 env-only ON → design_required", led.get("design_required") is True,
              f"(design_required={led.get('design_required')})")
    finally:
        rmtree(proj)

    # project config(false) 가 env(1) 을 이긴다(우선순위: config > env)
    proj2 = tempfile.mkdtemp(prefix="design-e2e-cfg-")
    try:
        init_repo(proj2)
        write_file(proj2, "design/gate.config", json.dumps({"enabled": False}))
        run_hook("user_prompt_submit.py",
                 {"hook_event_name": "UserPromptSubmit", "cwd": proj2, "prompt": UI_PROMPT},
                 cwd=proj2, design_on=True)  # env=1 이지만 config false 가 우선
        led2 = ledger_of(proj2)
        check("S6 config(false) > env(1) 우선순위", led2.get("design_required") is not True,
              f"(design_required={led2.get('design_required')})")
    finally:
        rmtree(proj2)


def scenario_freshness_and_liveness() -> None:
    """S7~S8: 안티게이밍(통과한 lint 의 신선도 무효화)과 라이브니스(2회 상한 후 cap-allow).
    둘 다 한 blocked 턴 안에서 검증한다 — Stop-allow 후 편집(비현실적 CC 흐름)을 쓰지 않는다.
    MAX_DESIGN_BLOCKS=2 라 한 턴에서 최대 2회 차단 → 회복 arc(S4)와 카운터가 겹치지 않게 분리."""
    proj = tempfile.mkdtemp(prefix="design-e2e-fresh-")
    try:
        init_repo(proj)
        enable_config(proj)
        run_hook("user_prompt_submit.py",
                 {"hook_event_name": "UserPromptSubmit", "cwd": proj, "prompt": UI_PROMPT}, cwd=proj)

        # 1차 차단: raw hex + 렌더검증 + design_lint 미통과
        write_file(proj, "src/App.css", BROKEN_CSS)
        ui_write(proj, "Write", "src/App.css", "s7-write")
        render_verify(proj, "s7-render1")
        _ = run_design_check(proj)
        rs1 = stop(proj)
        led = ledger_of(proj)
        check("S7 1차 design 차단", rs1.get("decision") == "block" and led.get("design_blocks") == 1,
              f"(decision={rs1.get('decision')}, design_blocks={led.get('design_blocks')})")

        # 토큰 수정 + design_lint 통과(design_check_passed=True, 신선)
        write_file(proj, "src/App.css", TOKEN_CSS)
        ui_write(proj, "Edit", "src/App.css", "s7-fix")
        render_verify(proj, "s7-render2")
        rc, _ = run_design_check(proj)
        led = ledger_of(proj)
        check("S7 수정 후 design_lint 통과(신선)",
              rc == 0 and led.get("design_check_passed") is True
              and int(led.get("design_check_seq") or 0) > int(led.get("design_last_change_seq") or 0),
              f"(rc={rc}, passed={led.get('design_check_passed')}, check_seq={led.get('design_check_seq')}, change_seq={led.get('design_last_change_seq')})")

        # 안티게이밍: 재린트 없이 또 편집 → 통과 결과가 신선도 무효화되어 재차단(2차)
        write_file(proj, "src/App.css", TOKEN_CSS + ".panel {\n  color: var(--ink);\n}\n")
        ui_write(proj, "Edit", "src/App.css", "s7-stale")  # run_design_check 생략
        render_verify(proj, "s7-render3")
        led = ledger_of(proj)
        check("S7 통과결과 신선도 무효화(check_seq < change_seq)",
              led.get("design_check_passed") is not True
              and int(led.get("design_check_seq") or 0) < int(led.get("design_last_change_seq") or 0),
              f"(passed={led.get('design_check_passed')}, check_seq={led.get('design_check_seq')}, change_seq={led.get('design_last_change_seq')})")
        rs2 = stop(proj)
        reason2 = rs2.get("reason") if isinstance(rs2.get("reason"), str) else ""
        check("S7 안티게이밍 재차단(2차)",
              rs2.get("decision") == "block" and ("design_lint" in reason2 or "디자인" in reason2),
              f"(decision={rs2.get('decision')}, reason={reason2[:70]})")
        led = ledger_of(proj)
        check("S7 design_blocks 2로 증가", led.get("design_blocks") == 2,
              f"(design_blocks={led.get('design_blocks')})")

        # S8 라이브니스: 상한(2) 도달 → 다음 Stop 은 cap-allow(무한잠금 방지, fail-open 아님)
        rs3 = stop(proj)
        msg3 = rs3.get("systemMessage") if isinstance(rs3.get("systemMessage"), str) else ""
        check("S8 2회 차단 후 cap-allow(라이브니스, fail-open 아님)",
              rs3.get("decision") != "block" and bool(msg3) and "fail-open" not in msg3,
              f"(decision={rs3.get('decision')}, systemMessage={msg3[:70]})")
        led = ledger_of(proj)
        check("S8 design_blocks 상한 유지=2", led.get("design_blocks") == 2,
              f"(design_blocks={led.get('design_blocks')})")
    finally:
        rmtree(proj)


def main() -> int:
    scenario_on_arc()
    scenario_off()
    scenario_toggles()
    scenario_freshness_and_liveness()
    passed = sum(1 for ok, _ in results if ok)
    print(f"\n=== 디자인 게이트 라이브 E2E: {passed}/{len(results)} ===")
    for ok, line in results:
        print(("  [OK] " if ok else "  [XX] ") + line)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
