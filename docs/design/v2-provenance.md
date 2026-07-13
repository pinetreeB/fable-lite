# show-me-the-work v2.0 P0 Change Provenance 설계

> 상태: 구현·릴리스 합의안 rev3
>
> 작성 기준: v1.2.0 태그 `c0b1bfd`, 현재 HEAD `eb0053b`
>
> 범위: 설계만 포함한다. 이 문서 작성 단계에서는 코어, 어댑터, 테스트를 구현하지 않는다.

## 변경 이력

### rev4, 2026-07-14 (v2.0.1)

- 홈 디렉터리처럼 프로젝트 루트가 과도하게 넓은 실사용 오탐을 막기 위해 soft exclude를 모든 경로 깊이에 적용한다. 명시적 `include`와 강제 관측 경로만 필요한 하위 가지를 다시 연다.
- 지원 관측 범위를 regular/symlink 10,000개와 총 256MiB로 고정하고, cold/full 8초·incremental 2초의 협력적 시간 예산을 둔다. 초과 결과는 일반 오류 `incomplete`와 구분한 `scope_too_large`이며 partial snapshot을 저장하지 않는다.
- `scope_too_large` 자체는 Stop을 차단하지 않고 한 번 안내한다. 다만 이미 확인된 비문서 변경의 fresh verification 요구는 그대로 유지한다.
- 직접 `ssh`와 로컬→원격 `scp`의 원격 mutation 가능성은 로컬 부작용 가능성과 독립적으로 epoch에 기록한다. 모든 shell invocation의 local snapshot 관측을 유지하며, redirect/pipeline/chain/substitution/제어 옵션이 섞여도 해당하면 양쪽을 모두 추적한다. 전체 exit가 실패여도 부분 원격 변경 가능성이 있으므로 검증 명령이 아닌 원격 mutation-capable 시도는 epoch를 올린다. 이후 별도 검증 invocation의 frozen `covers.through_seq`가 그 epoch 이상일 때만 해소한다.

### rev3, 2026-07-13

- 대표 규모 1k와 극한 규모 10k의 release SLO를 이원화했다. 1k는 기존 metadata/full p95 200ms/1,000ms를 유지하고, 10k는 1,000ms/6,000ms를 각각 hard gate로 적용한다.
- 10k 예산은 Windows에서 double-stat, 10,000회 file open, 256MiB BLAKE2b-256 전체 read만 수행한 격리 실험의 경험적 하한 1.48초와 실제 Stop p95 4.94초에 Defender/page-cache 변동 마진을 더해 정했다. agy의 5,000ms 제안은 관측 최악값에 밀착해 flapping 위험이 있어 3-AI 재합의에서 6,000ms로 조정했다.
- 이는 기존 결과를 통과시키기 위한 짜맞추기가 아니라 대표 사용자 규모와 극한 규모를 분리하고, 계약 무손상 최적화 및 물리 하한 실측 뒤 재현 가능한 회귀 예산을 다시 설정한 결정이다. native scanner는 stdlib-only·플랫폼 중립 계약을 깨므로 기각했다.
- 관측 scan이 500ms를 넘으면 hook stdout JSON과 분리된 stderr에 파일 상태 검증 진행 알림을 한 번 표시한다. 50k/2GiB stress의 crash·ledger·incomplete 정책은 유지한다.

### rev2, 2026-07-12

- turn-start reconcile에도 generation rebase를 의무 적용하고, 동일 물리 변경의 관측 소유권이 경합하면 change를 중복 생성하지 않은 채 effective source를 `external`로 하향하는 병합 정책을 확정했다.
- turn start 정본 경로를 직전 Stop의 `workspace-current` 재사용 fast-path로 바꿨다. metadata가 달라진 파일만 hash하며, 최초·cold·이전 incomplete·정책 변경 때만 full hash한다.
- 마이그레이션 저장의 temp+atomic replace, timestamp-suffixed corrupt backup, 실패 시 `ledger.v1.json.bak` 자동 복원을 요구했다. agy가 제시한 직접 백업 파괴 경로는 두 backup 이름이 달라 현행 그대로는 성립하지 않는다고 판정했다.
- Windows case-insensitive canonical key와 casefold 충돌, non-follow symlink, OneDrive/non-symlink reparse point의 unstable 분류를 snapshot 계약에 추가했다.
- source 정확도와 고신뢰 오귀속은 관측 지표로 낮추고, 세 어댑터 canonical replay 100% 일치만 기계적 source 계약 hard gate로 남겼다.
- W1-W8은 격리 fixture ledger만 사용하고, 실 원장 자동 마이그레이션 연결은 W9 hard gate 통과 후 릴리스 단계에서만 수행하도록 순서를 고정했다.

### rev1, 2026-07-12

- turn-start filesystem snapshot, FS-confirmed change event, verification `covers`, non-git 동등 경로, F6 transaction, one-shot migration, 성능·정확도 계획의 최초안을 확정했다.

## 1. 결론

v2.0 P0의 변경 판정 정본은 **턴 시작 시점의 논리적 파일시스템 콘텐츠 스냅샷**으로 한다. 정상 fast-path에서는 직전 Stop이 full reconcile한 `workspace-current`의 digest를 재사용하고 metadata sweep에서 불일치한 파일만 다시 hash한다. 최초 턴·cold 상태·이전 incomplete·관측 정책 변경 때만 full hash한다. 이후 PostTool과 Stop 경계에서 현재 파일시스템을 다시 관측해 baseline과 비교하고, 실제 바이트·파일 유형·실행 비트·심볼릭 링크 대상이 달라진 경우에만 `change` 이벤트를 확정한다.

- Git/VCS 상태는 필수 조건도 하드 진실도 아니다. 경로 열거와 성능 최적화 힌트로만 사용할 수 있다.
- Edit, Bash, PowerShell 같은 도구 이름은 변경의 원인 후보를 알려줄 뿐이다. 파일시스템 delta가 없으면 `change` 이벤트를 만들지 않는다.
- raw shell 명령 파서는 재해시할 후보 경로를 넓히는 보조 신호다. 파서 결과만으로 변경, 범위 이탈, 검증 필요 여부를 확정하지 않는다.
- 검증은 단순히 `verification.seq > last_change_seq`인지 보는 대신, 검증 시작 시 존재했던 정확한 변경 리비전과 `covers` 관계를 기록한다.
- Stop block 카운터는 읽기, 판정, 증가, 저장 전체를 `ledger_transaction` 안에서 수행하며 `(agent, turn_id)`별로 분리한다.
- turn start를 포함한 모든 workspace-current 갱신은 generation rebase를 거치며, 다중 에이전트 귀속 충돌은 안전하게 `external`로 하향한다.
- 구현은 Python 표준 라이브러리만 사용하고 Claude Code, Codex CLI, Antigravity 3개 어댑터가 같은 코어를 호출한다.

이 선택은 Bash 우회를 막기 위해 셸 문법을 더 많이 추측하는 것이 아니라, 어떤 경로로 변경됐든 최종 파일시스템 사실을 동일하게 관측하는 방향이다.

## 2. 목표와 비범위

### 2.1 목표

1. 구조화된 편집 도구, 셸 명령, 생성기, 다른 프로세스가 만든 변경을 동일한 파일시스템 기준으로 탐지한다.
2. 턴 시작 전부터 존재한 dirty 상태와 현재 턴에서 생긴 delta를 구분한다.
3. 검증 증거가 어떤 파일 리비전을 포함한 상태에서 실행됐는지 기계적으로 판정한다.
4. Git 저장소와 non-git 디렉토리에서 같은 판정 의미를 유지한다.
5. 공유 원장에서 여러 에이전트의 이벤트 순서와 Stop block 카운터를 유실 없이 직렬화한다.
6. 1만 파일 저장소에서 훅 지연 예산을 지키고, 예산 초과를 조용한 clean 판정으로 바꾸지 않는다.
7. v1.2 원장을 안전하게 읽고, 백업을 남긴 뒤 v2로 정확히 한 번 전환한다.

### 2.2 비범위

- 적대 모델이 타임스탬프, 파일 잠금, 프로세스 경쟁을 의도적으로 조작하는 상황의 완전 방어
- 프로젝트 루트 밖 파일, 레지스트리, 네트워크, 데이터베이스 등 파일시스템 외부 부작용 추적
- OS별 상주 파일 감시 데몬 또는 wmux 데몬
- 테스트 명령이 코드 의미를 실제로 얼마나 커버하는지 측정하는 언어별 coverage 분석
- Git index만 바꾸는 `git add` 또는 `.git/` 내부 변경을 작업 산출물 변경으로 간주하는 것
- 생성 후 다음 관측 전에 원상 복구되어 최종 파일 상태가 동일한 일시 파일의 감사 추적

## 3. 현행 구조에서 해결할 문제

### 3.1 변경 판정이 도구 이름에 묶여 있다

현재 세 어댑터의 PostTool 경로는 `EDIT_TOOLS`에 속한 도구가 제공한 경로를 곧바로 `record_event(event="change")`로 보낸다. 셸 도구는 검증 명령만 기록하며, 셸이 실제 파일을 썼는지는 확인하지 않는다.

따라서 다음 두 오류 방향이 함께 존재한다.

- 거짓 음성: `printf`, `tee`, `sed -i`, Python one-liner, 빌드 생성기 등으로 파일을 바꿔도 변경으로 기록되지 않는다.
- 거짓 양성: 편집 도구가 실패했거나 동일 바이트를 다시 썼어도 도구 payload에 경로가 있으면 변경으로 기록될 수 있다.

### 3.2 seq는 순서를 알지만 대상 상태를 알지 못한다

`core/ledger.py`는 전역 단조 `event_seq`와 `last_change_seq`를 유지하고, `core/verify_state.py`는 성공 검증의 `seq`가 최신 비문서 변경보다 큰지 확인한다. v1.2의 stale verification 문제는 막지만, 검증 중에 생성된 변경이나 동시에 들어온 다른 에이전트 변경까지 검증이 포함했다고 오인할 수 있다.

### 3.3 F6 Stop 카운터 경쟁

현재 `evaluate_stop()`은 원장을 락 없이 읽은 뒤 `_block_with_stop_counter()`에서 `stop_blocks`를 증가시키고 저장한다. 두 프로세스가 같은 값을 읽으면 증가 하나가 사라질 수 있다. `CHANGELOG.md`의 v1.2 Known Limitations도 이 이동을 v2.0으로 명시했다.

### 3.4 새 prompt가 공유 상태를 전역 리셋한다

현재 prompt 이벤트는 `changed_files_seen`, 검증 결과, block 카운터를 전역 리셋한다. 단일 에이전트 턴에는 맞지만, 공유 원장에서는 에이전트 A의 새 prompt가 에이전트 B의 활성 턴 상태를 지우게 된다.

## 4. 핵심 불변식

구현은 아래 불변식을 깨면 안 된다.

1. **변경 사실 불변식**: 콘텐츠 또는 추적 대상 메타데이터 delta가 확인되지 않으면 `change` 이벤트가 없다.
2. **원인 비의존 불변식**: 도구 이름, 명령 문자열, Git status 중 어느 것도 단독으로 `change`를 만들 수 없다.
3. **완전성 불변식**: 스캔이 시간 초과·권한 오류·경쟁으로 불완전하면 `no change`로 기록하지 않고 `provenance_status=incomplete`로 남긴다.
4. **순서 불변식**: 모든 ledger 이벤트의 `seq`는 공유 원장에서 유일하고 단조 증가한다.
5. **검증 불변식**: 성공 검증은 검증 시작 전에 존재하고 `covers`에 명시된 파일 리비전만 만족시킨다.
6. **락 불변식**: block 카운터 read-modify-write와 이벤트 seq 할당은 동일한 원장 락 안에서 끝난다.
7. **어댑터 불변식**: 어댑터는 payload를 정규화할 뿐 변경 판정 알고리즘을 복제하지 않는다.
8. **가용성 불변식**: 스캔 실패가 무한 Stop 루프가 되지 않도록 기존 최대 2회 block cap을 유지한다.

## 5. 선택한 변경 판정 방식

### 5.1 선택: workspace-current 재사용 기반 턴 시작 스냅샷

UserPromptSubmit 또는 이에 대응하는 턴 시작 훅에서 프로젝트 관측 범위의 baseline manifest를 만든다. 기본 경로는 직전 Stop full reconcile 결과인 `workspace-current`를 재사용하는 fast-path다. 전체 metadata를 sweep하고 불일치 경로만 hash해 새 baseline을 확정한다. 재사용 가능한 current가 없는 최초 턴, 이전 full reconcile이 incomplete인 경우, scanner/schema 또는 include/exclude 정책 digest가 달라진 경우에만 full content hash를 수행한다. 매 턴 무조건 full hash하는 구현은 금지한다.

여기서 cold는 OS page cache가 비어 있다는 뜻이 아니라, 재사용 가능한 `workspace-current`가 없는 논리적 cold state를 뜻한다.

fast-path는 턴 시작 지연을 줄이는 baseline cache validation이지 최종 clean 증명이 아니다. 직전 Stop 뒤 같은 size/mtime을 보존한 외부 쓰기가 있으면 turn start에서 재사용될 수 있지만, 현재 턴 Stop full reconcile이 digest 차이를 탐지해 `external` pending change로 남긴다.

manifest의 각 파일 항목은 다음 정보를 가진다.

```json
{
  "path": "core/ledger.py",
  "type": "regular",
  "size": 9123,
  "mtime_ns": 1783836101000000000,
  "mode": 420,
  "digest": "blake2b-256:..."
}
```

- `path`: 프로젝트 루트 기준 `/` 구분 상대 경로
- `type`: `regular`, `symlink` 중 하나. 기타 special file은 스캔 불완전 사유로 기록한다.
- `digest`: regular file은 바이트, symlink는 링크 대상 문자열의 BLAKE2b-256
- `mode`: POSIX 실행 비트 변화를 추적한다. Windows ACL은 P0 범위 밖이다.
- `snapshot_id`: 정렬된 manifest 항목 전체를 다시 BLAKE2b-256으로 해시한 콘텐츠 상태 ID
- `scope_policy_id`: scanner/schema 버전과 include/exclude 정책을 정규화해 해시한 ID. 직전 current와 다르면 fast-path를 쓰지 않는다.
- `full_reconciled_at`: 이 manifest가 마지막으로 full content reconcile된 시각. Stop full 결과에만 설정한다.

BLAKE2b-256은 Python `hashlib`에 포함돼 추가 의존성이 없고, 전체 파일 재대조 비용을 낮추면서 플랫폼 간 동일한 바이트에 동일한 ID를 제공한다.

#### 5.1.1 경로 키와 특수 파일

- 표시 경로는 원래 대소문자를 보존한다.
- Windows canonical manifest key는 `/`로 정규화한 루트 상대 경로를 `casefold()`한 값이다. POSIX key는 대소문자를 보존한다.
- 한 sweep에서 서로 다른 표시 경로가 같은 Windows canonical key로 들어오면 어느 한쪽으로 덮어쓰지 않고 `casefold_collision`로 기록해 scan을 incomplete 처리한다. case-sensitive Windows 디렉토리도 이 안전 규칙을 따른다.
- `os.scandir`와 stat은 `follow_symlinks=False`를 강제한다. 파일·디렉토리 symlink 모두 하위로 진입하지 않고 `os.readlink()`가 돌려준 링크 대상 문자열만 digest한다. 따라서 symlink graph를 순회하지 않아 링크 루프가 생길 수 없다.
- Windows symlink와 non-symlink reparse point를 `DirEntry.is_symlink()` 및 `stat_result.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT`로 구분한다. symlink는 위 규칙으로 처리하고, junction·OneDrive placeholder 등 non-symlink reparse point는 디렉토리면 절대 진입하지 않으며 파일이면 `unstable_reparse`로 분류한다.
- `unstable_reparse`는 metadata 변화만으로 change event를 만들지 않는다. 접근 가능한 파일 바이트가 두 번의 연속 관측에서 같은 digest로 안정화될 때만 manifest revision으로 승격하고, 그 전에는 provenance incomplete 사유로 남긴다. 이 정책은 hydration metadata 변동이 거짓 change 폭주로 바뀌는 것을 막는다.

### 5.2 관측 범위

기본값은 프로젝트 루트 아래 모든 파일이다. 다음 경로만 기본 제외한다.

- hard exclude: `.git/**`, `.fable-lite/**`, `.hg/**`, `.svn/**`
- soft exclude: `node_modules/**`, `.venv/**`, `venv/**`, `__pycache__/**`, `.pytest_cache/**`, `.mypy_cache/**`, `.ruff_cache/**`

hard exclude는 내부 상태 재귀와 VCS 내부 잡음을 막기 위해 어떤 힌트로도 포함하지 않는다. soft exclude는 구조화된 경로 또는 보조 셸 힌트가 특정 파일을 가리키면 그 파일만 강제 관측할 수 있다.

soft exclude 이름은 루트 첫 segment에만 적용하지 않고 모든 깊이에서 적용한다. 예를 들어 홈을 루트로 잘못 선택해도 `workspace/node_modules/**`와 `project/.venv/**`는 기본적으로 순회하지 않는다. `include`가 해당 하위 파일을 정확히 가리키면 그 파일까지 필요한 디렉터리만 다시 순회한다.

선택적 로컬 설정 `.fable-lite/provenance-config.json`은 표준 JSON만 사용한다.

```json
{
  "version": 1,
  "include": ["dist/**"],
  "exclude": ["vendor-cache/**"],
  "generated": ["dist/**", "build/**", "out/**", "coverage/**"]
}
```

- `include`는 soft exclude만 덮어쓴다.
- `generated`는 `source` 귀속에만 쓰며 변경 사실 판정에는 영향을 주지 않는다.
- 설정 파일 자체는 `.fable-lite/**` hard exclude 안에 있어 자기 기록이 새 변경을 만들지 않는다.
- 패턴은 `/`로 정규화한 루트 상대 경로에 `fnmatch.fnmatchcase` 의미로 적용하고, `include`가 `exclude`보다 우선한다.

### 5.3 관측 단계

```text
turn start
  -> ledger lock: generation + workspace-current 기준 고정
  -> metadata sweep
  -> 불일치 파일만 hash, cold/invalid current만 full hash
  -> generation rebase + ownership merge
  -> turn baseline 고정
  -> begin invocation: 현재 seq와 snapshot_id 고정
  -> tool runs
  -> PostTool: metadata sweep + 후보 파일 강제 hash
  -> 실제 delta만 change event
  -> verification event가 시작 전 리비전을 covers
  -> Stop: 모든 관측 파일 full re-hash
  -> pending 리비전과 covers 대조
  -> allow 또는 block
```

#### 턴 시작

1. 에이전트별 `turn_id`를 생성하고 baseline 준비 상태로 둔다. prompt 훅은 baseline 커밋 전 반환하지 않으므로 해당 에이전트의 첫 도구 실행보다 항상 앞선다.
2. 짧은 ledger lock 안에서 `manifest_generation`, `workspace-current`, `scope_policy_id`, 직전 Stop의 full/incomplete 상태를 읽는다.
3. 재사용 조건이 맞으면 lock 밖에서 전체 metadata를 sweep하고 `(type, size, mtime_ns, mode)`가 달라진 파일만 hash한다. 매 턴 전체 콘텐츠를 hash하지 않는다.
4. current가 없거나 cold/incomplete/policy-changed이면 이 턴에 한해 full hash한다.
5. 다시 lock을 잡고 5.4 generation rebase를 수행한다. turn-start reconcile도 예외 없이 같은 rebase와 소유권 병합 정책을 적용한다.
6. 기존 global current와의 실제 digest delta를 먼저 reconcile한다. 이미 활성인 다른 턴에는 이 delta가 `external` event로 보인다.
7. 첫 실행이라 global current가 없으면 현재 상태를 초기화만 하고 과거 변경 event를 만들지 않는다.
8. reconcile이 끝난 상태를 새 턴 baseline으로 고정하고, 그 시점의 `event_seq`를 `start_seq`로 저장한다.
9. 턴 시작 전 dirty 파일은 baseline에 포함하되 새 턴 변경으로 기록하지 않는다.
10. 해당 에이전트 턴의 `baseline_snapshot_id`, `current_snapshot_id`를 저장한다.

#### PreTool

1. `invocation_id`, `agent`, `turn_id`, 현재 `event_seq`, `current_snapshot_id`를 기록한다.
2. 구조화된 편집 경로와 셸 파서 후보 경로를 `candidate_paths`로 저장한다.
3. 이 단계에서는 변경 이벤트를 만들지 않는다.

#### PostTool

1. 전체 경로의 `(type, size, mtime_ns, mode)`를 이전 manifest와 비교한다.
2. 메타데이터가 달라진 파일과 `candidate_paths`는 콘텐츠를 다시 해시한다.
3. 실제 digest 또는 유형 delta가 있는 경로만 change event로 커밋한다.
4. 동일 바이트 재작성은 변경이 아니다.
5. 검증 명령이면 FS 관측을 먼저 커밋한 뒤 verification event를 기록한다.

#### Stop 또는 AfterAgent

1. 메타데이터 최적화를 사용하지 않고 관측 범위 파일을 모두 다시 해시한다.
2. 같은 크기와 mtime을 보존한 일반 셸 쓰기도 이 최종 대조에서 잡는다.
3. baseline으로 돌아간 경로는 감사 event는 유지하되 현재 `pending_changes`에서 제거한다.
4. 스캔이 불완전하고 현재 턴에 mutation-capable 도구가 있었다면 clean으로 허용하지 않고 Stop block cap을 소비한다.

### 5.4 스냅샷 일관성

파일 하나는 `stat before -> hash/readlink -> stat after` 순서로 읽는다. 두 stat이 다르면 한 번 재시도한다. 재시도 뒤에도 움직이면 해당 경로를 임의 값으로 확정하지 않고 `unstable_path`로 기록해 스캔을 incomplete 처리한다.

전체 디렉토리 관측 동안 다른 에이전트가 원장을 기다리지 않도록 file walk와 hash는 ledger lock 밖에서 수행한다. 아래 generation rebase는 PostTool·Stop뿐 아니라 **turn start reconcile에도 의무 적용**한다.

1. 짧은 락 안에서 observation 기준 generation을 읽는다.
2. 락 밖에서 스캔한다.
3. 다시 락을 잡고 generation을 확인한다.
4. generation이 같으면 scan 결과를 current에 커밋한다.
5. generation이 바뀌었으면 새 global current와 scan 결과를 path key별로 rebase하고 충돌 경로만 다시 stat/hash한다.
6. 충돌 경로의 재관측 결과가 새 current와 같으면 기존 transition을 재사용한다. 다르면 current의 after digest를 새 before로 삼을 수 있는 연속 transition인지 확인한다.
7. before/after 연속성을 입증할 수 없거나 한 번의 rebase로 안정화되지 않으면 중간 변경을 꾸며내지 않고 incomplete로 끝낸다.

이 방식은 장시간 락 보유를 피하면서 seq, manifest generation, 이벤트 커밋은 직렬화한다.

workspace에는 마지막으로 커밋된 전역 current manifest 하나만 둔다. 각 활성 턴은 자기 baseline manifest를 별도로 참조한다.

```text
.fable-lite/snapshots/
├── workspace-current.json
└── turns/
    └── <agent-key>/
        └── <turn-id>-baseline.json
```

- 물리 transition의 중복 키는 `(canonical_path_key, op, before_digest, after_digest)`의 BLAKE2b-256인 `change_id`다.
- 동일한 물리 변경을 다른 에이전트가 먼저 관측했으면 global current와 최초 change event를 재사용하고 두 번째 change event를 만들지 않는다.
- 두 번째 관측은 change epoch를 올리지 않는 `change_observation` 감사 event로만 남기고, 해당 `change_id`의 `observed_by` 집합에 agent key를 추가한다.
- 같은 agent·invocation의 재관측이면 최초 source/owner를 유지한다.
- 서로 다른 agent key가 같은 `change_id`의 소유권을 주장하거나 mutation window가 겹치면 안전 기본으로 effective `source=external`, `owner=null`, `attribution_status=contended`로 하향한다. 원래 change event는 append-only로 보존하고, `attribution_merge` 감사 event가 effective attribution을 supersede한다.
- pending revision과 verification `covers`는 `change_id`를 한 번만 참조하므로 귀속 병합이 verification epoch를 중복 증가시키지 않는다.
- 각 턴의 net delta는 `turn baseline`과 `workspace current` 비교로 계산한다.
- 에이전트 B가 늦게 시작하면 B의 baseline에는 이미 커밋된 A의 변경이 포함되므로 B의 작업으로 재귀속되지 않는다.
- 에이전트 A가 계속 활성 상태면 이후 B의 변경은 A의 baseline 대비 external delta로 보이며 A의 Stop 또는 오케스트레이터 check에서 숨지 않는다.
- 새 턴 baseline 준비 중 발견한 delta도 global current에 먼저 event로 커밋한 뒤 baseline을 잡으므로 기존 활성 턴의 변경 이력을 덮어쓰지 않는다.

## 6. Change 이벤트 v2

### 6.1 정규 스키마

```json
{
  "schema_version": 2,
  "event": "change",
  "event_id": "01J...:17",
  "seq": 17,
  "turn_id": "01J...",
  "agent": "codex",
  "source": "shell",
  "owner": "codex",
  "attribution_status": "exclusive",
  "observed_by": ["codex"],
  "confidence": 1.0,
  "source_confidence": 0.90,
  "invocation_id": "01J...",
  "observed_at": "post_tool",
  "snapshot_before": "blake2b-256:...",
  "snapshot_after": "blake2b-256:...",
  "paths": [
    {
      "change_id": "blake2b-256:...",
      "path": "core/ledger.py",
      "op": "modify",
      "kind": "code",
      "before": "blake2b-256:...",
      "after": "blake2b-256:...",
      "requires_verification": true
    }
  ]
}
```

### 6.2 필드 의미

- `source`: 변경 원인의 귀속 분류다. 변경 사실 자체와 분리한다.
- `agent`: 최초 change event를 원장에 커밋한 관측자다. 물리 변경 소유권과 동일하지 않다.
- `owner`, `attribution_status`, `observed_by`: ledger의 effective attribution projection이다. 경합 병합 뒤 resolved view는 `owner=null`, `attribution_status=contended`, `source=external`이 되며 append-only 원본은 `attribution_merge`로 supersede된다.
- `confidence`: 파일시스템 delta의 확정도다. P0은 콘텐츠가 확인된 이벤트만 저장하므로 새 v2 이벤트는 `1.0`이다. 추측 이벤트는 만들지 않는다.
- `source_confidence`: 원인 귀속 신뢰도다. 게이트 판정에는 사용하지 않는다.
- `paths`: 한 invocation에서 확인된 복수 경로를 묶는다.
- `change_id`: canonical path와 before/after transition의 결정론적 ID다. 다중 관측 dedupe와 ownership merge의 정본 key다.
- `op`: `create`, `modify`, `delete`, `type_change`, `mode_change` 중 하나다.
- `before`, `after`: 없는 쪽은 `null`이다.
- rename은 delete/create 두 사실로 보존하고, digest가 일대일로 일치할 때만 선택적 `rename_from` 관계를 추가한다.
- `requires_verification`: 기존 정책을 이어 `kind=docs`는 기본 false, 그 외는 true다. source가 generated라는 이유만으로 자동 면제하지 않는다.

### 6.3 source 판정

| source | 조건 | 기본 source_confidence |
|---|---|---:|
| `edit` | 구조화된 편집 도구 경로와 실제 delta 경로가 일치하고 동시 mutation window가 없음 | 1.00 |
| `shell` | 셸 invocation 직후 실제 delta가 발견되고 다른 mutation window와 겹치지 않음 | 0.90 |
| `generated` | 실제 delta가 설정의 `generated` 경로에 있고 활성 producer invocation과 연결됨 | 0.85 |
| `external` | 활성 invocation 밖에서 발견됐거나 여러 에이전트 mutation window가 겹쳐 원인을 안전하게 특정할 수 없음 | 1.00, 의미는 "원인 미귀속 확정" |

한 change event 안에 source가 섞이면 source별로 이벤트를 나눈다. 원인을 확신할 수 없을 때 `shell`을 억지로 고르지 않고 `external`로 낮춘다.

### 6.4 raw shell 명령 파서의 제한

파서는 다음 용도로만 쓴다.

- redirection, `tee`, `cp`, `mv`, `sed -i`, PowerShell `Set-Content` 등에서 후보 경로 추출
- 메타데이터가 우연히 같아도 후보 파일을 PostTool에서 강제 재해시
- source 귀속 신뢰도 개선
- 성능상 전체 콘텐츠 해시를 피하고 후보를 먼저 확인

파서는 다음을 할 수 없다.

- change event 생성
- `changed_files_seen` 직접 갱신
- 범위 이탈 확정
- verification epoch 갱신
- parser miss를 "변경 없음"으로 바꾸기

따라서 파서가 전혀 이해하지 못하는 `python -c`, 사용자 스크립트, 네이티브 바이너리 쓰기도 Stop full reconciliation에서 탐지된다. 파서가 쓰기를 예상했지만 바이트 delta가 없으면 이벤트는 0건이다.

### 6.5 v2.0.1 관측 상한과 원격 mutation epoch

release-green 지원 envelope는 관측 대상 10,000개, 총 regular-file 크기 256MiB다. 스캐너는 directory entry를 stream으로 방문하면서 entry/byte 예산을 hash 전에 검사한다. cold/full scan은 8초, incremental scan은 2초의 monotonic deadline을 사용한다. 어느 상한이든 초과하면 partial manifest와 baseline을 버리고 `provenance_status=scope_too_large`, `provenance_status_reason=entry_limit|byte_limit|deadline`을 기록한다.

이 시간 상한은 Python in-process scanner의 협력적 deadline이다. 단일 `stat`, `scandir`, 파일 read 같은 OS 호출 자체가 멈춘 경우 이를 선점하지는 못한다. 해당 수준의 hard timeout은 scanner subprocess가 필요한 별도 설계다.

`scope_too_large`는 관측 실패를 clean으로 바꾸는 상태가 아니다. 알려진 변경이 없을 때는 넓은 루트를 좁히라는 Stop 안내와 함께 fail-open하고, 이미 ledger에 확인된 비문서 변경이 있으면 기존 verification gate가 계속 적용된다. permission, unstable path, reparse 오류 같은 실제 관측 결함은 계속 `incomplete`이며 mutation-capable local turn을 차단한다.

직접 실행한 `ssh`와 로컬 파일을 원격 목적지로 보내는 `scp` upload는 로컬 snapshot으로 원격 결과를 증명할 수 없다. 원격 mutation 가능성은 로컬 부작용 가능성과 별도로 판정한다. 전체 exit가 실패해도 부분 원격 변경이 먼저 일어날 수 있으므로, 검증 명령이 아닌 원격 mutation-capable 시도는 `last_remote_mutation_seq`를 올리고 verification 필요 상태로 둔다. 별도 검증 invocation이 시작될 때 고정한 `covers.through_seq`가 해당 remote epoch 이상이어야 Stop을 허용한다. 같은 invocation 안의 원격 변경+검증은 스스로를 cover하지 않는다.

이 완화는 원격 상태를 관측해 clean을 증명한다는 뜻이 아니다. 모든 shell invocation은 프로젝트 루트의 local snapshot 관측을 유지한다. 로컬 redirect, pipe, chain, command substitution, 해석하기 어려운 SSH control/log option이 있어도 원격 실행 가능성이 확인되면 local reconcile과 remote epoch를 모두 기록한다. `ssh -G/-Q/-V/-N/-O/-W` 같은 비실행 동작과 `scp` download는 remote epoch를 만들지 않는다.

## 7. Verification covers와 seq/epoch 통합

### 7.1 covers 스키마

```json
{
  "schema_version": 2,
  "event": "verification",
  "event_id": "01J...:18",
  "seq": 18,
  "turn_id": "01J...",
  "agent": "codex",
  "invocation_id": "01J...",
  "command": "python -m pytest tests -q",
  "success": true,
  "evidence": "152 passed",
  "covers": {
    "through_seq": 16,
    "snapshot_id": "blake2b-256:...",
    "change_ids": ["blake2b-256:..."],
    "change_event_ids": ["01J...:11", "01J...:14"],
    "path_revisions": [
      {
        "change_id": "blake2b-256:...",
        "path": "core/ledger.py",
        "after": "blake2b-256:...",
        "change_event_id": "01J...:14"
      }
    ]
  }
}
```

`covers`는 테스트의 의미론적 코드 coverage 주장이 아니다. **검증 프로세스가 시작할 때 그 파일 리비전이 작업공간에 존재했다**는 시간·상태 관계다.

### 7.2 검증 시작과 종료 순서

1. PreTool에서 `through_seq`와 `snapshot_id`를 고정한다.
2. 검증 프로세스가 실행된다.
3. PostTool FS 대조에서 검증 도중 생긴 파일 delta를 먼저 change event로 기록한다.
4. verification event는 PreTool에서 고정한 change event와 리비전만 `covers`에 넣는다.

따라서 `수정 명령 && pytest`처럼 한 셸 invocation 안에서 코드 변경과 테스트를 함께 수행해도, 그 invocation이 만든 코드 변경은 같은 invocation의 검증에 자동 포함되지 않는다. 별도 검증이 필요하다. raw 명령 파싱으로 이 제한을 완화하지 않는다.

테스트 캐시처럼 기본 soft exclude 아래 생성물은 관측 범위 밖이다. `dist/` 등 관측 대상 생성물이 바뀌면 별도 change event가 되며, source가 generated라는 이유만으로 자동 검증 처리하지 않는다.

### 7.3 Stop 판정

각 활성 턴은 경로별 최신 net revision을 유지한다.

1. baseline과 현재 digest가 같으면 해당 경로는 pending이 아니다.
2. docs-only revision은 이벤트에는 남지만 verification pending에는 들어가지 않는다.
3. 비문서 pending revision마다 성공 verification의 `covers.path_revisions`에 동일한 `(change_id, path, after digest)`가 있어야 한다. `change_event_id`는 감사 추적용이며 attribution merge가 생겨도 검증 관계의 정본은 `change_id`다.
4. 하나라도 없으면 unverified다.
5. verification 뒤 같은 경로가 다시 바뀌면 새 revision ID가 생기므로 이전 covers는 자동으로 stale이 된다.

### 7.4 기존 seq/epoch와의 관계

- `event_seq`: 공유 원장의 전역 단조 순서로 유지한다.
- `last_change_seq`: v1 호환 projection으로, 현재 pending인 비문서 revision 중 최대 seq를 저장한다.
- `verification.seq`: 이벤트 자체의 기록 순서다.
- `covers.through_seq`: 검증 시작 시 보였던 최대 seq다.
- `snapshot_id`와 path digest: 같은 seq 범위에서 실제 콘텐츠 상태를 구별한다.

v2 판정은 단순 `verification.seq > last_change_seq`를 쓰지 않고 `covers`를 우선한다. covers가 없는 v1 verification만 기존 seq 규칙으로 평가한다. 이 dual-read 경로는 마이그레이션 턴에만 허용한다.

## 8. Git과 non-git 디렉토리

### 8.1 Git은 정본이 아니다

Git 전용 delta를 정본으로 삼지 않는 이유는 다음과 같다.

- 턴 시작 전 dirty 파일이 같은 dirty 상태 문자열을 유지하면서 다시 바뀔 수 있다.
- untracked, ignored, generated 파일 정책이 저장소마다 다르다.
- 셸이 commit, reset, checkout으로 HEAD와 worktree를 함께 바꾸면 현재 HEAD 기준 diff가 턴 delta를 숨길 수 있다.
- non-git 디렉토리에서 즉시 동작해야 한다.
- Git index 변경은 프로젝트 파일 콘텐츠 변경과 다른 개념이다.

Git이 있으면 추적 파일 목록이나 ignore 힌트를 경로 방문 순서와 후보 우선순위 최적화에 쓸 수 있지만, snapshot policy가 포함한 경로를 생략하는 데는 쓸 수 없다. 최종 이벤트에는 반드시 FS digest 비교가 있어야 한다. Git 실행 실패는 provenance 실패가 아니라 최적화 미사용이다.

### 8.2 non-git fallback

non-git에서는 동일한 `os.scandir` 기반 snapshot engine을 그대로 사용한다.

- baseline, PostTool, Stop 알고리즘과 이벤트 스키마가 같다.
- 기본 hard/soft exclude와 선택적 provenance config가 같다.
- Git 명령이 없다는 이유로 confidence를 낮추지 않는다.
- `.gitignore`가 없으므로 관측 범위는 show-me-the-work 기본값과 provenance config만으로 결정한다.

즉 non-git은 기능이 축소된 보조 경로가 아니라, VCS 최적화만 빠진 같은 정본 경로다.

## 9. 공유 원장과 F6 해소

### 9.1 v2 ledger 상위 구조

```json
{
  "schema_version": 2,
  "event_seq": 42,
  "manifest_generation": 9,
  "active_turns": {
    "codex": {
      "turn_id": "01J...",
      "start_seq": 8,
      "baseline_snapshot_id": "blake2b-256:...",
      "current_snapshot_id": "blake2b-256:...",
      "pending_change_ids": ["blake2b-256:..."],
      "blocks": {"stop": 1}
    }
  },
  "changed_files_seen": ["core/ledger.py"],
  "change_kinds": ["code"],
  "verification_results": [],
  "last_change_seq": 14,
  "stop_blocks": 1
}
```

`active_turns`가 정본이며 top-level v1 필드는 기본 에이전트 턴의 파생 projection이다. 한 에이전트의 prompt는 자기 `active_turns[agent]`만 교체하고 다른 에이전트 상태를 리셋하지 않는다.

에이전트 식별자가 없는 v1 어댑터는 `agent="default"`로 정규화한다. 같은 에이전트의 세션이 동시에 여러 개일 수 있으므로 실제 key는 내부적으로 `host:session_id:agent`를 사용하고 표시용 `agent`는 별도 보존한다.

### 9.2 F6 원자적 Stop 판정

`evaluate_stop()`의 구조를 다음처럼 바꾼다.

```text
with ledger_transaction(project_root):
    ledger = load_ledger_unlocked()
    turn = active_turn_for(payload)
    decision = evaluate_without_io(ledger, turn, payload)
    if decision is block:
        if turn.blocks.stop >= MAX_STOP_BLOCKS:
            return allow_after_cap
        turn.blocks.stop += 1
        refresh_v1_projection()
        save_ledger_unlocked()
    return decision
```

- read, cap 검사, 증가, projection 갱신, 저장을 한 락에서 끝낸다.
- `MAX_STOP_BLOCKS=2`는 `(agent_key, turn_id)`마다 적용한다.
- 다른 에이전트가 block cap을 소비하지 않는다.
- prompt는 자기 새 turn의 카운터만 0으로 만든다.
- 이벤트 기록과 Stop 판정이 같은 `ledger_transaction`을 사용하므로 change seq 커밋과 allow 판정 순서도 직렬화된다.
- 스캔은 락 밖에서 수행하고, Stop 판정은 커밋된 최신 manifest generation만 읽는다.

동시성 회귀 테스트는 동일 턴에 8개 프로세스가 동시에 Stop을 평가했을 때 정확히 2개만 block, 나머지는 cap allow, 최종 카운터는 2임을 요구한다. thread 테스트만으로 끝내지 않고 Windows와 POSIX subprocess 테스트를 각각 둔다.

### 9.3 이벤트 로그

- 새 이벤트는 에이전트별 `.fable-lite/agents/<safe-agent>.jsonl`에 기존처럼 append한다.
- seq 할당과 append는 ledger transaction 안에서 수행한다.
- v1 JSONL은 재작성하지 않고 읽을 때 v2 정규 형태로 변환한다.
- global current는 `.fable-lite/snapshots/workspace-current.json`, 턴 baseline은 `.fable-lite/snapshots/turns/<agent-key>/<turn-id>-baseline.json`에 원자 교체로 저장한다.
- 종료된 턴 baseline은 삭제하고 global current와 활성 baseline만 유지해 무한 증가를 막는다.

## 10. v1 원장 마이그레이션

### 10.1 선택: 락 안 one-shot migration + v1 projection 유지

schema_version이 없거나 1인 `ledger.json`을 실 원장 자동 트리거가 처음 열 때 한 번만 v2로 변환한다. 자동 트리거의 활성화 시점은 16장의 개발 정책을 따른다.

agy가 제시한 백업 직접 파괴 시나리오는 현행 `_preserve_corrupt_ledger()`가 쓰는 `ledger.json.bak`과 마이그레이션 archive `ledger.v1.json.bak`의 이름이 달라 그대로는 성립하지 않는다. 다만 현 함수가 기존 corrupt backup을 먼저 삭제하는 정책은 별도 손실 위험이므로 W1에서 마이그레이션보다 먼저 고친다.

선행 불변식:

- `_preserve_corrupt_ledger()`는 기존 backup을 unlink하지 않는다. `ledger.json.corrupt-<UTC timestamp>-<unique suffix>.bak`처럼 매번 새로운 timestamp suffix 경로로 옮긴다.
- v2 ledger 저장은 목적지와 같은 디렉토리의 temp 파일에 전체 JSON을 쓴 뒤 `os.replace`로 원자 교체한다. destination에 부분 JSON을 직접 쓰는 경로는 금지한다.
- migration archive와 corrupt backup은 파일명이 겹치지 않는다.

one-shot 절차:

1. `ledger_transaction`을 획득한다.
2. 원본 v1 bytes를 읽고 schema를 검증한 뒤 `.fable-lite/ledger.v1.json.bak`에 temp+atomic replace로 한 번 보존한다. 기존 archive가 있으면 bytes가 원본과 같은지 검증하고 절대 덮어쓰지 않는다.
3. v1 top-level 값을 메모리에서 `active_turns.default`의 `migration_mode="legacy_turn"`으로 옮긴다.
4. 기존 `event_seq`, `last_change_seq`, verification의 `seq`는 그대로 보존한다.
5. v1 changed path에는 존재하지 않았던 digest, source, confidence를 만들어내지 않는다.
6. 완성된 v2 JSON을 temp 파일에 모두 쓴 뒤 원자 교체하고, 즉시 다시 읽어 `schema_version=2`와 필수 projection을 검증한다.
7. serialize, temp write, replace, read-back 중 어느 단계든 실패하면 실패 산출물을 timestamp-suffixed `ledger.json.migration-failed-*.bak`으로 격리하고 `ledger.v1.json.bak`을 temp+atomic replace로 `ledger.json`에 자동 복원한다. 복원 뒤 v1 bytes가 archive와 같은지 재검증하며, 실패를 숨기고 default ledger로 진행하지 않는다.
8. 다음 prompt에서 새 baseline을 만들면 legacy turn을 종료하고 v2 covers만 사용한다.
9. v2 ledger에도 v1 top-level projection을 v2.0.x 동안 유지해 기존 report/check 소비자의 전환 시간을 확보한다.

이 설계는 v1.2 바이너리가 v2 다중 경로 이벤트를 완전히 이해한다고 약속하지 않는다. 운영자가 v1.2로 롤백해야 할 때도 모든 fable-lite 프로세스를 멈춘 뒤 `ledger.v1.json.bak`을 복원한다. v2에서 새로 시작한 턴의 증거는 rollback 후 재수집한다.

### 10.2 legacy verification 처리

- seq가 있는 v1 verification은 현재 v1.2 epoch 규칙을 유지한다.
- seq가 없는 v1 verification은 첫 새 변경 전까지만 기존 any-success 의미를 유지한다.
- 새 v2 change는 legacy seq-less verification을 절대 covers하지 않는다.
- migration 과정에서 unknown digest를 임의 confidence로 승격하지 않는다.

### 10.3 agent JSONL

과거 로그는 append-only 감사 자료이므로 one-shot rewrite 대상에서 제외한다. reader가 다음처럼 정규화한다.

- v1 `{event:"change", path:"x"}`는 legacy event로 표시한다.
- source와 digest가 없으므로 새로운 FS 사실 판정에는 사용하지 않는다.
- v2 이벤트부터 `schema_version`, `paths`, `confidence`, `covers`를 기록한다.

## 11. 오류와 보수적 동작

| 상황 | 동작 |
|---|---|
| 파일 읽기 권한 없음 | incomplete, 경로와 오류 유형 기록, clean 주장 금지 |
| 스캔 중 파일 계속 변경 | 1회 재시도 후 incomplete |
| turn-start fast-path metadata 불일치 | 불일치 경로만 hash하고 generation rebase, 전체 hash로 자동 확대 금지 |
| 직전 Stop current 없음·incomplete·policy 변경 | cold/fallback full hash와 명시적 사유 기록 |
| PostTool 지연 예산 초과 | 부분 결과로 change 확정 금지, Stop full scan 예약 |
| Stop full scan 2초 hard deadline 초과 | mutation-capable 턴이면 block cap 소비, 진단 메시지 출력 |
| 2회 block 뒤에도 incomplete | 기존 fail-open 정책대로 allow하되 `provenance incomplete`를 명시하고 green 증거로 기록하지 않음 |
| Git 없음 또는 Git 명령 실패 | FS snapshot 계속 수행, confidence 변화 없음 |
| shell parser 실패 | 후보 최적화만 포기, Stop full scan 계속 수행 |
| 파일이 baseline으로 복귀 | audit event 유지, pending에서 제거 |
| Windows casefold key 충돌 | 어느 entry도 덮어쓰지 않고 incomplete `casefold_collision` |
| symlink 또는 directory reparse | symlink target만 digest, directory traversal 금지 |
| OneDrive/non-symlink reparse metadata churn | change event 금지, `unstable_reparse`로 두 번 안정화 대기 |
| 동시 에이전트가 같은 물리 변경 관측 | `change_id` 하나로 dedupe, observation 병합, ownership 경합 시 effective source=external |
| migration 저장·검증 실패 | 실패 v2 격리 후 `ledger.v1.json.bak` 자동 원자 복원, default ledger 진행 금지 |

## 12. 기각한 대안

### 12.1 Git diff/status를 정본으로 사용

기각 사유: non-git 불가, pre-dirty 재변경과 HEAD 이동에 취약하며 ignored/generated 정책이 저장소 상태에 종속된다. Git은 최적화 힌트로만 허용한다.

### 12.2 raw shell 명령 정규식으로 쓰기 판정

기각 사유: 셸 문법, quoting, subshell, 스크립트, Python/Node one-liner, 네이티브 바이너리를 완전하게 해석할 수 없다. 오탐이 하드 게이트 마찰로 직결되고 로드맵의 3자 합의에도 반한다.

### 12.3 도구 이름별 변경 기록을 확대

기각 사유: 새로운 도구·호스트마다 누락이 재발하고, 도구 성공과 실제 바이트 delta가 다를 수 있다. source 힌트로만 남긴다.

### 12.4 mtime와 size만 비교

기각 사유: 이전 content digest 없이 metadata만 변경 정본으로 삼으면 같은 크기 재작성, 타임스탬프 보존 복사, 낮은 timestamp 해상도에서 거짓 음성이 생긴다. turn-start fast-path와 PostTool에서는 직전 Stop의 full digest를 재사용하기 위한 cache validation으로만 쓰고, 불일치 파일은 hash하며 Stop은 full content hash를 수행한다.

### 12.5 매 Turn start/PostTool마다 1만 파일 full hash

기각 사유: 정확하지만 대화 턴과 일반 편집마다 1초 수준의 지연이 누적된다. Turn start는 직전 Stop current의 metadata sweep + 불일치 hash, PostTool은 metadata sweep + 후보 hash, Stop만 full hash로 나눈다.

### 12.6 OS file watcher

기각 사유: 장기 프로세스, 이벤트 유실 복구, Windows/Linux/macOS 차이, 라이브러리 의존성이 생긴다. zero-dep·플랫폼 중립 원칙과 맞지 않는다.

### 12.7 스캔 동안 ledger lock 유지

기각 사유: 대형 repo hash 동안 모든 에이전트 이벤트와 Stop이 막히고 Windows stale-lock 판단 시간과 충돌할 수 있다. generation 기반 2단계 커밋을 선택한다.

### 12.8 전역 stop_blocks 유지

기각 사유: 한 에이전트의 재시도가 다른 에이전트 cap을 소모한다. `(agent_key, turn_id)`별 counter가 필요하다.

### 12.9 v1 로그 전체 rewrite

기각 사유: append-only 감사 기록을 다시 쓰면 실패 복구와 동시성 위험이 커지고, 과거 이벤트에 없던 source/digest를 사실처럼 만들게 된다. ledger만 one-shot migration하고 로그는 dual-read한다.

## 13. 성능 예산

### 13.1 기준 repo

릴리스 SLO는 로컬 SSD의 대표 규모와 극한 규모를 모두 hard gate로 사용한다.

- 대표 규모: regular file 1,000개, 총 약 25.6MiB
- 극한 규모: regular file 10,000개, 총 256MiB
- 두 규모 모두 아래 분포와 경로 특성을 동일하게 적용한다.

- 90%는 1-8 KiB, 9%는 64-256 KiB, 1%는 1-8 MiB
- 디렉토리 깊이 1-8
- 경로의 10%는 공백, Unicode, 한글 포함
- hard/soft exclude 바깥 파일만 10,000개로 계산

### 13.2 규모별 훅 허용 지연

#### 대표 규모 1k hard gate

| 경계 | 수행 | p95 | p99 | hard deadline |
|---|---|---:|---:|---:|
| Turn start fast-path | metadata sweep + 불일치 hash, 불일치 100개/16 MiB 이하 | 200 ms | 350 ms | 500 ms |
| Turn start cold/fallback | 최초·incomplete·policy 변경 시 full content baseline | 1,000 ms | 1,500 ms | 2,000 ms |
| 일반 PostTool | metadata sweep + 후보 hash, 변경 100개/16 MiB 이하 | 200 ms | 350 ms | 500 ms |
| Stop/AfterAgent | full enumerate + content reconcile | 1,000 ms | 1,500 ms | 2,000 ms |
| ledger lock 구간 | seq/counter/manifest commit만 | 25 ms | 50 ms | 250 ms |

#### 극한 규모 10k hard gate

| 경계 | 수행 | p95 | hard deadline |
|---|---|---:|---:|
| Turn start fast-path | metadata sweep + 불일치 hash, 불일치 100개/16 MiB 이하 | 1,000 ms | 2,000 ms |
| Turn start cold/fallback | 최초·incomplete·policy 변경 시 full content baseline | 6,000 ms | 8,000 ms |
| 일반 PostTool | metadata sweep + 후보 hash, 변경 100개/16 MiB 이하 | 1,000 ms | 2,000 ms |
| Stop/AfterAgent | full enumerate + content reconcile | 6,000 ms | 8,000 ms |

추가 기준:

- 10,000파일 manifest의 peak 추가 RSS는 80 MiB 이하
- 직전 Stop full result가 유효하고 metadata 불일치가 0건인 turn start fast-path의 콘텐츠 read는 0 bytes다.
- 매 turn start마다 full content hash가 실행되면 성능 hard gate 실패다. full hash 비율과 fallback 사유를 receipt에 기록한다.
- 변경 0건인 PostTool에서 파일 콘텐츠 read는 0 bytes가 목표다. metadata와 후보만 본다.
- 1k와 10k는 metadata 및 full phase를 각각 독립 hard gate로 판정하고 receipt에 규모별 결과를 분리 기록한다.
- 관측 scan이 500ms를 넘으면 stderr에 `[smtw] N개 파일 상태 검증 중...` 알림을 한 번 표시한다. stdout의 hook JSON 규약은 바꾸지 않는다.
- 50,000파일/2 GiB stress에서는 지연 SLO 대신 crash 0, ledger 손상 0, deadline 내 명시적 incomplete를 요구한다.

성능 예산을 넘기면 범위를 몰래 줄이거나 clean으로 간주하지 않는다. incomplete를 기록하고 사용자 설정으로 scan root/exclude를 조정할 수 있게 진단한다.

## 14. 정확도 검증 계획

### 14.1 shell write 대표 corpus

고정 golden corpus는 플랫폼별 200개 케이스로 구성한다.

- positive 120: create, append, truncate, same-size modify, delete, rename, copy, move, multi-file write
- 명령군: redirect, heredoc/here-string, `tee`, `sed -i`, `cp`, `mv`, `rm`, PowerShell `Set/Add/Out-Content`, Python/Node inline write, 사용자 스크립트, 빌드 생성기
- 경로군: 상대/절대, 공백, 한글/Unicode, glob, nested directory, symlink, soft-excluded 경로의 강제 후보
- 원인군: edit, shell, configured generated, 별도 외부 프로세스, 두 에이전트 overlap
- negative 80: `cat`, `rg`, `ls`, `git status`, 실패 명령, path 언급만 한 명령, 동일 바이트 rewrite, 프로젝트 밖 쓰기, hard-exclude 쓰기, 생성 후 baseline 복귀

동일한 실제 FS runner 위에 Claude Code, Codex CLI, Antigravity payload replay를 각각 얹어 어댑터가 판정 의미를 바꾸지 않는지 확인한다. Antigravity는 host hook 엔진이 실제 발동할 때까지 payload injection conformance와 라이브 발동 증거를 구분한다.

### 14.2 메트릭 정의

- 단위: 최종 Stop에서의 `(normalized path, op, after digest)`
- TP: 실제 in-scope net delta와 동일한 event가 존재
- FN: 실제 net delta가 있는데 event가 없음
- FP: baseline과 현재 콘텐츠가 동일한데 event가 pending으로 남음
- source accuracy: 실제 producer category와 source 일치. 원인을 특정할 수 없어 `external`로 낮춘 것은 오귀속으로 세지 않되 별도 unknown rate로 보고
- parser recall: 실제 write 경로를 candidate로 뽑은 비율. 참고 지표이며 release hard gate가 아님

### 14.3 사전 목표치

기계적 hard gate:

| 지표 | golden corpus hard gate | randomized 1,000회 목표 |
|---|---:|---:|
| path/op FN | 0/120 | 0.5% 이하, recall 99.5% 이상 |
| path/op FP | 0/80 | 0.1% 이하, precision 99.9% 이상 |
| 3어댑터 canonical replay | fixture 100% 동일 | 100% 동일 |
| non-git와 Git 판정 불일치 | 0건 | 0건 |

관측·개선 참고 지표, 비차단:

| 지표 | golden corpus 목표 | randomized 1,000회 목표 |
|---|---:|---:|
| source 정확도 | 95% 이상 | 95% 이상 |
| 고신뢰 오귀속 (`source_confidence>=0.9`) | 0건 | 0.1% 이하 |
| 비경쟁 케이스의 `external` unknown rate | 5% 이하 | 10% 이하 |
| parser candidate recall | 85% 이상 | 85% 이상, 비차단 지표 |

source 분류는 실제 LLM 의도를 기계적으로 증명할 수 없으므로 source 정확도와 고신뢰 오귀속을 릴리스 차단에 사용하지 않는다. source 계약의 기계적 hard gate는 동일 canonical invocation에 대해 Claude Code, Codex CLI, Antigravity가 100% 같은 정규 event 의미를 만드는 canonical replay다. 변경 정확도 hard gate는 parser 성능이 아니라 최종 FS reconciliation 결과로 판정한다.

### 14.4 seq/covers/F6 테스트

필수 hostile sequence:

1. verify -> shell edit -> Stop = block
2. edit -> verify -> Stop = allow
3. edit -> `modify && verify` 한 invocation -> Stop = block
4. edit -> verify -> docs edit -> Stop = allow
5. edit -> verify -> generated output change -> Stop = generated revision 정책대로 pending
6. edit -> revert to baseline -> Stop = verification 없이 allow, audit event 유지
7. agent A edit와 agent B verify overlap = B 검증이 A의 사후 revision을 cover하지 않음
8. 8개 동시 Stop = 정확히 2 block, counter=2
9. agent A의 새 prompt가 agent B turn/counter를 보존
10. v1 seq-less ledger -> migration -> 새 change가 legacy verification을 무효화
11. agent A/B 동시 turn start -> generation rebase 후 동일 `change_id` 1개, contended attribution은 external
12. Windows casefold collision -> manifest overwrite 0, explicit incomplete
13. directory symlink loop -> follow 0, 유한 시간 종료
14. OneDrive/non-symlink reparse metadata churn -> pending change 폭주 0, unstable 진단
15. migration 단계별 fault injection -> 부분 v2 JSON 0, v1 archive 자동 복원 100%

### 14.5 벤치 방법

표준 라이브러리 벤치 러너가 합성 repo를 만들고 다음을 측정한다.

1. 5회 warm-up 후 30회 측정
2. 1k, 10k, 50k 파일 규모
3. 직전 Stop full reconcile 직후의 turn start fast-path와 최초/cold full fallback을 별도 series로 측정
4. Git/non-git, clean/pre-dirty, 변경 0/1/100개 조합
5. fast-path에서는 콘텐츠 read bytes, metadata mismatch 수, full fallback 비율과 사유를 기록
6. Windows NTFS와 Linux ext4 CI에서 동일 seed 사용
7. `time.perf_counter_ns()`로 단계별 wall time, `tracemalloc`과 OS 가능 범위에서 peak memory, hashed bytes, stat count 기록
8. p50/p95/p99/max를 JSON receipt로 저장
9. 예산 초과 또는 정상 반복 턴의 무조건 full hash는 실패하고, 50k stress만 incomplete가 명시되면 통과

## 15. 3어댑터 공통 계약

세 어댑터는 host payload를 다음 canonical invocation dict로만 변환한다.

```json
{
  "host": "codex_cli",
  "agent": "codex",
  "session_id": "...",
  "turn_id": "...",
  "invocation_id": "...",
  "phase": "post_tool",
  "tool_family_hint": "shell",
  "candidate_paths": ["core/ledger.py"],
  "command_hint": "...",
  "success": true,
  "evidence": "..."
}
```

- `tool_family_hint`, `candidate_paths`, `command_hint`는 관측 최적화와 source 귀속용이다.
- change event와 covers 계산은 core provenance가 수행한다.
- Claude Code/Codex PostTool의 기존 `EDIT_TOOLS` 즉시 기록 분기를 제거한다.
- Antigravity `handle_after_tool`도 같은 core entrypoint를 사용한다.
- Stop/AfterAgent는 항상 final full reconciliation 후 `evaluate_stop`을 호출한다.
- 어댑터 conformance fixture 하나가 세 host payload를 같은 canonical dict와 같은 ledger 결과로 replay한다.

## 16. 구현 작업 분해

구현은 설계 승인 후 별도 위임에서 아래 순서로 진행한다. 각 W는 앞 단계의 테스트가 green이어야 다음 단계로 넘어간다.

### 개발 원장 보호 정책

- W1-W8의 모든 ledger·snapshot·migration 테스트는 `tmp_path` 또는 명시적 격리 fixture root만 사용한다.
- W1-W8에서는 개발 checkout이나 사용자 프로젝트의 실제 `.fable-lite/ledger.json`을 자동 마이그레이션하는 runtime 연결을 만들지 않는다.
- W4는 migration 함수를 순수하게 구현하되 fixture 전용 명시 호출로만 실행하며, 일반 `load_ledger()` 자동 경로에는 연결하지 않는다.
- W9 E2E도 disposable Git/non-git 프로젝트와 복사한 v1 fixture ledger만 사용한다. W9의 FS 정확도·canonical replay hard gate receipt가 green이 되기 전에는 실 원장 trigger를 활성화할 수 없다.
- 실 원장 자동 마이그레이션 연결은 W9 hard gate가 통과한 뒤 W10의 릴리스 빌드 단계에서만 추가한다. 개발 중간 빌드와 테스트 빌드는 v1 dual-read만 수행한다.

### W1. v2 스키마와 회귀 계약 고정

- 대상: `core/ledger.py`, 신규 schema fixture, `tests/`
- 먼저 추가: `_preserve_corrupt_ledger()`의 timestamp-suffixed 비덮어쓰기 backup, temp+atomic ledger save fault-injection, change v2, verification covers, active turns, v1 projection의 golden JSON 테스트
- 완료 기준: 기존 corrupt backup unlink 0, destination 부분 JSON 0, schema round-trip, 잘못된 confidence/path/covers 입력의 보수적 거부, 격리 v1 fixture 무변경 load

### W2. snapshot engine

- 대상: 신규 `core/provenance.py`
- 구현: 경로 정규화, Windows casefold key/collision, hard/soft exclude, BLAKE2b manifest, non-follow symlink, OneDrive/non-symlink reparse unstable 분류, double-stat, snapshot ID, net delta
- 완료 기준: create/modify/delete/type/mode/revert, same-size/same-mtime, casefold collision, symlink loop, reparse metadata churn hostile case 통과

### W3. 관측 lifecycle과 manifest 저장

- 대상: `core/provenance.py`, `core/agent_log.py`
- 구현: workspace-current turn-start fast-path, begin invocation, incremental PostTool, final Stop reconcile, turn-start 포함 generation rebase, `change_id` dedupe와 contended ownership external merge, atomic snapshot 저장
- 완료 기준: 정상 반복 turn의 full hash 0, 스캔 중 파일 변경과 두 observer 충돌에서 중복 physical change 0, ledger 손상·거짓 clean 0

### W4. ledger v2와 fixture-only one-shot migration engine

- 대상: `core/ledger.py`, migration tests
- 구현: `schema_version=2`, active turns, 전역 seq, v1 projection, immutable v1 archive, 단계별 실패 자동 복원, legacy turn 종료. 일반 runtime auto trigger 연결은 금지
- 완료 기준: 격리 v1 seq/seq-less fixture, 각 fault point의 v1 archive 자동 복원, downgrade projection, agent JSONL dual-read 통과

### W5. covers 기반 verification epoch

- 대상: `core/ledger.py`, `core/verify_state.py`, `fable_lite/check_support.py`
- 구현: PreTool covers 기준 고정, path revision 관계, pending 판정, v1 fallback
- 완료 기준: 15개 hostile sequence와 `check`/Stop 동일 판정

### W6. F6 원자적 block counter

- 대상: `core/verify_state.py`, `core/agent_log.py`
- 구현: evaluate-stop RMW 전체 transaction, `(agent_key, turn_id)` counter, 순수 판정 함수 분리
- 완료 기준: Windows/POSIX subprocess 동시 Stop에서 2 block, lost update 0, 다른 에이전트 counter 간섭 0

### W7. 세 어댑터 통합

- 대상: `adapters/claude_code/**`, `adapters/codex_cli/**`, `adapters/antigravity/**`
- 구현: canonical invocation mapping, change 즉시 기록 제거, core observation 호출, Stop final reconcile
- 완료 기준: 세 host payload replay 결과가 byte-equivalent canonical event 의미를 가짐

### W8. shell 후보 파서와 source 귀속

- 대상: 신규 `core/shell_hints.py`, provenance config
- 구현: Bash/PowerShell 대표 경로 힌트, generated 설정, overlap 시 external 하향
- 완료 기준: parser miss가 final detection에 영향 0, parser-only FP event 0. source 정확도·고신뢰 오귀속은 참고 receipt로 보고하고 릴리스를 차단하지 않음

### W9. 정확도 corpus와 non-git E2E

- 대상: `eval/provenance/`, `tests/`
- 구현: 플랫폼별 200 golden cases, randomized runner, Git/non-git 대조, 세 어댑터 replay
- 완료 기준: path/op FN·FP, Git/non-git 동일성, 3어댑터 canonical replay 100% 등 14.3 기계 hard gate 전부 충족. Antigravity receipt는 injection/live 상태를 정직하게 분리

### W10. 성능 벤치, 릴리스 마이그레이션 연결, 운영 문서

- 대상: `eval/bench_provenance.py`, W9 이후의 `core/ledger.py` release wiring, 설치/아키텍처/CHANGELOG 문서
- 구현: 1k/10k/50k fast/cold 벤치, JSON receipt, incomplete 진단, config 설명. W9 hard-gate receipt를 입력으로 확인한 릴리스 빌드에서만 migration engine을 일반 load 경로에 연결
- 완료 기준: 13.2의 1k/10k 규모별 metadata·full SLO가 모두 green, 정상 turn 무조건 full hash 0, 격리 설치본 migration·자동복원 E2E, 표준 검증 3종과 실제 shell write E2E green, 릴리스 receipt 보존. W9 receipt가 없거나 red면 auto migration 연결 빌드 실패

## 17. v2.0 P0 완료 하드게이트

아래 중 하나라도 실패하면 Change Provenance P0는 완료가 아니다.

1. 도구 이름 또는 raw 명령만으로 change event가 생성되는 경로 0
2. parser가 모르는 in-scope shell write가 Stop full reconciliation에서 누락되는 케이스 0/120
3. 동일 바이트/no-op/실패 명령 FP 0/80
4. verify-before-change와 same-invocation modify+verify가 allow되는 경로 0
5. Git/non-git 판정 불일치 0
6. v1 ledger migration 데이터 유실·부분 JSON 0, 단계별 실패 자동 복원 100%, 새 change가 legacy evidence를 stale 처리
7. 동시 Stop lost update 0, agent 간 cap 간섭 0
8. 동시 turn start generation rebase에서 동일 `change_id` 중복 0, contended attribution은 external
9. Claude Code/Codex/Antigravity canonical replay 100% 일치
10. Windows casefold collision 덮어쓰기 0, symlink follow 0, reparse metadata-only FP 0
11. 1k 대표 규모와 10k 극한 규모의 metadata·full 지연 SLO 및 메모리 예산 충족, 정상 turn 무조건 full hash 0
12. W9 hard gate 이전 실 원장 auto migration trigger 0
13. stdlib 외 런타임 의존성 0, wmux/상주 데몬 의존성 0

## 18. 합의 후 고정할 구현 판단

이 문서에서 다음 판단은 이미 고정했다. 구현자가 다시 선택하지 않는다.

- 정본: 직전 Stop `workspace-current` + turn-start metadata sweep + 불일치 hash fast-path. 최초/cold/incomplete/policy 변경만 full hash
- 해시: BLAKE2b-256
- 동시성: turn start 포함 모든 current 갱신에 generation rebase, 동일 물리 transition은 `change_id` dedupe, 소유권 경합은 external
- 경로: Windows casefold canonical key와 충돌 실패, symlink non-follow, non-symlink reparse unstable
- PostTool: metadata sweep + 후보 hash
- Stop: full content reconciliation
- VCS와 shell parser: auxiliary only
- v2 검증: covers path revisions 우선, seq는 순서와 v1 fallback
- migration: timestamp corrupt backup 선행, temp+atomic save, 실패 자동복원, v1 projection 유지, JSONL dual-read. 실 원장 auto trigger는 W9 이후 릴리스 빌드만
- F6: per-agent-turn counter의 transaction 내부 RMW
- non-git: 동일 snapshot engine
- 의존성: Python stdlib only

설계 승인 뒤 W1부터 순차 구현하며, 이 문서 단계에서는 어떤 구현 파일도 수정하지 않는다.
