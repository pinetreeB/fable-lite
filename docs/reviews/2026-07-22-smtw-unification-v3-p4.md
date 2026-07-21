# smtw v3.0 내부 통일 — P4 교차검토 종합

> 2026-07-22 좌상 종합. 대상 브랜치 `worker/v3-unification`(커밋 6). 설계 정본 `docs/design/smtw-unification-v3.md`.
> 3-AI: 구현 codex@yeongjin(Sol max) · 적대 리뷰 agy · 다차원 실측 ultracode(우하) · 중재 좌상.

## 구현 커밋 체인

| 커밋 | 내용 | 게이트 |
|------|------|--------|
| `e20ae05` | Foundation — `state_layout.py`(EMPTY/LEGACY/NATIVE/MIGRATED/MIGRATING/CONFLICT 읽기전용 판정) + `file_lock.py`(owner-token/PID/stale 락 primitive) + 이름 상수 + destructive_guard·provenance_policy 이중보호(.smtw·.fable-lite·staging) | 939 passed |
| `10e839d` | State migration — `state_migration.py`(staging copy·manifest 전후 대조·marker·target 부재 rename publish, 8단계+crash 복구) + CLI `smtw migrate` | 983 passed |
| `2b524cd` | Consumer SSOT — 전 consumer를 `state_dir()` 단일 facade로 전환(60파일) + 하드코딩 리터럴 0 회귀 테스트(allowlist=state_layout.py 1곳) | 995 passed |
| `3b503e8` | Env 통일 — `runtime_env.py`(SMTW_ canonical·FABLE_LITE_ 폴백·동시존재 값상이 시 `SmtwEnvConflictError`) 8키 | — |
| `a3bd52d` | Config 통일 — 우선순위 `.smtw.toml` > pyproject `[tool.smtw]` > `.fable-lite/config.json`, tri-state(ABSENT/VALID/DECLARED_INVALID) | 1066 passed |
| `19eab32` | D3-i 수리 — 어댑터가 `SmtwEnvConflictError`를 health fail-open과 분리해 fail-closed(deny/block)로 변환 | 1069 passed |

## 리뷰 (agy 적대)

- **W1 Foundation**: R2 이중보호(lexical+resolved), 마이그레이션 원자성(TOCTOU·source mutation 3회 대조·crash-cut), state_dir 부작용 0, marker 게이밍 방어 — 4관점 전부 통과.
- **W2 Consumer SSOT**: marker authority/pristine 검증 분리가 세탁표면을 열지 않음, 불변식 1(혼합 금지)·4(fallback 금지), LEGACY 무변경, 리터럴 allowlist 1곳 — 5관점 통과.
- **W3 Env/Config**: env 충돌 fail-closed, config DoS 차단(섹션 삭제=ABSENT·파싱에러만 corrupt), 우선순위 게이밍 흔적박제, inactive core import 0 — 4관점 통과.

## 실측 (ultracode 4차원)

- **D1 동시 경합**: 실물 규모(681파일) 8프로세스 동시 migrate 10라운드 — winner 정확히 1·나머지 idempotent·무손실·staging 잔여 0. **PASS**
- **D2 crash-cut**: 6단계(manifest·copy·marker·rename 전후·publish 후) 강제 kill — publish 전=legacy 권위+재시도 성공, publish 후=.smtw 권위, 원본 불변. **PASS**
- **D3 게이밍**: 아래 별도. **1건 수리·1건 NOFIX**
- **D4 흔적 0**: fake home·비활성 프로젝트에서 .smtw/staging/lock/receipt 생성 0·출력 {}. **PASS**

## D3 판정 (실측↔리뷰 갈림 → 좌상 중재)

- **D3-i env 충돌 fail-open** [Medium, 수리 O]: `resolve_smtw_env`는 `SmtwEnvConflictError`를 raise하나 어댑터 훅 통합 경로에서 health fail-open으로 흡수돼 확정문 Q3의 "fail-closed 충돌 에러" 의도가 무산 — 충돌 유발만으로 보조게이트를 끌 수 있는 표면. codex가 `fail_closed_runtime_env` 헬퍼로 수리(예외 타입을 `SmtwEnvConflictError`로 정확히 좁혀 PreToolUse→`permissionDecision:deny`, 그 외→`decision:block`; 다른 health 예외는 fail-open 유지). 통합 테스트 3어댑터 subprocess 실측 deny/block. **agy 재리뷰에서 신규 High(Claude Code가 UserPromptSubmit의 decision:block을 무시)를 제기했으나, Claude Code 공식 훅 스펙(UserPromptSubmit·PostToolUse·Stop 모두 최상위 decision:block 지원·PreToolUse만 permissionDecision) 팩트체크로 스펙 오판 확인·기각** → agy 최종 APPROVE.
- **D3-ii content-matched marker 위조** [Low, NOFIX]: legacy와 digest·count·bytes가 완전 일치하는 가짜 `.smtw`+marker를 R2 우회 직접생성 시 published 권위 획득. **codex·agy·좌상 3자 수렴 — Known Limitation**: (a).smtw 생성은 R2가 셸 차단 (b)content 일치면 데이터 손실 없음(정당한 마이그레이션과 구별 불가) (c)원장 세탁은 digest 불일치로 CONFLICT 차단 (d)동일 ACE 권한이면 live target도 직접 변조 가능해 로컬 marker 서명이 추가 신뢰를 못 만듦 (e)legacy 존재 강제는 불변식 4와 충돌. 런타임 전체 해시 재대조는 성능 파괴라 실익 없음.

## S6 실사본 리허설 (좌상 직접)

이 repo의 라이브 `.fable-lite`(201파일)로:
- **활성 상태**: migrate → `deferred`(active turn/open invocation 감지), .smtw 미생성·legacy 무손상 — 안전 보류.
- **정지 상태**(세션 재시작 시뮬레이션=active_turns 정리): migrate → `migrated`, layout MIGRATED, 무손실(.smtw==legacy, config.json은 설계대로 legacy 잔류), 멱등(재실행 `already_migrated`), 롤백 근거(legacy 영구 보존), staging 잔여 0.

## 게이트 총계

ruff green · compileall green · pytest 1069 passed 1 skipped(POSIX-only). 좌상 로컬 packaging 테스트 1건 실패는 구버전 dist-info 잔존(main 동형 재현·CI 무관).
