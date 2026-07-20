# F2 / post_tool 설계 의견서 — Codex

- 기준: `main` / `533df02` (2026-07-20)
- 검토 범위: S1 Q1~Q4, S3 Q5
- 직접 판독: `core/adapter_observation.py`, `core/provenance_lifecycle.py`, `core/ledger.py`, `core/agent_log.py`, `core/scorecard_coordination.py`
- 보조 판독: `core/provenance_manifest.py`, `core/provenance_store.py`, `core/provenance_lifecycle_scope.py`, `core/provenance_observation.py`, `core/ledger_v2.py`, 관련 테스트
- 이 의견서 외 production/test 소스는 수정하지 않았다.

## 결론 요약

| 질문 | 입장 | 선택 설계 |
|---|---|---|
| Q1 | **(a+) baseline+ledger만 authoritative transaction** | coordination journal append는 unlock 뒤 파생 관측으로 둔다. 단, 현재처럼 유실 가능한 fire-and-forget으로 끝내지 않고 ledger에 durable outbox를 함께 커밋하여 at-least-once로 투영한다. |
| Q2 | **(iv) 복구 가능한 순서 프로토콜 + 좁은 (ii) locked primitive** | `begin_invocation` 전체나 공용 락을 재진입화하지 않는다. public `record_event`는 유지하고 private lock-held reducer/save를 분리해 짧은 bootstrap helper 하나만 락을 소유한다. |
| Q3 | **완전 XA 원자성보다 self-heal 또는 explicit degraded면 충분** | process crash의 모든 절단점에서 재시도 가능해야 한다. `ready`인데 baseline이 없거나 ID가 다르면 현재 snapshot으로 재기준화하지 않고 명시적으로 degraded 처리한다. power-loss 내구성은 별도 계약이다. |
| Q4 | **경합 보호 필수** | 초기 baseline은 first-valid-write-wins, 이후 candidate 확장은 expected baseline ID + manifest generation CAS로만 허용한다. 파일 전체를 무조건 최초쓰기 불변으로 만들지는 않는다. |
| Q5 | **non-excluded replay 허용, 단 exclusion-filtered로 구현** | `_complete_observation()` 1줄 치환만 하면 excluded peer delta를 caller 소유로 오귀속할 수 있다. turn-baseline replay에서 `result.snapshot.exclusions`의 canonical key를 먼저 제거한다. |

## 1. 현행 코드에서 확인한 사실

### S1 호출/저장 경계

`begin_invocation()`은 다음 순서다.

1. `_baseline_missing()`이 ledger를 락 밖에서 먼저 읽는다 (`adapter_observation.py:113-121`, `:577-582`).
2. `ProvenanceLifecycle.resume_turn(... allow_full_bootstrap=True)`이 실제 baseline 파일을 읽고, 없으면 `save_turn_baseline()`으로 현재 snapshot을 저장한다 (`provenance_lifecycle.py:289-324`, 특히 `:316`).
3. candidate prime 뒤 `_record_invocation()`이 `baseline_status=ready`와 `baseline_snapshot_id=started.snapshot_id`를 ledger event로 보낸다 (`adapter_observation.py:452-481`).
4. `record_event()`만 `ledger_transaction`을 소유한다 (`ledger.py:114-141`). ledger 저장과 agent JSONL append가 끝난 뒤 락 밖에서 `_record_coordination_after_event()`를 호출한다.
5. coordination writer는 다시 별도의 `ledger_transaction`을 획득하고 JSONL append+`fsync`한다 (`scorecard_coordination.py:266-290`, `:319-342`).

`ledger_transaction()`은 동일 프로세스/스레드도 재진입할 수 없다. owner가 `pid:uuid`이고 살아 있는 자기 PID는 stale로 보지 않으므로, 같은 root의 중첩 획득은 deadline까지 기다린 뒤 `TimeoutError`다 (`agent_log.py:96-172`).

baseline 저장의 `tempfile + os.replace`는 torn JSON은 피하지만 semantic CAS는 아니다 (`provenance_store.py:60-63`, `:91-110`). 두 writer 모두 서로 다른 정상 snapshot을 성공적으로 replace할 수 있고 마지막 replace가 이긴다. 반면 manifest 전이는 이미 `manifest_pending -> workspace snapshot -> optional baseline -> finalize` 복구 구조를 갖고 있다 (`provenance_manifest.py:108-210`). F2는 이 선례를 재사용하는 편이 맞다.

### S3 `post_tool` 경계

- `COMPLETE` 계열 공통 predicate는 `COMPLETE`와 `COMPLETE_WITH_EXCLUSIONS`를 모두 포함한다 (`provenance_lifecycle.py:142-146`). start/resume/clean-claim 경로는 이미 이 predicate를 쓴다 (`:243`, `:309`, `:677`).
- `post_tool()`의 turn-baseline replay만 `result.status is COMPLETE` 엄격 비교가 남았다 (`:326-348`).
- 다만 초안의 “WITH_EXCLUSIONS이면 델타가 모두 미기록”은 범위가 넓다. 새 shared-current 전이는 그 전에 `_commit_if_current()`가 current→new snapshot delta를 `record_deltas()`하고 manifest event로 커밋한다 (`:539-601`). 엄격 비교가 막는 것은 그 뒤의 **turn-baseline→current 누적 replay**다.
- `record_deltas()`는 같은 change를 다른 agent가 다시 보면 `observed_by`를 합치고 owner를 제거하여 `contended`로 만든다 (`provenance_observation.py:16-50`). 따라서 누적 replay 누락은 주로 장수명 lifecycle/direct API의 관측자·경합 귀속 불일치다. production adapter는 hook마다 새 lifecycle을 만들고 replay의 반환값을 `ObservationResult.changes`에 합치지 않으므로, Q5의 최소 수정만으로 새 ledger change event가 늘어나는 것은 아니다.

## Q1. 원자화 범위

### 입장: (a+) authoritative 2-file commit

authoritative 불변식에 포함할 것은 다음 둘이다.

1. 해당 `(agent_key, turn_id)`의 실제 baseline 파일
2. ledger의 `baseline_status`, `baseline_snapshot_id`, recovery/degraded 상태

coordination journal은 게이트 판정 입력이 아니라 보고용 파생 채널이다. 세 파일을 같은 락 아래 쓰더라도 cross-file crash atomicity가 생기는 것은 아니며, coordination I/O 실패가 gate 상태를 롤백하게 만들면 현재 fail-open 계약과 충돌한다. 실패를 삼키면 애초에 3-file atomic commit도 아니다. 따라서 journal write 자체는 authoritative transaction 밖에 두는 것이 맞다.

다만 **현행 fire-and-forget을 그대로 유지하는 것은 반대**한다. ledger commit 직후 coordination 호출 전에 죽으면 recovered 관측이 영구히 사라진다는 것이 실측됐다. 선택안은 다음과 같다.

```text
짧은 owning ledger transaction
  1) baseline create/adopt/CAS
  2) baseline을 다시 읽어 snapshot_id 검증
  3) ledger MISSING -> READY 전이
  4) 같은 ledger write에 coordination_outbox[event_id] 저장
unlock
  5) coordination.jsonl에 exact event append/dedup
  6) 성공 또는 identical-existing이면 outbox ack
```

outbox에는 event ID만이 아니라 `coordination_event_json()`의 완전한 정규형(특히 `occurred_at`, `evidence_refs`)을 보존해야 한다. journal append 뒤 ack 전에 crash해도 동일 content로 재시도되어야 `record_coordination_event()`의 existing-equal 분기가 성공적인 dedup이 된다. `try_record_coordination_event()`의 `False`는 “동일 항목 존재”와 “오류/충돌”을 구분하지 못하므로 drainer는 strict writer를 호출하고 `True` 또는 identical `False`만 delivery로 인정해야 한다.

outbox는 bounded(예: 256)여야 하고 overflow/스키마 오류는 `coordination_degraded=true`처럼 보고해야 한다. 다음 ledger write/session bootstrap에서 drain하며, gate decision은 outbox backlog와 무관하게 유지한다. 이렇게 하면 coordination은 transaction 밖에 있으면서도 at-least-once 파생 관측이 된다.

### 리스크

- outbox가 새 ledger top-level 구조라면 `ledger_schema.py` 검증과 최대 크기 제한이 필요하다.
- delivery ack가 별도 transaction이므로 lock 횟수는 늘지만, bootstrap recovery는 희소 경로이고 journal append는 이미 별도 transaction이다.
- TURN_BOOTSTRAP stable ID는 현재 evidence ref를 ID 입력에서 제외한다. outbox는 최초 winner의 exact content를 재사용해야 concurrent loser와 content conflict가 나지 않는다.
- active-turn 내부 pending 필드로만 두면 turn GC/finish 때 유실될 수 있으므로 global bounded outbox가 더 안전하다.

### 예상 diff

- production: 약 60~110 LOC (`ledger.py`, `scorecard_coordination.py`, `ledger_schema.py` 중심)
- tests: 약 50~90 LOC (ledger-commit 직후 crash, append 뒤 ack crash, retry/dedup, overflow/degraded)

## Q2. 락 구조

### 입장: (iv) 상태기계 + 좁은 (ii), 일반 재진입화는 하지 않음

`ledger_transaction`의 전역/thread-local 재진입화는 선택하지 않는다.

- 재진입은 deadlock만 없앨 뿐 두 파일의 commit/rollback이나 crash 복구를 제공하지 않는다.
- `begin_invocation` 전체를 outer lock으로 감싸면 `ProvenanceLifecycle.__init__ -> load_manifest_view`, candidate prime의 load/commit, covers capture, `record_event`까지 모두 중첩된다.
- candidate scan은 증분 budget 2초이고 CAS 재시도가 있어, root-global 락을 스캔 동안 잡으면 concurrent Stop의 8초 예산을 잠식한다.
- `threading.local` 방식은 root 정규화, fork 후 inherited depth 초기화, thread 간 배제, A→B→A lock 순서까지 새 정확성 표면을 만든다.

대신 public API는 그대로 두고 짧은 private primitive를 분리한다.

```python
def record_event(payload):
    with ledger_transaction(root) as transaction:
        committed = _record_event_locked(payload, transaction)
    _drain_postcommit_observations(committed)
    return committed.ledger
```

- `ledger_transaction()`이 opaque transaction token을 yield하고, `_record_event_locked()`는 token의 active/root 일치를 assert한다. 단순 `assume_held=True` boolean보다 오용을 잡기 쉽다.
- `record_event()`의 public signature/return은 바꾸지 않는다.
- `provenance_manifest` 계층에 `ensure_turn_bootstrap(...)` 같은 전용 owner를 둔다. 이 함수만 `ledger_transaction`을 잡고 direct workspace/baseline load와 `_record_event_locked()`를 호출한다.
- full scan/candidate scan은 락 밖에서 수행하고, commit 직전에 generation/snapshot/baseline expected ID를 재검증한다. 실패하면 bounded retry한다.

### 구체 상태기계

사전판독은 최적화 hint로만 허용하고 recovery truth는 락 안에서 다시 계산한다. `_baseline_missing()`이 boolean 결정을 미리 확정하는 구조는 제거한다.

| ledger 상태 | baseline 파일 | 처리 |
|---|---|---|
| turn 없음 | 없음 | 새 turn bootstrap. recovered coordination은 만들지 않는다. |
| `missing` | 없음 | 검증된 workspace current를 초기 baseline으로 CAS-create한 뒤 실제 저장 ID로 READY 전이한다. current가 없으면 락 밖 full bootstrap 후 재시도한다. |
| `missing` | 정상 파일 있음 | 이전 crash의 PREPARED residue다. **그 파일을 winner로 채택**하고 그 ID로 READY를 finalize한다. current로 덮지 않는다. |
| `ready` | 정상 파일, ID 일치 | no-op. 기존 baseline을 load하고 일반 invocation 경로로 간다. |
| `ready` | 파일 없음/손상/ID 불일치 | 재기준화 금지. explicit DEGRADED 전이와 incomplete report를 남긴다. |
| `degraded` | 무엇이든 | 자동 bootstrap 금지. 진단 상태를 유지하고 정책상 명시된 복구만 허용한다. |

권장 순서는 다음과 같다.

1. lock 안에서 manifest pending recovery와 ledger/physical baseline을 재판독한다.
2. `missing+absent`면 baseline을 먼저 원자 저장한다. `missing+present`면 existing 파일을 채택한다.
3. baseline을 다시 load하여 parse와 snapshot ID를 확인한다.
4. 그 **persisted winner ID**로 전용 `turn_bootstrap_recovered` event를 `_record_event_locked()`에 적용한다. `started.snapshot_id`를 baseline ID로 사용하지 않는다.
5. 같은 ledger 저장에 Q1 outbox를 넣고 unlock한다.
6. 새 `ProvenanceLifecycle`을 만들어 authoritative baseline을 다시 load한 뒤 candidate prime과 invocation 기록을 진행한다. recovery event와 invocation event를 분리하므로, 둘 사이 crash 시 “tool은 아직 실행되지 않았고 baseline은 READY”라는 안전한 상태다.

candidate prime은 기존 manifest WAL을 계속 사용하되, optional baseline을 finalize할 때 active turn의 `baseline_snapshot_id`도 같은 ledger transaction에서 갱신해야 한다. 현재는 physical baseline만 바꾼 뒤 나중 invocation event가 ID를 맞추므로 그 사이 crash 창이 남는다. `provenance_lifecycle_scope.py`의 CAS retry도 최신 view마다 `_new_existing_candidates()`를 다시 계산해야 stale candidate가 winner baseline을 덮지 않는다.

### 락 crash 복구의 인접 조건

`agent_log._stale_record()`은 mtime이 10초 이하이면 PID 생존 여부조차 검사하지 않는다 (`agent_log.py:96-106`). 따라서 막 죽은 owner의 fresh lock은 기본 15초까지 recovery를 막을 수 있다. process-crash recovery를 SLO 안에서 보장하려면:

- 유효한 `pid:token`이고 PID가 확실히 죽었으면 age와 무관하게 즉시 reclaim
- malformed/부분기록 또는 PID 확인 불능일 때만 10초 grace
- live PID는 age와 무관하게 절대 탈취 금지

가 필요하다. PID reuse 위험은 잔존하므로 owner start-time까지 넣는 개선은 별도 hardening으로 볼 수 있다.

### 기각한 대안

- **(i) 일반 재진입 락**: 구현은 쉬우나 crash invariant가 없고 critical section이 과도하게 커진다.
- **(iii) baseline의 ledger 내 통합**: 큰 snapshot을 hot ledger에 넣어 모든 event write/validation 비용과 스키마 파급을 키운다.
- **별도 대형 bootstrap WAL**: 초기 baseline 파일 자체가 PREPARED intent 역할을 할 수 있어 과하다. 기존 `baseline_status=missing` + ordered write + 실제 파일 ID 검증으로 process-crash를 복구할 수 있다. 다만 candidate baseline 갱신은 기존 manifest WAL에 ledger-ID finalize를 추가해야 한다.

### 리스크

- 새 bootstrap event의 sequence 증가가 verification epoch나 open invocation close에 영향을 주지 않도록 reducer 계약을 고정해야 한다.
- private locked primitive가 lock 밖에서 호출되면 기존보다 더 위험하므로 opaque token 검증과 misuse 테스트가 필수다.
- full scan을 락 밖으로 뺀 뒤 commit CAS가 반복 실패할 수 있으므로 retry 상한과 explicit incomplete 반환이 필요하다.

### 예상 diff

- production: 약 80~140 LOC (`agent_log.py`, `ledger.py`, `provenance_manifest.py`, `provenance_lifecycle.py`, `adapter_observation.py`)
- tests: 약 100~170 LOC (locked primitive 오용, scan-outside-lock, 2-process winner, manifest retry, fresh dead/live owner)

## Q3. crash-safety 목표

### 입장: self-heal/degraded가 충분하며, “완전 원자성”이라고 부르지 않는다

서로 다른 baseline 파일, ledger JSON, coordination JSONL을 범용 파일시스템에서 XA처럼 보이게 만드는 것은 이 트랙의 위험/비용에 비해 이득이 작다. 필요한 계약은 다음 두 문장이다.

1. `READY => readable physical baseline exists && physical.snapshot_id == ledger.baseline_snapshot_id`
2. 이 불변식을 증명하지 못하면 current로 조용히 재기준화하지 않고 `INCOMPLETE/DEGRADED`를 보고한다.

process crash 절단점별 기대 동작은 다음과 같다.

| crash 지점 | 남는 상태 | 다음 세션 동작 |
|---|---|---|
| baseline temp 생성 전/중 | ledger `missing`, destination 없음(임시 파일만 있을 수 있음) | temp를 무시하고 bootstrap 재시도 |
| baseline replace 후 ledger save 전 | ledger `missing`, valid baseline 존재 | existing baseline ID를 채택해 READY finalize |
| ledger READY save 후 coordination append 전 | READY + outbox pending | journal append 재시도 |
| journal append 후 outbox ack 전 | READY + journal event + outbox pending | exact same event dedup 후 ack |
| ledger READY인데 baseline 없음/손상/ID 불일치 | 모순 상태 | `baseline_state_mismatch` degraded; 자동 재생성 금지 |

`baseline_status`에 `degraded`를 추가하는 것이 가장 명료하다. 스키마 변경을 피하려면 `missing + provenance_status_reason=baseline_state_mismatch`로 표현할 수도 있지만, 이 경우 일반 recoverable missing과 구분하여 bootstrap을 금지하는 별도 조건이 반드시 필요하다. 나는 상태 오판 가능성이 낮은 명시적 `degraded`를 선호한다.

adapter는 락/저장 오류를 예외로 hook 밖에 전파해 세션을 죽이지 말고 incomplete report를 반환해야 한다. 그러나 `ready`나 clean provenance를 주장해서는 안 된다. 즉 fail-open은 “프로세스가 죽지 않음”이지 “증거 없이 정상 상태를 선언함”이 아니다.

### 리스크와 내구성 범위

현행 baseline `_save()`와 ledger `atomic_write_text()`는 temp close + `os.replace`이지만 file/directory `fsync`가 없다. 따라서 위 계약은 **프로세스 crash/kill** 기준이다. OS/power loss까지 계약하려면 temp flush+`fsync`, replace 뒤 parent directory flush(플랫폼 지원 범위), 오류 주입 테스트가 추가되어야 한다. 이 경우 Windows/네트워크 볼륨 의미와 latency를 별도 측정해야 하며 이번 Medium 트랙의 기본 목표로 넣지 않는 편이 낫다.

### 예상 diff

Q2/Q4와 대부분 겹친다. 별도분은 status/reason/schema/진단 약 20~40 production LOC, crash-cut 테스트 약 60~100 LOC다.

## Q4. 동시 `save_turn_baseline` 덮어쓰기

### 입장: 보호가 반드시 필요하다

atomic replace는 “파일이 반만 써지지 않음”만 보장한다. 어떤 정상 snapshot이 turn baseline이어야 하는지는 보장하지 않는다. 같은 actor/turn에 서로 다른 current를 가진 두 recovery가 들어오면 둘 다 성공하거나 한쪽이 Windows sharing race로 `SnapshotStoreError`를 받고, 최종 winner는 비결정적이다.

다만 baseline에는 두 종류의 write가 있으므로 전역 “파일 최초 1회 영구불변”도 맞지 않는다.

1. **초기 recovery baseline**: first-valid-write-wins. existing valid 파일은 idempotent winner이며 절대 current로 덮지 않는다.
2. **PreTool candidate 확장**: 새 candidate를 mutation 전에 baseline scope에 넣는 의도된 갱신이다. `expected_baseline_snapshot_id`와 manifest generation이 모두 맞을 때만 CAS한다.

권장 storage API는 의미를 분리한다.

```text
initialize_turn_baseline(expected_absent, candidate) -> CREATED | EXISTING | CONFLICT
advance_turn_baseline(expected_snapshot_id, merged_snapshot, manifest_generation) -> COMMITTED | RETRY
```

- 모든 production 호출은 owning ledger transaction 또는 manifest transaction 안에서만 허용한다.
- 초기 경쟁 loser는 existing winner를 load하여 자기 lifecycle을 새로 만든다.
- ledger에는 `started.snapshot_id`가 아니라 저장 후 reload한 physical winner ID를 기록한다.
- candidate advance는 current snapshot 전체로 baseline을 단순 replace하면 안 된다. 이전 turn baseline의 이미 관측된 canonical key/bytes를 보존하고, 이번에 새로 primed된 candidate key만 mutation 전 값으로 merge해야 한다. 그렇지 않으면 앞선 tool delta가 baseline에 흡수되어 pending change가 사라질 수 있다.
- CAS retry 때 candidate set을 최신 current에 대해 다시 계산한다.

저수준 방어로 temp 파일을 같은 디렉터리에 만들고 no-replace publish를 사용할 수 있으나, Windows/파일시스템별 primitive 차이보다 공용 ledger lock + expected-ID 검사 + post-write reload 검증을 정본으로 삼는 편이 단순하다.

### 리스크

- `_safe_key()`로 정규화된 agent/turn 파일명이 서로 충돌할 수 있는 기존 표면도 CAS conflict로 드러날 수 있다. conflict를 overwrite로 해소하지 말고 degraded로 보고해야 한다.
- exclusion metadata가 있는 snapshot merge 시 excluded key를 새 baseline 값으로 오인하지 않도록 manifest 정본을 사용해야 한다.
- baseline advance가 event sequence/open-invocation close에 영향을 주지 않도록 전용 reducer 계약이 필요하다.

### 예상 diff

- production: 약 35~70 LOC (`provenance_store.py`, `provenance_manifest.py`, `provenance_lifecycle_scope.py`)
- tests: 약 70~120 LOC (Windows-compatible 2-process race, first-winner, intentional candidate CAS/merge, stale retry)

## Q5. `COMPLETE_WITH_EXCLUSIONS`의 non-excluded delta

### 입장: 기록하되 exclusion-filtered replay로 한정

“제외가 하나라도 있으면 관측 전체의 delta를 불신”할 근거는 코드의 exclusion 모델과 맞지 않는다. peer rescue는 모든 issue가 `unstable_path/unreadable_path`이고 유효한 peer open candidate와 매치될 때만 성립한다 (`provenance_lifecycle.py:68-108`). 그 뒤 excluded key만 현재 스캔에서 버리고 이전 shared-current entry를 carry하며, 다른 entry는 정상 scan 결과를 유지한다 (`:109-130`). 따라서 non-excluded path는 신뢰할 수 있다.

그러나 다음 1줄 교체는 안전하지 않다.

```python
if _complete_observation(result.status) and self._state.current is not None:
```

turn baseline과 shared current 사이에서 peer path가 이미 바뀐 뒤 이번 관측에서 그 path가 excluded되면, current에는 이전 shared-current의 “peer 변경 후” entry가 carry된다. 단순 baseline replay는 그 peer delta까지 다시 계산한다. 해당 path가 caller candidate에도 들어 있으면 `record_deltas()`가 이를 caller의 `source=edit, owner=caller`로 만들 수 있다.

선택 구조는 다음과 같다.

```python
if (
    _complete_observation(result.status)
    and not result.incomplete
    and self._state.current is not None
):
    excluded_keys = {
        canonical_manifest_key(item.path, self._state.current.is_casefolded)
        for item in (result.snapshot.exclusions if result.snapshot else ())
    }
    replay = tuple(
        delta
        for delta in self._observable_deltas(turn.baseline, self._state.current)
        if delta.canonical_key not in excluded_keys
    )
    record_deltas(self._state, ObservationInput(replay, ...))
```

이 설계의 의미는 명확하다.

- 이번 snapshot에서 실제로 제외된 key는 귀속하지 않는다.
- unrelated exclusion 하나가 다른 non-excluded delta의 `observed_by`/`contended` 승격까지 막지 못한다.
- unmatched/expired peer evidence는 기존대로 INCOMPLETE이므로 replay하지 않는다.
- non-candidate delta는 기존 `_source_for_delta()`에 의해 external로 남는다.
- excluded target을 반복해서 불안정하게 만드는 공격은 여전히 관측을 지연할 수 있지만 이는 Q5 이전부터 존재하는 peer-exclusion 정책 위험이다. snapshot exclusion에 peer/turn/invocation 증거가 남고 다음 scan에 강제 포함된다. `PEER_EXCLUSION` coordination emission은 후속 audit hardening 후보이다.

기존 “PostToolUse 기록이 없어도 Stop allow” 테스트는 실제 post_tool을 호출하지 않고 recovered bootstrap ledger를 평가한다 (`tests/test_multiagent_f3_observation.py:322-324`). 위 변경과 독립이므로 그 allow 계약을 그대로 회귀 테스트로 유지한다.

### 리스크

- exclusion path는 `SnapshotExclusion.path` 문자열 비교가 아니라 snapshot의 casefold 정책으로 canonicalize해야 Windows 우회를 막을 수 있다.
- direct API의 in-memory 귀속 개선과 adapter의 durable change event 증가를 같은 acceptance로 쓰면 구현이 과소평가된다.
- 유효한 peer lease로 target을 계속 unstable하게 만드는 지연 공격은 남는다. 이는 Q5 필터가 아니라 peer-exclusion audit/lease 정책에서 다뤄야 한다.

### 중요한 acceptance 범위

minimal Q5 fix는 in-memory `state.changes`의 baseline replay/관측자 정합화다. replay에서 새로 추가된 change를 `ObservationResult.changes`에 merge하지 않는 현 구조상, 이 수정만으로 adapter ledger에 누적 change event를 새로 내보내지는 않는다. “누적 replay도 새 audit event로 영속화”가 요구사항이면 generation-bound event dedup과 result merge를 별도 설계해야 하며, 이번 Q5의 1조건 정합화로 가장하면 안 된다.

### 필요한 테스트

1. caller baseline 뒤 updater가 `own.py`와 `peer.py`를 진전시키고, caller post가 `peer.py`만 exclusion한 경우: replay는 `own.py`만 포함하고 `peer.py`를 caller-owned로 만들지 않는다.
2. 기존 `own.py` change가 다른 observer 소유라면 non-excluded replay 후 `observed_by`가 합쳐지고 `contended/external`이 된다.
3. 같은 post에서 새 `own.py` delta + `peer.py` exclusion인 경우: immediate `result.changes`/ledger에는 `own.py`만 있고 peer false-delete/change는 없다.
4. peer evidence가 없거나 lease가 만료된 경우: INCOMPLETE, replay 0.
5. bootstrap 후 PostToolUse 미기록이어도 Stop allow라는 기존 테스트 유지.

### 예상 diff

- production: 약 15~35 LOC (`provenance_lifecycle.py`, 필요 시 작은 exclusion-key helper)
- tests: 약 70~110 LOC (`test_multiagent_f3_observation.py` 또는 lifecycle 테스트)
- 누적 replay의 ledger 영속화까지 요구하면 추가 40~80 production/test LOC가 필요하다.

## 2. 격리 실측 근거

모든 probe root는 OS temp 아래에 만들었고 repository source를 쓰지 않았다.

### 2.1 비재진입 락

`FABLE_LITE_TEST_LOCK_WAIT_SECONDS=0.20`으로 같은 thread에서 동일 root `ledger_transaction`을 중첩했다.

- 결과: `TimeoutError`, 211.3~211.8 ms
- 뜻: outer lock만 추가하고 현 `record_event/load_manifest_view`를 그대로 호출하는 설계는 즉시 자기 deadlock 경로가 된다.

### 2.2 baseline 경합

두 별도 Python 프로세스를 같은 시각에 시작해 동일 actor/turn에 A/B snapshot을 저장하는 실측 40회:

- 둘 다 성공: 32/40
- 한쪽 `SnapshotStoreError`, 다른 쪽 성공: 8/40
- 최종 winner: A 19회, B 21회
- 손상/읽기불가: 0회

즉 atomic replace는 무손상 파일은 제공하지만 winner 의미와 writer 성공을 보장하지 않는다.

두 lifecycle이 서로 다른 manifest A/B를 이미 읽은 상태에서 순서를 강제로 교차한 probe 20회:

- 최종 baseline=B: 20/20
- 두 recovery invocation agent event: 20/20
- stable ID dedup으로 recovered coordination은 1건: 20/20

Wave4 dedup은 coordination count만 수렴시켰고, baseline first-winner/CAS 문제는 남아 있다.

같은 lifecycle에서 `app.py` 변경을 먼저 post-tool로 관측한 뒤 새 `late.py` candidate를 prime한 순차 probe도 수행했다.

- prime 전 pending change: 1건
- prime 후 pending change: 0건
- prime 뒤 turn baseline의 `app.py` digest가 변경 후 current digest와 동일: 참

즉 현 candidate prime은 새 key만 보강하지 않고 current snapshot 전체를 baseline으로 바꾸어 이전 tool delta를 실제로 흡수한다. Q4의 “초기 FWW + candidate별 CAS merge” 구분은 경합 방어뿐 아니라 이 순차 소거를 막기 위해서도 필요하다.

### 2.3 baseline 저장 뒤 ledger 전이 전 crash

baseline A를 저장하고 ledger는 `missing`인 지점에서 중단한 뒤 workspace current를 B로 진전시키고 다음 `begin_invocation`을 호출했다(20회).

- physical baseline은 A 유지: 20/20
- report와 ledger baseline ID는 B: 20/20
- file/ledger ID 불일치인데 ledger `ready`: 20/20
- coordination은 `entered,recovered`로 겉보기 정상

원인은 기존 baseline A를 `resume_turn()`이 로드해도 `_record_invocation()`에 넘기는 값이 persisted turn baseline ID가 아니라 `started.snapshot_id`(current B)이기 때문이다. 이 결과가 Q2/Q3의 post-write reload와 winner-ID 기록 요구를 직접 뒷받침한다.

### 2.4 역방향 반쪽 상태

ledger는 `ready(snapshot:A-original)`, physical baseline은 없음, workspace current는 mutation 뒤 B인 root에서 `begin_invocation`을 호출했다.

- physical baseline과 ledger ID가 모두 B로 조용히 재생성/갱신
- report `incomplete=False`
- recovered coordination 없음

이는 원래 A 기준선 이후의 변화를 기준선 안으로 흡수할 수 있으므로 반드시 degraded로 바꿔야 한다.

### 2.5 ledger commit 뒤 coordination 전 crash

recovery ledger save 직후 coordination 호출 전에 process-death를 주입한 10회:

- ledger READY: 10/10
- recovered coordination 누락: 10/10
- 이후 routine invocation을 기록해도 계속 누락: 10/10

따라서 Q1에서 단순 post-commit best-effort만 유지해서는 Q3의 자가복구 조건을 만족하지 못한다.

### 2.6 Q5 경계

격리 lifecycle probe 결과:

- 이번 post에서 새로 생긴 non-excluded `own.py`는 현 strict guard에서도 immediate current-transition change로 기록됐다.
- shared current가 먼저 진전된 누적 replay에서는 strict guard 결과가 0건이었다.
- 단순 `_complete_observation` 적용을 모사하면 `own.py`, excluded `peer.py`가 모두 추가되었고 둘 다 `source=edit, owner=caller`가 됐다.
- exclusion canonical key를 제거한 replay는 `own.py`만 추가했다.

따라서 “유지 vs 1줄 정합화”의 이분법보다 exclusion-filtered 정합화가 정확하다.

## 3. 검증 기준과 총 diff 추정

현재 코드 기준 관련 회귀군을 명시적 Git Bash 경로와 UTF-8 환경으로 실행했다.

```powershell
& 'C:\Program Files\Git\bin\bash.exe' -lc \
  'cd /c/Users/gustj/fable-lite-dev && PYTHONIOENCODING=utf-8 python -m pytest \
   tests/test_multiagent_f3_observation.py \
   tests/test_provenance_lifecycle.py \
   tests/test_scorecard_coordination.py \
   tests/test_p4_core_regressions.py -q'
```

결과: **46 passed in 4.34s**. 이는 현행 기준선 검증이며 제안 구현 검증을 대신하지 않는다.

S1 선택안 전체는 중복을 제거하면 production 약 160~260 LOC, tests/support 약 180~280 LOC, 7~10 files로 예상한다. durable coordination outbox를 별도 generic 구조로 만들면 production이 약 40~80 LOC 늘 수 있다. Q5 exclusion-filtered fix는 production 15~35 LOC, tests 70~110 LOC다. 전체 트랙은 대략 **production 175~335 LOC + tests 250~390 LOC** 범위이며, 단순 Medium보다는 Medium-Large로 잡는 편이 안전하다.

구현 게이트에는 최소 다음이 필요하다.

- 실제 OS process 2개 동시 bootstrap: ledger/baseline ID 일치, recovery winner 1명, invocation은 각자 보존
- crash cut: baseline 전/후, ledger 전/후, coordination append 전/후
- ready+missing/corrupt/mismatch의 explicit degraded 및 자동 재기준화 금지
- candidate baseline CAS retry/merge와 기존 pending delta 보존
- dead owner 즉시 회수와 live owner 비탈취
- Q5 non-excluded replay + excluded non-attribution
- 기존 peer filter/F0~F4/verification epoch/Stop allow/8초 예산 무회귀
- 전체 pytest, strict probes, e2e smoke, Windows+Ubuntu CI

최종 판단은 **Q1 (a+) / Q2 (iv + 좁은 ii) / Q3 recover-or-degrade / Q4 초기 FWW + 갱신 CAS / Q5 exclusion-filtered 정합화**다.
