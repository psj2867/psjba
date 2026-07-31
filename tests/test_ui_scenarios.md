# UI 헤드리스 테스트 시나리오

> `QT_QPA_PLATFORM=offscreen` 환경에서 PySide6 위젯을 헤드리스로 테스트한다.
> `pytest-qt`의 `qtbot` fixture를 사용하여 시그널/클릭/키입력을 시뮬레이션한다.

---

## 사전 조건 (conftest.py)

```python
# tests/conftest.py 에 추가
os.environ["QT_QPA_PLATFORM"] = "offscreen"
# LD_LIBRARY_PATH 에 libEGL.so.1 경로 추가
```

---

## A. PromptInput (tests/test_ui_prompt_input.py)

### A-1. 초기 상태
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | PromptInput 생성 | `submitted`, `stopped`, `session_reset` 시그널 존재 |
| 2 | 상태 확인 | 전송 버튼 보임, 중지 버튼 숨김 |
| 3 | 텍스트 미입력 확인 | `get_text()` → `""` |

### A-2. Shift+Enter 전송
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | 텍스트 입력: "hello world" | 텍스트 편집창에 표시 |
| 2 | Shift+Enter 입력 | `submitted` 시그널 emit ("hello world") |
| 3 | 전송 후 텍스트 유지 확인 | `get_text()` → `"hello world"` (clear 안 함) |

### A-3. 전송 버튼 클릭
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | 텍스트 입력: "test prompt" | 텍스트 편집창에 표시 |
| 2 | 전송 버튼 클릭 | `submitted` 시그널 emit ("test prompt") |

### A-4. 빈 텍스트 전송 무시
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | 빈 텍스트 상태 | `get_text()` → `""` |
| 2 | Shift+Enter | `submitted` **emit 안 됨** |
| 3 | 전송 버튼 클릭 | `submitted` **emit 안 됨** |
| 4 | 공백만 입력: "   " | `get_text()` → `"   "` |
| 5 | Shift+Enter | `submitted` **emit 안 됨** (strip 후 빈 값이므로) |

### A-5. Enter 키 줄바꿈 (NoModifier)
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | 텍스트 입력: "line1" | |
| 2 | Enter (NoModifier) | 텍스트에 줄바꿈 추가, `submitted` emit 안 됨 |
| 3 | "line2" 추가 입력 | 텍스트: "line1\nline2" |

### A-6. Running 상태 전환
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | `set_running(True)` 호출 | 전송 버튼 숨김, 중지 버튼 표시 |
| 2 | 텍스트 읽기 전용 확인 | 편집창에 입력 불가 |
| 3 | 초기화 버튼 비활성화 확인 | `_reset_btn.enabled == False` |
| 4 | `set_running(False)` 호출 | 전송 버튼 표시, 중지 버튼 숨김 |
| 5 | 텍스트 편집 가능 확인 | 편집창에 입력 가능 |
| 6 | 초기화 버튼 활성화 확인 | `_reset_btn.enabled == True` |

### A-7. Running 중 전송 무시
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | `set_running(True)` | running 상태 |
| 2 | 텍스트 입력: "should not send" | |
| 3 | Shift+Enter | `submitted` **emit 안 됨** |
| 4 | 전송 버튼 숨겨져 있음 | 버튼 visible == False |

### A-8. 중지 버튼 클릭
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | `set_running(True)` | 중지 버튼 표시 |
| 2 | 중지 버튼 클릭 | `stopped` 시그널 emit |

### A-9. 초기화 버튼 클릭
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | 초기화(🔄) 버튼 클릭 | `session_reset` 시그널 emit |

### A-10. clear()
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | 텍스트 입력 후 `clear()` 호출 | `get_text()` → `""` |

---

## B. ChatWidget (tests/test_ui_chat_widget.py)

### B-1. 초기 상태
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | ChatWidget 생성 | 빈 상태, stretch만 존재 |
| 2 | clear() 호출 | 에러 없음 |

### B-2. 사용자 메시지 추가
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | `add_user_message("hello")` | 메시지 블록 1개 추가 |
| 2 | `add_user_message("world")` | 메시지 블록 2개 |

### B-3. AI 메시지 추가
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | `add_ai_block("답변입니다.")` | AI 블록 1개 추가 (🤖 아이콘) |
| 2 | `add_ai_block("답변2", [])` | AI 블록 2개 (툴콜 없음) |

### B-4. 툴콜 포함 AI 메시지
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | tool_calls=[{"name":"navigate","args":{"url":"..."},"result":"ok"}] 와 함께 `add_ai_block` 호출 | AI 블록에 _ToolCallWidget 포함 |
| 2 | _ToolCallWidget 헤더 표시 | "▶ navigate" 텍스트 |
| 3 | 상세 영역 초기에는 숨김 | `_detail.isVisible() == False` |

### B-5. _ToolCallWidget 토글
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | _ToolCallWidget 생성 | 헤더: "▶ ..." |
| 2 | 헤더 클릭 | `_detail` 표시, 헤더: "▼ ..." |
| 3 | 헤더 다시 클릭 | `_detail` 숨김, 헤더: "▶ ..." |

### B-6. load_messages()
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | `load_messages([{"role":"user","content":"안녕"}, {"role":"assistant","content":"네"}])` | 2개 블록 로드 |
| 2 | `load_messages([])` | 모두 제거 (stretch만 남음) |

### B-7. clear()
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | 여러 메시지 추가 후 `clear()` | 모든 블록 제거 |

---

## C. MainWindow (tests/test_ui_main_window.py)

### C-1. 초기 상태
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | MainWindow 생성 (config, db, manager mock) | 타이틀: "Browser Agent" |
| 2 | Chrome 버튼 상태 | "🔗 Chrome 연결 안 됨" |
| 3 | 설정 버튼 존재 | ⚙️ 버튼 있음 |

### C-2. Chrome 버튼 스타일
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | `_set_chrome_status("connected")` 호출 | 버튼 텍스트: "🔗 Chrome 연결 됨" |
| 2 | `_set_chrome_status("disconnected")` 호출 | 버튼 텍스트: "🔗 Chrome 연결 안 됨" |
| 3 | `_set_chrome_status("connecting")` 호출 | 버튼 텍스트: "🔗 Chrome 연결 중..." |

### C-3. 프롬프트 전송 (Chrome 미연결 시 무시)
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | manager.is_connected == False | |
| 2 | `_on_prompt_submitted("test")` 호출 | `prompt_submitted` emit 안 됨 (연결 안 됨) |

### C-4. add_ai_response()
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | `add_ai_response("답변")` | ChatWidget에 AI 블록 추가 |
| 2 | `add_ai_response("답변2", [{"name":"click","args":{},"result":"ok"}])` | ChatWidget에 AI 블록 + 툴콜 추가 |

### C-5. 설정 버튼 → SettingsDialog
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | ⚙️ 버튼 클릭 | SettingsDialog 열림 |
| 2 | 다이얼로그 탭 확인 | 설정/CRON/로그 3개 탭 |
| 3 | 저장 버튼 | 유효성 검사 후 config_saved emit |

---

## D. SettingsDialog (tests/test_ui_settings_dialog.py)

### D-1. 초기 로드
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | Config + mock DB로 SettingsDialog 생성 | 설정값이 필드에 로드됨 |
| 2 | 3개 탭 존재 | "설정", "CRON", "로그" |

### D-2. 저장 유효성 검사
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | API 키를 비움 | |
| 2 | 저장 버튼 클릭 | 경고 메시지, 저장 안 됨 |
| 3 | API 키 입력, 모델 비움 | |
| 4 | 저장 버튼 클릭 | 경고 메시지, 저장 안 됨 |

### D-3. 성공 저장
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | API 키 + 모델 입력 | |
| 2 | 저장 버튼 클릭 | `config_saved` 시그널 emit, 다이얼로그 close |
| 3 | config.ini에 암호화 저장 확인 | api_key 평문 없음 |

---

## E. 통합 시나리오

### E-1. 전체 앱 시작 흐름
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | Config 로드 | 정상 로드 |
| 2 | MainWindow 생성 | 타이틀, 크롬 버튼, 채팅, 입력창 모두 표시 |
| 3 | 세션 초기화 버튼 클릭 | 채팅 clear, 새 세션 생성 |

### E-2. PromptInput → MainWindow 시그널 체인
| 단계 | 동작 | 기대 결과 |
|------|------|-----------|
| 1 | PromptInput에서 Shift+Enter | PromptInput.submitted emit |
| 2 | MainWindow._on_prompt_submitted 호출 | (Chrome 미연결 시) 무시 |
| 3 | 세션 초기화 | session_reset_requested emit → DB/채팅 초기화 |

---

## 실행 방법

```bash
# libEGL.so.1 필요 (헤드리스 환경)
export LD_LIBRARY_PATH=/path/to/libEGL.so.1/dir:$LD_LIBRARY_PATH

# 단일 테스트
python -m pytest tests/test_ui_prompt_input.py -v

# 전체 UI 테스트
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui*.py -v

# pytest-qt 디버그 로그
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui_prompt_input.py -v -s
```

> ⚠️ SettingsDialog 테스트는 QDialog.exec() 블로킹 문제로 인해 헤드리스 환경에서 추가 설정이 필요할 수 있음.
> `QDialog.exec()` 대신 `QDialog.open()` 사용 또는 `QTimer.singleShot`으로 다이얼로그 열고 닫기 패턴 적용.