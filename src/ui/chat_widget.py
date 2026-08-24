"""ChatWidget — 세션 로그 표시 (접힌 툴콜 + 최종답변 + thinking skeleton)."""

import json
import logging
from datetime import datetime
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

        # Build detail text
        detail_parts = []
        if tool_input and tool_input not in ("{}", ""):
            if _looks_like_json(tool_input):
                detail_parts.append(f"Input:\n```json\n{_format_json_result(tool_input)}\n```")
            else:
                detail_parts.append(f"Input: {tool_input[:300]}")

        if tool_result:
            if _looks_like_json(tool_result):
                detail_parts.append(f"Result:\n```json\n{_format_json_result(tool_result)}\n```")
            else:
                detail_parts.append(f"Result: {tool_result[:500]}")

        if not detail_parts:
            detail_parts.append("(no args)")

        self._detail = QLabel("\n\n".join(detail_parts))
        self._detail.setStyleSheet(CODE_BLOCK_STYLE)
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
        timestamp: str = "",
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

        # 타임스탬프 (우측 하단, 작게)
        if timestamp:
            ts_lbl = QLabel(timestamp)
            ts_lbl.setStyleSheet(
                "color: #94a3b8; font-size: 9px; border: none; margin: 0px; padding: 0px;"
            )
            ts_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            layout.addWidget(ts_lbl)

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

    def _is_near_bottom(self, threshold: int = 50) -> bool:
        """스크롤이 하단에 가까우면 True (사용자가 의도적으로 올리지 않은 상태)."""
        sb = self._scroll_area.verticalScrollBar()
        if sb is None:
            return True
        return sb.maximum() - sb.value() <= threshold

    def _auto_scroll_if_needed(self) -> None:
        """스크롤이 하단 근처이면 최하단으로 내린다. (setMarkdown 직후에는 레이아웃 미반영이므로 QTimer 지연)"""
        if self._is_near_bottom():
            QTimer.singleShot(0, self._do_scroll)

    # --- 사용자 메시지 ---

    def add_user_message(self, text: str, timestamp: str = "") -> None:
        block = _MessageBlock("user", text, timestamp=timestamp)
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
        """스켈론 블록을 재사용하여 내용만 교체 (위젯 교체 없음 → 깜빡임 방지)."""
        block = self._thinking_block
        if block is None:
            self.add_ai_block(content, tool_calls)
            return

        # 기존 블록의 content browser 찾아서 텍스트 갱신
        content_browser: Optional[QTextBrowser] = None
        for browser in block.findChildren(QTextBrowser):
            if browser.objectName() == "content" and _qt_is_valid(browser):
                content_browser = browser
                break

        # tool_calls 위젯을 담을 레이아웃 (기존에 있으면 재사용, 없으면 생성)
        layout = block.layout()
        if not layout:
            return
        if content_browser is None:
            # 컨텐츠 브라우저가 없으면 새로 추가
            content_browser = _MessageBlock._make_content_browser(content)
            layout.addWidget(content_browser)

        # tool_calls 위젯 정리
        existing_tc = [
            layout.itemAt(i).widget()
            for i in range(layout.count())
            if layout.itemAt(i).widget()
            and isinstance(layout.itemAt(i).widget(), _ToolCallWidget)
        ]
        for tc_w in existing_tc:
            tc_w.deleteLater()
            layout.removeWidget(tc_w)

        if tool_calls:
            for tc in tool_calls:
                name = tc.get("name", "unknown")
                args_str = json.dumps(tc.get("args", {}), ensure_ascii=False)
                result = tc.get("result", "")
                tc_widget = _ToolCallWidget(name, args_str, result)
                layout.insertWidget(layout.indexOf(content_browser), tc_widget)

        has_content = content and content.strip()
        if has_content:
            content_browser.setMarkdown(content)
            content_browser.setVisible(True)
            # divider가 없으면 추가
            has_divider = any(
                isinstance(layout.itemAt(i).widget(), QFrame)
                and layout.itemAt(i).widget().frameShape() == QFrame.Shape.HLine
                for i in range(layout.count())
                if layout.itemAt(i).widget()
            )
            if tool_calls and not has_divider:
                divider = QFrame()
                divider.setFrameShape(QFrame.Shape.HLine)
                divider.setStyleSheet("color: #dce1e8;")
                layout.insertWidget(layout.indexOf(content_browser), divider)
        else:
            content_browser.setVisible(False)

        self._thinking_block = None
        self._last_ai_block = block
        self._scroll_to_bottom()

    # --- 실시간 AI content 갱신 ---

    def update_ai_content(self, content: str) -> None:
        """현재 AI 블록의 content를 실시간 갱신한다.

        _thinking_block이 있으면 skeleton을 실제 AI 블록으로 교체하고,
        _last_ai_block이 있으면 해당 블록의 content만 갱신한다.
        둘 다 없으면 새 블록을 추가한다.

        content 갱신 후 스크롤이 하단 근처이면 자동으로 최하단으로 이동한다.
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
                    self._auto_scroll_if_needed()
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
        timestamp: str = "",
    ) -> None:
        block = _MessageBlock("assistant", content, tool_calls, timestamp=timestamp)
        self._insert_block(block)
        self._last_ai_block = block
        self._scroll_to_bottom()

    # --- 세션 로드 / 초기화 ---

    def load_messages(self, messages: list[dict[str, Any]]) -> None:
        """DB에서 로드한 메시지 리스트를 UI 형태로 렌더링.

        DB에는 role='tool' 메시지가 별도 row로 저장되어 있지만,
        UI에서는 _ToolCallWidget으로 접어서 assistant 블록 안에 표시한다.
        """
        self.clear()
        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get("role", "")
            if role == "developer":
                i += 1
                continue

            content = msg.get("content", "")
            ts = msg.get("created_at", "")
            timestamp = ""
            if ts:
                try:
                    if isinstance(ts, str):
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00").replace("T", " "))
                    else:
                        dt = ts
                    timestamp = dt.strftime("%m-%d %H:%M")
                except (ValueError, AttributeError):
                    timestamp = str(ts)[:5]

            if role == "user":
                self.add_user_message(content, timestamp=timestamp)
                i += 1
                continue

            if role == "assistant":
                tool_calls_json = msg.get("tool_calls", "")
                tool_calls: list[dict[str, Any]] = []

                # Collect following tool messages by tool_call_id
                tool_msg_map: dict[str, str] = {}
                j = i + 1
                while j < len(messages) and messages[j].get("role") == "tool":
                    tc_id = messages[j].get("tool_call_id", "")
                    if tc_id:
                        tool_msg_map[tc_id] = messages[j].get("content", "")
                    j += 1

                if tool_calls_json:
                    try:
                        tc_list = json.loads(tool_calls_json)
                        for tc in tc_list:
                            tc_id = tc.get("id", "")
                            result = tool_msg_map.get(tc_id, "")
                            tool_calls.append({
                                "name": tc.get("name", "tool"),
                                "args": tc.get("args", {}),
                                "result": result,
                            })
                    except json.JSONDecodeError:
                        pass

                if not tool_calls:
                    # Fallback: infer from tool message content
                    for tc_id, tc_content in tool_msg_map.items():
                        name = "tool"
                        result = tc_content
                        if ":" in tc_content:
                            parts = tc_content.split(":", 1)
                            name = parts[0].strip()
                            rest = parts[1].strip() if len(parts) > 1 else ""
                            if "\n" in rest:
                                result = rest.split("\n", 1)[1].strip()
                        tool_calls.append({"name": name, "args": {}, "result": result})

                has_content = bool(content and content.strip())
                if not has_content and not tool_calls:
                    i = j
                    continue

                self.add_ai_block(content, tool_calls if tool_calls else None, timestamp=timestamp)
                i = j
                continue

            if role == "tool":
                self.add_ai_block(f"🛠 {content}", timestamp=timestamp)
                i += 1
                continue

            i += 1

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
