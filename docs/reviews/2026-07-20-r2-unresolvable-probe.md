# material-erp R2 게이트 "unresolvable_target" 오탐 조사

## 본문 — 쉬운 결론

이번 사고는 한 가지 원인이 아니라, 서로 다른 두 가지 사실이 겹쳐서 "계약을 등록해도 소용없이 차단당한다"는 인상을 만든 것입니다.

**첫 번째 사실(의도된 동작·버그 아님)**: 이 시스템에는 서로 대화하지 않는 두 문지기가 있습니다. 하나는 "이 작업에 대해 계획서(계약)를 써 놨는가"만 확인하고, 다른 하나는 "지금 실행하려는 터미널 명령이 위험해 보이는가"만 확인합니다. 위험 명령 문지기(이하 "위험차단")는 계획서 문지기보다 먼저 실행되고, 계획서가 있는지 아예 확인하지 않습니다. 그래서 워커가 정상적으로 계약을 등록해 놓았어도, 그 계약과 전혀 상관없이 위험차단이 따로 걸릴 수 있습니다. 이건 설계 문서에 "계약으로 면제되지 않는다"고 명시적으로 적혀 있는, 의도된 동작입니다. 심지어 워커가 "자기 몫의 계약 파일 자체"를 터미널 명령으로 쓰려고 해도 이 위험차단은 봐주지 않습니다 — 시스템 상태 폴더 전체가 무조건 보호 대상이기 때문입니다. 실제로 오늘 오전 10시 51분에 일어난 첫 번째 차단이 정확히 이 경우였습니다(사유: "시스템 폴더 보호").

**두 번째 사실(진짜 오탐)**: 같은 워커가 약 21초 뒤, 그리고 1시간 36분 뒤(12시 27분)에 다시 시도했을 때 사유가 "대상 경로를 확정할 수 없음(unresolvable_target)"으로 바뀌었습니다. 이 이름만 보면 "파일 경로가 이상해서 못 찾았다"는 뜻처럼 들리지만, 실제로 재현해보니 전혀 다른 이야기였습니다. 위험차단 로직은 PowerShell 명령의 맨 앞부분이 `$`로 시작하는 변수(예: `$content = "..."; Set-Content ...`처럼, 내용을 변수에 담아뒀다가 파일에 쓰는 아주 흔한 방식)이면, 그 명령이 무엇을 하려는지 제대로 살펴보지도 않고 "혹시 위장된 위험 명령일 수 있다"며 일단 차단해버립니다. 이때 내부적으로 기록되는 진짜 사유는 "이 명령을 해석할 수 없음"인데, 이 사유가 사람이 보는 최종 분류표에는 없어서 시스템이 기본값으로 "경로를 확정할 수 없음"이라고 잘못 표시해버립니다. 즉 **실제로는 경로 문제도, 계약 문제도, 다른 워커와의 충돌 문제도 아니고, 그저 "변수를 써서 파일을 쓰는 흔한 PowerShell 문법"을 시스템이 알아보지 못해서 생기는 이름표 오류**입니다.

이 오탐은 시스템 상태 폴더에만 국한되지 않습니다. 직접 재현해보니, `.fable-lite`와 전혀 상관없는 평범한 보고서(.md) 파일을 같은 방식(변수에 담아서 쓰기)으로 저장하려 해도 똑같이 차단되고 똑같이 "경로를 확정할 수 없음"으로 표시됐습니다. 반면 같은 내용을 변수 없이 통째로 한 줄에 적어서 쓰면 문제없이 통과했습니다. 계약이 있든 없든 결과는 동일했습니다 — 계약 유무는 애초에 이 판정에 전혀 관여하지 않기 때문입니다.

정리하면: "계약을 등록해도 차단된다"는 관찰은 절반은 설계상 원래 그런 것(계약이 위험차단을 면제해주지 않음)이고, 절반은 시스템이 흔한 스크립트 작성 습관을 위험 신호로 오인하면서 엉뚱한 이름표("경로 문제")를 붙이는 진짜 결함입니다. 워커가 실제로 자기 이름으로 된 계약을 그 시점에 갖고 있었는지는 로그상 확인되지 않았습니다(해당 워커 명의의 계약 파일이 저장소에 하나도 남아있지 않음) — 다만 이는 첫 번째 차단(시스템 폴더 보호) 자체가 "계약 파일을 쓰려는 시도"를 막았을 가능성과도 앞뒤가 맞습니다.

또 하나 바로잡을 점: 이 위험차단은 "이미 만들어진 파일을 되돌리는(롤백)" 기능이 없습니다. 명령이 실행되기 전에 미리 막는 것뿐이라, 파일이 "생겼다가 사라지는" 것처럼 보였다면 그건 이 차단 자체가 파일을 지운 게 아니라, 뒤이은 갱신·재작성 시도가 막혀서 기대한 최종본이 끝내 반영되지 않은 것으로 보는 편이 더 정확합니다.

**권고(코드 수정은 이번 조사 범위 밖)**: ①워커/어댑터 쪽에서 상태 파일이나 보고서를 터미널 명령으로 쓸 때는 변수에 담지 말고 값을 그대로 한 줄에 적는 방식을 쓰거나, 애초에 터미널 대신 정식 파일-쓰기 도구를 쓰도록 안내. ②제품 쪽에서는 "명령을 해석 못 함"과 "경로가 실제로 안 열림"을 같은 이름표로 뭉치지 않도록 분류표를 손봐서, 다음에 같은 상황이 생겼을 때 사람이 엉뚱하게 "경로가 왜 안 열리지"를 조사하며 시간을 낭비하지 않게 하는 것을 후속 과제로 남긴다.

---

## 조사 기록

가설 1: R1(계약 검증)과 R2(파괴 명령 차단)가 서로 독립된 별개 게이트라, 유효한 계약 등록이 R2 차단을 전혀 면제하지 못한다 — 버그가 아니라 설계 의도.
증거: `docs/design/multiagent-gate.md:125` "R1과 독립 판정(unrelated contract로 면제 불가 — 프로브 실측)... authoring 자기 예외는 R2 이후이므로 R2를 건너뛸 수 없다(계약 파일 자체가 파괴 대상인 경우 포함)".
증거: `adapters/claude_code/pre_tool_use.py:155-178` — R2(`evaluate_r2_destructive_gate`)가 R1(`evaluate_pretool_contract`) 호출보다 먼저 실행되고, R1 결과를 참조하지 않음.
증거: 프로브 Group G(`tmp/_r2_probe.py`) — 동일 명령을 (a) 유효한 네임스페이스 계약을 등록한 identity, (b) 계약이 전혀 없는 identity로 각각 실행해도 R2 판정(`state_dir_protected`/`unresolvable_target`)이 완전히 동일함을 실행으로 확인.
신뢰도: 높음(채택, 부분 원인).

가설 2: `.fable-lite` 상태 디렉토리 하드 차단(`state_dir_protected`)에는 "계약 파일 자체를 작성하는 경우"에 대한 예외가 없어, 워커가 자기 몫의 네임스페이스 계약을 셸 명령으로 쓰려는 시도 자체가 항상 막힌다 — 오늘 10:51 첫 차단의 직접 원인.
증거: `core/destructive_guard.py:504-508,634-636` `_is_state_dir_key` — 소유권 조회 이전에 무조건 하드 차단.
증거: 프로브 Group A — 유효 계약을 등록한 뒤 그 계약 파일 자체를 대상으로 한 리터럴 `Set-Content`가 `state_dir_protected`로 차단됨을 실행으로 재현.
증거: `material-erp/.fable-lite/scorecard/coordination.jsonl:3` — 오늘(2026-07-20T10:51:43Z) antigravity 세션 cb46812a의 r2_deny, reason_code=state_dir_protected.
증거: `material-erp/.fable-lite/contracts/` — antigravity 명의 네임스페이스 계약 파일이 하나도 존재하지 않음(claude_code 계약 4건만 존재) — 계약 작성 시도 자체가 이 차단으로 무산됐을 가능성과 정합.
신뢰도: 높음(채택).

가설 3(핵심): 파괴 명령 파서 `_detect_dynamic_command_head`가 명령(또는 `;` 구분 구간)의 첫 토큰이 `$`로 시작하면 — 흔한 PowerShell 변수 사용 관용구(`$content = ...; Set-Content ...`)를 포함 — 무조건 "파괴 명령 가능성"으로 fail-closed 차단하며, 그 내부 사유(`parse_unable_dynamic_command`/`parse_unable_dynamic_expression`)가 8개짜리 `R2_COORDINATION_REASON_MAP`에 없어 fallback 기본값인 `unresolvable_target`으로 라벨링된다. 대상이 `.fable-lite`인지, 계약이 있는지, 실제로 파괴적인지와 무관하게 발생.
증거: `core/destructive_guard.py:362-370` `_detect_dynamic_command_head` — `$` 포함 토큰이면 `CATEGORY_REMOVE, "parse_unable_dynamic_command"` 반환(명령의 실제 종류·대상과 무관).
증거: `core/destructive_guard.py:62-71,578-582` — `R2_COORDINATION_REASON_MAP`은 8개 키만 존재, `_coordination_reason_for_block`의 `.get(..., CoordinationReason.UNRESOLVABLE_TARGET)`이 미등록 사유 전부를 기본값으로 흡수.
증거: `tests/test_scorecard_coordination.py:222-232` — 매핑 8개 한정을 정확히 단정하는 테스트만 존재, `parse_unable_*` 계열의 의도적 라벨링을 검증하는 테스트는 없음.
증거: 프로브 Group B/C — `.fable-lite/contracts/<파일>`을 대상으로 한 `$content = ...; Set-Content ...`(단일행·heredoc 두 형태 모두) 실행 결과 `parsed.reason='parse_unable_dynamic_command'`, R2 `coordination_reason_code='unresolvable_target'`로 정확히 재현.
증거: 프로브 Group D — 동일한 `$`-변수 패턴을 `.fable-lite`와 무관한 평범한 `docs/reviews/report.md`에 적용해도 동일하게 `unresolvable_target` 차단(대상 경로 무관성 확인).
증거: 프로브 Group F(대조군) — 변수 없이 리터럴 값으로 동일 파일에 쓰면 `{'decision': 'allow', 'message': 'r2 pass'}`.
증거: `material-erp/.fable-lite/scorecard/coordination.jsonl:4` — 오늘(2026-07-20T12:27:38Z) 같은 세션의 두 번째 r2_deny, reason_code=unresolvable_target — 첫 차단(state_dir_protected, 10:51)과 21~22초 간격 패턴이 gates.jsonl의 `pretool.contract_missing`(10:51:22, 12:27:16) 뒤에 반복돼, 동일 워크플로 내 재시도로 명령 형태가 바뀌었음을 시사.
신뢰도: 높음(채택) — 오늘 관측된 정확한 사유 문자열·시퀀스를 코드 실행만으로 재현한 유일한 가설.

가설 4(경쟁): Windows 260자 장경로 제한으로 `Path.resolve()`가 실제 `OSError`를 던져 `canonicalization_unavailable`→`unresolvable_target`이 됐다(2026-07-19 Yeongjin 서버 장경로 조사, `daa47b5`와 같은 유형이 destructive_guard의 resolve() 호출에도 있는가).
증거: 프로브 Group H — 261자 상대경로(예상 절대경로 324자)로 `_canonicalize_target`을 직접 호출해도 `disposition='in_root'`로 정상 해석됨(이 머신 `LongPathsEnabled=1` 확인, 레지스트리값 조회로 검증).
증거: 실제 대상 후보 경로(네임스페이스 계약 파일명·일반 보고서 경로)는 material-erp 루트 기준 150자 미만으로, 260자 경계에 근접하지 않음.
기각: 가설 4 — 이 머신 조건에서 재현되지 않았고, 실제 대상 경로 길이도 임계값에 크게 못 미쳐 개연성이 낮다. (다른 물리 머신에서 실행됐을 가능성은 로그의 `host` 필드가 어댑터명일 뿐 물리 호스트명이 아니라 완전히 배제할 수는 없으나, 경로 길이 자체가 짧아 결정적 반증으로 충분하다.)

가설 5(경쟁): material-erp 저장소에 있는 불안정 경로(`customer-data/외상장부_개발자검토용_20260719/프로그램/외상장부.exe`, provenance observation에서 `unstable_path`로 반복 관측됨)가 canonicalization을 방해했다.
증거: `material-erp/.fable-lite/agents/antigravity.jsonl:1171,1182` — 두 r2_deny 시점의 observation 이벤트 모두 동일한 `외상장부.exe` unstable_path 샘플을 포함.
기각: 가설 5 — 이 observation은 R2의 `evaluate_r2_destructive_gate`/`_canonicalize_target`이 아니라 별도의 전역 provenance 스냅샷 관측 경로에서 나오며, R2는 명령 문자열에서 추출한 대상 경로만 정적으로 판정한다(`core/destructive_guard.py:624-633`). 두 이벤트에서 동일 파일이 반복 등장하는 것은 이 프로젝트의 상시 배경 상태이지 해당 셸 명령의 대상과의 인과관계가 아니다.

가설 6(경쟁): 실제로는 다른 에이전트가 대상 파일을 소유한 진짜 동시성 충돌(peer_unsettled)인데 사유가 잘못 기록됐다.
증거: `core/destructive_guard.py:62-71` — `peer_unsettled_revision`·`peer_open_invocation_candidate`는 `CoordinationReason.PEER_UNSETTLED`로 명시적으로 매핑되어 있어, 진짜 동시성 충돌이면 `unresolvable_target`이 아니라 `peer_unsettled`로 기록됐어야 한다.
기각: 가설 6 — 매핑 스키마상 동시성 충돌과 파싱/canonicalization 실패는 서로 다른 코드로 구분되어 기록되므로, 관측된 `unresolvable_target`이 동시성 충돌의 오기록일 가능성은 구조적으로 배제된다.

---
재현 스크립트(소스 미변경, tempfile 격리 root): `tmp/_r2_probe.py` / 실행 로그: `tmp/_r2_probe_output.txt`
