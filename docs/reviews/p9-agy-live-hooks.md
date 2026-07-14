# P9 · agy(Antigravity) 실호스트 라이브 발동 조사

> **⭐ 2026-07-15 재판정: agy 1.1.2에서 훅 발동 성공 — 아래 "발동 불가" 판정 뒤집힘(§재판정 참조).**
> 근본원인은 agy 엔진이 아니라 **hooks.json의 `command`가 상대경로(`python ...`)였던 것**. 절대경로(`C:/Python312/python.exe ...`)로 바꾸자 PreToolUse/PostToolUse/Stop 전부 실발동. 이하 원문(1.1.1 미발동 실측)은 이력으로 보존.

---

## 재판정 (2026-07-15, agy 1.1.2)

**결론: agy 1.1.2는 훅을 실제로 실행한다. 1.1.1 "엔진 미발동" 판정은 (a)1.1.2의 훅 로드/동기화 버그 수정 + (b)command 절대경로 요건 미충족의 복합이었다.**

### 근본원인 (실측 확정)
- **command 절대경로 필수**: `command: "python C:/.../probe.py ..."`(상대 `python`)는 미발동, `command: "C:/Python312/python.exe C:/.../probe.py ..."`(절대)는 즉시 발동. medium 가이드("The `command` parameter must use an absolute path. 스크립트는 exit 0 필수")와 일치. 상대 `python`은 훅 실행 컨텍스트에서 PATH 미해석 → exit 127 조용한 실패.
- **1.1.1→1.1.2 훅 버그 수정 기여**: 웹 검색 CHANGELOG — "workspace-local `.agents/hooks.json` not loading after trusting folder 수정(workspace 변경 시 reload)" + "`/hooks`가 잘못된 디렉토리에 쓰던 버그 수정(TUI-백엔드 동기화)". 1.1.1의 UI "No hooks configured"가 1.1.2에선 훅 3종 정상 표시로 개선됨(로드 성공).

### 실물 사양 (어댑터 개편 확정 자료)
- **이벤트 6종**: `PreToolUse` / `PostToolUse` / `PreInvocation` / `PostInvocation` / `Stop`(+`SessionStart`). 실발동 확인=PreToolUse·PostToolUse·Stop.
- **설정 경로(전부 유효)**: workspace `<root>/.agents/hooks.json`(국소) · 글로벌 `~/.gemini/config/hooks.json` · `~/.gemini/antigravity-cli/hooks.json`.
- **스키마**: `{ "<그룹명>": { "<이벤트>": [ { "matcher": "<정규식 or 빈문자열>", "hooks": [ { "type": "command", "command": "<절대경로>", "timeout": N } ] } ] } }`. matcher는 tool 이름과 정규식 매칭(`""`·`"*"`=catch-all).
- **payload(stdin JSON)**: `{conversationId, modelName, stepIdx, artifactDirectoryPath, transcriptPath, workspacePaths[], toolCall:{name, args}}`. PreToolUse엔 `toolCall` 존재, PostToolUse엔 `error` 필드(+toolCall null 스텝 다수). 어댑터의 (구)BeforeModel/BeforeTool/AfterTool/AfterAgent 스키마와 불일치.
- **실물 도구명**(Claude Code와 다름 — 매핑 필요): `view_file`(읽기)·`write_to_file`(쓰기)·`run_command`(셸)·`manage_task`·`call_mcp_tool` 등.
- **⚠️ fail-open 필수**: PreToolUse가 exit≠0이면 도구가 fail-closed 차단됨(웹 검색 cmux issue #4768 "PreToolUse hook fails closed... blocking all agy tool calls"). 어댑터는 어떤 경로에서도 exit 0 보장 필요.

### 재판정 매트릭스 (2026-07-15)
| # | command | 이벤트 | 결과 |
|---|---------|--------|------|
| R1 | 상대 `python ...` (자동승인 모드) | PreToolUse/PostToolUse | 미발동 (도구는 실행됨) |
| R2 | 상대 `python ...` (quit) | Stop | 미발동 |
| R3 | 상대 `python ...` (일반 모드) | PreToolUse/PostToolUse | 미발동 |
| R4 | **절대 `C:/Python312/python.exe ...`** | PreToolUse/PostToolUse | **발동** (view_file·write_to_file·run_command 등 다수) |
- probe.py 자체는 정상(수동 실행 시 파일 생성 확인) — R1~R3 미발동은 스크립트 결함 아닌 command PATH 문제.
- 부수 관측: 글로벌 훅은 **모든 열린 agy 세션에 즉시 적용**(다른 워크스페이스 material-erp 세션의 도구 호출까지 발동 → 실사용 오염). 글로벌 훅은 격리 테스트에 부적합, workspace `.agents/`가 안전. 훅 파일 삭제 시 매 실행 재평가라 즉시 반영(발동 즉시 중단 실측).

### 후속 (어댑터 개편 착수 가능)
1. `adapters/antigravity/hooks.json` 이벤트명을 실물 6종으로 개편(BeforeModel→PreInvocation, BeforeTool→PreToolUse, AfterTool→PostToolUse, AfterAgent→Stop) + command 절대경로 + oma_hook.py payload 파서를 실물 스키마로.
2. 실물 도구명 매핑(view_file/write_to_file/run_command → 코어의 read/edit/shell 판정).
3. fail-open 하드 보장(PreToolUse exit 0).
4. 라이브 E2E: workspace `.agents/hooks.json`로 격리 프로젝트에서 N1/Stop 실차단·회복 실관측.

---

## [이력] 발동 불가 실측 (2026-07-12, agy 1.1.1)

> v1.2 Evidence Integrity P0-1의 "라이브 E2E" 수용기준을 실호스트에서 검증하는 과정에서,
> OmA 어댑터가 당시 Antigravity CLI 1.1.1에서 **발동 자체가 불가능한 상태**로 6회 실측했다.
> (조사 방법: 격리 프로젝트 `tmp/agy-live` + 실패→수정→통과 시나리오 반복)
> ⚠️ 2026-07-15 재판정으로 원인이 **command 상대경로 + 1.1.1 훅 로드 버그**로 규명됨 — "엔진 미발동" 결론은 폐기.

## 결론 (요약)

1. **fable-lite OmA 어댑터(v5 실장)는 실호스트에서 한 번도 발동한 적이 없다.**
   어댑터 자체 로직은 subprocess 주입 테스트로 검증되어 있으나(P4 수정 라운드 포함),
   실제 agy 1.1.1 호스트는 어댑터가 등록을 시도하는 어떤 경로에서도 훅을 실행하지 않았다.
2. 불일치는 3중이다:
   - **이벤트명 불일치**: 어댑터·hooks.json 템플릿은 `BeforeModel/BeforeTool/AfterTool/AfterAgent`를 쓰지만,
     agy 1.1.1 실물(`/hooks` UI)의 이벤트는 `SessionStart/PreInvocation/PostInvocation/PreToolUse/PostToolUse/Stop` 6종(Claude Code 계열)이다.
   - **설치 경로 불일치**: INSTALL.ko.md가 안내한 `.gemini/hooks.json`(project-local)은 로드되지 않는다.
     실물 저장 위치는 글로벌 `~/.gemini/config/hooks.json`(`/hooks` UI로 등록 시 생성, 최상위 키=그룹명)이다.
   - **엔진 미발동**: 실물 위치·실물 이벤트·실물 스키마(UI 등록)로 걸어도 훅 프로세스가 실행되지 않았다.

## 실측 매트릭스 (6회 전부 미발동)

| # | 설치 위치 | 스키마/이벤트 | 세션 재시작 | 결과 |
|---|-----------|--------------|------------|------|
| 1 | `.gemini/hooks.json` (INSTALL.ko.md 방식) | flat + BeforeModel계 | O | 미발동 (`.fable-lite/` 미생성) |
| 2 | `.gemini/settings.json` `hooks` 키 (Gemini CLI 문서 방식) | matcher 중첩 + BeforeModel계 | O | 미발동 |
| 3 | `.agents/hooks.json` (바이너리 문자열 "customization root") | OmA 실물형 중첩 | O | 미발동 |
| 4 | `.agents/plugins/fable-lite/hooks.json` (바이너리 문자열 "plugins/<name>/hooks.json") | OmA 실물형 중첩 | O | 미발동 |
| 5 | 글로벌 `~/.gemini/config/hooks.json` — **/hooks UI로 직접 등록** (PreToolUse, timeout 30) | 실물 스키마 (UI 생성) | O | 미발동 |
| 6 | 5 + `matcher: "*"` | 실물 스키마 | O | 미발동 |

- 매 회차 agy는 과제(실패 pytest 재현→수정→통과)를 정상 수행 — 즉 도구 실행(run_shell_command·Edit)은 매회 발생했다.
- 프로브 훅(stdin을 파일로 덤프하는 python 원라이너)의 출력 파일이 한 번도 생성되지 않음 = 훅 프로세스 실행 0회.
- 참고: 글로벌 OmA 플러그인의 `~/.gemini/config/plugins/oh-my-antigravity/hooks.json`(BeforeModel/AfterAgent)도
  `/hooks` UI 목록에 나타나지 않았다("No hooks configured") — OmA 훅도 같은 이유로 죽어 있을 개연성이 높다.

## 실물 정보 (향후 어댑터 개편 자료)

- **이벤트 6종**: `SessionStart` / `PreInvocation` / `PostInvocation` / `PreToolUse` / `PostToolUse` / `Stop` — fable-lite 매핑 자연안: PreInvocation→(구)BeforeModel, PreToolUse→BeforeTool, PostToolUse→AfterTool, Stop→AfterAgent.
- **설정 파일**: `~/.gemini/config/hooks.json` — `{ "<그룹명>": { "<이벤트>": [ { "matcher": "", "hooks": [ { "type": "command", "command": "...", "timeout": N } ] } ] , ... } }` (미사용 이벤트는 `null`).
- **UI**: `/hooks` → 이벤트 선택 → matcher/훅 추가·토글·삭제. "Hooks run shell commands with your full permissions" 경고 표시.
- 프로젝트 로컬 커스터마이징 루트는 `.agents/`(skills·rules·plugins 문자열 실재)이나, hooks가 여기서 로드되는지는 실측상 부정적.

## 판정과 후속

- **가설 1**: 설치 경로만 틀렸고 올바른 경로면 발동한다 → **기각** (실물 경로·UI 등록으로도 미발동, #5·#6).
- **가설 2**: 이벤트명만 맞추면 발동한다 → **기각** (실물 이벤트 PreToolUse로 등록해도 미발동, #5·#6).
- **가설 3**: agy 1.1.1 훅 엔진이 이 환경(Windows·자동승인 모드)에서 실행되지 않는다 → **채택** (잔여 반증 없음. 단 다른 OS/모드/후속 버전에서 재검 필요).
- **증거**: 프로브 출력 0회 + 매회 도구 실행 확인 + `.fable-lite/` 미생성 (본 문서 매트릭스).
- **후속 (v1.2에 반영)**:
  1. INSTALL.ko.md에 "실호스트 발동 미확인 (agy 1.1.1 실측)" 경고와 본 문서 링크를 명시 — 검증되지 않은 설치 안내를 사실처럼 두지 않는다 (Evidence Integrity).
  2. 어댑터·템플릿의 이벤트명 개편(실물 6종 매핑)은 **agy 훅 엔진이 실제로 발동하는 버전이 확인된 뒤** 진행 (지금 개편해도 검증 불가).
  3. agy 신버전 릴리스 시 본 문서의 매트릭스 #5(UI 등록 + 재시작)만 재실행하면 엔진 활성화 여부를 3분 안에 재판정 가능.
