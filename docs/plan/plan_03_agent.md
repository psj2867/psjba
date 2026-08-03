# Browser Agent — 에이전트 (v0.1)

---

## 6. LangGraph 에이전트

### State (상태)
에이전트가 그래프를 순회하며 유지하는 상태 정보:
- messages: LangChain BaseMessage 시퀀스 (대화 내역, add_messages 리듀서 사용)
- session_id: 현재 SQLite 세션 ID
- url: 현재 브라우저 URL
- page_title: 현재 페이지 제목
- accessibility_tree: 최신 접근성 트리 텍스트 (LLM 컨텍스트용)
- last_screenshot_path: 최근 스크린샷 파일 경로 (사용자 요청 시, 선택적)
- error_count: 연속 오류 횟수 (self-healing 판단 기준)
- is_final: 종료 여부 플래그
- tool_call_count: 현재 프롬프트에서 실행된 누적 tool call 횟수 (max_tool_calls 초과 시 강제 종료)

### 그래프 구조 (3개 노드 + 1개 조건부 엣지)

**1. call_model 노드**
- 입력받은 메시지 + 시스템 프롬프트 + 접근성 트리 → LLM 호출
- LLM은 도구 바인딩되어 있어 응답에 tool_calls 포함 가능
- 시스템 프롬프트에 접근성 트리를 동적으로 주입
- messages 개수가 sliding_window_size 초과 시, 오래된 메시지를 제거 (슬라이딩 윈도우)
- **tool_call_count >= max_tool_calls 이면 LLM 호출 없이 즉시 is_final=True 반환** (무한루프 방지)

**2. execute_tool 노드**
- LLM이 요청한 도구 호출을 받아 PlaywrightManager의 해당 메서드로 직접 실행
- PlaywrightManager는 LangGraph 노드에서 직접 import하여 호출
- 도구 실행 결과를 메시지로 반환 + SQLite에 tool_call 로깅
- 실행 결과가 오류면 error_count 증가

**3. assess_error (조건부 라우팅)**
- 도구 실행 결과에 오류가 있는지 판단
- 오류 없음 → call_model로 돌아가서 다음 액션 결정
- 오류 있음 + error_count < 3 → self-healing 로직으로 재시도 (call_model로 복귀)
- 오류 있음 + error_count >= 3 → self-healing 포기, 사용자에게 오류 보고 후 종료

### 루프 흐름
call_model → (도구 있음) → execute_tool → assess_error → call_model (반복)
call_model → (도구 없음, 최종 응답) → END

### 메시지 슬라이딩 윈도우

- **파라미터**: `max_context_messages` (기본 30, config.ini의 `sliding_window_size`) / `min_keep_messages` (기본 10)
- **동작**: system 프롬프트를 제외한 **user 메시지** 개수가 `max_context_messages` 초과 시, 가장 오래된 user 메시지부터 제거. 단, 최근 `min_keep_messages`개의 user 메시지는 절대 제거되지 않음. assistant 메시지는 user 메시지에 종속되어 함께 제거됨.
- 즉, 컨텍스트 내 user 메시지 수는 항상 `min_keep_messages` ~ `max_context_messages` 사이에서 유지됨.
- **트리거**: call_model 노드 진입 시점에 messages 길이 체크
- **DB 처리**: 제거된 메시지들은 psjba_message 테이블에 그대로 보존됨 (UI 로그는 DB에서 조회하여 표시)

### 세션 내 메모리 검색 도구

에이전트가 사용할 수 있는 도구로 `memory_search`를 추가한다.

- **도구명**: `memory_search`
- **설명**: 같은 세션 내 이전 대화 내역을 키워드로 검색 (슬라이딩 윈도우에서 제거된 메시지도 DB에서 조회 가능)
- **파라미터**: query (str, 필수) — 검색할 키워드
- **동작**: 현재 session_id 기준으로 psjba_message 테이블에서 전문 검색
- **반환값**: 일치하는 메시지 목록 (role, content 일부, created_at)
- **사용처**: 에이전트가 이전 대화 내용이 필요할 때 자발적으로 호출
- **인덱스 동기화**: 새 메시지 저장/삭제 시 검색 인덱스도 함께 갱신

### LLM 클라이언트 (llm_client.py)

- **기본값**: OpenRouter API (openai 호환)
- **설정**: config.ini의 `provider`, `url`, `model`, `api_key`(복호화된 값) 사용
- **구현**: `langchain-openai`의 `ChatOpenAI` 사용 (OpenAI 호환 API이므로 OpenRouter, Together, Groq 등 모두 동일 인터페이스)
- **파라미터**: config.ini의 model, base_url, temperature, max_tokens, timeout, api_key(복호화) 사용
- **의존성**: `langchain-openai`만 필요 (Anthropic 등 타사는 openai 호환 API로 통일)
- **재시도**: 3회 자동 재시도

---

## 7. 시스템 프롬프트

### 구성 요소
1. **에이전트 역할 정의**: 브라우저 자동화 에이전트라는 설명
2. **현재 페이지 접근성 트리**: 동적으로 삽입 (실행 시점의 페이지 구조)
3. **사용 가능한 도구 목록**: 각 도구의 이름, 설명, 파라미터
4. **규칙**:
   - 먼저 browser_snapshot으로 페이지 파악
   - CSS 선택자 우선, XPath fallback
   - 선택자 실패 시 대체 전략 시도 (self-healing)
   - 각 액션 후 snapshot으로 결과 확인
   - 오류 발생 시 최대 3회 다른 방식으로 재시도
   - 중요한 액션 후 사용자에게 진행 상황 보고
   - browser_screenshot은 사용자가 스크린샷을 요청한 경우에만 사용할 것

### 접근성 트리 포맷
Playwright의 accessibility snapshot을 가져와서 계층적 텍스트로 변환. 각 노드의 role, name, value, focused, disabled, checked 상태를 포함. 불필요한 노드(빈 StaticText 등)는 제거.

**크기 제한 정책:**
- 접근성 트리 생성 시 **4000 토큰** 단일 제한 적용
- `accessibility.py`의 트리 생성 함수에서 토큰 수를 추정하며 4000 토큰 초과 시 불필요한 노드(빈 텍스트, 숨겨진 요소 등)를 우선 제거
- 최소한의 의미 있는 노드만 포함하도록 트리 생성 단계에서 필터링

---

## 8. PlaywrightManager 서비스 클래스

### 구현 방식
별도의 MCP 서버 레이어 없이, `PlaywrightManager`라는 서비스 클래스를 두고 LangGraph 노드가 직접 import하여 Playwright API를 호출한다. Python Playwright 라이브러리를 직접 래핑하며, in-process에서 동작한다.

### PlaywrightManager 생명주기
1. **초기화**: PlaywrightManager 인스턴스 생성, config.ini의 CDP/Chrome 설정 로드
2. **connect()**: async_playwright 시작 → Chrome CDP에 connect_over_cdp로 연결 → 기본 페이지 또는 새 페이지 획득
3. **도구 실행**: LangGraph 노드가 PlaywrightManager.메서드() 형태로 직접 호출
4. **disconnect()**: Playwright 세션만 종료 (Chrome 브라우저는 유지)

### PlaywrightManager 주요 메서드

PlaywrightManager는 브라우저 제어, 크론 작업, 메모리 검색 등 모든 서비스 메서드를 제공한다.

#### 브라우저 도구 (21개)

| 도구명 | 설명 | 비고 |
|--------|------|------|
| chrome_health | Chrome/CDP 연결 상태 및 브라우저 종합 정보 반환 | 연결 여부, 탭 수, 포커스 탭 URL/제목, CDP 호스트/포트, 헤드리스 여부, Chrome PID 등. 연결 안 됨 상태에서도 안전 호출 가능 |
| chrome_start | Chrome CDP 연결 시도 (실패 시 Chrome 실행 후 연결) | 연결 성공 시 chrome_health 정보 포함 반환 |
| browser_navigate | URL로 이동 | |
| browser_new_tab | 새 탭 열기 (지정 URL 또는 빈 페이지) | |
| browser_switch_tab | 탭 전환 (탭 인덱스 또는 URL 기준) | |
| browser_list_tabs | 현재 열린 탭 목록 반환 (인덱스, URL, 제목) | |
| browser_get_focused_tab | 현재 focused 탭 정보 반환 (인덱스, URL, 제목) | |
| browser_close_tab | 현재 탭 닫기 | |
| browser_click | CSS/XPath 선택자로 요소 클릭 | |
| browser_type | 선택자로 찾은 입력 필드에 텍스트 입력 | |
| browser_select_option | 드롭다운에서 옵션 선택 | |
| browser_hover | 요소 위에 마우스 호버 | |
| browser_snapshot | 현재 페이지 접근성 트리 반환 | |
| browser_screenshot | 스크린샷을 워크스페이스 폴더에 PNG 파일로 저장 | 사용자 요청 시에만. 저장 경로 반환. 파일명: {yyyyMMdd-HHmmss}.png |
| browser_evaluate | JavaScript 실행 | |
| browser_get_url | 현재 URL 반환 | |
| browser_get_title | 페이지 제목 반환 | |
| browser_wait | 지정 시간 대기 | |
| browser_press_key | 키보드 키 입력 (Enter, Tab, Escape 등) | |
| browser_scroll | 위/아래/맨 위/맨 아래 스크롤 | |
| browser_go_back | 브라우저 뒤로 가기 | |

#### 크론 도구 (5개)

| 도구명 | 설명 | 비고 |
|--------|------|------|
| cron_add | 새 크론 작업 등록 | 이름, 스케줄(크론 표현식), 프롬프트 |
| cron_list | 등록된 크론 작업 목록 조회 | |
| cron_edit | 크론 작업 수정 (이름/스케줄/프롬프트) | 작업 ID, (변경할 필드) |
| cron_toggle | 크론 작업 활성/비활성 전환 | 작업 ID |
| cron_remove | 크론 작업 삭제 | 작업 ID |

#### 메모리 검색 도구 (1개)

| 도구명 | 설명 | 비고 |
|--------|------|------|
| memory_search | 동일 세션 내 과거 대화를 키워드로 검색 | 슬라이딩 윈도우로 제거된 메시지도 DB에서 조회 가능 |

### chrome_health 상세
- **호출 조건**: AI가 Chrome 연결 상태를 확인하거나 브라우저 종합 정보가 필요할 때 자발적으로 호출
- **동작**: 별도 CDP/Playwright 호출 없이 PlaywrightManager 내부 상태만으로 정보 구성 (연결 안 됨 상태에서도 호출 가능)
- **반환 항목**:
  - `connected`: 연결 여부 (true/false)
  - `cdp_host`, `cdp_port`: CDP 연결 정보
  - `headless`: 헤드리스 모드 여부
  - `tab_count`: 열린 탭 수 (연결 시)
  - `focused_tab`: 현재 포커스된 탭의 index, URL, title (연결 시)
  - `tabs`: 전체 탭 목록 (연결 시, index/URL/title)
  - `chrome_pid`: Chrome 프로세스 PID (본 프로그램이 실행한 경우)
- **연결 안 됨 상태에서도 항상 안전하게 호출 가능** (에러 없이 상태 정보만 반환)

### chrome_start 상세
- **호출 조건**: AI가 브라우저 조작이 필요하지만 chrome_health()에서 연결 안 됨을 확인한 경우
- **동작**: `connect(launch_if_needed=True)` 호출 → CDP 확인 후 Chrome 실행 → 연결
- **반환값**: 연결 성공 시 chrome_health 정보 포함, 실패 시 오류 메시지

### browser_screenshot 상세
- **호출 조건**: 사용자가 스크린샷을 요청한 경우에만 LLM이 호출
- **동작**: Playwright screenshot() 실행 → 워크스페이스 폴더 내 screenshots/ 하위에 `{timestamp}.png` 파일로 저장
- **파일명**: `{yyyyMMdd-HHmmss}.png` (예: `20260720-095738.png`, `20260720-100123.png`)
- **반환값**: 파일 경로 (절대 경로)
- **워크스페이스 폴더**: config.ini의 workspace_dir 값 사용 (기본 ./psjba_temp)

### 브라우저 탭 관리
PlaywrightManager는 여러 탭을 관리할 수 있다.

- `browser_new_tab(url?)`: 새 브라우저 탭을 열고 해당 탭으로 전환. URL이 주어지면 해당 페이지로 이동, 없으면 about:blank. 새 탭의 context(page 객체)를 내부 상태에 추가하고 focused page로 설정.
- `browser_list_tabs`: 현재 열린 모든 탭의 목록을 인덱스, URL, 페이지 제목과 함께 반환. LLM이 전체 탭 상태를 파악할 때 사용.
- `browser_get_focused_tab`: 현재 focused page의 인덱스, URL, 제목을 반환. LLM이 현재 작업 중인 탭을 확인할 때 사용.
- `browser_switch_tab(target)`: 인덱스(0-based) 또는 URL 문자열로 탭 전환. 해당 page 객체를 focused page로 설정. 존재하지 않으면 오류 반환.
- `browser_close_tab`: 현재 focused page를 닫고, 남은 탭 중 가장 최근 활성화된 탭으로 전환. 마지막 탭이면 빈 탭 새로 생성.
- 모든 브라우저 도구(click, type, snapshot, screenshot 등)는 현재 focused page를 대상으로 실행.

### Self-healing Selector
CSS 선택자 실패 시 다음 순서로 대체 전략을 시도한다:
1. CSS 선택자 (원본)
2. XPath로 변환
3. 텍스트 매칭 (get_by_text)
4. aria-label 속성 매칭
5. role 기반 매칭 (get_by_role)

모든 전략이 실패하면 최종 실패를 반환한다.

---

## 9. Chrome 프로세스 관리자

### Chrome 실행 정책
- **시작 시**: CDP 포트(기본 9222)에 연결 가능한지 확인 (**timeout 5초, 자동 실행 안 함**)
  - 가능 → Chrome이 이미 실행 중이므로 PlaywrightManager가 연결만 함 → "Chrome 연결됨"
  - 불가능 → "Chrome 연결하기" 상태로 시작 (사용자가 버튼 클릭 시 실행)
- **버튼 클릭 시**: 1초 타임아웃으로 CDP 연결 확인 → 실패 시 config.ini의 path로 Chrome 실행 → 연결
- **종료 시**: PlaywrightManager만 disconnect. Chrome 프로세스는 유지
- **재실행 시**: CDP 포트 확인 후 기존 Chrome 재사용 or 새로 실행

### Chrome 실행 옵션
- --remote-debugging-port={port}
- --remote-allow-origins=http://127.0.0.1:{port}
- --no-first-run
- --no-default-browser-check
- --disable-extensions
- --disable-blink-features=AutomationControlled
- --user-data-dir (설정된 경우)
- Windows 콘솔 창 없이 실행 (CREATE_NO_WINDOW 플래그)

### 실행 확인
Chrome 실행 후 최대 5초간 CDP 연결을 폴링하여 정상 실행 여부를 확인한다.