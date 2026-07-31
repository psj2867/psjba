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

## 디자인 시스템 (v0.1)

> (삭제됨 — 디자인 테마 개념을 제거하고 단일 디자인으로 고정)
