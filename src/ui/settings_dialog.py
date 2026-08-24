"""SettingsDialog — 설정/CRON/로그 3탭 다이얼로그 (탭 클래스 위임)."""

import asyncio
import logging
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# ─── NoWheel 믹스인 ────────────────────────────────────────────────


class NoWheelMixin:
    """QSpinBox / QDoubleSpinBox / QComboBox 용 믹스인: 휠 이벤트 무시."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelSpinBox(NoWheelMixin, QSpinBox):
    pass


class NoWheelDoubleSpinBox(NoWheelMixin, QDoubleSpinBox):
    pass


class NoWheelComboBox(NoWheelMixin, QComboBox):
    pass


# ─── 상수 ──────────────────────────────────────────────────────────

LOG_LEVELS = {"warning": "Warning", "info": "Info", "debug": "Debug"}

FIELD_TOOLTIPS: dict[str, str] = {
    "api_key": "LLM API 키 (UI에서 입력, config.ini에는 암호화 저장)",
    "model": "사용할 모델명 (기본값: qwen/qwen3.6-27b)",
    "url": "LLM API 엔드포인트 URL",
    "max_tokens": "최대 토큰 수",
    "temperature": "생성 온도",
    "timeout": "API 타임아웃 (초)",
    "host": "CDP 호스트",
    "port": "CDP 포트",
    "connection_timeout": "CDP 연결 타임아웃 (초, 기본 1.0)",
    "path": "Chrome 실행 파일 전체 경로",
    "user_data_dir": "사용자 프로필 디렉토리 (선택)",
    "headless": "헤드리스 모드",
    "sqlite_path": "SQLite DB 파일 경로",
    "workspace_dir": "프로그램이 읽고 쓸 수 있는 작업 폴더",
    "sliding_window_size": "슬라이딩 윈도우 크기 (user 메시지 기준)",
    "cron_timeout": "크론 작업별 최대 실행 시간 (초)",
    "cron_tool_calls": "크론 최대 tool call 횟수",
    "font_size": "폰트 크기",
    "max_chat_lines": "대화 로그 최대 줄 수",
    "logging_level": "로그 레벨 (warning=기본, info=상세, debug=디버그)",
    "logging_path": "비어있으면 콘솔 출력, 입력하면 파일에만 기록",
}

logger = logging.getLogger("psjba.ui.settings_dialog")


# ─── 비동기 헬퍼 ───────────────────────────────────────────────────


def _run_async(coro: Any) -> Any:
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        _run_async_task(coro)
        return None
    else:
        return loop.run_until_complete(coro)


def _run_async_task(
    coro: Any,
    callback: Optional[Callable[[Any], None]] = None,
    task_store: Optional[list] = None,
) -> None:
    """이미 실행 중인 이벤트 루프에서 비동기 태스크를 안전하게 스케줄링.

    qasync의 call_soon은 nested task wakeup 충돌을 유발하므로
    asyncio.create_task를 직접 사용한다.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return

    if not loop.is_running():
        return

    try:
        task = asyncio.create_task(coro)
    except (RuntimeError, ValueError):
        return

    if task_store is not None:
        task_store.append(task)
        task.add_done_callback(
            lambda t: task_store.remove(t) if t in task_store else None
        )
    if callback:
        task.add_done_callback(
            lambda t: callback(t.result() if not t.exception() else None)
        )


# ─── UI 헬퍼 ───────────────────────────────────────────────────────


def _make_tooltip_icon(text: str) -> QLabel:
    lbl = QLabel("?")
    lbl.setFixedSize(18, 18)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        "QLabel { background-color: #dce1e8; color: #4f46e5; border-radius: 9px; "
        "font-size: 10px; font-weight: bold; }"
        "QLabel:hover { background-color: #e8ecf0; }"
    )
    lbl.setCursor(Qt.CursorShape.WhatsThisCursor)
    lbl.setToolTip(text)
    return lbl


def _make_section(title: str, parent: Optional[QWidget] = None) -> QFrame:
    """섹션 그룹을 생성하여 QFrame을 반환한다."""
    frame = QFrame(parent)
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    layout = QVBoxLayout(frame)
    lbl = QLabel(title)
    lbl.setStyleSheet("font-weight: bold; font-size: 14px; border: none; color: #1e293b;")
    layout.addWidget(lbl)
    return frame


def _row(
    label_text: str, widget_or_layout: Any, tooltip_key: str = ""
) -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(label_text)
    lbl.setFixedWidth(150)
    lbl.setStyleSheet(
        "border: none; color: #1e293b; font-size: 13px; "
        "background-color: #f0f2f5; padding: 2px 6px; border-radius: 4px;"
    )
    row.addWidget(lbl)
    if tooltip_key and tooltip_key in FIELD_TOOLTIPS:
        row.addWidget(_make_tooltip_icon(FIELD_TOOLTIPS[tooltip_key]))
    if isinstance(widget_or_layout, QHBoxLayout):
        row.addLayout(widget_or_layout)
    else:
        row.addWidget(widget_or_layout)
    return row


def _detail_row(label: str, value: str) -> QWidget:
    row = QWidget()
    row.setStyleSheet("border: none;")
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 2, 0, 2)
    lbl = QLabel(label)
    lbl.setStyleSheet("font-weight: bold; border: none; color: #475569; font-size: 12px;")
    lbl.setFixedWidth(110)
    rl.addWidget(lbl)
    val = QLabel(value)
    val.setStyleSheet("border: none; color: #1e293b; font-size: 12px;")
    rl.addWidget(val)
    rl.addStretch()
    return row


def _make_pagination(prev_fn: Any, next_fn: Any) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addStretch()
    prev_btn = QPushButton("◀")
    prev_btn.setFixedSize(30, 30)
    prev_btn.clicked.connect(prev_fn)
    row.addWidget(prev_btn)
    page_lbl = QLabel("1 / 1")
    page_lbl.setStyleSheet("border: none; color: #94a3b8;")
    row.addWidget(page_lbl)
    next_btn = QPushButton("▶")
    next_btn.setFixedSize(30, 30)
    next_btn.clicked.connect(next_fn)
    row.addWidget(next_btn)
    row.addStretch()
    row._page_lbl = page_lbl  # type: ignore
    row._prev_btn = prev_btn  # type: ignore
    row._next_btn = next_btn  # type: ignore
    return row


def _update_pagination(row: QHBoxLayout, current: int, total: int) -> None:
    row._page_lbl.setText(f"{current} / {total}")  # type: ignore
    row._prev_btn.setEnabled(current > 1)  # type: ignore
    row._next_btn.setEnabled(current < total)  # type: ignore


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count() > 1:
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.deleteLater()


# ─── 메인 다이얼로그 ───────────────────────────────────────────────

from src.config import Config
from src.database import Database
from src.ui.settings_tab_settings import SettingsTab
from src.ui.settings_tab_cron import CronTab
from src.ui.settings_tab_log import LogTab


class SettingsDialog(QDialog):
    """설정/CRON/로그 탭이 있는 메인 설정 다이얼로그."""

    config_saved = Signal()

    def __init__(
        self,
        config: Config,
        db: Database,
        parent: Optional[QWidget] = None,
        manager: Any = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._db = db
        self._manager = manager

        self._settings_tab = SettingsTab(self, config)
        self._cron_tab = CronTab(self, config, db, manager)
        self._log_tab = LogTab(self, config, db)

        self._init_ui()
        self._load_settings()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._cron_tab.refresh()

    def _init_ui(self) -> None:
        self.setWindowTitle("설정")
        self.setMinimumSize(600, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._settings_tab, "설정")
        self._tabs.addTab(self._cron_tab, "CRON")
        self._tabs.addTab(self._log_tab, "로그")

    def _apply_theme(self) -> None:
        self.setStyleSheet("""
            QDialog { background-color: #f0f2f5; color: #1e293b; }
            QToolTip { background-color: #ffffff; color: #1e293b;
                border: 1px solid #dce1e8; border-radius: 6px;
                padding: 6px 10px; font-size: 12px; }
            QFrame { background-color: #ffffff; border: 1px solid #dce1e8;
                border-radius: 10px; padding: 14px; }
            QTabWidget::pane { background-color: #f0f2f5; border: none;
                border-top: 1px solid #dce1e8; }
            QTabBar::tab { background-color: #e8ecf0; color: #64748b;
                padding: 8px 20px; border: none; border-top-left-radius: 8px;
                border-top-right-radius: 8px; margin-right: 2px; font-size: 12px; }
            QTabBar::tab:selected { background-color: #f0f2f5; color: #4f46e5;
                border-bottom: 2px solid #4f46e5; }
            QTabBar::tab:hover:!selected { background-color: #dde3ea; color: #475569; }
            QLabel { color: #1e293b; background-color: transparent; }
            QLineEdit { background-color: #ffffff; color: #1e293b;
                border: 1px solid #dce1e8; border-radius: 6px;
                padding: 6px 10px; font-size: 13px;
                selection-background-color: #e0e7ff; }
            QLineEdit:focus { border: 1px solid #4f46e5; }
            QComboBox { background-color: #ffffff; color: #1e293b;
                border: 1px solid #dce1e8; border-radius: 6px;
                padding: 6px 10px; font-size: 13px; }
            QComboBox:focus { border: 1px solid #4f46e5; }
            QComboBox QAbstractItemView { background-color: #ffffff;
                color: #1e293b; border: 1px solid #dce1e8; border-radius: 6px;
                selection-background-color: #e0e7ff; padding: 4px; }
            QSpinBox, QDoubleSpinBox { background-color: #ffffff; color: #1e293b;
                border: 1px solid #dce1e8; border-radius: 6px;
                padding: 4px 8px; font-size: 13px; }
            QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #4f46e5; }
            QPushButton { background-color: #ffffff; color: #1e293b;
                border: 1px solid #dce1e8; border-radius: 6px;
                padding: 6px 14px; font-size: 13px; }
            QPushButton:hover { background-color: #f0f2f5; border: 1px solid #4f46e5; }
            QPushButton:pressed { background-color: #e0e7ff; }
            QCheckBox { color: #1e293b; background-color: transparent; spacing: 6px; }
            QCheckBox::indicator { width: 18px; height: 18px; border-radius: 4px;
                border: 1px solid #dce1e8; background-color: #ffffff; }
            QCheckBox::indicator:checked { background-color: #4f46e5;
                border: 1px solid #4f46e5; }
            QScrollArea { background-color: transparent; border: none; }
            QScrollBar:vertical { background-color: transparent; width: 8px; margin: 0; }
            QScrollBar::handle:vertical { background-color: #cbd5e1;
                border-radius: 4px; min-height: 30px; }
            QScrollBar::handle:vertical:hover { background-color: #94a3b8; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QTableWidget { background-color: #ffffff; color: #1e293b;
                border: 1px solid #dce1e8; border-radius: 8px;
                gridline-color: #e8ecf0; }
            QTableWidget::item { padding: 6px 10px; }
            QTableWidget::item:selected { background-color: #e0e7ff; color: #4f46e5; }
            QHeaderView::section { background-color: #e8ecf0; color: #1e293b;
                border: none; border-bottom: 1px solid #dce1e8;
                padding: 8px 10px; font-weight: bold; }
            QMessageBox { background-color: #f0f2f5; color: #1e293b; }
            QMessageBox QLabel { color: #1e293b; background-color: transparent; }
            QMessageBox QPushButton { background-color: #ffffff; color: #1e293b;
                border: 1px solid #dce1e8; border-radius: 6px;
                padding: 6px 20px; min-width: 70px; font-size: 13px; }
            QMessageBox QPushButton:hover { background-color: #f0f2f5;
                border: 1px solid #4f46e5; }
        """)

    # ─── 설정 로드/저장 ───────────────────────────────────────────

    def _load_settings(self) -> None:
        self._settings_tab.load_settings()
        self._cron_tab.refresh()
        self._log_tab.refresh()
        logger.debug("Settings loaded")

    def _save(self) -> None:
        api_key, model = self._settings_tab.save_settings()
        if api_key is None or model is None:
            return  # validation failed

        self._config.save()
        self.config_saved.emit()
        self.accept()