# show-me-the-work (쇼미더워크)

**show-me-the-work**(`smtw`, 쇼미더워크)는 AI가 “완료했다”고 말하기 전에 실제 실행 증거가 있는지 검사하는 로컬 훅 기반 작업 감독 도구입니다.

[![version](https://img.shields.io/badge/version-2.6.0-brightgreen.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> English documentation: [README.md](README.md)

## 1. 한 문장으로 이해하기

코딩 AI는 유능하지만, 코드를 실행하지 않고 완료했다고 하거나 다음 작업을
약속한 뒤 멈추거나 직접 보지 않은 화면을 완성됐다고 보고할 수 있습니다.
show-me-the-work는 로컬 훅 이벤트를 관측하고 작업 증거를 기록한 뒤, 근거 없는
완료 보고에 이의를 제기합니다.

검증 없는 완료를 최대 두 번 되돌려 보내고, 이후에는 교착을 막기 위해
경고·감사기록과 함께 통과시킵니다. 모델을 더 똑똑하게 만들거나 실행 증거를
코드 정답의 증명으로 바꾸는 도구는 아닙니다.

### 왜 “show-me-the-work”인가요?

이 프로젝트는 **fable-lite**라는 이름으로 시작했습니다. “Fable 5의 일하는
규율을 하위 모델에 옮길 수 있을까?”라는 실험에서 모델 능력 자체는 이식할 수
없지만 조사·검증·완료 규율은 절차로 구현할 수 있다는 결론을 얻었습니다.
v2.0의 새 이름은 그 좁고 정직한 제품 범위를 설명합니다. AI가 완료했다고
말하면 실행·관측·증거를 보여달라고 요구합니다. **Show me the work.**

## 2. 보장하는 것과 보장하지 않는 것

### 제공하도록 설계된 보장

- verification command가 실제 실행됐는지와 성공·실패 결과를 관측합니다.
- 인정된 검증 뒤 관련 파일이 다시 바뀌면 fresh verification이 아님을
  탐지합니다.
- 일부 high-risk 작업에 identity별 계약을 요구합니다.
- R2가 다른 에이전트의 미정산 변경이나 보호 상태를 지울 수 있는 파괴 작업을
  막습니다.
- 일반 증거 게이트는 완료를 최대 두 번 되돌린 뒤, 명시적 감사기록을 남기고
  fail-open합니다.
- 턴·invocation·경로·goals·scorecard·차단 명령의 로컬 감사 증거를 남깁니다.

### 보장하지 않는 것

- 코드, 조사 내용, 시각 결과가 정답이라는 보장
- DB·서비스·네트워크·원격 부작용의 완전한 관측
- 의도적인 arbitrary-code 우회에 대한 완전 방어
- 인간 검토나 운영 승인의 대체
- 모든 AI host의 live 지원
- 모든 shell·PowerShell·동적 언어 문법의 완전한 해석

프롬프트 지시만 사용한 한 실험에서는 자연 준수가 0/3이었고, Stop 게이트를
켠 세션은 3/3이 실제 증거를 갖춘 차단 후 회복으로 수렴했습니다
([실험 보고서](docs/reviews/p5b-n1-natural.md)). 5개 과제 블라인드 비교에서는
검증 규율에서 ON이 5/5 이겼지만 과제 정답률은 같았습니다
([A/B 보고서](docs/reviews/e1-ab-report.md)). 작은 행동 측정이지 일반적인
정답 보장은 아닙니다.

### 호스트 지원

| 호스트 | 현재 상태 |
|---|---|
| Claude Code | 라이브 훅 체인 확인 |
| Codex CLI | 라이브 훅 체인 확인 |
| Antigravity | host 1.1.2+에서 payload·config load 정합성 확인, config-path 훅의 live 실행은 미확인 |

## 3. 이름·패키지·경로

| 표면 | canonical | legacy / 호환 |
|---|---|---|
| 제품 | show-me-the-work | fable-lite(과거 이름) |
| CLI | `smtw` | `fable-lite` |
| Python import | `smtw` | `fable_lite` |
| distribution | 당분간 `fable-lite` | 동일 distribution |
| runtime state | 신규·이관 프로젝트의 `.smtw/` | 미이관 프로젝트의 `.fable-lite/` |
| project config | `.smtw.toml`, 그다음 `pyproject.toml` `[tool.smtw]` | `.fable-lite/config.json` fallback |
| runtime env | `SMTW_*` | `FABLE_LITE_*` read alias |

한 시점에 authoritative state tree는 하나입니다. runtime authority를
이관한 뒤에도 호환 기간에는 legacy activation config가 fallback config
source로 남을 수 있습니다.

## 4. 1분 설치

전제 조건은 `PATH`에서 실행 가능한 Python 3.12+입니다. runtime은 Python
표준 라이브러리만 사용합니다. 감독하지 않는 프로젝트가 매 훅마다 Python
기동 비용을 내지 않도록 project scope 설치를 권장합니다.

```bash
git clone https://github.com/pinetreeB/show-me-the-work
cd show-me-the-work
claude plugin marketplace add .
claude plugin install show-me-the-work@show-me-the-work --scope project
smtw init --root .
smtw doctor --root .
```

`smtw init`은 정확한 사용자 홈을 거절하고, 기존 canonical/legacy config를
덮어쓰지 않으며, runtime state를 만들거나 legacy state를 이관하지 않습니다.
기본값은 `.smtw.toml`을 만들고 `.gitignore`에 runtime 패턴을 제안한 뒤
`smtw doctor`를 안내합니다. `--config pyproject`를 쓰면 canonical table을
`pyproject.toml`에 추가하고, `--no-gitignore`는 ignore 파일을 건드리지
않습니다.

개인 전용 미커밋 플러그인 등록은 Claude의 `--scope local`을 사용하십시오.
user scope 설치도 가능하지만 감독이 꺼진 프로젝트에서도 빈 훅 결과를
반환하기 전에 Python 프로세스가 시작됩니다. 정확한 사용자 홈은 config가
있어도 항상 비활성입니다.

최초 `cwd 폴백은 best-effort`일 뿐 security trust boundary가 아닙니다.
Claude가 `CLAUDE_PROJECT_DIR`를 제공하거나 session root가 latch되기 전에는
위조된 hook payload 또는 working directory가 최초 상향 config search를
다른 위치로 유도할 수 있습니다. mismatch environment root는 선택된
프로젝트가 자체 exact opt-in config를 가진 경우에만 해당 hook에 유효합니다.

## 5. 프로젝트 설정

권장 dedicated config:

```toml
# .smtw.toml
schema_version = 1
supervision = true
```

canonical 대안:

```toml
# pyproject.toml
[tool.smtw]
schema_version = 1
supervision = true
```

config 우선순위는 엄격합니다.

1. `.smtw.toml`
2. `pyproject.toml`의 `[tool.smtw]`
3. legacy `.fable-lite/config.json`

우선순위가 높은 config가 선언됐지만 유효하지 않으면 오류입니다. 활성화된
legacy config로 조용히 fallback하지 않습니다. canonical/legacy runtime
환경변수는 값이 같을 때만 공존할 수 있습니다. 값 충돌은 fail-closed이며,
`smtw doctor`는 secret 원문을 출력하지 않고 key 이름만 보고합니다.

### 기존 legacy 프로젝트

기존 프로젝트는 다음 호환 activation file을 유지할 수 있습니다.

```json
{"schema_version": 1, "supervision": true}
```

`smtw init`은 이 파일을 보존하고 명시적인 migration check를 안내합니다.
canonical config에서 `supervision = false`로 두거나 activation config를
모두 제거하면 감독이 비활성화됩니다. 비활성화는 기존 state를 삭제하지
않습니다. `SMTW_TEST_FORCE_ENABLE=1`은 자동 테스트 전용이며 일반 세션에서
사용하면 안 됩니다.

## 6. 상태 authority와 migration

`smtw status`와 `smtw doctor`는 현재 authority를 보여줍니다.

| layout | 의미 | authority / 조치 |
|---|---|---|
| `EMPTY` | state tree가 없음 | 다음 활성 write는 `.smtw/` 사용 |
| `LEGACY` | 미이관 legacy tree만 있음 | `.fable-lite/`; migration은 선택적·명시적 |
| `NATIVE` | migration marker 없는 canonical tree가 있음 | `.smtw/` |
| `MIGRATING` | legacy source와 소유된 staging state가 있음 | `.fable-lite/`; 현재 migration을 기다리거나 조사 |
| `MIGRATED` | 검증·publish된 canonical tree가 있음 | `.smtw/`; 보존된 legacy는 fallback authority가 아님 |
| `CONFLICT` | 안전한 단일 authority를 증명할 수 없음 | authority 없음; 관련 작업 block 또는 degraded |

```bash
smtw status --root .
smtw migrate --root . --check
smtw migrate --root .
smtw doctor --root .
```

layout migration은 자동 실행되지 않습니다. `--check`는 write-free입니다.
migration은 새 authority를 copy·verify한 뒤 원자적으로 publish하며, active
turn이나 open invocation이 있으면 defer합니다. 성공 뒤에도 명시적 rollback
판단을 위해 source tree를 보존하고 publish 이후에는 그 tree로 조용히
fallback하지 않습니다. 지원되는 state writer는 공통 layout barrier를
사용하므로 publish 경계에서 성공한 write를 잃지 않습니다.

layout migration과 versioned ledger backfill은 서로 다른 기능입니다. ledger
migration 환경 스위치를 `smtw migrate` 대신 사용하지 마십시오.

## 7. 빠른 운영 명령

```bash
smtw doctor --root .
smtw doctor --root . --json
smtw status --root .
smtw migrate --root . --check
smtw quarantine list --root .
smtw scorecard --root . --view coordination
smtw goals status --root . --identity <host:session-id:agent>
```

`doctor`는 tool/distribution/module 버전, Python, host/plugin/config, 환경
충돌, state authority, migration readiness, active work, ledger/provenance
health, quarantine 사용량, probe/host 상태를 보고합니다. 종료코드는 `0`
healthy, `1` unsafe/error, `2` inactive/deferred/action required입니다.
`status`는 더 짧은 runtime view입니다.

multi-story 작업 예시:

```bash
smtw goals plan --root . --goal "release" --story "Windows 검증" --verify-cmd "python -m pytest"
smtw goals verify --root . --story "Windows 검증" --evidence "pytest green"
```

wmux 형태의 위임에서는 `brief`가 작업 규율 블록을 만들고 `check`가 원장과
worktree를 대조합니다.

```bash
smtw brief --paths "core/**,tests/**" --verify-cmd "python -m pytest tests/" --sentinel tmp/.done --target codex
smtw check --root . --agent codex --since-file tmp/.delegation-start
```

## 8. 게이트 동작

| gate / 경계 | event | 사용하는 evidence | block cap | 실패 정책 | Known Limitation |
|---|---|---|---|---|---|
| N1 조사 | prompt routing, Stop | 한·영 hypothesis/evidence/rejection marker | Stop cap 2회 공유 | cap 이후 감사기록과 함께 fail-open | marker는 보고 구조만 증명하고 가설의 진위를 증명하지 않음 |
| N2 goals / intent / design | prompt, completion checkpoint | identity별 plan·검증 증거·확정 intent | gate별 2회 | cap 이후 감사기록과 함께 fail-open | synthetic/foreign identity는 다른 active identity를 만족시킬 수 없음 |
| verification completion | Stop / AfterAgent | 현재 변경을 덮는 성공 command observation | 2회 | cap 이후 감사기록과 함께 fail-open | 성공한 test가 잘못 선택된 test일 수 있음 |
| R1 high-risk contract | PreToolUse | authoritative tree 아래 evidence-bearing identity contract | 일반 cap 없음 | contract 전까지 hard block | 선택된 위험군을 다루며 외부 승인은 아님 |
| R2 파괴 보호 | PreToolUse | parsed target, logical/resolved candidate, peer ownership, protected state | cap 없음 | ambiguity와 peer risk는 fail-closed | 동적 실행은 항상 정적으로 decode할 수 없음 |
| scope drift | PostToolUse | 요청 scope와 관측 경로 비교 | advisory | 턴별 dedupe warning | edit를 되돌리지는 않음 |
| stale mutation | PreToolUse | 현재 turn identity와 lifecycle | cap 없음 | mutation deny, 입증된 read-only는 기존 정책 유지 | 구버전의 오래된 turn은 abandoned work와 구분하기 어려울 수 있음 |
| runtime env conflict | 활성 hook 전체 | canonical/legacy 변수 존재·값 일치 | cap 없음 | fail-closed | secret 원문이 아니라 이름만 보고 |
| state layout conflict | state consumer / mutation | 검증된 layout과 migration marker | cap 없음 | authority 없음, block 또는 degraded | operator 조사가 필요할 수 있음 |

일반 observer·health 기록 실패는 대체로 warning과 함께 fail-open합니다. 이
정책은 R2, 환경변수 충돌, authority 충돌 경계를 약화하지 않습니다. inline
Python path hint는 마찰 장치이지 권한 경계가 아니며, 독립된 R2가 권한 경계를
담당합니다.

## 9. 멀티에이전트 운영

- **identity:** exact identity는 `host:session-id:agent`입니다. exact active
  identity가 하나면 자동 선택할 수 있지만 둘 이상이면 `--identity` 또는
  일치하는 `--host`, `--session-id`, `--agent`가 필요합니다. synthetic
  identity를 성공한 ownership처럼 표시하지 않습니다.
- **candidate ownership:** 도구가 선언한 경로는 attribution용 logical
  project-relative candidate로 보존하고, 현재 resolve된 경로는 R2 peer
  matching용으로 따로 추적합니다. symlink가 교체돼도 선언 ownership 기록은
  사라지지 않습니다.
- **settlement:** peer의 open invocation 또는 unsettled revision은 파괴
  작업이 소비하기 전에 종료하거나 명시적으로 정산해야 합니다. 시간이
  지났다는 이유만으로 ownership을 지우지 않습니다.
- **R2:** 파괴 명령은 peer candidate와 canonical/legacy state path를 모두
  검사합니다. 파괴 target을 해석할 수 없으면 안전하다고 추측하지 않고
  거절합니다.
- **goals:** checkpoint는 identity namespace에 저장됩니다. 한 identity의
  plan으로 다른 identity의 N2 gate를 회복할 수 없습니다.
- **quarantine:** R2에 차단된 명령은 authoritative tree에 best-effort로
  보관됩니다. 보관 성공·실패는 deny 결정을 바꾸지 않습니다. operator가
  list/show/clear할 수 있지만 자동 apply는 없습니다.
- **stale turn:** stale turn의 mutation-capable 작업은 invocation 등록 전에
  거절됩니다. 현재 prompt를 제출해 새 turn을 시작해야 합니다.
- **migration:** migration 중 state를 수동 수정하지 마십시오. layout barrier가
  지원 writer를 직렬화하고, live work가 보이면 migration이 defer합니다.

[LazyCodex/OmO](https://github.com/code-yeongyu/oh-my-openagent)의 `ulw`가
작업을 끝까지 진행시키고 show-me-the-work가 완료 증거를 검사하는 방식으로
함께 사용할 수 있습니다. 두 도구는 서로 보완하지만 상대 도구의 권한을
확장하지 않습니다.

## 10. 호환성

legacy state/config/environment read와 `fable_lite` Python shim은 v3.x 호환
기간 동안 유지됩니다. 제거는 v4보다 이르지 않게 계획되어 있으며, upgrade
전 changelog를 확인해야 합니다. state migration이 publish된 뒤 호환 read가
옛 authority로의 파일별 fallback을 뜻하지는 않습니다.

| legacy 표면 | 지원 상태 | canonical 대체 |
|---|---|---|
| `fable-lite` console script | 호환 상태로 지원 | `smtw` |
| `import fable_lite`와 public submodule | 프로세스당 한 번 `DeprecationWarning`을 내는 alias | `import smtw` |
| `python -m fable_lite` | 지원, deprecated | `python -m smtw` |
| `python -m fable_lite.cli` | physical thin shim 지원 | `python -m smtw` |
| `python -m fable_lite.scorecard` | physical thin shim 지원 | `smtw scorecard` |
| `python -m fable_lite.migrate` | physical thin shim 지원 | `smtw migrate` |
| 그 밖의 public `fable_lite.<module>` 실행 | physical compatibility shim 존재, CLI module은 `smtw`로 위임 | top-level `smtw` CLI 권장 |

기본 warning 처리는 지원합니다. `-W error::DeprecationWarning` 또는
`PYTHONWARNINGS=error`로 `DeprecationWarning`을 오류 승격하는 환경은
의도적으로 호환 계약 밖입니다.

source checkout에서는 인접 `pyproject.toml`과 `.git`이 있으면, 더 오래된
global distribution이 설치돼 있어도 source version을 우선합니다.
`smtw doctor`가 module/distribution 경로와 버전을 모두 보고하고 불일치를
경고합니다. wheel 설치에서는 distribution metadata가 authoritative합니다.

## 11. 성능과 scope

- project-scope 플러그인 설치를 권장합니다. 비활성 global 설치는 프로젝트에
  write하지 않지만 매 hook event에 Python interpreter가 시작됩니다.
- release 지원 범위는 관측 entry 10,000개와 regular-file content 256 MiB입니다.
  full reconciliation은 8초, incremental observation은 2초의 협력적 deadline을
  사용합니다.
- 상한 근처의 Stop full reconciliation은 수 초가 걸릴 수 있습니다. entry,
  byte, time 예산 중 하나를 넘으면 partial snapshot을 버리고 완전 관측을
  주장하는 대신 `scope_too_large`를 보고합니다.
- deadline은 filesystem call과 hash chunk 사이에서 검사합니다. 한 OS call이
  멈춘 경우 이 in-process scanner가 선점할 수 없습니다.
- layout inspection과 `status`는 로컬 filesystem 작업입니다. `doctor`는
  config·ledger·quarantine inventory·마지막 probe receipt도 읽지만 network
  call은 하지 않습니다.

생성물·vendor tree를 제외한 집중된 project root를 사용하십시오. provenance
exclude는 관측 범위를 줄이는 trust decision이므로 검토가 필요합니다.

## 12. 개인정보·보존·삭제

state는 프로젝트 authority에 로컬로 저장되지만 민감한 작업 문맥을 포함할 수
있습니다.

- command와 영향 경로
- file digest, invocation·turn metadata
- prompt에서 유도한 intent·goals·high-risk contract evidence
- agent log, verification observation, gate journal, scorecard
- 차단 명령의 quarantine content

runtime state는 기본적으로 commit하거나 외부 전송하지 마십시오. `smtw init`은
canonical/legacy tree 모두에 대한 ignore pattern을 제안합니다. quarantine은
64개, 총 16 MiB, 7일로 제한되며, command 한 건은 1 MiB까지 저장하고 초과하면
original/stored byte count와 SHA-256 metadata를 남깁니다. 그 밖의 state는
프로젝트 정책이나 operator가 제거할 때까지 남습니다.

```bash
smtw status --root .
smtw quarantine list --root .
smtw quarantine clear --root . --all
```

전체 state를 자동 초기화하는 명령은 없습니다. 수동 삭제 전 supervision을
끄고 active agent를 종료한 뒤 `status`/`doctor`로 authority를 확인하고, 필요한
감사·rollback 자료를 보존하며, canonical tree와 보존된 legacy tree를 별개로
취급하십시오. 잘못된 tree 삭제는 증거나 rollback 자료를 없앨 수 있습니다.
supervision 비활성화만으로 파일이 삭제되지는 않습니다.

## 13. 개발과 검증

blocking CI matrix는 Ubuntu·Windows, Python 3.12에서 다음을 실행합니다.

```bash
python scripts/sync_version.py --check
ruff check core adapters fable_lite goals tests eval contrib scripts smtw --exclude eval/ab
python -m pytest tests/ -q
python eval/run_probes.py --strict --output tmp/smtw-probes.json
python eval/e2e_smoke.py
python -m compileall -q core adapters fable_lite goals eval contrib scripts smtw
python -m eval.provenance.run --output tmp/smtw-provenance.json
python -m build --wheel --outdir dist
python scripts/check_wheel_contents.py --wheel-dir dist
```

이후 clean Python 3.12 virtual environment에 wheel을 설치해 canonical/legacy
module·console entry point를 smoke합니다. release-quality는 randomized
provenance, 1k/10k performance receipt(shared runner 변동 때문에 non-blocking),
8-process Stop counter race도 실행합니다.

결정론적 probe runner는 자동 항목을 pass/fail로 기록하고 모델 판정 항목은
`manual`로 남깁니다. 실패 시 non-zero가 필요하면 `--strict`를 사용하고, 로컬
receipt 교체를 피하려면 scratch `--output` 경로를 지정하십시오.

### 출처와 라이선스

조사·검증·분해·조기종료 방지 절차는
[fivetaku/fablize](https://github.com/fivetaku/fablize)(MIT)에서 검증된
아이디어를 참고했습니다. intent interview 방법은
[Yeachan-Heo/gajae-code](https://github.com/Yeachan-Heo/gajae-code)(MIT)에서
차용했습니다. 평가 루프는
[rennf93/opus-fable-playbook](https://github.com/rennf93/opus-fable-playbook)과
[elon-choo/fablever](https://github.com/elon-choo/fablever)의 아이디어를
참고했습니다. 문장과 코드는 모두 새로 작성했습니다.

MIT © pinetreeB
