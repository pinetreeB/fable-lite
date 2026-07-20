# 적대적 설계 검토 의견서 (agy)

> **작성자**: agy (리뷰·분석 워커)
> **대상 문서**: tmp/f2-track-spec-draft.md (v2.3.x 후속 트랙)
> **초점**: 적대적 관점(게이밍 표면, fail-open 우회, SLO 회귀, 멀티에이전트 게이트 F0~F4 상호작용)

---

## S1: F2 — turn bootstrap 3-트랜잭션 원자화

### Q1. 원자화 범위
- **입장**: (a) baseline 저장과 ledger 전이 2개만 트랜잭션으로 묶고, coordination은 현행처럼 fire-and-forget을 유지해야 합니다.
- **근거 및 리스크**:
  - **Fail-open 우회 방어**: coordination은 scorecard 관측 용도이며 멀티에이전트 게이트(F0~F4)의 차단 판정에 영향을 주지 않습니다(`ledger.py:211`에서 예외를 삼킴). 3개를 모두 원자화할 경우, 공격자가 디스크 I/O 지연이나 스키마 제약 위반을 고의로 유발하여 scorecard 기록을 실패하게 만들 수 있습니다. 이로 인해 핵심 ledger 전이까지 롤백되면 정상 턴이 차단(fail-closed)되거나, 예외 처리 미흡 시 게이트 우회(fail-open) 표면이 열릴 위험이 큽니다.
  - **SLO 회귀**: 3단계 원자화는 트랜잭션 점유 시간을 늘려 186ms SLO를 위협하며 동시성 병목을 심화시킵니다.

### Q2. 락 구조 (비재진입 제약 하)
- **입장**: (iv) WAL형 순서 보장 (Intent 선기록 → baseline 저장 → ledger 전이 → committed)을 강력히 권장합니다.
- **근거 및 리스크**:
  - (i) 스레드 로컬 재진입이나 (ii) 락 상위 이동은 동일 프로세스 내의 중첩 호출만 막을 뿐, 프로세스가 크래시(OOM 킬 등)될 때 발생하는 파일 시스템 간(baseline 파일 vs ledger 파일)의 반쪽 상태를 근본적으로 막지 못합니다.
  - 공격자가 의도적으로 프로세스 킬을 유발하여 ledger 기록 없이 baseline만 오염시키는 공격을 수행할 수 있습니다. WAL 패턴을 적용하면, 크래시 발생 시 불완전한 Intent가 관측되어 다음 세션에서 명시적 degraded 상태로 감지 및 복구(또는 R2 차단)로 이어지게 만들어 공격을 무력화할 수 있습니다.

### Q3. crash-safety 목표
- **입장**: 물리적인 완전 원자성 확보는 불가능하므로, "관측 없이 상태만 남는 반쪽 상태가 명시적 degraded로 보고되는 수준"이면 충분하며 구조적으로 우수합니다.
- **근거 및 리스크**:
  - 멀티에이전트 설계 원칙("귀속은 주장이 아니라 관측으로 성립")과 부합합니다.
  - 반쪽 상태가 조용히 무시되면 공격자는 이를 이용해 맹점을 만듭니다(fail-open). 크래시가 명시적 에러 상태로 캡처되어 게이트(R2)가 이를 인지하고 차단하게 만드는 것이 가장 안전한 방어책입니다.

### Q4. 동시 `save_turn_baseline` 덮어쓰기 경합
- **입장**: 원자화 범위와 무관하게, baseline 쓰기 작업 자체에 `O_EXCL` 기반의 First-Write-Wins(또는 CAS) 보호가 절대적으로 필요합니다.
- **근거 및 게이밍 리스크**:
  - 현재 `save_turn_baseline`에는 락이 없습니다. 공격자가 동일한 `turn_id`로 다수의 `begin_invocation`을 고의로 병렬 호출하는 레이스 컨디션을 유발할 수 있습니다.
  - 스레드 A가 정상 baseline을 기록하고 스레드 B가 오염된 baseline을 덮어쓸 경우, ledger 상으로는 정상 수렴한 것으로 보이나 디스크의 실제 기준선은 오염됩니다. 이는 F3(이중근거 교차) 게이트를 완전히 맹인으로 만들어, 후속 델타 검증을 조작할 수 있는 심각한 취약점입니다.

---

## S3: post_tool 잔존 엄격비교 정합화

### Q5. WITH_EXCLUSIONS 상태의 델타 기록 여부
- **입장**: `ProvenanceStatus.COMPLETE` 엄격 비교를 폐기하고, FIX-1과 동일하게 `_complete_observation(result.status)`을 사용하여 **non-excluded 델타를 반드시 장부에 기록해야 합니다.**
- **근거 및 게이밍 표면 (Critical)**:
  - 현재 로직(`provenance_lifecycle.py:335`)은 상태가 `COMPLETE_WITH_EXCLUSIONS`로 강등되면 `record_deltas`를 전체 스킵합니다.
  - **델타 세탁 공격 시나리오**: 공격자 에이전트가 악성 코드를 수정함과 동시에, 백그라운드 서브에이전트나 셸을 통해 무관한 파일에 락(`unstable_path`)을 고의로 유발합니다. 이로 인해 턴 상태가 `COMPLETE_WITH_EXCLUSIONS`로 전환됩니다.
  - 결과적으로 공격자가 성공적으로 수정한 악성 파일의 델타마저 장부(`record_deltas`)에서 통째로 누락됩니다. 이후 Stop 게이트는 장부에 누락된 변경사항을 인지하지 못하고 통과(allow)시킵니다.
  - `adjust_snapshot_for_peer_activity` 구현을 보면 배제된 파일(excluded keys)은 이전(previous) 스냅샷의 엔트리로 복원됩니다. 따라서 `calculate_net_delta` 계산 시 배제된 파일은 정상적으로 '변경 없음'으로 처리(안전)되고, 공격자가 변경한 파일만 델타로 정확히 추출됩니다. 
  - 그러므로 `_complete_observation` 조건으로 완화하여 남은 델타들을 기록하게 하는 것이 의도에 부합하며, 위와 같은 고의적 델타 세탁 공격(게이밍)을 원천 봉쇄합니다.
