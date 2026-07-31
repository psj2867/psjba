# PSJBA 프로젝트 구조 요약

## 개요
- **프로젝트명**: PSJBA (Browser Agent)
- **설명**: Windows Chrome 브라우저를 CDP로 자동 제어하는 데스크톱 앱
- **동작 방식**: 사용자가 자연어 프롬프트 입력 → LangGraph 에이전트 → Playwright로 브라우저 조작
- **Python 3.11+**, **PySide6 + qasync** (asyncio 통합 GUI)

---

## 폴더 구조

```
psjba/
├── AGENTS.md              # 에이전트 작업 가이드 (프로젝트 루트)
├── README.md              # README
├── main.py                # 진입점 (11단계 시작 시나리오)
├── __main__.py            # zipapp 진입점
├── pyproject.toml         # 프로젝트 메타
├── requirements.txt       # 의존성 목록
│
├── config/                # 설정 파일 디렉토리 (비어있음)
├── docs/plan/             # 기획 문서
│   ├── plan_01_core.md    # 시스템 아키텍처, 개요
│   ├── plan_02_config_ui.md  # 설정, DB 스키마, UI 구조 (가장 방대)
│   ├── plan_03_agent.md   # LangGraph 에이전트, PlaywrightManager
│   ├── plan_04_ops.md     # 시작 시나리오, 크론, 엣지 케이스
│   └── plan_05_review.md  # 적용된 결정 사항 히스토리
│
├── src/                   # 소스 코드
│   ├── config.py          # 설정 로드/저장 + API 키 암호화 (Fernet, MAC 주소 기반)
│   ├── database.py        # SQLite CRUD + FTS5 (aiosqlite)
│   ├── agent/             # LangGraph 에이전트
│   │   ├── graph.py       # 에이전트 그래프
│   │   ├── state.py       # 상태 정의
│   │   ├── nodes.py       # 그래프 노드
│   │   ├── prompts.py     # 프롬프트 템플릿
│   │   ├── tools.py       # 에이전트 도구
│   │   └── llm_client.py  # LLM 클라이언트
│   ├── browser/           # 브라우저 제어
│   │   ├── manager.py     # PlaywrightManager (핵심, 633줄)
│   │   ├── accessibility.py # 접근성 트리 추출
│   │   └── self_healing.py  # CSS 선택자 셀프힐링
│   ├── chrome/            # Chrome 런처
│   │   └── launcher.py    # Chrome 프로세스 실행/CDP 연결
│   ├── cron/              # 크론 작업
│   │   ├── scheduler.py   # 스케줄러
│   │   └── executor.py    # 실행기
│   ├── models/            # 데이터 모델
│   │   └── schemas.py     # Pydantic 스키마 (Session, Message, CronJob, Meta)
│   ├── security/          # 보안
│   │   └── sandbox.py     # 파일 접근 샌드박스
│   └── ui/                # UI
│       ├── main_window.py # 메인 윈도우
│       ├── chat_widget.py # 채팅 위젯
│       ├── prompt_input.py# 프롬프트 입력
│       └── settings_dialog.py # 설정 다이얼로그 (38KB, 가장 큼)
│
├── tests/                 # 테스트 코드 (15개 파일)
│   ├── conftest.py
│   ├── test_accessibility.py
│   ├── test_agent_graph.py
│   ├── test_agent_llm_client.py
│   ├── test_agent_nodes.py
│   ├── test_agent_prompts.py
│   ├── test_agent_state.py
│   ├── test_agent_tools.py
│   ├── test_chrome_launcher.py
│   ├── test_config.py
│   ├── test_database.py
│   ├── test_sandbox.py
│   ├── test_schemas.py
│   └── test_self_healing.py
│
├── images/                # 이미지 (비어있음)
│
└── psjba_temp/            # 런타임 파일 (screenshots, downloads, exports)
```

---

## 기술 스택

| 계층 | 기술 | 비고 |
|------|------|------|
| 언어 | Python 3.11+ | |
| UI | PySide6 + qasync | asyncio 통합 |
| 에이전트 | LangGraph | 상태 그래프 |
| LLM | langchain-openai (ChatOpenAI) | OpenRouter 기본, OpenAI 호환 |
| 브라우저 | Playwright (Python) | CDP 연결 |
| DB | SQLite (aiosqlite) | schema version 관리, FTS5 |
| 크론 | aiocron | asyncio 기반 |
| 암호화 | cryptography.fernet | MAC 주소 기반 키 |

---

## 핵심 구조

### 1. main.py — 11단계 시작 시나리오
1. Config 로드
2. 로깅 설정
3. 워크스페이스 생성
4. DB 연결/초기화
5. PlaywrightManager 생성
6. CronExecutor/CronScheduler
7. MainWindow
8. 시그널 연결 (prompt_submitted, session_reset_requested)
9. 세션 복원
10. 필수값 확인 (api_key, model)
11. qasync 이벤트 루프 실행

### 2. PlaywrightManager (src/browser/manager.py)
- Chrome CDP 연결/해제
- 탭 관리 (열기, 닫기, 전환, 목록)
- 네비게이션 (이동, 뒤로가기, URL/제목 조회)
- 요소 조작 (클릭, 입력, 선택, 호버, 스크롤, 포커스, 키입력)
- 정보 조회 (접근성 트리, DOM, 스크린샷)
- JavaScript 실행
- 크론/메모리 기능 (스텁)

### 3. Config (src/config.py)
- configparser 기반 설정 관리
- API 키 Fernet 암호화 (MAC 주소 기반 키 유도)
- 7개 섹션: llm, cdp, chrome, sqlite, agent, workspace, ui, logging
- DEFAULTS 딕셔너리로 기본값 관리
- TYPE_MAP으로 타입 캐스팅 (str, int, float, bool)

### 4. Database (src/database.py)
- aiosqlite 사용
- 4개 테이블: psjba_meta, psjba_session, psjba_message, psjba_cron_job
- FTS5 가상 테이블 (psjba_message_fts) + 트리거
- 세션 관리 (생성, 보관, 목록, 복원)
- 메시지 CRUD + FTS5 검색
- 크론 작업 CRUD + 로그 조회

---

## 실행 관련 참고사항

### 대상 환경
- **OS**: Windows (배포), Linux (개발/테스트)
- **Chrome**: CDP (`--remote-debugging-port`)로 연결
- **UI**: PySide6 정상 렌더링 (GUI 창 있음)

### Linux 헤드리스 실행 방법
```bash
# xvfb 사용
xvfb-run python main.py

# 또는 offscreen 모드
QT_QPA_PLATFORM=offscreen python main.py
```

### 의존성 (requirements.txt)
```
PySide6>=6.5, langchain-core>=0.3, langchain-openai>=0.3, langgraph>=0.3,
playwright>=1.40, cryptography>=41.0, aiohttp>=3.9, aiosqlite>=0.19,
aiocron>=1.5, qasync>=0.27, typing_extensions>=4.8
```

---

## 주의사항
- ✅ 기획 문서(`docs/plan/`)를 먼저 읽고 시작할 것
- ❌ Chrome 프로세스는 종료하지 않음 (종료 시에도 유지)
- ❌ 파일 접근 샌드박스 위반 금지 (config.ini, SQLite DB, workspace_dir만 접근)
- ❌ config.ini에 api_key 평문 저장 금지 (암호화 필수)
