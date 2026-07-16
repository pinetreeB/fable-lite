# 설계 — 멀티에이전트 게이트 본편 (v-next)

> 2026-07-16 rev3. 3-AI 토론 합의본: 좌상 원안 rev0 → codex 프로브 검토 + agy 적대 검토 → rev1 → 미션모드 1차(codex 52·crit 5 / agy 70·crit 1) 반영 → rev2 → **2차(codex 72·crit 3 / agy 95·crit 0) 반영 보완** → rev3.
> 실측 근거: `docs/reviews/2026-07-16-observation-error-third-path.md` · `2026-07-16-multiagent-repair-destroys-concurrent-work.md` · `2026-07-15-home-root-provenance-scope-incident.md` · 07-14 근거 #3 · `docs/design/wmux-orchestration.md`.
> 토론 산출물: `tmp/multiagent-gate/{debate-codex,debate-agy,mco-codex,mco-agy,mco-claude}.md`

## 0. 문제 정의 (실측 → 결함 3계열)

같은 프로젝트 루트에서 여러 AI 세션이 동시에 작업하는 것이 기본 운영 형태(wmux 4-pane)인데, 하네스는 단일 에이전트 전제라 다음이 구조적으로 발생한다:

| 계열 | 실측 | 근본 원인 |
|------|------|----------|
| A. 관측 오염 | observation_error 차단(재현 15/15) — 동료가 쓰는 동안 스캔이 흔들리면 차단, 당한 쪽은 자구책 없음 | 흔들림의 원인(동료의 기록된 활동)을 구분할 정보가 관측 경로에 없음 |
| B. 상태 오염 | 계약 오염(A세션 contract로 B세션 판정) · v1 projection last-writer-wins · active_turns 26개 무한누적 · Stop 카운터 전역 폴백 | `contract.json`·projection·카운터 폴백이 프로젝트 전역 단일 |
| C. 소유권 부재 | 서브에이전트가 동료 미커밋 변경을 `git checkout --`/`rm`으로 파괴(07-16 실사고) | "이 변경은 누구 것"이라는 귀속 정보가 없어 파기 감지·차단 불가 |

## 1. 위협 모델 (명시)

- **방어 대상**: 합리화·우발로 게이트를 우회하려는 하위 AI 모델 — "그건 동료가 바꿨다" 세탁, 관측 실패 핑계, 계약 재사용, 파괴 조치의 무단 실행.
- **비보장**: 같은 OS 계정 프로세스의 **강한 적대**(HMAC 시크릿 외부 보관·ACL·브로커 없이는 원천 불가). 상태 조작의 *완전 차단*은 주장하지 않는다 — 대신 §6-5의 **이중 근거 교차 확인**으로 "조작해도 이득이 없게"(면제 불성립) 만들고, 조작 시도는 append-only 감사 로그에 영구히 남긴다.
- **비범위 공격**: 동료 겨냥 DoS(타 에이전트 파일에 쓰레기 주입해 차단 유도) — external 변경은 검증/원복으로 해소 가능, 고의 방해는 오케스트레이션 레이어 소관.

## 2. 설계 원칙

1. **귀속은 주장이 아니라 관측으로만 성립**: 자기 신고로는 아무것도 면제되지 않는다. 귀속 = (그 에이전트 자신의 기록된 도구 호출의 canonical candidate 경로 일치) + (관측된 콘텐츠 해시 일치)일 때만.
2. **완화 레이어는 fail-conservative, 게이트 코어는 fail-open 유지**: 이 설계가 추가하는 것은 두 종류다 — (a) *완화*(peer 면제·peer_activity·계약 네임스페이스)는 이상 상황(손상·용량 초과·파싱 실패)에서 **전부 꺼진다**(기존 보수 차단으로 강등). (b) *신규 보호*(R2 파기 차단)는 **fail-closed** — 귀속을 신뢰할 수 없으면 파괴 명령을 차단한다(§6-3·§6-4, 1차 심사 C3 반영: 보호가 소멸하는 방향의 강등 금지). 게이트 코어의 기존 fail-open(코드 예외 시 통과)·2회 cap은 불변.
3. **코어 플랫폼 중립**: wmux·MCP 의존 금지. 훅 payload의 identity(`host:session:agent`)만 사용.
4. **성능 SLO 준수**: Stop에서 파일 재해시 금지 — 기존 change event의 `after` digest 재사용(10k 대조 1.089ms 실측). 수용 여부는 **atomic write·Windows 락 포함 실측 후 판정**(선행 선언 금지 — 1차 심사 Low 반영).
5. **하위 호환**: 단일 에이전트 프로젝트는 peer 레코드가 생기지 않아 §6 완화 경로 전부 무발동. 구 ledger는 필드 부재 시 빈 값.

## 3. 선행 수정 (F0 — 이것 없이는 귀속 신뢰 불가)

### 3-1. F0a: persisted generation CAS + 이벤트 순서 결합 (codex C1 반영)
- 현행: `_generation_current`/`_commit_if_current`가 인스턴스 메모리 generation만 비교 → cross-process stale snapshot commit 승인·최신 digest 소실(프로브 실측).
- 수정 1: `manifest_generation`을 ledger에 영속화, 스냅샷 커밋 시 CAS — 불일치면 재스캔/rebase.
- 수정 2 (**1차 심사 C1**): change event에 **그 관측이 속한 commit generation을 결합**해 기록하고, `apply_v2_event()`는 `path_attribution` 갱신 시 **인덱스에 기록된 generation보다 낡은 이벤트를 거부**(구 generation 적용 금지). snapshot 커밋 순서와 change event 기록 순서가 역전돼도(별도 트랜잭션이므로 가능) 인덱스의 최신성이 깨지지 않는다.
- 수정 3 (**2차 심사 RC2 — crash-atomicity**): 기록 순서를 WAL형으로 고정 — ①change event를 ledger에 **선기록**(digest·generation 포함, `commit_state=uncommitted`) ②snapshot 저장 ③ledger 트랜잭션에서 `committed` 확정+generation CAS. 중간 crash 시 uncommitted event가 **변경 증거로 잔존**하므로 다음 lifecycle이 이를 감지해 재관측(baseline이 변경을 무증거 흡수하는 경로 차단). 현행(snapshot 저장 후 별도 트랜잭션 event 기록)은 snapshot 직후 crash에서 증거가 영구 소실됨 — 순서 자체를 뒤집는 것이 핵심.
- **uncommitted event 비권위 규칙 (3차 심사 반영)**: `commit_state=uncommitted` 이벤트는 **crash 복구 증거 전용** — `path_attribution`·pending revision·covers·v1 projection·settlement 어느 것의 권위 입력도 아니다. committed 전환 후에만 판정에 참여한다.

### 3-2. F0b: candidate-matched first-party 귀속 (codex H3)
- 현행: `record_deltas()`가 delta가 도구 호출의 candidate 경로인지 확인하지 않고 PostTool source=edit이면 현재 에이전트 소유로 기록 → 동료 변경 오귀속(프로브 실측).
- 수정: first-party(self)는 canonical candidate 경로와 일치하는 delta만. 불일치 delta는 external.

## 4. A — 변경 귀속 (attribution)

### 4-1. 귀속 인덱스: ledger top-level `path_attribution`
- **"현재 revision 인덱스"이지 역사 원장이 아니다.** 역사·감사는 기존 `agents/*.jsonl`(append-only)이 담당.
- key = Windows-aware canonical path. 값(엄격 스키마 검증):

```json
{
  "generation": 17,
  "status": "exclusive | contended",
  "owners": [
    { "agent_key": "codex_cli:abc:codex", "turn_id": "t1", "revision_seq": 812,
      "after_digest": "sha256:…", "invocation_id": "inv-…", "settled": false }
  ]
}
```

- `owners[]`(**1차 심사 High-2 구체화**): 경로를 만진 에이전트별 최신 미정산 revision 1건씩. 상한 8 owner/경로(초과 시 그 경로는 `contended`+overflow 표기, 완화 불성립). **settlement 정의**: 에이전트 X의 revision은 (a) X의 성공 검증이 그 revision_seq 이후에 기록되거나(covers 포함) (b) 관측된 현재 digest가 그 revision과 불일치하며 다른 owner의 최신 revision과 일치할 때(대체됨) `settled=true`. settled owner만 aging 대상.
- 갱신: `apply_v2_event()`가 change event의 `after` digest·generation을 재사용해 ledger-scope로 갱신(§3-1 수정 2의 낡은 generation 거부 포함).
- **상한 10,000 live 경로**. 초과 시 LRU 축출 금지 — `attribution_capacity_exceeded` 기록 + peer 면제 전면 off(§2-2a) + **R2는 fail-closed**(§2-2b).

### 4-2. 귀속 등급 (경합 보수 규칙 — agy 결함 A 봉쇄)
| 등급 | 성립 조건 | 게이트 효과 |
|------|----------|------------|
| `self` | 내 턴의 기록된 도구 호출(candidate 일치) + 해시 일치 | 기존 changed 판정(검증 의무) |
| `peer` | 타 에이전트 턴의 기록 + 해시 일치 **AND 내 턴에 그 경로 도구 호출 이력이 전혀 없음** **AND §6-5 이중 근거 교차 통과** | 내 차단 사유에서 제외(§6-1), `peer_changes` 정보 보고 |
| `contended` | 같은 경로에 나+타 에이전트 이력이 모두 존재 | 둘 다 보수(양쪽 모두 검증 의무 유지) |
| `external`/불명 | 어느 쪽에도 매칭 실패 | 기존대로 보수(차단 사유 유지) |

### 4-3. Bash 변경의 귀속
- static canonical candidate(리다이렉트·cp/mv·Set-Content·Out-File 등 `shell_hints` 추출 subset) + 성공 invocation + post digest 일치 → `self`.
- dynamic expression·glob·subshell·스크립트 간접 실행·파서 불능 → `external`(보수).
- destructive 대상 파서는 귀속 파서와 **별도**(§6-4).

## 5. B — 상태 격리 (isolation)

### 5-1. 계약 네임스페이스
- `contracts/<safe-key>-<identity-hash>.json` — safe prefix + identity 해시 suffix(Windows `:` 불가·치환 충돌 방지). 비교는 exact identity.
- R1 게이트는 자기 identity의 계약만 인정. authoring 자기 예외도 네임스페이스 경로 기준.
- legacy `contract.json` 폴백은 **활성 exact identity가 1개일 때만**. 2개 이상이면 legacy 무시. legacy_default 세션은 기존 경로 유지.

### 5-2. 전역 폴백 금지 invariant
- 게이트 판정(Stop·PreToolUse)에서 payload 없는 `active_turn(ledger)` 호출 금지 — exact identity에서 turn 미일치 = `turn_not_started`.
- Stop 카운터는 턴 스코프만. 전역 `stop_blocks` 폴백은 legacy synthetic identity 경로에만.
- v1 projection은 **유지하되 게이트 입력 전면 금지**(invariant를 테스트로 고정). 제거는 별도 호환성 트랙.

### 5-3. 턴 위생 — 2단계 종료 전이 (codex C5 반영)
- **turn_not_started/degraded 명시 상태**: start 관측 실패에도 identity 턴 항상 등록(`baseline_status=missing`) → KeyError 연쇄 제거. 후속 full bootstrap 성공 시 complete 회복.
- degraded 턴의 Stop: mutation-capable=기존 incomplete 차단·2회 cap 유지 / read-only="clean 비주장" 안내 allow.
- **종료는 2단계 전이**(1차 심사 C5 — 즉시 마킹은 Stop 판정 대상을 먼저 지움): `finish_requested`(finish_turn 관측 완료) → `evaluate_stop` 판정 → **allow 시 `turn_finished` 확정 / block 시 active 유지**(restart_blocked_turn 경로 보존). crash recovery: `finish_requested` 상태로 잔존한 턴은 다음 트랜잭션에서 last_event_at 기준 stale 처리.
- **턴 GC**: `started_at`/`last_event_at` 기록, 24h stale 턴 정리. attribution 소유권은 TTL로 지우지 않음(§4-1 settlement 기반만).

## 6. C — 협업 조정 (coordination)

### 6-1. Stop 게이트: peer 변경 차단 제외 — 4지점 관통
- 동일 규칙이 4지점을 관통해야 혼합 턴이 풀린다(프로브 실측: allow→late peer→block 재현):
  1. `record_path_revisions` — revision에 `attribution` 저장
  2. `pending_revisions` — 검증 의무 집합 = self/external/contended만
  3. `capture_covers` — filtered pending만 freeze
  4. `covers_verified` — 동일 filtered 집합만 대조
- remote epoch는 peer 필터와 분리 유지(파일 소유권이 아니라 원격 mutation 명령의 검증 의무).

### 6-2. 관측 조정: peer_activity (agy 공격 B 봉쇄 조건 내장)
- capture가 unstable/unreadable을 만나면, 해당 경로가 **활성 peer 턴의 "기록된 도구 호출의 candidate 경로"와 일치**할 때만(단순 시간 겹침 아님) `complete_with_exclusions`로 커밋. 사유 `peer_activity`.
- 전제 조건(없으면 구현 금지 — codex C3(구 rev0)·H2):
  - **invocation open/close window**: started/completed seq·시각 기록. **lease(1차 심사 High-1)**: PostTool/close가 유실된 open invocation은 그 peer의 다음 이벤트 또는 Stop에서 강제 close, lease 만료(기본 30분) 후 peer_activity 근거로 사용 금지.
  - **exclusion carry-forward**: 제외 경로는 이전 snapshot entry 보존(생략 시 delete delta 오염 — 프로브 실측). diff 무시 집합·snapshot ID에 exclusion 증거 포함.
  - **exclusion은 턴 스코프**(1차 심사 High-1): `complete_with_exclusions`의 제외는 해당 턴에서만 유효, 다음 턴 관측에서 재시도 — 해소 실패 시 기존 observation_error로 복귀(무기한 diff 은닉 금지).
- 백그라운드 `touch` 루프 공격: 공격자 프로세스는 "peer의 기록된 도구 호출"이 아니므로 면제 불성립 → 기존 observation_error 차단 유지.

### 6-3. 이상 상황 처리의 두 방향 (codex C3 반영 — 방향 분리가 핵심)
| 레이어 | 이상 상황(손상·스키마 위반·용량 초과·degraded marker) 시 |
|--------|--------------------------------------------------|
| 완화(peer 면제·peer_activity·네임스페이스 계약 인정) | **전부 off** → 기존 보수 차단 경로로 강등 |
| **R2 파기 차단** | **fail-closed** — 귀속 인덱스를 신뢰할 수 없으면 파괴 명령을 차단(귀속 없음=통과가 아님) |
| 게이트 코어(기존 Stop·R1·cap) | 기존 fail-open·2회 cap 불변 |
- 구현 요건 (**2차 심사 RC1 반영 — 순서 invariant + durable marker**):
  - **R2-first invariant**: PreToolUse 어댑터에서 **R2 판정이 최초의 상태 접근**이다 — `resolve_active_invocation()` 등 다른 ledger read보다 먼저, 자체 try 안에서 수행. R2 내부의 ledger load 실패(schema 예외 포함) = degraded = 파괴 카테고리 명령이면 차단. 이후 단계의 광역 except fail-open은 R2 판정을 되돌리지 못한다.
  - **durable degraded marker**: marker는 반환값이 아니라 디스크 상태 — `.fable-lite/*.corrupt-*.bak`의 존재 자체(현행이 이미 남김)를 degraded 신호로 사용. 미해소 bak이 있는 한 매 호출에서 degraded 판정 지속(첫 호출 뒤 소실 방지). 해소는 명시 절차(사용자/오케스트레이터가 원인 확인 후 bak 정리)로만.

### 6-4. R2 파기 차단 게이트 (07-16 사고의 직접 처방 — codex C2·C4, agy 결함 B 반영)
- **R1과 독립 판정**(unrelated contract로 면제 불가 — 프로브 실측). 호출 순서(**3차 심사 문구 충돌 해소**): **R2가 어댑터 최상단 자체 try에서 최우선**(=첫 state 접근, §6-3 R2-first invariant) → intent/goals → authoring 자기 예외 → R1 → begin_invocation. authoring 자기 예외는 R2 이후이므로 R2를 건너뛸 수 없다(계약 파일 자체가 파괴 대상인 경우 포함).
- **deny-by-category (1차 심사 C4)**: 파괴 카테고리 명령은 "대상 파싱 성공 + 전 대상이 자기 귀속(또는 미추적)"일 때만 통과. **파싱 불능·암시적 전체 범위(`.`·무인자·repo-wide)·unknown 변형은 차단.**
  - 카테고리(최소 corpus — acceptance에 고정): `git checkout --`·`git restore`·`git reset --hard`·`git clean`·`git stash(-u 포함)`·`git switch --discard-changes`·`git read-tree -u`·pathspec magic/`--pathspec-from-file` / `rm`·`Remove-Item`·`del`·`rd/rmdir` / **truncate-redirect**(`> file`·`cat /dev/null > file`·`Set-Content`류가 타 에이전트 미정산 revision을 대상으로 할 때 — agy 결함 B 보강).
  - 대상 경로의 최신 귀속이 **타 에이전트의 미정산(exclusive|contended) revision**이면 하드 차단 + "파괴 조치는 보고로 강등하라" 메시지.
  - **pre-attribution 창 보호 (2차 심사 RC3)**: 파괴 대상이 **활성 peer open invocation의 candidate 경로**와 일치하면 귀속(change event)이 아직 없어도 차단 — §6-2의 invocation window를 R2 preflight 입력에 연결. peer가 candidate에 안 잡히는 경로를 만든 직후의 잔여 창(dynamic bash 신규 파일 등)은 알려진 한계로 명시.
  - **계약 생성 이벤트 스키마 (2차 심사 Medium)**: 계약 파일은 provenance hard-exclude이므로 전용 `contract_authored` 이벤트(identity·계약 경로·내용 digest 포함)를 authoring 시 감사 로그에 기록 — §6-5 교차 확인의 근거. 이벤트 부재·digest 불일치 시 R1은 그 계약을 불인정.
- **원복 승인 메커니즘 없음 (1차 심사 C2 반영 — one-shot revert_authorization 폐기)**: 자기 계약 authoring으로 자가발급이 가능하고(계약 내용 무검증 allow 실측) one-shot 소비의 원자성도 확보 불가 → **면제 경로 자체를 두지 않는다**. R2 차단의 해소는 절차로: ①소유 에이전트 본인이 자기 세션에서 실행(자기 귀속=통과) ②소유 에이전트가 검증/정리로 settlement 후 실행 ③사용자(인간)가 직접 실행(훅 밖). 차단 메시지에 이 3경로를 안내. — 07-16 재발 방지 3항("파괴 조치는 오케스트레이터 전결")의 게이트화이며, 오탐 비용보다 우회 표면 제거를 우선한다.
- **알려진 한계(명시)**: ①check와 실행 사이 TOCTOU — best-effort 차단(실시간 lock 비범위) ②파서 밖 의미론적 파괴의 완전 차단 불가 — truncate-redirect 보강으로 좁히되, 잔여는 §4 귀속·검증 의무(덮어쓴 파일은 self 변경화)로 보상.

### 6-5. 상태 조작 대응: 이중 근거 교차 확인 (agy critical 반영 — "차단"에서 "무익화"로)
- 1차 심사(agy)가 옳다: PreToolUse 텍스트 필터는 간접 쓰기(python 스크립트·cd 후 상대경로)로 우회 가능 — **필터를 방어의 본체로 삼지 않는다**(마찰 장치로 유지는 함: 명시적 `.fable-lite/**` 직접 변경 명령 차단).
- **방어의 본체 = 이중 근거**: peer 면제·settlement 등 모든 완화 판정에서 `path_attribution` 인덱스 단독을 신뢰하지 않고, **대응하는 change event가 그 peer의 `agents/<agent>.jsonl`(append-only)에 존재하고 seq·digest·generation이 정합**함을 교차 확인한다.
  - ledger만 조작(agy 시나리오 A의 jq 실행례) → 감사 로그에 대응 이벤트 없음 → 면제 불성립(세탁 무익).
  - 감사 로그까지 위조 append → 가능함을 인정(§1 비보장). 단 append-only 파일에 영구 기록이 남고, seq 연속성·인접 이벤트와의 정합까지 맞춰야 하므로 "합리화·우발" 수준을 벗어난 명백한 고의 — 위협 모델 밖으로 명시.
- 계약(§5-1)도 동일: R1 인정 시 계약 파일 단독이 아니라 **계약 생성 이벤트가 자기 감사 로그에 존재**해야 함(타 계약 복사 무익화 — agy 시나리오 C).

## 7. 성능 예산
- Stop 재해시 금지 — change event digest 재사용(10k 대조 1.089ms 실측).
- 10k map ≈ 2.75MB·serialize p95 25.9~44.1ms(CPU, 재실행 변동) — **수용 판정은 유보**: 릴리스 벤치에 attribution 0/1k/10k on/off·**atomic write·Windows AV/lock 포함** p95/p99를 추가하고 실측으로 판정(기존 Windows 10k Stop p95 3,716ms/예산 6,000ms 대비).
- §6-5 교차 확인 비용: peer 면제 후보 경로만 대상(O(peer 변경 수)), jsonl 전체 replay 금지 — 최근 이벤트 역방향 탐색+seq 인덱스.

## 8. 마이그레이션·호환
- ledger v2에 `path_attribution`·`manifest_generation`·turn 타임스탬프·finish 상태 필드 추가 — 구 ledger는 필드 부재 시 빈 값(자동).
- `contracts/` 신설 — legacy 폴백은 §5-1 조건부.
- 단일 에이전트 프로젝트 동작 무변경.

## 9. 비범위
- 실시간 lock/조정 프로토콜(작업카드 경로 배타가 상위 레이어 해결) — TOCTOU 한계의 원인이며 수용.
- 원격 분산 원장 동기화 / wmux MCP 연동 / tmp 멀티세션 격리(운영 소관) / v1 projection 제거(별도 트랙) / 강한 적대 보안(HMAC·ACL·브로커).

## 10. 구현 순서 (P2 플랜 입력) + acceptance 고정 (1차 심사 Medium 반영)
1. **F0**: generation CAS + change event generation 결합·낡은 적용 거부(§3-1) + candidate-matched 귀속(§3-2)
2. **F1**: `path_attribution`(엄격 스키마·owners[] settlement·10k cap·degraded marker)(§4, §6-3)
3. **F2**: peer/contended 필터 4지점 관통 + 이중 근거 교차(§6-1, §6-5) + remote epoch 분리
4. **F3**: invocation window·lease + exclusion carry-forward(턴 스코프) + peer_activity(§6-2) + turn 2단계 종료·GC(§5-3)
5. **F4**: 계약 네임스페이스+생성 이벤트 교차(§5-1, §6-5) + 전역 폴백 금지 invariant(§5-2) + R2 deny-by-category·fail-closed(§6-4, §6-3)
6. **F5**: 릴리스 게이트 — **acceptance matrix 고정**: CAS 역순 커밋 거부 / 중간 crash 회복 2종(finish_requested 잔존 + **snapshot 저장↔change event 확정 사이 crash에서 증거 보존**) / R2 corpus(§6-4 카테고리 전 항목: 파싱 불능·암시적 범위 차단 확인) / **R2-first invariant(schema 예외 상태에서 파괴 명령 차단)** / **peer 신규 파일 pre-attribution 파기 차단(open invocation candidate 교차)** / corrupt·overflow 시 완화 off+R2 closed(durable marker 지속성 포함) / unclosed invocation lease 만료 / 혼합 턴 allow→late peer→allow / 세탁(ledger 단독 조작) 면제 불성립 / 성능 p95·p99(atomic write 포함).

## 11. 토론·심사 이력
- codex 검토(프로브 실측): stale commit 승인·owner history 부재·exclusion 삭제 delta·covers 관통·invocation window·candidate 오귀속·R2 독립성·상태 파일 무방비·TOCTOU 등.
- agy 검토(공격): ledger 변조 세탁·hot-write 면제·계약 복사·경합 해시 맹점·fail-open 충돌.
- **미션모드 1차(rev1: codex 52·crit 5 / agy 70·crit 1 / 좌상 92·crit 0)** → rev2 반영: C1 generation 결합 / C2 원복 승인 폐기(절차 해소) / C3 이상 처리 방향 분리(완화 off vs R2 closed) / C4 deny-by-category+corpus / C5 2단계 종료 / High lease·settlement 구체화 / agy critical 이중 근거 교차(무익화 전략 전환) / 성능 결론 유보.
- **미션모드 2차(rev2: codex 72·crit 3 / agy 95·crit 0 — 1차 7건 중 6건 닫힘, 무익화 전략 "실질 봉쇄" 판정)** → rev3 반영: RC1 R2-first invariant+durable degraded marker(corrupt bak 존재=신호) / RC2 WAL형 순서(change event 선기록→snapshot→확정, crash 시 증거 잔존) / RC3 pre-attribution 창 보호(open invocation candidate를 R2 입력에 연결) / Medium 계약 생성 이벤트 스키마·acceptance crash 2종 추가.
