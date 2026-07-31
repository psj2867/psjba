"""MainWindow 헤드리스 UI 테스트.

시나리오 문서: tests/test_ui_scenarios.md (C. MainWindow)

Note: Config, Database, PlaywrightManager 는 mock fixture 로 대체.
MainWindow 생성자에서 _startup_chrome_check() 가 비동기로 실행되어
Chrome 상태가 변경될 수 있으므로, 상태 테스트는
_startup_chrome_check 실행 후 충분한 대기 시간을 둔다.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from PySide6.QtCore import Qt

from src.ui.main_window import (
    MainWindow,
    CHROME_BTN_STYLE_CONNECTED,
    CHROME_BTN_STYLE_CONNECTING,
    CHROME_BTN_STYLE_DISCONNECTED,
)


class TestMainWindowInitial:
    """C-1. 초기 상태"""

    def test_window_title(self, qtbot, mock_config, mock_db, mock_manager):
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        assert w.windowTitle() == "Browser Agent"

    def test_chrome_btn_starts_connecting(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        """생성 직후 startup check 로 '연결 중...' 상태가 된다."""
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        # _startup_chrome_check 가 즉시 _set_chrome_status("connecting") 호출
        assert "연결 중" in w._chrome_btn.text()
        assert not w._chrome_btn.isEnabled()

    def test_settings_btn_exists(self, qtbot, mock_config, mock_db, mock_manager):
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        assert w._settings_btn is not None
        assert "⚙️" in w._settings_btn.text()


class TestMainWindowChromeButton:
    """C-2. Chrome 버튼 스타일"""

    def test_set_connected(self, qtbot, mock_config, mock_db, mock_manager):
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        w._set_chrome_status("connected")
        assert "Chrome 연결 됨" in w._chrome_btn.text()
        assert w._chrome_btn.isEnabled()

    def test_set_disconnected(self, qtbot, mock_config, mock_db, mock_manager):
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        w._set_chrome_status("disconnected")
        assert "Chrome 연결 안 됨" in w._chrome_btn.text()
        assert w._chrome_btn.isEnabled()

    def test_set_connecting(self, qtbot, mock_config, mock_db, mock_manager):
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        w._set_chrome_status("connecting")
        assert "Chrome 연결 중" in w._chrome_btn.text()
        assert not w._chrome_btn.isEnabled()

    def test_chrome_btn_click_disconnected_calls_connect_chrome(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        mock_manager.is_connected = False
        # spy on _connect_chrome
        original = w._connect_chrome
        called = False

        def spy():
            nonlocal called
            called = True
            original()

        w._connect_chrome = spy
        w._on_chrome_btn_click()
        assert called, "_connect_chrome should be called"

    def test_chrome_btn_click_connected_calls_disconnect_chrome(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        mock_manager.is_connected = True
        original = w._disconnect_chrome
        called = False

        def spy():
            nonlocal called
            called = True
            original()

        w._disconnect_chrome = spy
        w._on_chrome_btn_click()
        assert called, "_disconnect_chrome should be called"


class TestMainWindowPrompt:
    """C-3. 프롬프트 전송"""

    def test_prompt_ignored_when_chrome_disconnected(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        signals = []

        def capture(s):
            signals.append(s)

        w.prompt_submitted.connect(capture)
        w._on_prompt_submitted("test")
        assert len(signals) == 0

    def test_prompt_emitted_when_chrome_connected(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        mock_manager.is_connected = True
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        with qtbot.waitSignal(w.prompt_submitted, timeout=500) as blocker:
            w._on_prompt_submitted("hello agent")
        assert blocker.args == ["hello agent"]


class TestMainWindowAIResponse:
    """C-4. add_ai_response()"""

    def test_add_ai_response_no_toolcalls(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        w.add_ai_response("답변")
        assert w._prompt._is_running is False

    def test_add_ai_response_with_toolcalls(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        tool_calls = [{"name": "click", "args": {}, "result": "ok"}]
        w.add_ai_response("답변2", tool_calls)
        assert w._prompt._is_running is False


class TestMainWindowSessionReset:
    """C-5. 세션 초기화"""

    def test_session_reset_signal_emitted(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        with qtbot.waitSignal(w.session_reset_requested, timeout=500):
            w._on_session_reset()


class TestMainWindowSettingsBtn:
    """C-6. 설정 버튼"""

    def test_settings_btn_opens_settings(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        called = False

        def spy():
            nonlocal called
            called = True

        w._open_settings = spy
        qtbot.mouseClick(w._settings_btn, Qt.MouseButton.LeftButton)
        assert called