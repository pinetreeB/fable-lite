# Codex CLI 어댑터 설치

이 어댑터는 Codex CLI 0.142.5에서 확인한 hook 계약을 기준으로 작성되었습니다.

## 확인한 Codex 계약

- 기능 플래그의 canonical key는 `[features] hooks = true`입니다. `[features] codex_hooks = true`도 동작하지만 Codex manual에서 deprecated alias로 설명합니다.
- Codex는 `hooks.json` 또는 `config.toml`의 inline `[hooks]`를 로드합니다. 한 레이어에서 둘을 동시에 쓰면 병합 경고가 날 수 있으므로 `hooks.json` 한 방식만 권장합니다.
- project-local `.codex/config.toml`과 `.codex/hooks.json`은 프로젝트가 trusted일 때만 로드됩니다.
- non-managed command hook은 신뢰 검토가 필요합니다. 자동화에서만 `--dangerously-bypass-hook-trust`를 사용할 수 있습니다.
- `timeout` 단위는 초이고, 생략 시 Codex 기본값은 600초입니다. show-me-the-work hook은 10초로 둡니다.
- 현재 matcher가 의미 있는 이벤트는 `PreToolUse`, `PostToolUse` 등입니다. `UserPromptSubmit`과 `Stop`은 matcher가 무시됩니다.

## 실제 payload 차이

Codex live capture(`codex exec -c hooks...`)에서 확인한 payload는 Claude Code와 거의 같지만 다음 차이가 있습니다.

- `hook_event_name`을 사용합니다.
- `tool_name`에 `apply_patch`가 들어올 수 있습니다.
- `apply_patch`의 파일 경로는 `tool_input.command` 안의 patch 본문에서 파싱해야 합니다.
- `PostToolUse.tool_response`가 객체가 아니라 문자열일 수 있습니다.
- `Stop` payload는 `last_assistant_message`를 직접 제공합니다.

## 설치

어느 작업 디렉터리에서든 show-me-the-work의 `install.py` 경로와 대상 프로젝트 루트를 넘깁니다.

```powershell
python "C:\경로\show-me-the-work\adapters\codex_cli\install.py" --target "C:\경로\대상 프로젝트"
```

```bash
python3 "/path/to/show-me-the-work/adapters/codex_cli/install.py" --target "/path/to/대상 프로젝트"
```

설치기는 자신의 `install.py` 위치에서 show-me-the-work 저장소 루트를 찾고, 원본 `hooks.json`의
`{SMTW_ROOT}` 토큰 8개(4개 이벤트의 `command`/`commandWindows`)를 검증한 뒤
플랫폼별로 안전하게 인용한 절대 명령을 구조적으로 렌더링해 대상의 `.codex/hooks.json`만
새로 만듭니다. 따라서 Codex를 대상 프로젝트에서 실행해도 현재 작업 디렉터리나
`PYTHONPATH`에 의존하지 않습니다. 원본 `hooks.json`은 설치용 템플릿이므로 대상에 직접
복사하지 마세요.

설치기는 `.codex/config.toml`과 사용자 전역 `~/.codex/config.toml`을 만들거나 수정하지
않습니다. `[features] hooks = true` 활성화가 필요한 환경에서는 기존 Codex 설정에서
사용자가 별도로 관리해야 합니다.

대상 `.codex/hooks.json`이 이미 있으면 기본 설치는 내용을 보존한 채 오류로 종료합니다.
기존 manifest 안의 foreign entry를 유지하면서 smtw가 소유한 4개 entry만 원자적으로
갱신하려면 검토·백업 후 `--upgrade`를 명시합니다.

```powershell
python "C:\경로\show-me-the-work\adapters\codex_cli\install.py" --target "C:\경로\대상 프로젝트" --upgrade
```

Codex CLI에서 `/hooks`를 열어 새 hook을 검토하고 trust 처리합니다. 자동화 검증에서만 다음처럼 trust 검토를 우회할 수 있습니다.

```powershell
codex exec --dangerously-bypass-hook-trust -C . "간단한 테스트 프롬프트"
```

## 검증 메모

메인 `~/.codex/config.toml`은 수정하지 않았습니다. 실제 payload 확인은 CLI `-c hooks...` override로 임시 로깅 hook을 주입해 수행했습니다. 하위 임시 디렉터리의 `.codex/`는 project trust 경계 때문에 로드되지 않았고, repo-root 임시 `.codex/`도 현재 세션의 trust 조건에서는 캡처 hook이 로드되지 않았습니다. 따라서 live self-test는 `-c hooks...` 방식의 격리 실행을 기준 증거로 남겼습니다.

## v2 change provenance 상태

v2 훅은 도구 이름이나 shell parser만으로 변경을 확정하지 않습니다. 실제 프로젝트 파일의 metadata와
digest를 공통 provenance 코어가 관측하며, Codex/Claude Code/Antigravity payload는 같은 canonical
event 계약으로 정규화됩니다. 상태는 대상 프로젝트의 `.fable-lite/snapshots/`와
`.fable-lite/ledger.json`에만 저장됩니다.

- 정상 turn start와 변경 없는 PostTool은 콘텐츠를 읽지 않는 metadata fast-path를 사용합니다.
- Stop은 parser가 모르는 shell write도 잡도록 전체 콘텐츠를 reconcile합니다.
- scan이 불완전하면 clean으로 간주하지 않고 기존 2회 차단 상한을 적용합니다.
- v1 ledger 및 `status` 없는 구 v2 invocation의 자동 migration은 `SMTW_AUTO_MIGRATION=1`로 명시적으로 opt-in하고 W9 정확도 receipt와 W10 성능 receipt가 모두 green일 때만 켜집니다. v3에서는 `FABLE_LITE_AUTO_MIGRATION`도 legacy alias로 읽지만 두 세대 값이 다르면 충돌 오류로 중단합니다.

패키지에 포함된 `core/release_receipts/bench-latest.json`은 rev3의 1k 대표 규모와 10k 극한 규모 hard
gate가 모두 green입니다. 함께 패키징된 W9 정확도 receipt도 green이므로 환경 opt-in을 설정한
프로세스에서 one-shot migration을 사용할 수 있습니다. opt-in이 없으면 구 v2 invocation 원장은
원본 바이트를 보존한 채 attribution-degraded로 동작합니다. opt-in 상태에서도 status-less invocation은
30분 lease를 초과한 경우에만 `closed`가 되고, 최근 행은 `open`으로 보존됩니다. R2 보호 증거가
불완전하거나 migration이 실패하면 원본을 쓰지 않고 degraded를 유지하며 `core.ledger` warning에
실패 stage/detail을 출력합니다. `eval/results/`의 ignored 재측정 산출물은 release 승인을 자동 갱신하지
않습니다. packaged receipt를 수동으로 수정해 가드를 우회하면 안 됩니다. 재측정은 저장소 루트에서 다음
명령으로 실행합니다.

```powershell
python -m eval.provenance.run
python -m eval.bench_provenance
```

50k/2GiB stress는 명시적 선택 사항입니다.

```powershell
python -m eval.bench_provenance --stress
```
