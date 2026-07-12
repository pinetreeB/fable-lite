# Codex Stop 프로세스 리퍼

Codex CLI의 정상적인 Stop 허용 직후, **현재 Codex 세션이 남긴 MCP/node_repl 잔재만**
회수하는 Windows 전용 opt-in contrib입니다. 기본값은 **기본 OFF**이며 `core/`에는 연결되지
않습니다.

## 안전 계약

- 훅 프로세스의 부모 체인을 올라가 가장 가까운 `codex.exe` PID를 자기 세션으로 확정합니다.
- 그 PID의 자손 중 `mcp-reaper.ps1` v2와 같은 MCP command-line 화이트리스트 및
  `node_repl.exe`만 후보로 봅니다.
- 후보의 최초 생성 시각부터 5분 동안 생긴 프로세스는 세션 최초 도구 세트로 보호합니다.
  생성 시각이 없는 프로세스도 보수적으로 보호합니다.
- 보호 창 뒤 후보만 `taskkill.exe /PID ... /T /F`로 회수합니다. 다른 Codex, Claude,
  agy pane은 부모 PID 루트가 다르므로 대상 집합에 들어올 수 없습니다.
- 기존 Stop 게이트가 `block`한 동안에는 실행하지 않습니다. 게이트가 `allow`한 뒤에만
  `adapters/codex_cli/stop.py`가 contrib를 호출합니다.
- 프로세스 조회, 종료, 로그 기록 중 어떤 오류가 나도 Stop 훅은 exit 0으로 완료됩니다.
  리퍼는 stdout/stderr를 사용하지 않고 로그에만 결과를 남깁니다.
- 스레드 수, MCP 종류, 모델, reasoning effort 등 Codex 능력 설정은 읽거나 변경하지 않습니다.

## 활성화

먼저 Codex 어댑터가 설치되어 있고 `[features] hooks = true`가 활성화되어 있어야 합니다.
사용자 전역 설정을 수정할 필요는 없습니다. 현재 PowerShell에서 시작할 Codex 세션 하나에만
활성화하려면 다음처럼 환경변수를 설정합니다.

```powershell
$env:FABLE_LITE_CODEX_REAPER = '1'
$env:FABLE_LITE_CODEX_REAPER_LOG = Join-Path $PWD '.fable-lite\codex-process-reaper.log'
codex
```

`FABLE_LITE_CODEX_REAPER_LOG`를 생략하면 현재 프로젝트의
`.fable-lite/codex-process-reaper.log`를 사용합니다. JSON Lines 로그의 `before`, `protected`,
`targets`, `after`, `outside_before`, `outside_after`로 회수와 다른 세션 무손상을 확인할 수
있습니다.

프로젝트 하나에만 고정하려면 설치된 프로젝트 로컬 `.codex/hooks.json`의 기존 Stop
`commandWindows` 앞부분에 다음 환경변수 대입을 추가한 뒤 원래 `stop.py` 명령을 그대로
이어 실행합니다.

```powershell
$env:FABLE_LITE_CODEX_REAPER='1'; $env:FABLE_LITE_CODEX_REAPER_LOG=(Join-Path (Get-Location) '.fable-lite\codex-process-reaper.log'); & <기존 python> <기존 stop.py>
```

기존 Stop 항목을 두 개의 병렬 훅으로 나누지 마세요. 리퍼가 gate block 전에 실행될 수 있어
허용 판정 뒤 호출이라는 안전 순서를 깨뜨립니다.

## 비활성화

셸 단위 활성화는 Codex를 종료한 뒤 다음처럼 제거합니다.

```powershell
Remove-Item Env:FABLE_LITE_CODEX_REAPER -ErrorAction SilentlyContinue
Remove-Item Env:FABLE_LITE_CODEX_REAPER_LOG -ErrorAction SilentlyContinue
```

프로젝트 로컬 활성화는 `.codex/hooks.json`에서 위 환경변수 대입 부분만 제거합니다. 변수 값이
`1`, `true`, `yes`, `on` 중 하나가 아니면 리퍼는 파일 생성도 하지 않는 no-op입니다.

## 시간당 백스톱과의 관계

`C:\Users\rotat\scripts\mcp-reaper.ps1` 및 Task Scheduler의 시간당 실행은 그대로 유지해야
합니다. Stop 훅은 정상 종료 시의 1차 방어선이고, 시간당 `mcp-reaper.ps1`은 OOM, 강제 종료,
훅 미발동을 회수하는 2차 백스톱입니다. 이 contrib는 백스톱 스크립트나 스케줄러를 설치,
수정, 비활성화하지 않습니다.

## 운영 확인

종료 후 로그 마지막 줄을 확인합니다.

```powershell
Get-Content .fable-lite\codex-process-reaper.log -Tail 1
```

필요하면 실제 종료 없이 판정과 before/after 조회만 수행할 수 있습니다.

```powershell
$env:FABLE_LITE_CODEX_REAPER_DRY_RUN = '1'
```

검증 후 `FABLE_LITE_CODEX_REAPER_DRY_RUN`을 제거하면 실제 회수가 다시 활성화됩니다.
