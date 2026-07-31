"""ChatWidget — 세션 로그 표시 (접힌 툴콜 + 최종답변)."""

import json
import logging
from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("psjba.ui.chat_widget")

TOOL_CALL_STYLE = (
    "QFrame { border: 1px solid #2a2a4a; border-radius: 6px; margin: 2px 0; "
    "background-color: #16213e; }"
)
USER_MSG_STYLE = (
    "QFrame { background-color: #0f3460; border-radius: 10px; "
    "padding: 10px; margin: 4px 40px 4px 8px; }"
)
AI_MSG_STYLE = (
    "QFrame { background-color: #1e1e3a; border-radius: 10px; "
    "padding: 10px; margin: 4px 8px 4px 40px; }"
)


class _ToolCallWidget(QFrame):
    """접힌/펼친 툴콜 표시 위젯. ▶ 클릭 시 상세 펼침."""

    def __init__(
        self,
        tool_name: str,
        tool_input: str,
        tool_result: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(TOOL_CALL_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(2)

        self._header = QLabel(f"▶ {tool_name}")
        self._header.setStyleSheet("color: #4FC3F7; font-weight: bold; border: none;")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.mousePressEvent = self._toggle
        layout.addWidget(self._header)

        self._detail = QLabel()
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet("color: #aaa; font-size: 11px; border: none;")
        self._detail.setText(
            f"Input: {tool_input[:300]}\n\nResult: {tool_result[:500]}"
        )
        self._detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._detail.setVisible(False)
        layout.addWidget(self._detail)

    def _toggle(self, event=None) -> None:
        visible = self._detail.isVisible()
        self._detail.setVisible(not visible)
        text = self._header.text()
        prefix = "▼" if not visible else "▶"
        self._header.setText(f"{prefix} {text[2:]}")


class _MessageBlock(QFrame):
    """단일 메시지 블록 (사용자 또는 AI)."""

    def __init__(
        self,
        role: str,
        content: str,
        tool_calls: Optional[list[dict[str, Any]]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._role = role

        if role == "user":
            self.setStyleSheet(USER_MSG_STYLE)
            layout = QHBoxLayout(self)
            layout.addStretch()
            text_label = QLabel(content)
            text_label.setWordWrap(True)
            text_label.setStyleSheet("color: white; border: none;")
            text_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            layout.addWidget(text_label)
            icon = QLabel("👤")
            icon.setStyleSheet("border: none; font-size: 16px;")
            layout.addWidget(icon)
        else:
            self.setStyleSheet(AI_MSG_STYLE)
            layout = QVBoxLayout(self)
            layout.setSpacing(4)

            header = QHBoxLayout()
            icon = QLabel("🤖")
            icon.setStyleSheet("border: none; font-size: 16px;")
            header.addWidget(icon)
            header.addStretch()
            layout.addLayout(header)

            if tool_calls:
                for tc in tool_calls:
                    name = tc.get("name", "unknown")
                    args_str = json.dumps(tc.get("args", {}), ensure_ascii=False)
                    result = tc.get("result", "")
                    tc_widget = _ToolCallWidget(name, args_str, result)
                    layout.addWidget(tc_widget)

                divider = QFrame()
                divider.setFrameShape(QFrame.Shape.HLine)
                divider.setStyleSheet("color: #555;")
                layout.addWidget(divider)

            content_label = QLabel(content)
            content_label.setWordWrap(True)
            content_label.setStyleSheet("color: white; border: none;")
            content_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            layout.addWidget(content_label)


class ChatWidget(QWidget):
    """세션 로그 영역 — QScrollArea + 메시지 블록 관리.

    메인 윈도우의 세션 로그 영역과 설정 탭의 세션 상세 로그에서
    동일한 포맷으로 사용된다.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setStyleSheet(
            "QScrollArea { border: none; background-color: #1a1a2e; }"
        )
        layout.addWidget(self._scroll_area)

        self._container = QWidget()
        self._container.setStyleSheet("background-color: #1a1a2e;")
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(8, 8, 8, 8)
        self._container_layout.setSpacing(4)
        self._container_layout.addStretch()

        self._scroll_area.setWidget(self._container)

    def add_user_message(self, text: str) -> None:
        block = _MessageBlock("user", text)
        self._insert_block(block)

    def add_ai_block(
        self,
        content: str,
        tool_calls: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        block = _MessageBlock("assistant", content, tool_calls)
        self._insert_block(block)

    def add_tool_result(self, tool_name: str, result: str) -> None:
        tc_widget = _ToolCallWidget(tool_name, "", result)
        self._insert_widget(tc_widget)

    def load_messages(self, messages: list[dict[str, Any]]) -> None:
        """DB에서 로드한 메시지 리스트를 화면에 렌더링.

        messages: [{"role": str, "content": str, "tool_calls": [...]}]
        """
        self.clear()
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")
            if role == "user":
                self.add_user_message(content)
            elif role == "assistant":
                self.add_ai_block(content, tool_calls)

    def _insert_block(self, widget: QWidget) -> None:
        count = self._container_layout.count()
        self._container_layout.insertWidget(count - 1, widget)
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _insert_widget(self, widget: QWidget) -> None:
        count = self._container_layout.count()
        self._container_layout.insertWidget(count - 1, widget)
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        sb = self._scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def clear(self) -> None:
        while self._container_layout.count() > 1:
            item = self._container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
