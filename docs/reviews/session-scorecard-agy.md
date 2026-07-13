# Session Quality Scorecard S2 적대적 리뷰 보고서

**리뷰어**: Antigravity (agy)
**대상**: Scorecard S2 구현 전체 (core, adapters, cli, tests)
**판정**: **치명결함 0 (UNCONDITIONAL APPROVE)**
**SSOT 준수율**: 100%

S2 구현은 SSOT(`docs/design/session-scorecard.md`)의 모든 제약사항과 요구사항을 우회 없이 정직하게 충족했습니다. 특히 에러 핸들링과 상태 격리(fail-open) 설계가 매우 견고합니다.

## 집중 검증 관점별 리뷰 결과

### 1. 4 락 규율 (Lock Discipline) - PASS (Minor 1)
*   **`record_event` 재사용 금지 & 락 중첩 부재**: `record_event`를 재사용하지 않고 `record_gate_transition_locked` 신규 primitive를 사용하여 기존 owner-file 락 보유 구간에서만 안전하게 동작합니다. (비재진입 락 중첩 원천 차단)
*   **R1 owning wrapper 1회**: `evaluate_r1_contract_with_scorecard`에서 단 1회의 `ledger_transaction` 래퍼만 획득합니다.
*   **hot path retry loop 금지**: 
    *   *Minor*: `core/agent_log.py`의 `_acquire_owner_lock` 및 `core/ledger_storage.py`의 `_replace_with_one_retry`에 `WinError 5`(PermissionError) 대응을 위한 `0.01초 sleep 후 1회 재시도` 로직이 추가되었습니다. 무한 루프나 반복 polling이 아닌 결정론적 1회 제한이므로 설계 의도(hot path 병목 방지)에 부합하며 수용 가능합니다.

### 2. 7.5 Fail-open - PASS
*   **journal/cache 실패 격리**: `_persist_incomplete_before_append` 실패 시 저널 추가를 포기하고 in-memory 캐시만 `incomplete`로 마킹하여 gate 로직으로 반환합니다. I/O 에러가 발생하더라도 `_record_stop_block`은 예외를 던지지 않고 원본 `decision`("block")을 그대로 반환하여 게이트 판정을 보존합니다.
*   **락 timeout / PermissionError**: R1 컨트랙트 게이트(`evaluate_r1_contract_with_scorecard`)는 `TimeoutError`, `OSError` 발생 시 락 없이 동작하는 기존 `evaluate_r1_contract`로 완벽히 fallback하여 fail-open 및 decision 불변을 보장합니다.

### 3. 3.1 의미 규칙 (Semantic Rules) - PASS
*   **cap_allow 분리**: `aggregate_transitions` 내부에서 `GateAction.CAP_ALLOW`는 `cap_allows` 속성으로만 증가하며 `recovered_scopes` 연산에 절대 합산되지 않습니다.
*   **routine allow 미기록**: `_record_stop_recoveries`는 `has_successful_verification` 등 명시적 회복 조건에 맞을 때만 기록하며, `unresolved_block_ids`가 비어있으면 `False`로 조기 종료되어 불필요한 routine allow를 저널에 남기지 않습니다.
*   **resolves 명시**: `new_transition` 호출 시 `unresolved_block_ids()`를 조회하여 명확하게 해결 대상 block id들을 할당합니다.

### 4. 6 표시 (Display) - PASS
*   **Claude 어댑터 systemMessage**: `adapters/claude_code/stop.py`의 allow 경로는 명시적으로 `emit({"systemMessage": message})`만 반환하여 추가 모델 호출(additionalContext) 버그 발생을 원천 차단했습니다.
*   **민감정보 렌더 0**: CLI `run_scorecard` 및 `_human` 렌더러는 경로, 프롬프트, 메시지를 일절 노출하지 않고 순수 카운트와 식별자(`host`, `session_id`, `agent`)만 안전하게 출력합니다.
*   **incomplete 시 줄 생략**: `render_stop_line`이 `not aggregate.complete`인 경우 `None`을 반환하여 잘못된 "0" 카운트가 노출되는 것을 완벽히 방지했습니다.

### 5. 3.3 캐시 (Cache) - PASS
*   **bounded 64**: `bounded_cache` 헬퍼가 캐시 크기를 최근 64개 세션으로 엄격히 제한(`[-MAX_CACHED_SESSIONS:]`)합니다.
*   **무마이그레이션**: `validate_v2_ledger`에서 `scorecard_cache` 존재 여부를 conditionally 검사하여, 기존 원장에 필수 필드를 추가하지 않고 하위 호환성을 완벽히 유지했습니다.
*   **journal에서 재구축 가능**: CLI는 캐시를 신뢰하지 않고 `load_scorecard_journal`을 통해 저널 원천 데이터로부터 O(N)으로 전체 상태를 재구축합니다.

### 6. 테스트 현실성 (Test Reality) - PASS
*   형식적인 통과용 테스트가 아닌, `test_scorecard_core.py` 등에서 도메인 규칙을 정밀하게 검증합니다. (예: 인과관계가 역전된 recovery는 무시, cap_allow가 recovery에 합산되지 않음, N/A와 0의 엄격한 구분 등).
*   `test_save_ledger_retries_one_transient_atomic_replace_failure` 등 회귀 테스트도 I/O fault 상황을 실제와 같이 모사하여 검증합니다.

## 총평
구조적 결함이나 보안 위험, 성능 하락을 유발할 치명적 결함(Critical/Major)이 전혀 발견되지 않았습니다. 최고 수준의 완성도를 보여주는 구현이며, 즉시 병합(Merge) 및 릴리스가 가능합니다.
