"""PromptInput — 프롬프트 입력 위젯 (입력창 + 전송/중지/🔄 세션초기화)."""

import logging

logger = logging.getLogger("psjba.ui.prompt_input")

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QTextEdit, QVBoxLayout, QWidget

logger = logging.getLogger("psjba.ui.prompt_input")


class PromptInput(QWidget):
    """프롬프트 입력 + 전송/정지/🔄 세션초기화.

    - Enter: 전송, Shift+Enter: 줄바꿈
    - Agent 실행 중에도 입력 가능 (intervention 큐)
    - 진행 중이면 정지 버튼 활성화
    """

    submitted = Signal(str)
    stopped = Signal()
    session_reset = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._is_running = False
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._text_edit = QTextEdit()
        self._text_edit.setPlaceholderText("브라우저에게 지시할 내용... (Enter: 전송, Shift+Enter: 줄바꿈)")
        self._text_edit.setMaximumHeight(120)
        self._text_edit.setAcceptRichText(False)
        font = QFont()
        font.setPointSize(12)
        self._text_edit.setFont(font)
        self._text_edit.setStyleSheet(
            "QTextEdit { background-color: #16213e; color: #e0e0e0; "
            "border: 1px solid #2a2a4a; border-radius: 8px; padding: 8px; "
            "selection-background-color: #0f3460; }"
            "QTextEdit:focus { border: 1px solid #4FC3F7; }"
        )
        self._text_edit.installEventFilter(self)
        layout.addWidget(self._text_edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._reset_btn = QPushButton("🔄")
        self._reset_btn.setFixedSize(32, 32)
        self._reset_btn.setToolTip("세션 초기화")
        self._reset_btn.clicked.connect(self.session_reset.emit)
        btn_layout.addWidget(self._reset_btn)

        # 정지 버튼 — 전송 버튼 왼쪽, 진행 중일 때만 활성
        self._stop_btn = QPushButton("■")
        self._stop_btn.setFixedSize(32, 32)
        self._stop_btn.setToolTip("실행 정지")
        self._stop_btn.setStyleSheet(
            "QPushButton { background-color: #4a1a1a; color: #ff6b6b; "
            "border: 1px solid #ff6b6b; border-radius: 6px; font-weight: bold; font-size: 14px; }"
            "QPushButton:hover { background-color: #5a2a2a; }"
            "QPushButton:disabled { background-color: #1a1a2e; color: #444; "
            "border: 1px solid #2a2a4a; }"
        )
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        btn_layout.addWidget(self._stop_btn)

        self._send_btn = QPushButton("전송")
        self._send_btn.setFixedWidth(80)
        self._send_btn.setStyleSheet(
            "QPushButton { background-color: #0f3460; color: white; "
            "border: 1px solid #4FC3F7; border-radius: 6px; padding: 6px; "
            "font-weight: bold; }"
            "QPushButton:hover { background-color: #1e2d50; }"
            "QPushButton:disabled { background-color: #16213e; color: #555; "
            "border: 1px solid #2a2a4a; }"
        )
        self._send_btn.clicked.connect(self._on_send)
        btn_layout.addWidget(self._send_btn)

        layout.addLayout(btn_layout)

    def eventFilter(self, obj, event):
        if obj == self._text_edit and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Return:
                if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
                    cursor = self._text_edit.textCursor()
                    cursor.insertText("\n")
                    return True
                elif event.modifiers() == Qt.KeyboardModifier.NoModifier:
                    self._on_send()
                    return True
        return super().eventFilter(obj, event)

    def _on_send(self) -> None:
        logger.info("Send button clicked")
        text = self._text_edit.toPlainText().strip()
        if not text:
            return
        self._text_edit.clear()
        self.submitted.emit(text)

    def _on_stop(self) -> None:
        logger.info("Stop button clicked")
        self.stopped.emit()

    def set_running(self, running: bool) -> None:
        """Agent 실행 상태에 따라 UI 전환."""
        self._is_running = running
        self._stop_btn.setEnabled(running)
        self._reset_btn.setEnabled(not running)
        # 전송 버튼은 항상 활성화 (비어있을 때만 전송 안 함)
        # 텍스트 에디트는 항상 편집 가능 (intervention 입력)

    def clear(self) -> None:
        self._text_edit.clear()

    def get_text(self) -> str:
        return self._text_edit.toPlainText().strip()
