# show-me-the-work Antigravity 어댑터 설치 가이드

Antigravity CLI 1.1.2에서 전역 `~/.gemini/config/hooks.json`에 설치하면 6종 이벤트가 실제 발동하는 것을 확인했습니다(2026-07-16 라이브 실측: PreInvocation·PreToolUse·PostToolUse 발동 + `.fable-lite/agents/antigravity.jsonl` 기록, Stop 디스패치는 07-15 executor 로그로 확인).

⚠️ **command에 따옴표를 쓰면 훅이 전부 exit 1로 죽습니다.** Antigravity(Go)는 훅 command를 Windows에서 `cmd /c`로 실행하는데, 이때 Go의 재인용(re-quoting)이 `\"` 시퀀스를 만들어 cmd가 명령을 파싱하지 못합니다(실측 stderr: `'"C:/Python312/python.exe"'은(는) 내부 또는 외부 명령...이 아닙니다`). 겉보기엔 "로드만 되고 실행 안 됨"처럼 보이지만 실제로는 매 이벤트마다 발동→spawn 실패입니다. 상대 명령 `python ...` 역시 훅 실행 환경에서 PATH가 해석되지 않아 사용할 수 없습니다.

## 1. 절대 경로 치환 (따옴표 금지)

`adapters/antigravity/hooks.json`의 두 placeholder를 모두 **공백이 없는 실제 절대 경로**로 바꾸십시오. 따옴표는 절대 추가하지 마십시오.

- `{PYTHON_EXECUTABLE}`: Python 실행 파일의 절대 경로(예: `C:/Python312/python.exe`)
- `{SMTW_ROOT}`: show-me-the-work 저장소의 절대 경로(예: `C:/Users/rotat/show-me-the-work`)

치환 뒤 command는 다음처럼 따옴표 없이 실행 파일과 스크립트가 모두 절대 경로여야 합니다.

```text
C:/Python312/python.exe C:/Users/rotat/show-me-the-work/adapters/antigravity/oma_hook.py PreToolUse
```

경로에 공백이 포함된 환경(예: `C:/Program Files/...`)이라면 공백 없는 경로에 Python을 설치하거나, 공백 없는 경로에 wrapper `.cmd`를 만들어 그 wrapper를 command로 지정하십시오.

## 2. 설정 파일 설치

⚠️ **워크스페이스 로컬 `<project>/.agents/hooks.json`은 `/hooks` UI에 인식되지만 실제로는 발동하지 않습니다**(Antigravity 1.1.2 실측, 2026-07-15 — 도구는 실행되나 훅 프로세스가 실행되지 않음). 훅이 실제로 발동하려면 아래 전역 경로 중 하나에 설치해야 합니다.

```bash
# 예: 전역 설치
cp /absolute/path/to/show-me-the-work/adapters/antigravity/hooks.json ~/.gemini/config/hooks.json
```

- `~/.gemini/config/hooks.json`
- `~/.gemini/antigravity-cli/hooks.json`

기존 파일이 있으면 덮어쓰지 말고 최상위 `show-me-the-work` 그룹을 병합하십시오.

⚠️ **전역 훅은 열려 있는 모든 Antigravity 워크스페이스에 적용됩니다.** 특정 프로젝트에만 하네스를 적용하려면, 어댑터가 payload의 `workspacePaths`로 프로젝트를 식별하므로 훅 command에서 대상 워크스페이스만 필터링하거나 프로젝트 루트 기준으로 게이트를 조건화해 범위를 좁히십시오. (워크스페이스 로컬 `.agents/hooks.json`이 실제 발동하는 Antigravity 버전이 확인되면 프로젝트 로컬 방식으로 전환을 권장합니다.)

## 3. 등록되는 실물 이벤트

템플릿은 Antigravity 1.1.2의 다음 6종 이벤트를 등록합니다.

- `SessionStart`
- `PreInvocation`
- `PostInvocation`
- `PreToolUse`
- `PostToolUse`
- `Stop`

실물 stdin payload의 `conversationId`, `modelName`, `stepIdx`, `artifactDirectoryPath`, `transcriptPath`, `workspacePaths`, `toolCall`을 어댑터 경계에서 수용합니다. `view_file`, `write_to_file`, `run_command`는 각각 코어의 읽기, 편집, 셸 판정으로 정규화하며 `PostToolUse.error`는 실패 결과로 기록합니다.

## 4. fail-open 확인

Antigravity는 `PreToolUse` hook이 0이 아닌 exit code를 반환하면 도구 자체를 차단할 수 있습니다. 이 어댑터는 정상 판정, 차단 결정, 잘못된 JSON, 누락 payload, 알 수 없는 이벤트를 포함한 모든 실행 경로에서 프로세스 exit code 0을 반환합니다. 설치 후에는 `/hooks`에서 6종 이벤트가 보이는지와 절대 경로 치환이 남아 있지 않은지, command에 따옴표가 없는지 확인하십시오.

## 5. 발동 검증 방법

설치 뒤 진행 중인 Antigravity 세션에 도구 사용을 유발하는 메시지를 하나 보내고(훅은 세션 재시작 없이 턴마다 lazy-load됩니다) 다음을 확인하십시오.

1. `~/.gemini/antigravity-cli/log/cli-*.log`에 `Loaded hooks.json from ...config/hooks.json: 1 named hooks, 5 total handlers`가 찍히는지
2. 같은 로그에 `command_hook_executor` stderr나 `hook ... command failed` 에러가 **없는지** (있다면 대부분 따옴표/경로 문제)
3. 대상 프로젝트의 `.fable-lite/agents/antigravity.jsonl`에 `prompt`/`invocation`/`observation` 이벤트가 추가되는지

라이브 재판정 근거와 실측 매트릭스는 `docs/reviews/p9-agy-live-hooks.md`의 재판정 섹션에 있습니다. 다만 그 문서의 "실행 0회" 결론은 2026-07-16 따옴표 원인 규명으로 정정되었습니다 — 실행은 매번 시도되었고 spawn이 실패한 것입니다.
