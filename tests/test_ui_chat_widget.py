"""ChatWidget 헤드리스 UI 테스트.

시나리오 문서: tests/test_ui_scenarios.md (B. ChatWidget)
"""

import pytest
from PySide6.QtCore import Qt

from src.ui.chat_widget import ChatWidget, _MessageBlock, _ToolCallWidget


def _show(qtbot, w):
    qtbot.addWidget(w)
    w.show()
    qtbot.waitExposed(w)


class TestChatWidgetInitial:
    """B-1. 초기 상태"""

    def test_create_and_clear(self, qtbot):
        w = ChatWidget()
        qtbot.addWidget(w)
        assert w._container_layout.count() == 1  # stretch only

    def test_clear_empty_no_error(self, qtbot):
        w = ChatWidget()
        qtbot.addWidget(w)
        w.clear()  # should not raise


class TestChatWidgetMessages:
    """B-2. 사용자 메시지 추가"""

    def test_add_user_message(self, qtbot):
        w = ChatWidget()
        qtbot.addWidget(w)
        w.add_user_message("hello")
        assert w._container_layout.count() == 2  # msg + stretch

    def test_add_two_user_messages(self, qtbot):
        w = ChatWidget()
        qtbot.addWidget(w)
        w.add_user_message("hello")
        w.add_user_message("world")
        assert w._container_layout.count() == 3

    """B-3. AI 메시지 추가"""

    def test_add_ai_block(self, qtbot):
        w = ChatWidget()
        qtbot.addWidget(w)
        w.add_ai_block("답변입니다.")
        assert w._container_layout.count() == 2

    def test_add_ai_block_with_empty_toolcalls(self, qtbot):
        w = ChatWidget()
        qtbot.addWidget(w)
        w.add_ai_block("답변2", [])
        assert w._container_layout.count() == 2

    """B-4. 툴콜 포함 AI 메시지"""

    def test_ai_block_with_tool_calls(self, qtbot):
        w = ChatWidget()
        qtbot.addWidget(w)
        tool_calls = [
            {"name": "navigate", "args": {"url": "https://example.com"}, "result": "ok"}
        ]
        w.add_ai_block("답변", tool_calls)

        # _MessageBlock 내부에 _ToolCallWidget 이 있는지 확인
        for i in range(w._container_layout.count()):
            item = w._container_layout.itemAt(i)
            if item and item.widget():
                block = item.widget()
                if isinstance(block, _MessageBlock):
                    # _MessageBlock 내부 _ToolCallWidget 찾기
                    tc_widgets = block.findChildren(_ToolCallWidget)
                    assert len(tc_widgets) == 1
                    assert "navigate" in tc_widgets[0]._header.text()
                    assert not tc_widgets[0]._detail.isVisible()
                    return
        pytest.fail("_MessageBlock not found")

    """B-5. _ToolCallWidget 토글"""

    def test_tool_call_toggle(self, qtbot):
        tc = _ToolCallWidget("click", "{'selector': '#btn'}", "clicked OK")
        _show(qtbot, tc)
        assert "▶" in tc._header.text()
        assert not tc._detail.isVisible()

        # 열기
        tc._toggle()
        assert "▼" in tc._header.text()
        assert tc._detail.isVisible()

        # 닫기
        tc._toggle()
        assert "▶" in tc._header.text()
        assert not tc._detail.isVisible()

    """B-6. load_messages()"""

    def test_load_messages(self, qtbot):
        w = ChatWidget()
        qtbot.addWidget(w)
        msgs = [
            {"role": "user", "content": "안녕"},
            {"role": "assistant", "content": "네"},
        ]
        w.load_messages(msgs)
        # user msg + assistant msg + stretch = 3
        assert w._container_layout.count() == 3

    def test_load_messages_empty(self, qtbot):
        w = ChatWidget()
        qtbot.addWidget(w)
        w.load_messages([])
        assert w._container_layout.count() == 1  # stretch only

    """B-7. clear()"""

    def test_clear_removes_all_blocks(self, qtbot):
        w = ChatWidget()
        qtbot.addWidget(w)
        w.add_user_message("a")
        w.add_user_message("b")
        w.add_ai_block("c")
        assert w._container_layout.count() == 4
        w.clear()
        assert w._container_layout.count() == 1  # stretch only