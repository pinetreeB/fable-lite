# fable-lite Antigravity (OmA) 어댑터 설치 가이드

이 문서는 fable-lite 코어를 Antigravity CLI(OmA) 환경에 통합하는 방법을 설명합니다.

## 주의: 메인 설정 훼손 방지

OmA의 메인 설정 파일인 `~/.gemini/config/plugins/oh-my-antigravity/hooks.json`을 직접 수정하는 것은 권장되지 않습니다. 대신 프로젝트 단위 로컬 상태(`.omg/state/hooks.json`)나 별도의 프로파일을 구성하여 주입하는 것을 권장합니다.

## 설치 방법

1. **절대 경로 치환**
   현재 디렉토리의 `hooks.json` 파일을 열고, `{FABLE_LITE_ROOT}` 부분을 실제 fable-lite 설치 절대 경로(예: `C:/Users/rotat/fable-lite`)로 모두 치환합니다.
   
2. **로컬 프로젝트에 적용**
   fable-lite를 적용하려는 프로젝트 경로로 이동하여, 훅 설정 파일을 병합합니다.
   
   ```bash
   mkdir -p .omg/state
   cp /path/to/fable-lite/adapters/antigravity/hooks.json .omg/state/hooks.json
   ```
   
   만약 이미 다른 로컬 훅이 있다면 JSON 병합 도구(jq 등)를 사용하여 병합하세요.

3. **작동 확인**
   로컬 디렉토리에서 Antigravity CLI를 구동하면 `oma_hook.py`가 자동으로 호출되어 페이로드를 Python 코어 판정 로직으로 전달합니다.
