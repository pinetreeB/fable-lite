# smtw 내부 통일 마이그레이션 — 설계 확정 (v3.0)

> 2026-07-21 좌상 종합. 입력: codex 의견서(구현·마이그레이션 실현성, tmp/smtw-unification-codex.md) + agy 적대검토(데이터 안전·게이밍, tmp/smtw-unification-agy.md) + 좌상 종합.
> Status: **구현 완료 (2026-07-22, 브랜치 `worker/v3-unification` 커밋 6, v3.0.0)**. Q1(디렉토리)·Consumer SSOT·Q3(env)·Q4(config) 반영. **Q2(모듈명)은 확정대로 후속 minor+shim으로 이월**. P4 종합=`docs/reviews/2026-07-22-smtw-unification-v3-p4.md`. 규모 v3.0 major.

## 핵심 판정: codex (c)와 agy (b)는 합쳐진다

두 이종 모델이 Q1에서 반대 결론을 냈으나 **오해에서 비롯**:
- agy는 codex의 (c)를 "직접 `.smtw/`에 copytree"로 오독 → "멀티세션 동시 copytree partial write" 우려로 기각.
- 실제 codex (c)는 **staging에 copy → 검증 → target 부재 시 원자적 rename으로 publish**. 즉 agy가 원한 **rename 원자성을 publish 단계에서 이미 사용**하고, 거기에 원본 보존(롤백)까지 더한 것.
- agy의 (b) 단순 rename은 codex 지적대로 "원본 소멸→롤백 불가 + config.json까지 이동해 세션 비활성화" 문제.

→ **채택: staging copy(원본 보존) + 검증 + `.smtw/` 부재 확인 후 원자적 rename publish + layout lock(멀티세션 winner 1명).** agy의 원자성 요구와 codex의 원본 보존을 동시 충족. agy가 우려한 동시 copytree는 layout lock으로 원천 차단.

## 확정 결론 (Q1~Q6)

- **Q1 마이그레이션 방식** = codex (c) 정본. **불변식 5**(codex): ①권위 트리 항상 하나(파일별 혼합 금지) ②`.smtw.toml`(추적 config)≠`.smtw/`(미추적 런타임) 분리 ③`state_dir()`는 쓰기 부작용 없음 ④publish 후 legacy 자동 fallback 금지(split-brain) ⑤legacy "읽기 가능"≠"새 상태 씀". 구현=`core/state_layout.py`(읽기전용 판정 EMPTY/LEGACY/NATIVE/MIGRATED/MIGRATING/CONFLICT)+`core/file_lock.py`(agent_log 락 primitive 추출, `.smtw-migration.lock` sibling)+`core/state_migration.py`(명시적 `migrate_state`). 8단계 state machine+crash 복구 규칙(codex §마이그레이션 안전).
- **★자동 마이그레이션 금지** (codex+agy 완전 일치): `state_dir()` 호출마다 lazy 평가 금지. **CLI 부트스트랩 1회 동기 수행** + **혼합 버전(v2 writer) 활동 중 자동 migration 불가 → 설치 시 세션 재시작 요구, active/open lease 있으면 보류**. exact home은 디스크 흔적 0.
- **Q2 모듈명 `fable_lite`→`smtw`** = **보류/별도 트랙**(codex "Q1/3/4 안정화 후 독립 PR" + agy "재설치 누락 시 fail-closed 마비, 디렉토리 마이그레이션과 동시 진행 시 crash-cut 규명 극난"). v3.0은 디렉토리·env까지. 모듈명은 shim(`fable_lite`=`smtw` 별칭) 동반해 후속 minor.
- **Q3 env** = 단일 헬퍼 `smtw_env(name)` SMTW_ 우선·FABLE_LITE_ 폴백. **★agy 게이밍 차단: 두 변수 동시 존재+값 상이 시 fail-closed 충돌 에러**(악성 워커가 구 env로 게이트 은밀 무력화 방지).
- **Q4 config malformed** = **agy 확정안**(codex 미해결 1건 해소): **파싱 에러(유효하지 않은 TOML)만 corrupt, `[tool.smtw]` 섹션 없음은 "설정 없음"으로 정상 다음-소스 폴백**. 섹션 누락을 corrupt로 보면 악성 워커가 섹션 삭제로 감독 DoS 가능 → 금지.
- **Q5 하위호환 수명** = v3.0 도입, 폴백(읽기 legacy·env FABLE_LITE_·모듈 shim)은 v3 유지·v4 제거. deprecation 경고 1회/세션.
- **Q6 롤백·R2** = 원본 `.fable-lite/` 보존이 롤백 근거(publish 전 crash=legacy 권위). **★agy 치명 발견: R2 state_dir 이중 보호** — 통일 후 `_is_state_dir_key`가 `.smtw`만 보호하면 잔재 `.fable-lite/`가 R2 파괴보호에서 빠져 `rm -rf .fable-lite/` 무제지(감사기록·백업 삭제). **레거시 디렉토리명 `.fable-lite`도 R2 영구 차단 목록에 하드코딩 이중 보호.**

## SSOT 전환
- 상태 디렉토리: `state_dir()` 단일 SSOT 강제(리터럴 15곳→헬퍼, 하드코딩 0 회귀 테스트). 디렉토리명 상수 하나로 전환.
- env: `smtw_env()` 단일 헬퍼.
- consumer 전 계층(ledger·attribution/snapshot·contract/goals/intent·scorecard/coordination/audit·adapter log)이 선택된 트리 하나 주입.

## 규모·릴리스
- Q1만 60~75파일·1,200~2,000 LOC(codex). 전체 v3.0 major. 배포된 설치본은 재설치+`smtw migrate`+세션 재시작으로 이전.
- 게이트: 마이그레이션 안전 리허설(실 라이브 `.fable-lite/` 사본 무손실·멱등·롤백·멀티세션 경합·crash-cut, B5 방식) + ruff+pytest+probes+e2e+로컬 wheel smoke + 하드코딩 리터럴 0.
- P4: 데이터 유실·폴백 게이밍·R2 이중보호·멀티세션·홈세션.

## 비범위(v3.0)
- 모듈명 변경(후속 minor+shim) / 완전 자동 온라인 마이그레이션(명시적 명령만) / 원격 상태.
