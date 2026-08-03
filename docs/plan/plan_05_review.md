# 검토 및 결정 사항 기록

## 적용된 결정 사항

| # | 결정 | 적용 파일 | 상태 |
|---|------|----------|------|
| 1 | ~~세션 초기화 버튼: 우측 상단 작게~~ → **입력창 우측 🗑️ 아이콘** | plan_02_config_ui.md, plan_04_ops.md | ✅ |
| 2 | 크론 "마지막 실행 결과" 제거, 클릭 시 세션 로그 | plan_02_config_ui.md, plan_04_ops.md | ✅ |
| 3 | language 우선순위: psjba_meta 제거, config.ini로 통일 | plan_02_config_ui.md, plan_03_agent.md, plan_04_ops.md | ✅ |
| 4 | browser_screenshot 파일명: {yyyyMMdd-HHmmss}.png | plan_03_agent.md, plan_04_ops.md | ✅ |
| 5 | browser_close 제거, tab_close만 유지 | plan_03_agent.md | ✅ |
| 6 | memory_search: SQLite FTS5 사용 | plan_03_agent.md | ✅ |
| 7 | 슬라이딩 윈도우: system 제외, user 메시지 기준 | plan_03_agent.md | ✅ |
| 8 | LLM Provider: openrouter 기본, openai 호환, ini에 provider url | plan_02_config_ui.md, plan_03_agent.md | ✅ |
| 9 | compaction.py 사용 안 함 정리 | plan_04_ops.md | ✅ |
| 10 | requirements.txt: cryptography 추가 | plan_04_ops.md | ✅ |
| 11 | CDP connection_timeout: 기본 1초, ini 설정 | plan_02_config_ui.md | ✅ |
| 12 | Chrome 실행 옵션: --disable-blink-features=AutomationControlled | plan_03_agent.md | ✅ |
| 13 | psjba_session: type 필드(chat/cron) 추가 | plan_02_config_ui.md, plan_04_ops.md | ✅ |
| 14 | 세션 제목: 첫 메시지 앞 50자 | plan_02_config_ui.md | ✅ |
| 15 | **UI 완전 갈아엎음**: 메인 윈도우 심플화 (⚙️ + 🔗 Chrome 버튼 + 프롬프트), 설정은 QDialog(설정/CRON/로그 탭) | plan_02_config_ui.md, plan_01_core.md, plan_03_agent.md, plan_04_ops.md | ✅ |
| 16 | **Chrome 실행 정책 변경**: 시작 시 5초 테스트(자동 실행 안 함), 버튼 클릭 시 1초→실패→Chrome 실행 | plan_02_config_ui.md, plan_03_agent.md, plan_04_ops.md | ✅ |
| 17 | **ui/ 폴더 구조 변경**: main_window + settings_dialog + chat_widget + prompt_input | plan_04_ops.md | ✅ |
| 18 | **Provider 설정 제거**: OpenAI 호환 API만 사용하므로 provider 드롭다운 삭제, model 기본값 `qwen/qwen3.6-27b` | config.py, settings_dialog.py, llm_client.py | ✅ |
| 19 | **UI 리뉴얼**: 전체 UI 스타일 통일 | settings_dialog.py, main_window.py, chat_widget.py, prompt_input.py | ✅ |
| 20 | **chrome_health / chrome_start 툴 추가**: AI가 Chrome 연결 상태를 파악(chrome_health)하고 필요 시 연결(chrome_start)할 수 있는 도구. chrome_health는 연결 안 됨 상태에서도 안전 호출 가능 | manager.py, tools.py, prompts.py | ✅ |
| 21 | **CDP 연결 WebSocket 경로 수정**: `ws://host:port/` root 대신 `/json/version` 에서 `webSocketDebuggerUrl` 을 먼저 조회 후 Playwright 연결 (Chrome 150+ 404 방지) | launcher.py, manager.py | ✅ |
| 22 | **disconnect 시 Chrome 프로세스 유지**: `browser.close()` → `browser.disconnect()` 로 변경, 탭 닫기 루프 제거. WebSocket 만 끊고 Chrome 은 계속 실행 | manager.py | ✅ |
| 23 | **Chrome user-data-dir 기본값**: config 에 값이 없으면 `tempfile.gettempdir()/psjba/chrome/` 자동 생성하여 사용 | launcher.py | ✅ |
| 24 | **task_complete(summary) 도구 추가**: LLM 이 작업 완료 시 명시적으로 호출하는 전용 도구. execute_tool 노드에서 is_final=True 감지 → conditional edge → END. prompt rule 8에서 사용 지시 | tools.py, nodes.py, prompts.py | ✅ (v0.6 제거) |
| 25 | **Tool 결과 명확화**: get_url/get_title → "Current URL/Current title:" 레이블 추가; execute_script → "Script returned:" 포맷; click/press_key → 실행 후 URL+title 포함 | manager.py | ✅ |
| 26 | **프롬프트 입력 즉시 클리어**: `_on_send()` 에서 `_text_edit.clear()` 호출 추가. 전송 직후 입력창을 비워 사용자 피드백 개선 | prompt_input.py | ✅ |
| 27 | **LLM debug 로그 강화**: call_model 에서 요청 메시지 타입/내용 요약 로그, 응답 시간 + tool_call 목록 또는 텍스트 응답 로깅 | nodes.py | ✅ |
| 28 | **Chat thinking skeleton**: `add_thinking_skeleton` / `update_thinking` / `replace_thinking_skeleton` 메서드 추가. Agent 실행 중 "🤖 응답 중..." → "🔧 browser_navigate 실행 중..." → 최종 응답 교체 | chat_widget.py, main_window.py, main.py | ✅ |
| 29 | **프롬프트에 브라우저 상태 자동 첨부**: 매 질문마다 `get_browser_context()` (연결 상태, 탭 리스트, 포커스 탭)를 수집하여 HumanMessage 앞에 자동 삽입. LLM이 chrome_health/list_tabs 를 호출할 필요 없어짐 | manager.py, main.py | ✅ |
| 30 | **기본 모델 변경**: `qwen/qwen3.6-27b` → `deepseek/deepseek-v4-flash` | config.py | ✅ |
| 31 | **LLM 응답 로그에 finish_reason 추가**: `finish_reason=stop` / `length` / `tool_calls` 로그에 포함 | nodes.py | ✅ |
| 32 | **Self-healing 코루틴 미소비 경고 수정**: find_element에서 모든 전략을 순차적으로 try/await 하여 미소비 코루틴 경고 제거 | self_healing.py | ✅ |
| 33 | **접근성 트리 ref 번호 + click_by_ref 툴 추가**: 상호작용 가능 요소에 [#N] ref 번호 부여, browser_click_by_ref(ref) 툴로 정확한 클릭 지원 | accessibility.py, manager.py, tools.py | ✅ |
| 34 | **max_tool_calls 제한 도입**: 한 프롬프트 당 최대 tool call 횟수(기본 30) 제한. call_model 진입 시 tool_call_count >= max_tool_calls 면 즉시 종료. config.ini [agent] 섹션에서 설정 가능 | config.py, state.py, graph.py, nodes.py, main.py | ✅ |
| 35 | **task_complete 툴 제거**: tool_choice="any" 에서 모델이 억지로 task_complete 를 호출하는 패턴 방지. 일반 텍스트 응답으로 종료 | tools.py, nodes.py, prompts.py, plan_05_review.md | ✅ |

## 디자인 시스템 (v0.1)

> (삭제됨 — 디자인 테마 개념을 제거하고 단일 디자인으로 고정)
