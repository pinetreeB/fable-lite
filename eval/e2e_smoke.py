"""show-me-the-work E2E 스모크 — 실제 Claude Code 훅 스키마 페이로드로 훅 체인을 구동해
   AC를 종단 검증한다. 실제 CC 설치 없이 훅 command를 그대로 실행한다.

   실행: python eval/e2e_smoke.py   (repo 루트에서)
   종료코드 0 = 전부 통과.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import TypeAlias

reconfigure = getattr(sys.stdout, "reconfigure", None)
if callable(reconfigure):
    reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADAPT = ROOT / "adapters" / "claude_code"

JsonScalar: TypeAlias = str | int | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
results: list[tuple[bool, str]] = []


def run_hook(script: str, payload: JsonObject) -> JsonObject:
    proc = subprocess.run(
        [sys.executable, str(ADAPT / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    out = proc.stdout.strip()
    try:
        return json.loads(out) if out else {}
    except json.JSONDecodeError:
        return {"_raw": out, "_returncode": proc.returncode}


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((cond, f"{name}: {'PASS' if cond else 'FAIL'} {detail}"))


def make_transcript(proj: str, assistant_text: str) -> str:
    """assistant 레코드 1개를 담은 JSONL transcript를 만들어 경로 반환."""
    project = pathlib.Path(proj)
    tp = str(project.parent / f"{project.name}-transcript.jsonl")
    rec = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": assistant_text}]}}
    pathlib.Path(tp).write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    return tp


def main() -> int:
    proj = tempfile.mkdtemp()

    # E2E-1: 한국어 디버그 라우팅 + N1 배선 (AC5·AC3·AC10)
    r = run_hook("user_prompt_submit.py", {"hook_event_name": "UserPromptSubmit", "cwd": proj, "prompt": "왜 로그인이 안 돼"})
    ctx = json.dumps(r, ensure_ascii=False)
    check("AC5 한국어 디버그 라우팅", "가설" in ctx and "조사 팩" in ctx, "(investigation 팩 주입)")
    check("AC10 한국어 메시지", "show-me-the-work 활성화" in ctx)
    led = os.path.join(proj, ".fable-lite", "ledger.json")
    check("AC3 N1 배선(ledger 플래그)", os.path.exists(led) and json.loads(pathlib.Path(led).read_text(encoding="utf-8")).get("requires_investigation_compliance") is True)

    # E2E-2: N1 미준수 → Stop 차단, 준수 → 통과 (AC3)
    # v1.1.3: N1 마커는 파일 변경이 있는 턴에만 요구 — 변경 이벤트를 먼저 기록한다.
    pathlib.Path(proj, "app.py").write_text("n1 probe\n", encoding="utf-8")
    _ = run_hook("post_tool_use.py", {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": proj,
                                      "tool_input": {"file_path": "app.py"},
                                      "tool_response": {"filePath": "app.py", "success": True}})
    tp_bad = make_transcript(proj, "그냥 이거 고치면 됩니다.")
    r = run_hook("stop.py", {"hook_event_name": "Stop", "cwd": proj, "transcript_path": tp_bad})
    check("AC3 N1 미준수 Stop 차단", r.get("decision") == "block", f"({r.get('decision')})")
    # 준수 케이스는 정상 완료 흐름(수정→검증 성공→마커 보고)이어야 Stop 검증 게이트도 함께 통과한다.
    _ = run_hook("pre_tool_use.py", {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": proj,
                                     "tool_use_id": "e2e-verification",
                                     "tool_input": {"command": "python -m pytest"}})
    _ = run_hook("post_tool_use.py", {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": proj,
                                      "tool_use_id": "e2e-verification",
                                      "tool_input": {"command": "python -m pytest"},
                                      "tool_response": {"stdout": "3 passed", "exit_code": 0}})
    tp_ok = make_transcript(proj, "가설 1: A\n가설 2: B\n가설 3: C\n증거: x.py:1 관측\n기각: 가설 2 — 반증됨")
    r2 = run_hook("stop.py", {"hook_event_name": "Stop", "cwd": proj, "transcript_path": tp_ok, "stop_hook_active": True})
    check("AC3 N1 준수 시 통과", r2.get("decision") != "block")

    # E2E-3: R1 high-risk spec-before-edit 차단 (AC8)
    sqlp = os.path.join(proj, "migrations", "001.sql")
    os.makedirs(os.path.dirname(sqlp), exist_ok=True)
    r = run_hook("pre_tool_use.py", {"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": proj,
                                     "tool_input": {"file_path": sqlp, "new_string": "DROP TABLE users;"}})
    check("AC8 R1 spec 없는 high-risk 차단", r.get("decision") == "block", f"({r.get('decision')})")

    # E2E-3b: R1이 Bash 명령도 검사 (H3)
    r = run_hook("pre_tool_use.py", {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": proj,
                                     "tool_input": {"command": "psql -c 'DROP TABLE users'"}})
    check("H3 R1 Bash 명령 검사", r.get("decision") == "block", f"({r.get('decision')})")

    # E2E-4: N3 범위 이탈 경고 (AC4) — 요청은 auth.py인데 README를 수정
    run_hook("user_prompt_submit.py", {"hook_event_name": "UserPromptSubmit", "cwd": proj, "prompt": "auth.py 로그인 버그 고쳐줘"})
    rdm = os.path.join(proj, "README.md")
    pathlib.Path(rdm).write_text("x\n", encoding="utf-8")
    r = run_hook("post_tool_use.py", {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": proj,
                                      "tool_input": {"file_path": rdm, "new_string": "y"},
                                      "tool_response": {"filePath": rdm, "success": True}})
    message = r.get("systemMessage")
    detail = message[:30] if isinstance(message, str) else ""
    check("AC4 N3 범위 이탈 경고", "범위 이탈" in json.dumps(r, ensure_ascii=False), f"({detail})")

    # E2E-5: PostToolUse ledger 기록 — 정상 범위(파일명 요청) + 파일 기반 검증 (AC1)
    run_hook("user_prompt_submit.py", {"hook_event_name": "UserPromptSubmit", "cwd": proj, "prompt": "app.py 파일을 수정해줘"})
    setp = os.path.join(proj, "app.py")
    pathlib.Path(setp).write_text("x=1\n", encoding="utf-8")
    run_hook("post_tool_use.py", {"hook_event_name": "PostToolUse", "tool_name": "Edit", "cwd": proj,
                                  "tool_input": {"file_path": setp, "new_string": "x=2"},
                                  "tool_response": {"filePath": setp, "success": True}})
    led_data = json.loads(pathlib.Path(led).read_text(encoding="utf-8")) if os.path.exists(led) else {}
    seen = led_data.get("changed_files_seen", [])
    check("AC1 ledger 변경 기록(파일 검증)", any("app.py" in str(s) for s in seen), f"(seen={len(seen)}건)")

    # E2E-5: fail-open — 깨진 JSON에도 세션 생존 (AC9)
    for script in ("user_prompt_submit.py", "pre_tool_use.py", "post_tool_use.py", "stop.py"):
        proc = subprocess.run([sys.executable, str(ADAPT / script)], input="{ broken", capture_output=True, text=True, encoding="utf-8")
        ok = proc.returncode == 0 and "fail-open" in proc.stdout
        check(f"AC9 fail-open [{script}]", ok, f"(rc={proc.returncode})")

    # 발견A 회귀: Stop allow 경로는 additionalContext를 채우지 않는다 (반복 재호출 유발 방지)
    tp_ok2 = make_transcript(proj, "가설 1: A\n가설 2: B\n가설 3: C\n증거: x.py:1 관측\n기각: 가설 2 — 반증됨")
    r = run_hook("stop.py", {"hook_event_name": "Stop", "cwd": proj, "transcript_path": tp_ok2, "stop_hook_active": True})
    hso = r.get("hookSpecificOutput", {})
    check("발견A Stop allow는 additionalContext 없음", r.get("decision") != "block" and not (isinstance(hso, dict) and hso.get("additionalContext")), f"(hso={hso})")

    # 발견B 회귀: 단일 버그 수정("~하고 있어")은 다중 스토리로 오분류되지 않는다
    r = run_hook("user_prompt_submit.py", {"hook_event_name": "UserPromptSubmit", "cwd": proj, "prompt": "add 함수가 뺄셈을 하고 있어 고쳐줘"})
    check("발견B '하고' 단일수정 오분류 없음", "goals 체크포인트" not in json.dumps(r, ensure_ascii=False), "(needs_goals 미발동)")

    # E2E-6: .fable-lite/ 단일 상태 디렉토리 (아키텍처 계약)
    strays = [p for p in ("ledger.json", "goals.json", "contract.json") if os.path.exists(os.path.join(proj, p))]
    check("상태파일 .fable-lite/ 격리", not strays, f"(루트 잔존: {strays})")

    import shutil
    shutil.rmtree(proj, ignore_errors=True)

    passed = sum(1 for ok, _ in results if ok)
    print(f"\n=== show-me-the-work E2E 스모크: {passed}/{len(results)} ===")
    for ok, line in results:
        print(("  [OK] " if ok else "  [XX] ") + line)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
