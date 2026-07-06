# Codex CLI 어댑터 설치

이 어댑터는 Codex CLI 0.142.5에서 확인한 hook 계약을 기준으로 작성되었습니다.

## 확인한 Codex 계약

- 기능 플래그의 canonical key는 `[features] hooks = true`입니다. `[features] codex_hooks = true`도 동작하지만 Codex manual에서 deprecated alias로 설명합니다.
- Codex는 `hooks.json` 또는 `config.toml`의 inline `[hooks]`를 로드합니다. 한 레이어에서 둘을 동시에 쓰면 병합 경고가 날 수 있으므로 `hooks.json` 한 방식만 권장합니다.
- project-local `.codex/config.toml`과 `.codex/hooks.json`은 프로젝트가 trusted일 때만 로드됩니다.
- non-managed command hook은 신뢰 검토가 필요합니다. 자동화에서만 `--dangerously-bypass-hook-trust`를 사용할 수 있습니다.
- `timeout` 단위는 초이고, 생략 시 Codex 기본값은 600초입니다. fable-lite hook은 10초로 둡니다.
- 현재 matcher가 의미 있는 이벤트는 `PreToolUse`, `PostToolUse` 등입니다. `UserPromptSubmit`과 `Stop`은 matcher가 무시됩니다.

## 실제 payload 차이

Codex live capture(`codex exec -c hooks...`)에서 확인한 payload는 Claude Code와 거의 같지만 다음 차이가 있습니다.

- `hook_event_name`을 사용합니다.
- `tool_name`에 `apply_patch`가 들어올 수 있습니다.
- `apply_patch`의 파일 경로는 `tool_input.command` 안의 patch 본문에서 파싱해야 합니다.
- `PostToolUse.tool_response`가 객체가 아니라 문자열일 수 있습니다.
- `Stop` payload는 `last_assistant_message`를 직접 제공합니다.

## 설치 스니펫

프로젝트 루트에 아래 두 파일을 둡니다.

```toml
# .codex/config.toml
[features]
hooks = true
```

```powershell
New-Item -ItemType Directory -Force .codex
Copy-Item adapters\codex_cli\hooks.json .codex\hooks.json
```

Codex CLI에서 `/hooks`를 열어 새 hook을 검토하고 trust 처리합니다. 자동화 검증에서만 다음처럼 trust 검토를 우회할 수 있습니다.

```powershell
codex exec --dangerously-bypass-hook-trust -C . "간단한 테스트 프롬프트"
```

## 검증 메모

메인 `~/.codex/config.toml`은 수정하지 않았습니다. 실제 payload 확인은 CLI `-c hooks...` override로 임시 로깅 hook을 주입해 수행했습니다. 하위 임시 디렉터리의 `.codex/`는 project trust 경계 때문에 로드되지 않았고, repo-root 임시 `.codex/`도 현재 세션의 trust 조건에서는 캡처 hook이 로드되지 않았습니다. 따라서 live self-test는 `-c hooks...` 방식의 격리 실행을 기준 증거로 남겼습니다.
