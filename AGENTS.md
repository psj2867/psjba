# AGENTS.md — PSJBA (Browser Agent)

> 이 파일이 있는 디렉토리(`psjba/`)가 **워크스페이스 루트**입니다.
> 모든 코드 작업은 이 디렉토리를 기준으로 작성하세요.

---

## 1. 프로젝트 개요

Windows Chrome 브라우저를 CDP로 자동 제어하는 데스크톱 앱.
사용자가 자연어 프롬프트를 입력하면 LangGraph 에이전트가 Playwright로 브라우저를 조작한다.

## 2. 기획 문서

모든 기획/요구사항은 아래 경로에 있습니다.  
**코딩 전에 반드시 먼저 확인하세요.**

```
docs/plan/
├── plan_01_core.md        # 시스템 아키텍처, 개요
├── plan_02_config_ui.md   # 설정, DB 스키마, UI 구조 (아주 상세함)
├── plan_03_agent.md       # LangGraph 에이전트, PlaywrightManager
├── plan_04_ops.md         # 시작 시나리오, 크론, 엣지 케이스, 프로젝트 구조
└── plan_05_review.md      # 적용된 결정 사항 히스토리
```

> ⚠️ **plan_02_config_ui.md**가 가장 방대하고 상세합니다.  
> UI 구현 시 반드시 이 문서의 UI 구조/레이아웃/동작을 그대로 따라야 합니다.

## 3. 기술 스택

| 계층 | 기술 | 비고 |
|------|------|------|
| 언어 | Python 3.11+ | |
| UI | PySide6 + qasync | asyncio 통합 |
| 에이전트 | LangGraph | 상태 그래프 |
| LLM | langchain-openai (ChatOpenAI) | OpenRouter 기본, OpenAI 호환 |
| 브라우저 | Playwright (Python) | CDP 연결 |
| DB | SQLite (내장) | schema version 관리 |
| 크론 | aiocron | asyncio 기반 |
| 암호화 | cryptography.fernet | MAC 주소 기반 키 |

## 4. 폴더 구조 (목표)

```
psjba/                          # ← 워크스페이스 루트
├── AGENTS.md                   # (이 파일)
├── README.md
├── main.py                     # 진입점
├── config.py                   # 설정 로드/저장 + API 키 암호화
├── database.py                 # SQLite CRUD + FTS5
├── config.ini                  # 설정 파일
├── requirements.txt
│
├── ui/
│   ├── __init__.py
│   ├── main_window.py
│   ├── settings_dialog.py
│   ├── chat_widget.py
│   └── prompt_input.py
│
├── agent/
│   ├── __init__.py
│   ├── graph.py
│   ├── state.py
│   ├── nodes.py
│   ├── prompts.py
│   ├── tools.py
│   └── llm_client.py
│
├── browser/
│   ├── __init__.py
│   ├── manager.py
│   ├── self_healing.py
│   └── accessibility.py
│
├── chrome/
│   ├── __init__.py
│   └── launcher.py
│
├── cron/
│   ├── __init__.py
│   ├── scheduler.py
│   └── executor.py
│
├── security/
│   ├── __init__.py
│   └── sandbox.py
│
├── models/
│   ├── __init__.py
│   └── schemas.py
│
├── docs/
│   └── plan/                   # ← 기획 문서 (코딩 전 확인 필수)
│
└── psjba_temp/                 # 런타임 파일 (screenshots, downloads, exports)
```

## 5. 코딩 규칙

### 일반
- 변수명: `snake_case`
- 클래스명: `PascalCase`
- 상수: `UPPER_SNAKE_CASE`
- 모든 public 함수에 docstring 작성
- 타입 힌트 필수 (Python 3.11+)

### async
- UI(qasync) → LangGraph(async) → PlaywrightManager(async) — 모든 계층 asyncio
- `asyncio.run()` 대신 qasync의 이벤트 루프 사용

### import 규칙
- PlaywrightManager는 서비스 클래스. LangGraph 노드에서 직접 `import`하여 호출
- 별도 MCP 서버 레이어 없음

### 에러 처리
- CSS 선택자 실패 시 self-healing (XPath → 텍스트 → aria-label → role 순)
- 연속 3회 오류 시 self-healing 포기, 사용자 보고 후 종료
- **`except Exception:`만 잡아서 `pass` 금지.** 반드시 `logger.debug()`(예상된 fallback) 또는
  `logger.warning()`(예상치 못한 오류)로 예외를 로그에 남길 것.
  fallback 체인의 일부로 예외 발생이 정상 흐름인 경우만 예외 허용 (예: `DOM.getOuterHTML` 같은
  best-effort enrichment). 그 외에는 `pass` 대신 최소 `logger.debug(...)`를 남겨야 한다.

### 보안
- 파일 접근은 다음 세 경로로만 제한: `config.ini`, `SQLite DB`, `workspace_dir`
- 시스템 경로 접근 차단 (sandbox.py)

## 6. 책임 분리 규칙

각 모듈은 자기 책임에 집중하고, 다른 모듈의 책임을 대신하지 않는다.

- `browser/accessibility.py`: 접근성 트리 구축, DOM enrichment, ref 맵 생성, selector 빌드용
  속성(`_nth_of_type`, `_class`, `_id`, `_aria_label` 등) 수집.
- `browser/self_healing.py`: CSS/XPath self-healing, selector 중복 해소 로직.
- `browser/manager.py` (`browser_snapshot`): 접근성 트리 텍스트 요청 + ref 저장 + 텍스트 반환.
  **절대 `page.evaluate()`로 CSS selector 검증/중복 해소를 하지 않는다.**
  (selector 검증은 `browser_click` 등 실제 사용 시점에 CDP로 한다.)
- `browser/manager.py` (`browser_click` / `browser_hover`): 실제 요소 조작.
  selector는 `_build_click_selector(node)`로 필요 시점에 생성하고,
  중복 해소는 `SelfHealingSelector.make_unique_selector`에 위임.
- `browser/manager.py` (`_build_click_selector`): static method. refs dict에 저장된
  속성만으로 CSS selector 문자열을 생성. `page`나 `cdp`에 접근하지 않는다.

## 7. 작업 순서

기획 문서를 읽고 다음 순서로 개발하세요:

1. **1차**: `config.py`, `database.py`, `models/schemas.py` — 코어 데이터 레이어
2. **2차**: `browser/manager.py`, `browser/accessibility.py`, `browser/self_healing.py`, `chrome/launcher.py` — 브라우저/Chrome 레이어
3. **3차**: `agent/` 전반 — LangGraph 에이전트
4. **4차**: `ui/` 전반 — PySide6 UI
5. **5차**: `cron/`, `security/sandbox.py` — 크론 + 보안
6. **6차**: `main.py` — 진입점, 시작 시나리오 조립

## 7. 환경 정보

### 대상 환경 (배포)
- **OS**: Windows
- **Chrome**: CDP (`--remote-debugging-port`)로 연결
- **UI**: PySide6 정상 렌더링 (GUI 창 있음)

### 개발/테스트 환경 (현재)
- **OS**: Linux
- **디스플레이**: 헤드리스 (GUI 창 없음)
- **PySide6 테스트**: 아래 방법으로 헤드리스 실행 필요

```bash
# 방법 1: xvfb (가상 프레임버퍼) 사용
apt install xvfb
xvfb-run python main.py

# 방법 2: QT_QPA_PLATFORM=offscreen
QT_QPA_PLATFORM=offscreen python main.py

# 방법 3: pytest fixture에서 offscreen 설정
# conftest.py 예시:
#   import os
#   os.environ["QT_QPA_PLATFORM"] = "offscreen"
```

> ⚠️ PySide6 위젯 단위 테스트는 `QT_QPA_PLATFORM=offscreen`으로 가능하지만,
> 실제 화면 렌더링이 필요한 UI 통합 테스트는 xvfb가 필요합니다.
>
> ⚠️ Chrome 브라우저 실행/연결 테스트는 이 Linux 환경에서도 가능합니다.
> (Playwright는 Linux에서도 Chrome을 헤드리스로 실행 가능)

### 폴리필 / 크로스 플랫폼 참고
- `os.name` / `platform.system()`으로 Windows/Linux 분기 필요 시 구분
- Chrome 경로(`chrome.path`)는 Windows 기준 (`C:\Program Files\...`)으로 config.ini 기본값 설정
- Linux 테스트 시에는 chrome.path를 로컬 Chrome/Chromium 경로로 변경 필요

## 8. 주의사항

- ✅ 기획 문서(`docs/plan/`)를 먼저 읽고 시작할 것
- ✅ 기획에 없는 기능은 추가하지 말 것
- ✅ 기획과 상충되는 구현은 하지 말 것
- ❌ Chrome 프로세스는 종료하지 않음 (종료 시에도 유지)
- ❌ 파일 접근 샌드박스 위반 금지
- ❌ config.ini에 api_key 평문 저장 금지 (암호화 필수)

## 9. 아키텍처 결정 사항

### 9.1 `_a11y_refs` session 격리 (contextvars)

`PlaywrightManager._a11y_refs`는 브라우저 접근성 트리에서 상호작용 가능한 요소의 ref 번호 → 노드 정보 매핑을 저장.

크론 작업과 메인 에이전트가 동일한 `PlaywrightManager` 인스턴스를 공유하므로, `_a11y_refs`가 덮어씌워지면 ref 조회(`browser_snapshot(ref=N)` / `browser_click_by_ref`)가 엉뚱한 요소를 찾음.

**해결**: `_a11y_refs`를 `dict[str, dict[int, dict[str, Any]]]`로 변경, 키를 `session_id`로 함. 각 asyncio task의 session_id는 `contextvars.ContextVar`로 격리.

**설정 시점**:
- `agent/nodes.py` `execute_tool()`: `manager.set_session_id(state["session_id"])` — 각 tool 호출 직전
- `cron/executor.py` `_run()`: `set_session_id(session.id)` — graph 실행 직전

**읽기/쓰기**: `_get_session_refs()` / `_set_session_refs()` 헬퍼가 현재 task의 session_id로 자동 라우팅.

**파일**:
- `browser/manager.py`: `set_session_id()` 함수, `_current_session_id` ContextVar, `_get_session_refs()`, `_set_session_refs()`
- `agent/nodes.py`: `execute_tool()`에서 session 설정
- `cron/executor.py`: `_run()`에서 session 설정
