"""PromptInput 헤드리스 UI 테스트.

시나리오 문서: tests/test_ui_scenarios.md (A. PromptInput)
"""

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent

from src.ui.prompt_input import PromptInput


def _show(qtbot, w):
    """위젯을 표시하고 노출될 때까지 대기한다."""
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)


class TestPromptInputInitialState:
    """A-1. 초기 상태"""

    def test_signals_exist(self, qtbot):
        """submitted, stopped, session_reset 시그널 존재."""
        w = PromptInput()
        qtbot.addWidget(w)
        assert hasattr(w, "submitted")
        assert hasattr(w, "stopped")
        assert hasattr(w, "session_reset")

    def test_send_btn_visible_stop_btn_hidden(self, qtbot):
        """전송 버튼 보임, 중지 버튼 숨김."""
        w = PromptInput()
        _show(qtbot, w)
        assert w._send_btn.isVisible()
        assert not w._stop_btn.isVisible()

    def test_initial_text_empty(self, qtbot):
        """초기 get_text() → ''."""
        w = PromptInput()
        qtbot.addWidget(w)
        assert w.get_text() == ""


class TestPromptInputSend:
    """A-2. Shift+Enter 전송"""

    def test_shift_enter_emits_submitted(self, qtbot):
        w = PromptInput()
        qtbot.addWidget(w)
        with qtbot.waitSignal(w.submitted, timeout=500) as blocker:
            w._text_edit.setPlainText("hello world")
            qtbot.keyClick(w._text_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        assert blocker.args == ["hello world"]

    def test_send_after_shift_enter_text_preserved(self, qtbot):
        """전송 후 텍스트 유지 (clear하지 않음)."""
        w = PromptInput()
        qtbot.addWidget(w)
        with qtbot.waitSignal(w.submitted, timeout=500):
            w._text_edit.setPlainText("hello world")
            qtbot.keyClick(w._text_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        assert w.get_text() == "hello world"

    """A-3. 전송 버튼 클릭"""

    def test_send_btn_emits_submitted(self, qtbot):
        w = PromptInput()
        qtbot.addWidget(w)
        w._text_edit.setPlainText("test prompt")
        with qtbot.waitSignal(w.submitted, timeout=500) as blocker:
            qtbot.mouseClick(w._send_btn, Qt.MouseButton.LeftButton)
        assert blocker.args == ["test prompt"]

    """A-4. 빈 텍스트 전송 무시"""

    def test_empty_text_does_not_emit(self, qtbot):
        w = PromptInput()
        qtbot.addWidget(w)
        signals = []

        def capture(s):
            signals.append(s)

        w.submitted.connect(capture)
        qtbot.keyClick(w._text_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        assert len(signals) == 0

    def test_blank_text_does_not_emit(self, qtbot):
        w = PromptInput()
        qtbot.addWidget(w)
        signals = []

        def capture(s):
            signals.append(s)

        w.submitted.connect(capture)
        w._text_edit.setPlainText("   ")
        qtbot.keyClick(w._text_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        assert len(signals) == 0

    def test_send_btn_empty_does_not_emit(self, qtbot):
        w = PromptInput()
        qtbot.addWidget(w)
        signals = []

        def capture(s):
            signals.append(s)

        w.submitted.connect(capture)
        qtbot.mouseClick(w._send_btn, Qt.MouseButton.LeftButton)
        assert len(signals) == 0


class TestPromptInputNewline:
    """A-5. Enter 키 줄바꿈 (NoModifier)"""

    def test_enter_inserts_newline(self, qtbot):
        w = PromptInput()
        _show(qtbot, w)
        w._text_edit.setPlainText("line1")
        # cursor 를 끝으로 이동
        cursor = w._text_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        w._text_edit.setTextCursor(cursor)
        qtbot.keyClick(w._text_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
        # get_text()는 strip() 하므로 toPlainText()로 직접 확인
        assert "\n" in w._text_edit.toPlainText()

    def test_enter_does_not_emit_submitted(self, qtbot):
        w = PromptInput()
        qtbot.addWidget(w)
        signals = []

        def capture(s):
            signals.append(s)

        w.submitted.connect(capture)
        w._text_edit.setPlainText("text")
        qtbot.keyClick(w._text_edit, Qt.Key.Key_Return)
        assert len(signals) == 0


class TestPromptInputRunningState:
    """A-6. Running 상태 전환"""

    def test_set_running_true_hides_send_shows_stop(self, qtbot):
        w = PromptInput()
        _show(qtbot, w)
        w.set_running(True)
        assert not w._send_btn.isVisible()
        assert w._stop_btn.isVisible()

    def test_set_running_true_text_readonly(self, qtbot):
        w = PromptInput()
        qtbot.addWidget(w)
        w.set_running(True)
        assert w._text_edit.isReadOnly()

    def test_set_running_true_reset_disabled(self, qtbot):
        w = PromptInput()
        qtbot.addWidget(w)
        w.set_running(True)
        assert not w._reset_btn.isEnabled()

    def test_set_running_false_restores_send(self, qtbot):
        w = PromptInput()
        _show(qtbot, w)
        w.set_running(True)
        w.set_running(False)
        assert w._send_btn.isVisible()
        assert not w._stop_btn.isVisible()
        assert not w._text_edit.isReadOnly()
        assert w._reset_btn.isEnabled()

    """A-7. Running 중 전송 무시"""

    def test_shift_enter_ignored_when_running(self, qtbot):
        w = PromptInput()
        _show(qtbot, w)
        w.set_running(True)
        signals = []

        def capture(s):
            signals.append(s)

        w.submitted.connect(capture)
        w._text_edit.setPlainText("should not send")
        qtbot.keyClick(w._text_edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        assert len(signals) == 0

    """A-8. 중지 버튼 클릭"""

    def test_stop_btn_emits_stopped(self, qtbot):
        w = PromptInput()
        _show(qtbot, w)
        w.set_running(True)
        with qtbot.waitSignal(w.stopped, timeout=500):
            qtbot.mouseClick(w._stop_btn, Qt.MouseButton.LeftButton)

    """A-9. 초기화 버튼 클릭"""

    def test_reset_btn_emits_session_reset(self, qtbot):
        w = PromptInput()
        qtbot.addWidget(w)
        with qtbot.waitSignal(w.session_reset, timeout=500):
            qtbot.mouseClick(w._reset_btn, Qt.MouseButton.LeftButton)

    """A-10. clear()"""

    def test_clear_empties_text(self, qtbot):
        w = PromptInput()
        qtbot.addWidget(w)
        w._text_edit.setPlainText("some text")
        w.clear()
        assert w.get_text() == ""