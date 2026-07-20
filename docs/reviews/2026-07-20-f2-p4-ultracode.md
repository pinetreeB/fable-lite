# F2(worker/f2-atomicity) P4 다차원 실측 검증 보고서

## 총괄 판정: **CONDITIONAL(조건부 승인)**

5개 검증 차원 중 4개는 실제 코드를 대상으로 한 독립 재현에서 설계 계약을 그대로 만족했습니다. 1개(coordination 발신함, outbox)에서 진짜 결함을 하나 찾았습니다 — 심각도는 낮지만(현재 프로덕션 경로에서는 예외가 삼켜져 크래시로 이어지지 않음) 설계 문서가 명시적으로 요구한 "스키마 오류도 초과 시와 똑같이 안전하게 보고돼야 한다"는 계약을 어기고 있습니다. 이 한 건을 고치기 전에는 이 브랜치를 main에 합치는 것을 권하지 않습니다. 나머지는 모두 이 브랜치 그대로 승인해도 됩니다.

## 본문 — 쉬운 결론

이번 작업(F2)은 "여러 AI 워커가 같은 프로젝트에서 동시에 작업할 때, 중간에 프로세스가 죽어도 상태가 꼬이지 않게 만드는" 안전장치를 손보는 작업입니다. 설계 문서는 5가지 약속을 했고, 저는 실제로 프로세스를 죽여보고, 진짜로 별도의 두 프로세스를 동시에 띄워보고, 일부러 값을 손상시켜 보면서 그 약속이 실제로 지켜지는지 직접 확인했습니다(구현자가 만든 테스트를 그냥 믿지 않고, 제가 따로 만든 실험으로 다시 확인했습니다).

**잘 지켜진 것 (4가지)**
1. **중간에 죽어도 복구된다**: "기준 파일을 만드는 도중", "만들고 나서 기록하기 전", "기록하고 나서 보고하기 전" 등 5개 지점 각각에서 실제로 그 지점까지만 진행시키고 멈춘 뒤 다시 시작해봤더니, 매번 설계서가 약속한 그대로 복구되거나 "이상하니 손대지 않겠다"고 정직하게 보고했습니다. 특히 "기준이 꼬였는데 시스템이 조용히 새로 만들어버리는" 위험한 상황은 한 번도 일어나지 않았습니다 — 심지어 나중에 정상 파일을 다시 갖다 놔도 시스템은 "이미 의심스러운 상태였다"는 걸 계속 기억하고 함부로 되돌리지 않았습니다.
2. **동시에 두 워커가 부딪혀도 결과가 하나로 정리된다**: 진짜로 별도의 두 프로그램을 동시에 실행해 같은 자원을 두고 경쟁시키는 실험을 120번 반복했는데, 매번 승자가 정확히 하나로 정해졌고, 기록된 값이 실제 파일과 항상 일치했습니다. 예전에 있었다던 "승자가 운에 따라 달라지는" 문제는 한 번도 재현되지 않았습니다.
3. **다른 워커가 낸 변경이 내 것으로 잘못 채워지지 않는다**: "이 파일은 이번에 못 봤다"고 제외 처리된 항목이, 나중에 다시 계산할 때 실수로 "내가 바꾼 것"으로 둔갑하는 버그가 있을 뻔했는데, 실제 구현은 이를 막고 있었습니다(제외된 파일만 정확히 걸러내고, 걸러내지 않았을 때 실제로 그 버그가 재현되는 것까지 대조 확인했습니다).
4. **기존 약속(기록이 없어도 안전하게 통과시켜준다)이 안 깨졌다**: 이번 변경과 무관한 기존 안전장치도 여전히 정상 작동했고, 전체 테스트 786개가 전부 통과했습니다(신규 실패 0건).

**문제가 있는 것 (1가지)**: "보고함"(coordination outbox)이라는 대기열이 있습니다. 여기에 쌓아둔 보고가 256개를 넘으면 "지금 보고가 밀리고 있다"고 정직하게 표시하도록 잘 만들어져 있었습니다. 그런데 보고 내용 자체가 형식에 안 맞는 경우(정상적으로는 절대 안 생기지만, 코드 구조상 이론적으로 생길 수 있는 경우)에는 그 형식 오류를 정중하게 알리는 대신, 시스템 내부에서 처리되지 않은 오류가 튀어나오면서 그 사실 자체가 기록에 전혀 남지 않았습니다. 다행히 실제로 이 대기열을 사용하는 유일한 통로는 항상 미리 검증된 값만 넣도록 짜여 있어서, 지금 당장 사용자가 이 버그를 만날 가능성은 낮습니다. 다만 앞으로 이 대기열을 다른 곳에서도 쓰게 되면 조용히 위험해질 수 있는 지뢰이고, 무엇보다 설계 문서가 "이런 경우도 안전하게 보고돼야 한다"고 명시적으로 약속한 부분이라서, 고치고 넘어가는 것을 권합니다.

**권고**: outbox 삽입 시점에 전체 형식을 미리 검사하도록 하거나(이미 "초과" 케이스는 이렇게 하고 있으니 같은 방식을 적용), 저장 단계에서 형식 오류가 나면 그 실패를 붙잡아 "보고 지연" 상태로 정직하게 남기도록 고친 뒤, 다시 이번과 같은 방식으로 재검증하는 것을 권합니다. 이 외의 4개 차원은 추가 수정 없이 그대로 진행해도 좋습니다. 설계 문서에 있던 나머지 표준 게이트(strict probe·e2e smoke·성능 벤치마크)는 이번 5개 차원 지시 범위 밖이라 실행하지 않았으므로, 최종 merge 전에는 별도로 확인이 필요합니다.

---

## 차원별 결과 요약

| # | 차원 | 판정 | 핵심 근거(요지) |
|---|------|------|------------------|
| D1 | crash-cut 5지점 (Q3) | **PASS** | 5개 지점 + 2문장 계약(READY⇒물리/원장 일치, 증명 못하면 조용한 재기준화 금지) 전부 독립 프로브로 재현·확인 |
| D2 | 실제 2-OS-프로세스 경합 (Q2/Q4) | **PASS** | subprocess 2개 실경합 120회(40x3) 전부 결정론적 승자·physical/ledger 일치·recovered dedup=1 |
| D3 | coordination outbox (Q1) | **FAIL** | overflow degraded·drain dedup/ack는 정상, 스키마 오류 시 uncaught 예외로 degraded 미기록 |
| D4 | Q5 exclusion-filtered replay | **PASS** | 필요한 테스트 5종 + casefold 우회 방지 + negative control로 판별력까지 확인 |
| D5 | 회귀(Stop-allow 계약 + 표준게이트) | **PASS** | 대상 테스트 PASS(main도 대조 PASS), 전체 786 passed 0 failed |

---

## 차원별 상세

### D1 — crash-cut 5지점 (Q3)

Q3 계약표 5개 지점 전부를, `core.adapter_observation.begin_invocation()`과 `core.provenance_manifest.ensure_turn_bootstrap()`을 실제 호출하고 `unittest.mock.patch.object`로 지정된 지점에서 예외를 던져 "그 지점에서 죽었다"를 재현하는 방식(`tmp/p4-probes/crash_cut_probe.py`, tempfile.TemporaryDirectory() 격리)으로 검증했다.

- 지점1(temp 생성 전/중): 잔여 `.tmp` 파일이 있어도 무시되고 정상 bootstrap — PASS
- 지점2(replace 후 ledger 전): 기존 물리 baseline을 그대로 winner로 채택(새로 안 만듦) — PASS
- 지점3(READY 저장 후 coordination 전): outbox에 1건 대기, journal 미기록 상태를 만든 뒤 재시도 시 정확히 1회만 append — PASS
- 지점4(journal append 후 ack 전): journal은 이미 기록됐고 outbox만 대기 상태에서 재시도해도 중복 라인 없이 ack만 완료 — PASS
- 지점5(READY인데 baseline 없음/손상/ID불일치, 3가지 손상 모드): 매번 `degraded` + `baseline_state_mismatch`로 전이, 물리 파일에 대한 쓰기 자체가 0바이트(디스크 비교로 확인), 나중에 정상 파일을 복원해도 degraded 유지(자동 복귀 없음) — PASS

두 문장 계약(READY⇒물리/원장 스냅샷ID 일치, 증명 불가시 조용한 재기준화 금지)도 위 5개 케이스 전반에서 함께 확인됨.

### D2 — 실제 2-OS-프로세스 경합 (Q2/Q4)

`subprocess.Popen`으로 진짜 별도 OS 프로세스 2개를 배리어 파일로 동기화해 거의 동시에 실행시키는 방식으로 3세트(각 40회, 총 120회) 실험했다.

- raw CAS 계층(`initialize_turn_baseline`) 40/40: 항상 정확히 (created, existing) 1쌍, 예외/CONFLICT 0건
- 신규 부트스트랩 경합 40/40: 양쪽 다 성공, physical baseline == ledger.baseline_snapshot_id 40/40 일치, 예외 0건
- 기존 missing-baseline 복구 경합 40/40: 마찬가지로 physical/ledger 일치 40/40, **coordination journal의 recovered 이벤트가 정확히 1건**(0건도 2건 이상도 전혀 없음)으로 dedup 불변식 유지

설계 문서가 인용한 "고치기 전" 관찰(40회 중 32/8 분포, 승자 비결정적)은 120회 전부에서 단 한 번도 재현되지 않았다.

### D3 — coordination outbox (Q1) — **결함 발견**

세 가지를 검증했다: (a) 256개 초과 시 degraded 처리 (b) 스키마 오류 시 같은 degraded 처리 (c) drain의 at-least-once dedup/ack.

- (a) PASS: 256개를 채운 뒤 257번째 시도 시 거부(accepted=False), outbox는 256 유지, `coordination_degraded=True`가 실제로 저장됨
- (c) PASS: 같은 이벤트를 두 번 append해도 journal 라인이 늘지 않고, 크래시 재시도 시나리오에서도 outbox→delivered 이관이 중복 없이 idempotent
- (b) **FAIL**: `event_id`만 필드로 채우고 다른 필수 필드(예: `reason_code`)가 빠진 outbox 항목을 넣으면, 그 순간에는 통과하지만 뒤이은 `save_ledger()`(내부적으로 `serialize_v2_ledger()`→`validate_v2_ledger()`)에서 **처리되지 않은 `LedgerSchemaError`가 그대로 터진다**. 이 경우 ledger 파일 자체가 기록되지 않고, `coordination_degraded` 플래그도 어디에도 남지 않는다 — "초과일 때와 똑같이 안전하게 보고한다"는 설계 계약과 정반대다.

상세는 아래 "결함 상세" 절 참조.

### D4 — Q5 exclusion-filtered replay

설계 문서 "필요한 테스트" 1~5를 각각 독립 스크립트로 재현(fixture 재사용 없이 직접 구성):

1. peer.py 제외 시 own.py만 반영, peer.py는 caller 소유로 전혀 등록 안 됨 — PASS
2. own.py가 이미 다른 관찰자 소유일 때 observed_by 병합 + contended 승격 — PASS
3. 같은 post에서 own.py 신규 + peer.py 제외: 즉시 반영분에 peer.py 유출 없음 — PASS
4. peer 증거 없음/lease 만료: INCOMPLETE, replay 0건 — PASS
5. PostToolUse 없이도 Stop allow(경량 대조) — PASS

추가로: 설계 문서가 "위험하다"고 명시한 1줄 치환(엄격비교 단순 대체)을 별도 스크립트로 그대로 재구현해 실행했더니, 실제로 peer.py가 caller 소유로 잘못 등록되는 버그가 재현됐다 — 즉 이번 프로브가 "아무거나 통과시키는" 무의미한 테스트가 아니라 실제로 그 결함을 가려낼 수 있음을 대조군으로 증명했다. Windows 대소문자 우회 시도도 막혀 있음을 확인(정책이 casefold일 때 대소문자만 다른 제외 경로도 정상 차단, 정책이 case-sensitive일 때는 과잉 차단하지 않음).

### D5 — 회귀

- 지목된 "PostToolUse 기록 없이 Stop allow" 테스트(`test_pretool_peer_rescue_atomically_recovers_missing_turn_without_posttool`)를 f2gate 브랜치에서 실행 — PASS. main(533df02)에도 같은 테스트가 이미 있었고 거기서도 PASS — 즉 F2가 새로 만든 게 아니라 기존 계약을 그대로 지킨 것으로 확인.
- `test_multiagent_f3_observation.py` 11/11, `test_provenance_lifecycle.py`+`test_scorecard_coordination.py` 58/58, F2 자체 테스트군 48/48, **전체 스위트 786 passed / 0 failed**(221.77초).
- 별도 독립 프로브로 같은 계약을 처음부터 다시 구성해 재현 — PASS.

---

## 결함 상세 — coordination outbox 스키마 오류 시 무기록 예외 (D3)

### 조사 기록

가설 1: 스키마 검증 누락은 설계자가 "당연히 있을 것"이라 가정하고 실제로는 빠뜨린 구현 결함이다.
증거: `core/ledger.py`의 `enqueue_coordination_event()`/`_enqueue_coordination_raw()`는 삽입 전에 `event_id` 존재 여부만 확인하고, 그 외 필드(`reason_code` 등)의 존재는 검사하지 않는다. 반면 초과(256+1) 케이스는 삽입 시점에 즉시 거부되고 `coordination_degraded`가 정상 기록된다(프로브 결과 `accepted_257th_is_false/outbox_len_stays_256/degraded_flag_set_true` 전부 true) — 즉 "초과"라는 한 가지 실패 유형은 제대로 처리해놓고, "형식 오류"라는 다른 실패 유형은 처리 코드 자체가 없다.
신뢰도: 높음(채택).

가설 2: 이 결함은 이론적일 뿐 실제 프로덕션에서는 절대 도달 불가능하므로 실질 위험이 없다.
증거: 유일한 프로덕션 호출부(`scorecard_coordination.record_peer_coordination()`)는 항상 이미 스키마 검증을 통과한 `CoordinationEvent` 데이터클래스에서 payload를 만들어 넘기므로, 손상된 raw dict가 정상 흐름에서 자연 발생하기는 어렵다. 이 호출부는 넓은 `except Exception`으로 예외를 삼키고 `False`를 반환해 호출자를 죽이지는 않는다.
기각(부분): 가설 2는 "지금 당장 크래시로 사용자에게 보이지는 않는다"는 점만 맞다. 그러나 "예외가 삼켜진다"는 것과 "설계 계약(스키마 오류도 초과처럼 degraded로 안전 보고돼야 한다)이 지켜진다"는 것은 다른 문제다 — 프로브로 확인한 바 이 경로에서는 `coordination_degraded`도, 어떤 디스크 기록도 전혀 남지 않는다(완전한 무기록). 계약 위반 자체는 실재하며, `enqueue_coordination_event()`가 두 번째 호출부를 얻거나 덜 신뢰된 출처에서 raw payload를 받게 되는 순간 잠재 위험으로 전환된다.

가설 3: `record_event()`가 내부적으로 같은 미검증 삽입 경로를 타므로, 정상적인 턴 진행 중에도 이 예외가 우연히 발생하면 ledger 커밋 전체가 깨질 수 있다.
증거: `_record_event_locked()` 안의 `_enqueue_coordination_after_event()` 호출과, `record_event()` 끝의 `save_ledger()` 호출 모두 이 스키마 검증 실패를 붙잡는 try/except가 없다(코드 확인). 다만 내부적으로 생성되는 coordination payload는 항상 `coordination_event_json(event)`을 거친 검증된 값이라 실제로 손상된 값이 여기 들어갈 정상 경로는 확인되지 않았다.
기각: 가설 3의 "정상 진행 중 우연히 발생" 부분은 별도 프로브로 반증하지 못했다(재현 안 함) — 코드 구조상 가능성은 낮다고 판단하지만 확정적 기각은 아니다. 다음 리뷰에서 `record_event()` 경로 자체에 대한 fault-injection 프로브를 추가로 권고한다.

가설 4(경쟁): 이 예외는 어딘가 상위에서 결국 잡혀 `coordination_degraded`가 세팅될 것이다.
증거: 프로브 실행 결과 예외는 `enqueue_coordination_event()` 밖으로 uncaught 상태로 전파됐고(standalone 재현에서 전체 트레이스백 확보), `ledger.json` 자체가 기록되지 않아 `coordination_degraded` 필드가 존재하지 않는 상태(null)로 재조회됐다.
기각: 가설 4 — 실행 결과가 직접 반증했다. 상위에서 잡히는 지점은 프로덕션의 유일한 호출부(`record_peer_coordination`)뿐이며, 그마저도 "무기록으로 삼킨다"이지 "degraded로 안전 보고한다"가 아니다.

### 권고(수정은 이번 조사 범위 밖)

`enqueue_coordination_event()`(또는 `_enqueue_coordination_raw()`) 삽입 시점에 `core/ledger_schema.py`의 `_validate_coordination_outbox_entry`와 동등한 전체 형식 검사를 먼저 수행해 초과 케이스와 동일한 방식으로 즉시 거부+degraded 처리하거나, `save_ledger()` 호출부를 try/except로 감싸 스키마 실패 시 degraded로 폴백하도록 고치는 것을 권한다. 수정 후에는 이번과 동일한 방식(스키마 위반 항목을 tempfile 격리 root에서 직접 삽입)으로 재검증할 것을 권한다.

---

## 스코프 밖 (이번 5개 차원 지시에 포함되지 않음)

설계 문서의 표준 게이트 목록 중 `python eval/run_probes.py --strict`, `python eval/e2e_smoke.py`, SLO 벤치마크(`python -m eval.bench_provenance`)는 사용자가 지정한 5개 차원에 포함되지 않아 이번 검증에서 실행하지 않았다. main push 전 최종 게이트로 별도 확인이 필요하다.

## 재현 자료

프로브 스크립트 전량(fable-lite-f2gate 비수정, tempfile 격리 실행) `tmp/p4-probes/`:
`crash_cut_probe.py` · `cas_race_worker.py` · `bootstrap_race_worker.py` · `run_bootstrap_race_probe.py` · `analyze_results.py` · `probe_q1_overflow.py` · `probe_q1_outbox_invalid_entry.py` · `probe_q1_drain_dedup_ack.py` · `q5_exclusion_replay_probe.py` · `q5_naive_replay_control.py` · `p5_stop_allow_without_posttool.py` (+ 결과 JSON `results_cas.json`/`results_fresh.json`/`results_recovery.json`).

Workflow 실행 원장: `wf_cdeb58a6-d39` (5 agents, 0 errors, 212 tool uses, ~17.8분).
