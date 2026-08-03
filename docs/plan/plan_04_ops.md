# Browser Agent — 운영 (v0.1)

---

## 10. 프로그램 시작 시나리오

1. **설정 파일 로드**: config.ini 파일 읽기. 파일 없으면 기본값으로 생성.
2. **로깅 시스템 설정**: Python `logging` 모듈 초기화
   - config.ini의 `[logging]` 섹션에서 `level`과 `path` 값 읽기
   - `level`이 `disabled`면 → **파일 로깅 비활성화**, 콘솔 핸들러만 등록 (기본 WARNING 레벨, stderr 출력)
   - `level`이 유효한 로그 레벨(DEBUG/INFO/WARNING/ERROR/CRITICAL)이면 → 파일 핸들러 추가 (해당 레벨 이상 로그를 `path` 경로에 기록). 콘솔 핸들러도 함께 유지
   - `path`가 비어있으면 기본값 `./psjba.log` 사용
   - 포맷: `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
   - 로거 계층: `psjba.*` 네임스페이스 사용 (예: `psjba.config`, `psjba.agent`, `psjba.cron`, `psjba.ui`)
3. **API 키 복호화**: — config.ini의 `api_key`(암호문)를 복호화하여 메모리에 보관
   - `cryptography.fernet` + MAC 주소 기반 키로 복호화
   - 복호화 실패 (MAC 변경 등) → `api_key = ""` (빈 값, 설정 화면에서 재입력 유도)
   - `api_key` 자체가 없으면 → `api_key = ""` (빈 값)
4. **워크스페이스 폴더 확인**: workspace_dir 경로가 없으면 생성. screenshots/ 하위 폴더도 생성
5. **SQLite 연결**: DB 파일 열기. 없으면 새로 생성 + 테이블 생성. schema_version 비교. 불일치 시 오류 출력 후 종료
6. **대화 세션 복원**: psjba_meta에서 `current_session_id` 조회
   - 해당 세션이 존재하고 status = 'active'면 → 해당 세션을 현재 대화 세션으로 설정. 메인 윈도우에 기존 대화 로그 로드 (**최근 N개 메시지만 로드**, N = sliding_window_size × 2 또는 min(전체 메시지 수, 100). 메모리 이슈 방지를 위해 전체 메시지를 한 번에 로드하지 않음)
   - **무한 스크롤**: 사용자가 채팅 로그를 위로 스크롤하면 DB에서 이전 메시지를 추가 로드 (QListWidget 또는 QTextBrowser scroll event 감지, 한 번에 30개씩)
   - **메모리 캐시 정리**: 스크롤되어 화면 밖으로 나간 메시지 중 **3분 이상 경과**한 메시지는 UI 위젯에서 제거 (메모리에서만 제거, DB에는 보존). 사용자가 다시 위로 스크롤하면 DB에서 다시 로드
   - 없거나 status가 'archived'면 → 새 psjba_session 생성 (title: "새 대화", type: chat, status: active). `current_session_id`에 새 세션 ID 저장
7. **Chrome CDP 연결 확인 (5초 타임아웃)**: CDP 포트에 5초 타임아웃으로 연결 확인. 결과에 따라 UI 상태를 "Chrome 연결됨" 또는 "Chrome 연결하기"으로 설정. (자동 실행 안 함, 사용자가 버튼 클릭 시에만 Chrome 실행)
8. **크론 작업 복원**: psjba_cron_job에서 status='enable'인 작업을 읽어 aiocron 스케줄러에 등록
9. **언어 설정 로드**: config.ini에서 language 값 읽기 (기본값 "ko"). 이후 모든 에러 메시지 및 UI 출력에 적용
10. **필수값 확인 및 설정 화면 표시**: 메모리의 `api_key`와 `model` 값 확인
   - `api_key`가 비어있거나 `model`이 비어있으면 → 설정 창(⚙️ QDialog)을 자동으로 열어 사용자 입력 유도 (비모달, 설정 창을 닫아도 프로그램은 계속 실행)
   - 값이 있으면 → 정상 진행
   - **api_key/model이 없는 상태에서 전송 버튼은 비활성화**, hover 시 툴팁: "설정(⚙️)에서 API 키와 모델을 설정해주세요"
11. **UI 실행**: PySide6 메인 윈도우 실행. asyncio 이벤트 루프와 통합 (qasync)

---

## 10.1 프로그램 종료 시그널 핸들링

SIGINT(Ctrl+C) 및 SIGTERM 수신 시 안전하게 리소스를 정리하고 종료한다.

```python
import signal
import asyncio

def setup_signal_handlers(loop, db_conn, cron_manager, playwright_manager):
    """SIGINT/SIGTERM 시 안전 종료"""
    def shutdown(signum, frame):
        print(f"[SIGNAL] {signum} received, shutting down...")
        # 1. asyncio task 취소
        for task in asyncio.all_tasks(loop):
            task.cancel()
        # 2. 크론 스케줄러 중지 (aiocron crontab 모두 stop)
        if cron_manager:
            cron_manager.stop_all()
        # 3. PlaywrightManager 정리 (Chrome 브라우저는 유지, CDP 세션만 disconnect)
        if playwright_manager:
            asyncio.ensure_future(playwright_manager.disconnect(), loop=loop)
        # 4. DB close
        if db_conn:
            db_conn.close()
        # 5. 이벤트 루프 중지
        loop.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
```

**동작 순서:**
1. asyncio 모든 태스크 취소 (진행 중인 에이전트 실행, 크론 작업 등)
2. aiocron 스케줄러 중지 (모든 crontab stop)
3. PlaywrightManager disconnect (CDP WebSocket 연결 종료, **Chrome 프로세스는 유지**)
4. **SQLite DB 연결 종료** (DB close)
5. 이벤트 루프 중지

**시작 시점:** 11번(UI 실행) 직전 또는 직후에 `setup_signal_handlers()` 호출.

---

## 11. 프롬프트 처리 시나리오

1. 사용자가 프롬프트 입력 후 Enter 또는 전송 버튼 클릭
2. 전송 버튼 즉시 0.3초간 비활성화 + 색상 변화 피드백, 이후 "중지" 버튼으로 변경
3. 입력 검증 (빈 칸 체크)
4. SQLite에 사용자 메시지 저장 (current_session_id에 해당하는 세션 사용, session_id를 LangGraph State에 전달)
5. UI 상태 변경: Agent 실행중 → 입력 비활성화
6. LangGraph 에이전트 async 실행
7. 각 단계별 이벤트를 UI 로그에 실시간 표시:
   - call_model: LLM 응답 표시 + SQLite 저장 (메시지 개수 초과 시 슬라이딩 윈도우 적용)
   - execute_tool: 도구 호출 내용 및 결과 표시 + SQLite 저장
   - assess_error: 오류 시 self-healing 시도 표시
8. **중단 처리**: 사용자가 "중지" 버튼 클릭 시 asyncio task 취소, 현재까지의 컨텍스트를 정리하고 UI 대기 상태로 복원
9. 최종 응답 SQLite 저장
10. UI 상태 복원: Agent 대기 → 입력 활성화, "중지" 버튼 → "전송" 버튼 복원

---

## 11.1 세션 초기화 시나리오

1. 사용자가 메인 윈도우의 **세션 초기화 버튼** (입력창 우측 작은 아이콘, 🔄) 클릭
2. 확인 다이얼로그 표시: "현재 대화를 초기화하면 모든 메시지가 사라집니다. 계속하시겠습니까?"
3. [취소] → 아무 동작 없음
4. [확인] → 다음 순서로 처리:
   a. 현재 psjba_session의 status를 'archived'로 변경 (updated_at 갱신)
   b. 새 psjba_session 생성 (title: "새 대화", type: chat, status: active)
   c. psjba_meta의 `current_session_id`를 새 세션 ID로 업데이트
   d. 대화 로그 영역 클리어
   e. LangGraph 상태 초기화 (새 session_id로 시작)
   f. UI에 "세션이 초기화되었습니다" 메시지 표시 (설정된 언어로)
5. 이후 모든 프롬프트는 새 세션에 저장됨
6. (참고) 초기화된 이전 세션은 설정 창 → 로그 탭에서 조회 가능

### 원칙
프로그램이 읽고 쓸 수 있는 파일/디렉토리는 다음 세 가지로 제한한다.

1. **설정 파일**: config.ini (프로그램 루트)
2. **DB 파일**: config.ini의 sqlite.path 경로 (기본 ./browser_agent.data)
3. **워크스페이스 폴더**: config.ini의 workspace_dir 경로 (기본 ./psjba_temp)

### 워크스페이스 폴더 구조
```
psjba_temp/
├── screenshots/          # browser_screenshot 저장 위치 ({yyyyMMdd-HHmmss}.png)
├── downloads/            # 브라우저 다운로드 파일 (선택)
└── exports/              # 기타 에이전트가 생성한 파일
```

### 제한
- 모든 파일 생성/읽기/쓰기 작업은 위 세 경로로만 허용
- 시스템 경로, 다른 디렉토리의 파일에는 접근 불가
- 도구 실행 시에도 파일 경로 파라미터는 워크스페이스 폴더 내로 제한 검증

---

## 13. 크론 시스템

### 크론 작업 등록
- 설정 창 → CRON 탭에서 추가, 또는 PlaywrightManager의 cron_add/cron_edit/cron_toggle/cron_remove 메서드를 통해 LLM이 등록/수정/전환/삭제
- 작업 정보: 이름, 스케줄(크론 표현식, 5필드), 실행할 프롬프트
- UI에서는 시/분/요일 선택기로 표시, 사용자는 cron 표현식을 직접 알 필요 없음
- DB 저장은 항상 크론 표현식 문자열 (예: "*/5 * * * *")
- 등록 시 psjba_cron_job 테이블에 저장, aiocron crontab 생성 및 시작

### 크론 실행
- aiocron이 cron 표현식에 따라 asyncio 태스크로 작업 실행
- 실행 시: 새 psjba_session을 생성하여 psjba_session 테이블에 저장 (title: "[크론] {작업이름}", type: cron, **cron_job_id: 해당 크론의 UUID**), 해당 세션에 LangGraph 에이전트를 실행하여 프롬프트 처리
- 실행 결과는 psjba_message에 저장 (크론 전용 세션)
- last_run_at, next_run_at 업데이트

### 크론 비활성화/삭제 시 세션 정리
- 크론 작업을 **비활성화**할 때: 해당 cron job의 status='disable'로 변경. **연관된 세션은 그대로 유지** (비활성화는 일시 중지 개념이므로 복원 시 세션 이력 참조 가능)
- 크론 작업을 **삭제**할 때: 해당 cron job을 DB에서 실제 삭제하지 않고 **status='deleted'** 로 변경. **연관된 모든 세션의 status를 'archived'로 변경** (DB에서 삭제하지 않고 보존)
- 정리 기준: psjba_session에서 **cron_job_id = 해당 cron job의 UUID** 조건으로 조회 (문자열 패턴 매칭이 아닌 FK 기반 → 크론 이름 변경에도 안전)
- 크론이 활성화된 상태에서 수동 실행된 세션도 동일하게 처리됨
- 삭제 확인 다이얼로그에 "관련 세션 {N}개가 archived 처리됩니다" 안내 메시지 표시

### UI — 설정 창 → CRON 탭 (3단계 드릴다운)

설정 창(⚙️ 클릭)의 CRON 탭에서 크론 작업 관리. **3단계 드릴다운(QStackedWidget)** 구조.
UI 상세 구성은 `plan_02_config_ui.md`의 **CRON 탭 상세** 참조.

#### 전환 시나리오

1. Level 1 (크론 작업 목록): `📋 로그` 버튼 클릭 → Level 2
2. Level 2 (크론 실행 세션 목록): 세션 행 클릭 → Level 3
3. Level 3 (세션 상세 로그): `✕ 닫기` → Level 2, `◀ 크론 목록으로` → Level 1
4. 좌측 시간 목록 클릭 시 우측 패널만 갱신 (다른 세션 로드)

#### 주기 저장 시 동작

- 주기 편집 다이얼로그에서 저장 시: DB `schedule` 업데이트 + aiocron crontab 재생성 (기존 stop → 새 crontab start)

---

## 14. 엣지 케이스 처리

| 상황 | 처리 |
|------|------|
| Chrome이 실행 중인데 다른 포트 | config.ini 포트 변경 안내 |
| Chrome 실행 중 갑자기 종료됨 | CDP WebSocket `disconnect` 이벤트를 즉시 감지 → 사용자 알림, 연결 상태 "미연결"로 변경 (PlaywrightManager 호출 실패 대기보다 능동적/즉각적 감지) |
| CDP 포트 충돌 | 포트 변경 후 재시도 |
| LLM API 키 오류 | UI에 에러 메시지 표시 (설정된 언어로), 설정 파일 재확인 안내 |
| 네트워크 단절 (LLM 호출 실패) | 1회 재시도, 실패 시 사용자에게 알림 (설정된 언어로) |
| 페이지 로딩 타임아웃 | wait 시간 증가, self-healing |
| 접근성 트리 없음 (iframe, canvas) | JS evaluate로 우회 |
| 긴 실행 시간 (30초+) | asyncio 타임아웃 설정, UI에 진행 상태 표시 |
| 사용자가 중간에 취소 | LangGraph asyncio task cancel |
| 모델이 존재하지 않는 도구 호출 | LangGraph tool binding으로 자연 차단 |
| 연속 3회 오류 | self-healing 포기, 사용자에게 오류 상황 보고 |
| 파일 경로가 워크스페이스 외부로 접근 | 보안 정책에 따라 차단 및 오류 반환 |
| 크론 작업 실행 중 오류 | 로그 기록, 에이전트가 사용자에게 보고 |
| 크론 작업 동시 실행 충돌 | **동일 cron_job_id의 실행 중인 태스크가 있으면 새 실행을 스킵하고 로그만 남김.** 각 크론별 최대 실행 시간은 `cron_timeout`(config.ini, 기본 300초) 적용. 타임아웃 초과 시 강제 종료. |

---

## 15. 프로젝트 디렉토리 구조

```
browser_agent/
├── main.py                     # 진입점
├── config.py                   # 설정 로드/저장 + API 키 암호화
├── database.py                 # SQLite CRUD + FTS5
├── config.ini                  # 설정 파일
├── requirements.txt
├── README.md
│
├── ui/                         # PySide6 UI
│   ├── __init__.py
│   ├── main_window.py         # 메인 윈도우 (설정 버튼 + Chrome 버튼 + 프롬프트 입력 + 대화 로그)
│   ├── settings_dialog.py     # 설정 창 QDialog (설정/CRON/로그 탭)
│   ├── chat_widget.py         # 대화 로그 위젯
│   └── prompt_input.py        # 프롬프트 입력 위젯
│
├── agent/                      # LangGraph 에이전트
│   ├── __init__.py
│   ├── graph.py               # 그래프 정의
│   ├── state.py               # State 타입
│   ├── nodes.py               # 노드 함수들
│   ├── prompts.py             # 시스템 프롬프트
│   ├── tools.py               # 모든 LangGraph 도구 정의 (memory_search 등)
│   └── llm_client.py          # LLM API 클라이언트 (ChatOpenAI)
│
├── browser/                    # PlaywrightManager (MCP 대체)
│   ├── __init__.py
│   ├── manager.py             # PlaywrightManager 서비스 클래스
│   ├── self_healing.py        # Self-healing selector
│   └── accessibility.py       # 접근성 트리 포맷팅
│
├── chrome/                     # Chrome 프로세스 관리
│   ├── __init__.py
│   └── launcher.py            # Chrome 실행/연결/상태 확인
│
├── cron/                       # 크론 스케줄러
│   ├── __init__.py
│   ├── scheduler.py           # aiocron crontab 관리 (등록/해제/목록)
│   └── executor.py            # 크론 작업 실행기
│
├── security/                   # 보안
│   ├── __init__.py
│   └── sandbox.py             # 파일 접근 검증
│
└── models/
    ├── __init__.py
    └── schemas.py              # 데이터 모델 클래스
```

---

## 16. 의존성 패키지

- langgraph: LangGraph 에이전트 프레임워크
- langchain-core, langchain-openai: LLM 인터페이스
- playwright: 브라우저 자동화
- PySide6: UI 프레임워크
- qasync: PySide6 + asyncio 통합
- aiohttp: 비동기 HTTP (CDP 상태 확인용)
- aiocron: asyncio 기반 크론 스케줄러
- cryptography: API 키 암호화 (fernet)

---

## 17. 향후 확장 포인트

- 멀티 세션: 여러 탭/페이지 동시 제어
- 레코딩 모드: 사용자 조작을 기록하고 재생
- 비주얼 모드: 접근성 트리 대신 스크린샷 + 비전 LLM으로 제어
- 헤드리스 모드: 백그라운드에서 Chrome 실행
- 프록시 지원: 설정에 proxy 항목 추가
- 히스토리 검색: SQLite 기반 대화 검색 UI
- 도구 사용량 통계: 대시보드 형태로 시각화
- 크론 알림: 크론 작업 완료 시 시스템 알림