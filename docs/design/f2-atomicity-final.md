# F2 트랙 설계 확정본 (구현 지시서)

> 2026-07-20 좌상 종합. 입력: tmp/f2-track-spec-draft.md + tmp/f2-design-codex.md(Sol ultra, 격리 실측 6종) + tmp/f2-design-agy.md(적대 검토) + 좌상 팩트체크.
> 3자 수렴·충돌 없음. codex안을 구조 정본으로, agy 방어 논거를 수용 기준에 반영.

## 확정 결론 (Q1~Q5)

- **Q1 = (a+)**: authoritative transaction은 **baseline 파일 + ledger(baseline_status/baseline_snapshot_id/recovery 상태) 2-file**만. coordination journal은 게이트 판정 입력이 아니므로 트랜잭션 밖 — 단 현행 fire-and-forget은 폐기하고 **같은 ledger 커밋에 bounded coordination outbox(≤256)를 함께 저장 → unlock 후 at-least-once drain**(exact content 재시도 dedup, ack는 identical-existing 인정). overflow/스키마 오류는 `coordination_degraded` 보고. gate decision은 outbox backlog와 무관.
- **Q2 = (iv) 순서 프로토콜 + 좁은 (ii)**: `ledger_transaction` 일반 재진입화 금지(candidate scan이 락 잡으면 Stop 8초 예산 잠식). public `record_event` 시그니처 유지, private `_record_event_locked(payload, transaction)` 분리(opaque transaction token, active/root assert). 전용 owner `ensure_turn_bootstrap`만 락 소유. **사전판독 `_baseline_missing`은 hint로 강등, recovery truth는 락 안 재계산.**
- **Q2 상태기계** (bootstrap 정본):
  | ledger | baseline 파일 | 처리 |
  |---|---|---|
  | turn 없음 | 없음 | 새 bootstrap, recovered coordination 없음 |
  | missing | 없음 | 검증된 current를 CAS-create 후 **저장·reload한 physical winner ID**로 READY |
  | missing | 정상 있음 | 이전 crash residue → **기존 파일 winner 채택**, current로 덮지 않음 |
  | ready | 정상·ID 일치 | no-op |
  | ready | 없음/손상/ID 불일치 | **재기준화 금지, 명시적 DEGRADED**(`baseline_status=degraded` 신설) + incomplete report |
  | degraded | 무엇이든 | 자동 bootstrap 금지 |
  recovery event와 invocation event는 분리(사이 crash = "tool 미실행+baseline READY" 안전 상태).
- **Q2 인접(stale lock)**: `_stale_record`가 mtime≤10s면 PID 검사 생략 → 유효 `pid:token`+PID 확실히 죽음이면 age 무관 즉시 reclaim / malformed·확인불능만 10s grace / **live PID는 age 무관 절대 탈취 금지**(v2.0.1 계약 유지). owner start-time 추가는 후속 hardening.
- **Q3**: 완전 XA 아님 — 계약 2문장: ①`READY ⇒ 물리 baseline 존재 ∧ physical.snapshot_id == ledger.baseline_snapshot_id` ②불변식 증명 실패 시 조용한 재기준화 금지, INCOMPLETE/DEGRADED 보고. fail-open은 "프로세스를 죽이지 않음"이지 "증거 없이 정상 선언"이 아님. power-loss fsync 내구성은 **비범위**(Known Limitations에 명시).
- **Q4**: baseline write 이원화 — `initialize_turn_baseline(expected_absent, candidate) → CREATED|EXISTING|CONFLICT`(first-valid-write-wins, existing winner 채택) / `advance_turn_baseline(expected_snapshot_id, merged_snapshot, manifest_generation) → COMMITTED|RETRY`(candidate 확장 CAS). **candidate advance는 단순 replace 금지** — 기존 관측 key/bytes 보존 + 신규 primed candidate만 merge(앞선 tool delta의 baseline 흡수→pending 소실 방지). CAS retry 시 candidate set 재계산. `_safe_key` 충돌은 overwrite 금지·degraded 보고. production 호출은 owning transaction 안에서만.
- **Q5**: 엄격비교 폐기하되 **1줄 치환 금지** — exclusion-filtered replay로 구현: `_complete_observation(status) and not result.incomplete` + `result.snapshot.exclusions`의 canonical key(스냅샷 casefold 정책으로 canonicalize — 문자열 비교 금지)를 replay 델타에서 제거 후 record_deltas. 심각도 판정: 좌상 팩트체크로 agy "Stop allow 세탁"은 불성립(Stop 재대조가 changed→검증요구/incomplete 차단), 실효는 **귀속 품질 저하(Medium)** — 단 수정 필요성은 3자 일치. in-memory 정합화가 acceptance이며 ledger 누적 이벤트 증가를 가장하지 않는다.

## Wave 분할 (커밋 단위)

- **W1 (Q2+Q4 코어)**: 상태기계 bootstrap(`ensure_turn_bootstrap`) + `_record_event_locked` 분리 + baseline 이원 API + `_baseline_missing` hint 강등 + stale lock 개선 + `baseline_status=degraded` 스키마.
- **W2 (Q1)**: coordination outbox(ledger_schema 검증+bounded 256+drain+ack+degraded 보고).
- **W3 (Q5)**: post_tool exclusion-filtered replay + 테스트 5종(codex 의견서 §Q5 "필요한 테스트" 1~5 그대로).
- **W4 (F9+F7)**: eval receipt 4종 untrack(`git rm --cached`)+`.gitignore`에 `eval/results/`+ci.yml:35 `--output` 부여(참조 전수 grep 재확인 선행) / coordination malformed "유효 JSON+스키마 위반" 테스트 추가.

## 게이트 (각 Wave 후 실행, 최종 전체)

- `python -m pytest tests -q` 전체 green (기존 708+신설)
- 신설 동시성 테스트: 실제 2-process baseline 경합(winner 결정론)·crash-cut 5지점(codex §Q3 표) 재현
- `python eval/run_probes.py --strict` / `python eval/e2e_smoke.py`
- SLO 무회귀: 벤치 p95 기존 대비 열화 없음 (`python -m eval.bench_provenance --output <temp>`)
- 검증 명령에 파이프·셸 연산자 금지(리다이렉트 2>&1만), PYTHONIOENCODING=utf-8

## 비범위(이번 트랙에서 하지 말 것)

- power-loss fsync 내구성 / PID reuse start-time hardening / PEER_EXCLUSION coordination audit / 누적 replay의 ledger 영속화 / peer-exclusion lease 정책 — 전부 Known Limitations·후속 백로그로 기재만.

## 작업 절차 (영진)

- 브랜치 `worker/f2-atomicity` 생성 후 Wave별 커밋(메시지 영어, 관례 유지). 소스는 main `533df02` 기준.
- 완료 후: 전체 게이트 재실행 → `git bundle create $env:USERPROFILE\f2-atomicity.bundle main..worker/f2-atomicity` 생성 → 좌상이 회수.
- CHANGELOG [Unreleased] 또는 v2.3.1 절에 Fixed/Changed 초안 + Known Limitations 갱신(F2 항목 해소 반영, 비범위 항목 신규 기재).
