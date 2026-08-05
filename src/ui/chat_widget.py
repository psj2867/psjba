"""ChatWidget — 세션 로그 표시 (접힌 툴콜 + 최종답변 + thinking skeleton)."""

import json
import logging
from typing import Any, Optional

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger("psjba.ui.chat_widget")

try:
    from shiboken6 import isValid as _qt_is_valid
except ImportError:
    def _qt_is_valid(obj: object) -> bool:
        return True

TOOL_CALL_STYLE = (
    "QFrame { border: 1px solid #dce1e8; border-radius: 6px; margin: 2px 0; "
    "background-color: #f8fafc; }"
)
CODE_BLOCK_STYLE = (
    "QLabel { background-color: #f1f5f9; color: #334155; border: 1px solid #e2e8f0; "
    "border-radius: 4px; padding: 6px 8px; font-family: Consolas, Menlo, monospace; "
    "font-size: 11px; }"
)
MSG_STYLE = (
    "QFrame { background-color: #818cf8; border-radius: 8px; "
    "padding: 6px 12px; margin: 3px 12px; }"
)
AI_MSG_STYLE = (
    "QFrame { background-color: #ffffff; border-radius: 8px; "
    "padding: 6px 12px; margin: 3px 12px; }"
)


def _looks_like_json(text: str) -> bool:
    """JSON 형식(객체/배열)으로 시작하는지 검사한다."""
    stripped = text.strip()
    if not stripped:
        return False
    return stripped[0] in ("{", "[")


def _format_json_result(text: str, limit: int = 3000) -> str:
    """JSON 결과를 보기 좋게 들여쓰기된 코드블럭 텍스트로 포맷팅한다."""
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
        pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, ValueError):
        pretty = stripped
    if len(pretty) > limit:
        pretty = pretty[:limit] + "\n... (truncated)"
    return pretty


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
        self._header.setStyleSheet("color: #4f46e5; font-weight: bold; border: none;")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.mousePressEvent = self._toggle
        layout.addWidget(self._header)

        # result가 JSON이면 코드블럭으로 포맷팅
        if _looks_like_json(tool_result):
            formatted = _format_json_result(tool_result)
            self._detail = QLabel(
                f"Input: {tool_input[:300]}\n\nResult:\n```json\n{formatted}\n```"
            )
            self._detail.setStyleSheet(CODE_BLOCK_STYLE)
        else:
            self._detail = QLabel(
                f"Input: {tool_input[:300]}\n\nResult: {tool_result[:500]}"
            )
            self._detail.setStyleSheet("color: #64748b; font-size: 11px; border: none;")
        self._detail.setWordWrap(True)
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


class _ContentBrowser(QTextBrowser):
    """내용물 높이만큼만 차지하는 QTextBrowser (내부 스크롤 없음, sizeHint 최소화)."""

    def __init__(self, content: str, align_right: bool = False, text_color: str = "#1e293b") -> None:
        super().__init__()
        self._last_width = 0
        self.setObjectName("content")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        self.setStyleSheet(
            f"QTextBrowser {{ background: transparent; color: {text_color}; border: none; "
            "padding: 0px; margin: 0px; }"
        )
        if align_right:
            self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setMarkdown(content)

    def setMarkdown(self, text: str) -> None:
        super().setMarkdown(text)
        self.updateGeometry()

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def sizeHint(self) -> QSize:
        w = self.viewport().width()
        if w <= 0:
            w = 600
        self.document().setTextWidth(w)
        h = int(self.document().size().height())
        return QSize(w, max(h, 20))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        w = self.viewport().width()
        if w != self._last_width:
            self._last_width = w
            # 레이아웃이 sizeHint 재조회할 시간을 줌 (즉시 호출 시 미반영되는 경우 방지)
            QTimer.singleShot(0, self.updateGeometry)


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
            self.setStyleSheet(MSG_STYLE)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(8, 6, 8, 6)
            layout.setSpacing(4)
            layout.addWidget(self._make_content_browser(content, align_right=True, text_color="#ffffff"))
        else:
            self.setStyleSheet(AI_MSG_STYLE)
            layout = QVBoxLayout(self)
            layout.setContentsMargins(8, 6, 8, 6)
            layout.setSpacing(4)

            has_content = content and content.strip()

            if tool_calls:
                for tc in tool_calls:
                    name = tc.get("name", "unknown")
                    args_str = json.dumps(tc.get("args", {}), ensure_ascii=False)
                    result = tc.get("result", "")
                    tc_widget = _ToolCallWidget(name, args_str, result)
                    layout.addWidget(tc_widget)

            if has_content:
                    divider = QFrame()
                    divider.setFrameShape(QFrame.Shape.HLine)
                    divider.setStyleSheet("color: #dce1e8;")
                    layout.addWidget(divider)

            if has_content:
                layout.addWidget(self._make_content_browser(content))

    @staticmethod
    def _make_content_browser(content: str, align_right: bool = False, text_color: str = "#1e293b") -> _ContentBrowser:
        """마크다운(테이블/코드블록 포함)을 렌더링하는 읽기 전용 브라우저."""
        return _ContentBrowser(content, align_right=align_right, text_color=text_color)


class ChatWidget(QWidget):
    """세션 로그 영역 — QScrollArea + 메시지 블록 관리.

    메인 윈도우의 세션 로그 영역과 설정 탭의 세션 상세 로그에서
    동일한 포맷으로 사용된다.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._thinking_block: Optional[_MessageBlock] = None
        self._last_ai_block: Optional[_MessageBlock] = None  # 실시간 갱신용
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._scroll_area.setStyleSheet(
            "QScrollArea { border: none; background-color: #f0f2f5; }"
        )
        layout.addWidget(self._scroll_area)

        self._container = QWidget()
        self._container.setStyleSheet("background-color: #f0f2f5;")
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(4, 2, 4, 2)
        self._container_layout.setSpacing(2)
        self._container_layout.addStretch()

        self._scroll_area.setWidget(self._container)

    # ─── 스크롤 헬퍼 ──────────────────────────────────────────

    def _scroll_to_bottom(self) -> None:
        """스크롤바를 최하단으로 이동시킨다. (레이아웃 적용 후 비동기)"""
        QTimer.singleShot(0, self._do_scroll)

    def _do_scroll(self) -> None:
        sb = self._scroll_area.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    # --- 사용자 메시지 ---

    def add_user_message(self, text: str) -> None:
        block = _MessageBlock("user", text)
        self._insert_block(block)
        self._scroll_to_bottom()

    # --- AI thinking skeleton ---

    def add_thinking_skeleton(self, text: str = "🤖 응답 중...") -> None:
        """AI 응답 대기 중임을 표시하는 스켈론 블록을 추가한다.

        이미 스켈론이 있으면 텍스트만 갱신한다.
        이후 replace_thinking_skeleton() 으로 실제 응답으로 교체한다.
        """
        if self._thinking_block:
            self._update_thinking(text)
            return
        self._thinking_block = _MessageBlock("assistant", text)
        self._insert_block(self._thinking_block)
        self._scroll_to_bottom()

    def update_thinking(self, text: str) -> None:
        """현재 스켈론 텍스트를 갱신한다."""
        block = self._thinking_block
        if block is None:
            return
        if not _qt_is_valid(block) or not block.isWidgetType():
            self._thinking_block = None
            return
        for browser in block.findChildren(QTextBrowser):
            if browser.objectName() == "content" and _qt_is_valid(browser):
                browser.setMarkdown(text)
                break

    def _update_thinking(self, text: str) -> None:
        """내부용: 스켈론 텍스트 즉시 갱신."""
        self.update_thinking(text)

    def replace_thinking_skeleton(
        self,
        content: str,
        tool_calls: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """스켈론 블록을 최종 AI 응답 블록으로 교체한다."""
        if not self._thinking_block:
            self.add_ai_block(content, tool_calls)
            return

        old_block = self._thinking_block
        self._thinking_block = None

        # 새로운 블록을 생성해서 old_block 위치에 삽입
        new_block = _MessageBlock("assistant", content, tool_calls)

        idx = self._container_layout.indexOf(old_block)
        if idx >= 0:
            self._container_layout.insertWidget(idx, new_block)
            self._container_layout.removeWidget(old_block)
        else:
            # fallback: 그냥 끝에 추가
            self._insert_block(new_block)

        old_block.deleteLater()
        self._last_ai_block = new_block  # 실시간 갱신 대상 등록
        self._scroll_to_bottom()

    # --- 실시간 AI content 갱신 ---

    def update_ai_content(self, content: str) -> None:
        """현재 AI 블록의 content를 실시간 갱신한다.

        _thinking_block이 있으면 skeleton을 실제 AI 블록으로 교체하고,
        _last_ai_block이 있으면 해당 블록의 content만 갱신한다.
        둘 다 없으면 새 블록을 추가한다.
        """
        if self._thinking_block:
            # 응답 대기중 skeleton → 첫 AI content로 교체
            self.replace_thinking_skeleton(content)
            return

        block = self._last_ai_block
        if block is not None and _qt_is_valid(block) and block.isWidgetType():
            for browser in block.findChildren(QTextBrowser):
                if browser.objectName() == "content" and _qt_is_valid(browser):
                    browser.setMarkdown(content)
                    break
        else:
            self._last_ai_block = None
            self.add_ai_block(content)

    def finalize_ai_response(
        self,
        content: str,
        tool_calls: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """최종 AI 응답 표시.

        스켈론이 남아있으면 교체하고, 이미 실시간으로 표시된 블록이 있으면
        content만 최종 갱신한다. (중복 블록 방지)
        """
        if self._thinking_block:
            self.replace_thinking_skeleton(content, tool_calls)
            return

        block = self._last_ai_block
        if block is not None and _qt_is_valid(block) and block.isWidgetType():
            for browser in block.findChildren(QTextBrowser):
                if browser.objectName() == "content" and _qt_is_valid(browser):
                    browser.setMarkdown(content)
                    break
            self._scroll_to_bottom()
        else:
            self._last_ai_block = None
            self.add_ai_block(content, tool_calls)

    # --- AI 응답 블록 ---

    def add_ai_block(
        self,
        content: str,
        tool_calls: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        block = _MessageBlock("assistant", content, tool_calls)
        self._insert_block(block)
        self._last_ai_block = block
        self._scroll_to_bottom()

    # --- 세션 로드 / 초기화 ---

    def load_messages(self, messages: list[dict[str, Any]]) -> None:
        """DB에서 로드한 메시지 리스트를 화면에 렌더링."""
        self.clear()
        for msg in messages:
            role = msg.get("role", "")
            if role == "developer":
                continue  # developer 메시지는 표시하지 않음
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls")
            if role == "user":
                self.add_user_message(content)
            elif role in ("assistant", "tool"):
                # content도 tool_calls도 없는 빈 AI 메시지는 건너뜀 (공백 블록 방지)
                if not (content and content.strip()) and not tool_calls:
                    continue
                self.add_ai_block(content, tool_calls)
        self._scroll_to_bottom()

    def clear(self) -> None:
        while self._container_layout.count() > 1:
            item = self._container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._thinking_block = None
        self._last_ai_block = None

    def scroll_to_message(self, index: int) -> None:
        """N번째 메시지 블록으로 스크롤 이동. (로그 타임라인용)"""
        count = self._container_layout.count()
        # count-1 = stretch 아이템 (마지막)
        if index < 0 or index >= count - 1:
            return
        item = self._container_layout.itemAt(index)
        if item and item.widget():
            self._scroll_area.ensureWidgetVisible(item.widget())

    def set_font_size(self, size: int) -> None:
        """기존 메시지 블록의 폰트 크기를 갱신한다."""
        font = QFont()
        font.setPointSize(size)
        for browser in self.findChildren(QTextBrowser):
            if browser.objectName() == "content":
                browser.setFont(font)

    # --- 내부 헬퍼 ---

    def _insert_block(self, widget: QWidget) -> None:
        count = self._container_layout.count()
        self._container_layout.insertWidget(count - 1, widget)
        QTimer.singleShot(50, self._scroll_to_bottom)

    def _insert_widget(self, widget: QWidget) -> None:
        count = self._container_layout.count()
        self._container_layout.insertWidget(count - 1, widget)
        QTimer.singleShot(50, self._scroll_to_bottom)

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
