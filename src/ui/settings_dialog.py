"""SettingsDialog — 설정/CRON/로그 3탭 다이얼로그 (드릴다운 포함)."""

import asyncio
import logging

logger = logging.getLogger("psjba.ui.settings_dialog")
from datetime import datetime
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class NoWheelMixin:
    """QSpinBox / QDoubleSpinBox 용 믹스인: 휠 이벤트 무시."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class NoWheelSpinBox(NoWheelMixin, QSpinBox):
    pass


class NoWheelDoubleSpinBox(NoWheelMixin, QDoubleSpinBox):
    pass


from src.config import Config
from src.database import Database

LANGUAGES = {"ko": "한국어", "en": "English"}
LOG_LEVELS = {"disabled": "비활성화", "info": "Info", "debug": "Debug"}

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
    "language": "UI 표시 언어",
    "font_size": "폰트 크기",
    "max_chat_lines": "대화 로그 최대 줄 수",
    "logging_level": "로그 레벨 (disabled=파일 로깅 비활성화)",
    "logging_path": "로그 파일 경로",
}


def _run_async(coro: Any) -> Any:
    """qasync 이벤트 루프에서 비동기 코루틴을 실행.

    - 이벤트 루프가 실행 중이면: ``_run_async_task()`` 로 태스크를 예약하고 ``None`` 을 반환한다.
    - 이벤트 루프가 실행 중이 아니면: ``loop.run_until_complete()`` 로 동기 실행.
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        # 루프 실행 중: create_task 로 예약 (결과는 콜백으로 처리)
        _run_async_task(coro)
        return None
    else:
        return loop.run_until_complete(coro)


def _run_async_task(coro: Any, callback: Optional[Callable[[Any], None]] = None,
                    task_store: Optional[list] = None) -> None:
    """실행 중인 이벤트 루프에 코루틴을 태스크로 예약한다.

    callback이 있으면 완료 시 호출된다 (task.result() 전달).
    task_store가 있으면 태스크 참조를 저장하여 GC를 방지한다.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            task = asyncio.ensure_future(coro)
            if task_store is not None:
                task_store.append(task)
                task.add_done_callback(task_store.remove)
            if callback:
                task.add_done_callback(lambda t: callback(t.result() if not t.exception() else None))
    except RuntimeError:
        pass


def _make_tooltip_icon(text: str) -> QLabel:
    lbl = QLabel("?")
    lbl.setFixedSize(18, 18)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(
        "QLabel { background-color: #2a2a4a; color: #4FC3F7; border-radius: 9px; "
        "font-size: 10px; font-weight: bold; }"
        "QLabel:hover { background-color: #3a3a5a; }"
    )
    lbl.setCursor(Qt.CursorShape.WhatsThisCursor)
    lbl.setToolTip(text)
    return lbl


class SettingsDialog(QDialog):
    """설정/CRON/로그 탭이 있는 메인 설정 다이얼로그."""

    config_saved = Signal()

    def __init__(self, config: Config, db: Database, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = config
        self._db = db
        self._cron_l2_page: int = 1
        self._cron_l2_total_pages: int = 1
        self._cron_l2_cron_job_id: Optional[str] = None
        self._log_l1_page: int = 1
        self._log_l1_total_pages: int = 1
        self._cron_l3_time_list: list[dict] = []
        self._log_l2_time_list: list[dict] = []
        self._async_tasks: list[Any] = []
        self._init_ui()
        self._load_settings()

    def _init_ui(self) -> None:
        self.setWindowTitle("설정")
        self.setMinimumSize(600, 500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._tabs = QTabWidget()
        layout.addWidget(self._tabs)

        self._tabs.addTab(self._build_settings_tab(), "설정")
        self._tabs.addTab(self._build_cron_tab(), "CRON")
        self._tabs.addTab(self._build_log_tab(), "로그")

    def _apply_theme(self) -> None:
        """단일 고정 다크 테마 적용."""
        self.setStyleSheet("""
            QDialog {
                background-color: #1a1a2e;
                color: #e0e0e0;
            }
            QToolTip {
                background-color: #16213e;
                color: #e0e0e0;
                border: 1px solid #0f3460;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 12px;
            }
            QFrame {
                background-color: #1e1e3a;
                border: 1px solid #2a2a4a;
                border-radius: 10px;
                padding: 14px;
            }
            QTabWidget::pane {
                background-color: #1a1a2e;
                border: none;
                border-top: 1px solid #2a2a4a;
            }
            QTabBar::tab {
                background-color: #1e1e3a;
                color: #888;
                padding: 8px 20px;
                border: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 2px;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background-color: #1a1a2e;
                color: #4FC3F7;
                border-bottom: 2px solid #4FC3F7;
            }
            QTabBar::tab:hover:!selected {
                background-color: #252545;
                color: #aaa;
            }
            QLabel {
                color: #e0e0e0;
                background-color: transparent;
            }
            QLineEdit {
                background-color: #16213e;
                color: #e0e0e0;
                border: 1px solid #2a2a4a;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
                selection-background-color: #0f3460;
            }
            QLineEdit:focus {
                border: 1px solid #4FC3F7;
            }
            QComboBox {
                background-color: #16213e;
                color: #e0e0e0;
                border: 1px solid #2a2a4a;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }
            QComboBox:focus {
                border: 1px solid #4FC3F7;
            }
            QComboBox::drop-down {
                border: none;
                padding-right: 8px;
            }
            QComboBox::down-arrow {
                image: none;
                width: 0;
            }
            QComboBox QAbstractItemView {
                background-color: #16213e;
                color: #e0e0e0;
                border: 1px solid #2a2a4a;
                border-radius: 6px;
                selection-background-color: #0f3460;
                padding: 4px;
            }
            QSpinBox, QDoubleSpinBox {
                background-color: #16213e;
                color: #e0e0e0;
                border: 1px solid #2a2a4a;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 13px;
            }
            QSpinBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #4FC3F7;
            }
            QPushButton {
                background-color: #16213e;
                color: #e0e0e0;
                border: 1px solid #2a2a4a;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #1e2d50;
                border: 1px solid #4FC3F7;
            }
            QPushButton:pressed {
                background-color: #0f3460;
            }
            QCheckBox {
                color: #e0e0e0;
                background-color: transparent;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid #2a2a4a;
                background-color: #16213e;
            }
            QCheckBox::indicator:checked {
                background-color: #4FC3F7;
                border: 1px solid #4FC3F7;
            }
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background-color: transparent;
                width: 8px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background-color: #2a2a4a;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #3a3a5a;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QTableWidget {
                background-color: #1a1a2e;
                color: #e0e0e0;
                border: 1px solid #2a2a4a;
                border-radius: 8px;
                gridline-color: #2a2a4a;
            }
            QTableWidget::item {
                padding: 6px 10px;
            }
            QTableWidget::item:selected {
                background-color: #0f3460;
                color: #4FC3F7;
            }
            QHeaderView::section {
                background-color: #1e1e3a;
                color: #e0e0e0;
                border: none;
                border-bottom: 1px solid #2a2a4a;
                padding: 8px 10px;
                font-weight: bold;
            }
            QMessageBox {
                background-color: #1a1a2e;
                color: #e0e0e0;
            }
            QMessageBox QLabel {
                color: #e0e0e0;
                background-color: transparent;
            }
            QMessageBox QPushButton {
                background-color: #16213e;
                color: #e0e0e0;
                border: 1px solid #2a2a4a;
                border-radius: 6px;
                padding: 6px 20px;
                min-width: 70px;
                font-size: 13px;
            }
            QMessageBox QPushButton:hover {
                background-color: #1e2d50;
                border: 1px solid #4FC3F7;
            }
        """)

    # ─── 설정 탭 ────────────────────────────────────────────────

    def _build_settings_tab(self) -> QWidget:
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        page = QWidget()
        main_layout = QVBoxLayout(page)

        llm_box = self._make_section("LLM", parent=page)
        llm_layout = llm_box.layout()
        self._llm_url = QLineEdit()
        self._llm_model = QLineEdit()
        self._llm_api_key = QLineEdit()
        self._llm_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._llm_api_key.setPlaceholderText("API 키를 입력하세요")
        self._llm_max_tokens = NoWheelSpinBox()
        self._llm_max_tokens.setRange(256, 128000)
        self._llm_temperature = NoWheelDoubleSpinBox()
        self._llm_temperature.setRange(0.0, 2.0)
        self._llm_temperature.setSingleStep(0.1)
        self._llm_timeout = NoWheelSpinBox()
        self._llm_timeout.setRange(10, 300)

        api_key_row = QHBoxLayout()
        api_key_row.addWidget(self._llm_api_key)
        toggle_btn = QPushButton("표시")
        toggle_btn.setFixedWidth(50)
        toggle_btn.clicked.connect(lambda: self._toggle_api_key(toggle_btn))
        api_key_row.addWidget(toggle_btn)

        llm_layout.addLayout(self._row("url:", self._llm_url, "url"))
        llm_layout.addLayout(self._row("model:", self._llm_model, "model"))
        llm_layout.addLayout(self._row("api_key:", api_key_row, "api_key"))
        llm_layout.addLayout(self._row("max_tokens:", self._llm_max_tokens, "max_tokens"))
        llm_layout.addLayout(self._row("temperature:", self._llm_temperature, "temperature"))
        llm_layout.addLayout(self._row("timeout:", self._llm_timeout, "timeout"))
        main_layout.addWidget(llm_box)

        cdp_box = self._make_section("CDP", parent=page)
        cdp_layout = cdp_box.layout()
        self._cdp_host = QLineEdit()
        self._cdp_port = NoWheelSpinBox()
        self._cdp_port.setRange(1024, 65535)
        self._cdp_timeout = NoWheelDoubleSpinBox()
        self._cdp_timeout.setRange(0.5, 30.0)
        self._cdp_timeout.setSingleStep(0.5)
        cdp_layout.addLayout(self._row("host:", self._cdp_host, "host"))
        cdp_layout.addLayout(self._row("port:", self._cdp_port, "port"))
        cdp_layout.addLayout(self._row("connection_timeout:", self._cdp_timeout, "connection_timeout"))
        main_layout.addWidget(cdp_box)

        chrome_box = self._make_section("Chrome", parent=page)
        chrome_layout = chrome_box.layout()
        self._chrome_path = QLineEdit()
        chrome_path_row = QHBoxLayout()
        chrome_path_row.addWidget(self._chrome_path)
        browse_btn = QPushButton("찾아보기")
        browse_btn.clicked.connect(self._browse_chrome_path)
        chrome_path_row.addWidget(browse_btn)
        self._chrome_user_data = QLineEdit()
        user_data_row = QHBoxLayout()
        user_data_row.addWidget(self._chrome_user_data)
        browse_ud_btn = QPushButton("찾아보기")
        browse_ud_btn.clicked.connect(self._browse_user_data)
        user_data_row.addWidget(browse_ud_btn)
        self._chrome_headless = QCheckBox("헤드리스 모드")
        chrome_layout.addLayout(self._row("path:", chrome_path_row, "path"))
        chrome_layout.addLayout(self._row("user_data_dir:", user_data_row, "user_data_dir"))
        chrome_layout.addWidget(self._chrome_headless)
        main_layout.addWidget(chrome_box)

        sqlite_box = self._make_section("SQLite", parent=page)
        sqlite_layout = sqlite_box.layout()
        self._sqlite_path = QLineEdit()
        self._sqlite_path.setReadOnly(True)
        sqlite_layout.addLayout(self._row("sqlite_path:", self._sqlite_path, "sqlite_path"))
        main_layout.addWidget(sqlite_box)

        ws_box = self._make_section("Workspace", parent=page)
        ws_layout = ws_box.layout()
        self._ws_dir = QLineEdit()
        self._ws_dir.setReadOnly(True)
        ws_layout.addLayout(self._row("workspace_dir:", self._ws_dir, "workspace_dir"))
        main_layout.addWidget(ws_box)

        agent_box = self._make_section("Agent", parent=page)
        agent_layout = agent_box.layout()
        self._agent_window = NoWheelSpinBox()
        self._agent_window.setRange(10, 200)
        self._agent_cron_timeout = NoWheelSpinBox()
        self._agent_cron_timeout.setRange(30, 3600)
        agent_layout.addLayout(self._row("sliding_window_size:", self._agent_window, "sliding_window_size"))
        agent_layout.addLayout(self._row("cron_timeout:", self._agent_cron_timeout, "cron_timeout"))
        main_layout.addWidget(agent_box)

        ui_box = self._make_section("UI", parent=page)
        ui_layout = ui_box.layout()
        self._ui_lang = QComboBox()
        for k, v in LANGUAGES.items():
            self._ui_lang.addItem(v, k)
        self._ui_font_size = NoWheelSpinBox()
        self._ui_font_size.setRange(8, 32)
        self._ui_max_chat = NoWheelSpinBox()
        self._ui_max_chat.setRange(100, 10000)
        ui_layout.addLayout(self._row("language:", self._ui_lang, "language"))
        ui_layout.addLayout(self._row("font_size:", self._ui_font_size, "font_size"))
        ui_layout.addLayout(self._row("max_chat_lines:", self._ui_max_chat, "max_chat_lines"))
        main_layout.addWidget(ui_box)

        log_box = self._make_section("Logging", parent=page)
        log_layout = log_box.layout()
        self._log_level_combo = QComboBox()
        for k, v in LOG_LEVELS.items():
            self._log_level_combo.addItem(v, k)
        self._log_path = QLineEdit()
        log_layout.addLayout(self._row("level:", self._log_level_combo, "logging_level"))
        log_layout.addLayout(self._row("path:", self._log_path, "logging_path"))
        main_layout.addWidget(log_box)

        main_layout.addStretch()
        scroll.setWidget(page)
        outer_layout.addWidget(scroll)

        hint = QLabel("API 키는 MAC 주소 기반으로 암호화되어 저장됩니다")
        hint.setStyleSheet("color: #666; font-size: 11px; padding: 4px 0;")
        outer_layout.addWidget(hint)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_btn = QPushButton("💾 저장")
        save_btn.setStyleSheet(
            "QPushButton { background-color: #0f3460; color: white; "
            "border: 1px solid #4FC3F7; border-radius: 8px; "
            "padding: 8px 24px; font-weight: bold; font-size: 13px; }"
            "QPushButton:hover { background-color: #1e2d50; }"
        )
        save_btn.clicked.connect(self._save)
        save_row.addWidget(save_btn)
        outer_layout.addLayout(save_row)

        return outer

    def _toggle_api_key(self, btn: QPushButton) -> None:
        if self._llm_api_key.echoMode() == QLineEdit.EchoMode.Password:
            self._llm_api_key.setEchoMode(QLineEdit.EchoMode.Normal)
            btn.setText("숨기기")
        else:
            self._llm_api_key.setEchoMode(QLineEdit.EchoMode.Password)
            btn.setText("표시")

    def _browse_chrome_path(self) -> None:
        logger.debug("Chrome path browse")
        path, _ = QFileDialog.getOpenFileName(self, "Chrome 실행 파일 선택")
        if path:
            self._chrome_path.setText(path)

    def _browse_user_data(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "User Data 폴더 선택")
        if path:
            self._chrome_user_data.setText(path)

    # ─── CRON 탭 (3단계 드릴다운) ────────────────────────────────

    def _build_cron_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self._cron_stacked = QStackedWidget()
        layout.addWidget(self._cron_stacked)

        self._cron_stacked.addWidget(self._build_cron_level1())
        self._cron_stacked.addWidget(self._build_cron_level2())
        self._cron_stacked.addWidget(self._build_cron_level3())

        self._cron_stacked.setCurrentIndex(0)
        return page

    def _build_cron_level1(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._cron_card_container = QWidget()
        self._cron_card_layout = QVBoxLayout(self._cron_card_container)
        self._cron_card_layout.setContentsMargins(8, 8, 8, 8)
        self._cron_card_layout.setSpacing(8)
        self._cron_card_layout.addStretch()
        scroll.setWidget(self._cron_card_container)
        layout.addWidget(scroll)

        return w

    def _build_cron_level2(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        header = QHBoxLayout()
        back_btn = QPushButton("◀ 크론 목록으로")
        back_btn.clicked.connect(lambda: self._cron_stacked.setCurrentIndex(0))
        header.addWidget(back_btn)
        self._cron_l2_title = QLabel()
        self._cron_l2_title.setStyleSheet("font-weight: bold; font-size: 13px;")
        header.addWidget(self._cron_l2_title)
        header.addStretch()
        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._cron_session_container = QWidget()
        self._cron_session_layout = QVBoxLayout(self._cron_session_container)
        self._cron_session_layout.setContentsMargins(8, 8, 8, 8)
        self._cron_session_layout.setSpacing(4)
        self._cron_session_layout.addStretch()
        scroll.setWidget(self._cron_session_container)
        layout.addWidget(scroll)

        self._cron_l2_pagination = self._make_pagination(
            self._on_cron_l2_prev, self._on_cron_l2_next
        )
        layout.addLayout(self._cron_l2_pagination)
        return w

    def _build_cron_level3(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        header = QHBoxLayout()
        back_btn = QPushButton("◀ 세션 목록으로")
        back_btn.clicked.connect(lambda: self._cron_stacked.setCurrentIndex(1))
        header.addWidget(back_btn)
        header.addStretch()
        layout.addLayout(header)

        split = QHBoxLayout()

        self._cron_l3_time_container = QWidget()
        self._cron_l3_time_container.setMaximumWidth(150)
        self._cron_l3_time_layout = QVBoxLayout(self._cron_l3_time_container)
        self._cron_l3_time_layout.setContentsMargins(4, 4, 4, 4)
        self._cron_l3_time_layout.setSpacing(2)
        self._cron_l3_time_layout.addStretch()
        time_scroll = QScrollArea()
        time_scroll.setWidgetResizable(True)
        time_scroll.setWidget(self._cron_l3_time_container)
        time_scroll.setMaximumWidth(160)
        split.addWidget(time_scroll)

        from src.ui.chat_widget import ChatWidget

        self._cron_l3_chat = ChatWidget()
        split.addWidget(self._cron_l3_chat)

        layout.addLayout(split)
        return w

    def _on_cron_l2_prev(self) -> None:
        if self._cron_l2_page > 1:
            self._cron_l2_page -= 1
            self._load_cron_l2_sessions()

    def _on_cron_l2_next(self) -> None:
        if self._cron_l2_page < self._cron_l2_total_pages:
            self._cron_l2_page += 1
            self._load_cron_l2_sessions()

    def _load_cron_level1(self) -> None:
        self._clear_layout(self._cron_card_layout)

        async def _fetch_and_render() -> None:
            jobs = await self._db.list_cron_jobs()
            self._clear_layout(self._cron_card_layout)
            if not jobs:
                # 빈 상태 메시지
                empty_lbl = QLabel(
                    '크론 작업이 없습니다.\n\n'
                    '에이전트에게 "매일 오전 9시에 뉴스 확인해줘" 같은 '
                    '프롬프트를 입력하면 자동으로 추가됩니다.'
                )
                empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                empty_lbl.setWordWrap(True)
                empty_lbl.setStyleSheet(
                    "color: #666; border: none; font-size: 13px; padding: 40px;"
                )
                count = self._cron_card_layout.count()
                self._cron_card_layout.insertWidget(count - 1, empty_lbl)
                return
            for job in jobs:
                card = self._make_cron_card(job)
                count = self._cron_card_layout.count()
                self._cron_card_layout.insertWidget(count - 1, card)

        _run_async_task(_fetch_and_render(), task_store=self._async_tasks)

    def _make_cron_card(self, job: Any) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { border: 1px solid #2a2a4a; border-radius: 8px; padding: 10px; "
            "background-color: #1e1e3a; }"
            "QFrame:hover { background-color: #252545; }"
        )
        layout = QVBoxLayout(frame)

        top = QHBoxLayout()
        icon = QLabel("📋")
        icon.setStyleSheet("border: none; font-size: 14px;")
        top.addWidget(icon)
        name_lbl = QLabel(job.name)
        name_lbl.setStyleSheet("font-weight: bold; border: none; color: #e0e0e0;")
        top.addWidget(name_lbl)
        top.addStretch()

        toggle_btn = QPushButton()
        toggle_btn.setCheckable(True)
        toggle_btn.setChecked(job.status == "enable")
        toggle_btn.setFixedWidth(80)
        self._style_toggle(toggle_btn, job.status == "enable")
        toggle_btn.clicked.connect(lambda: self._on_toggle_cron(job.id, toggle_btn))
        top.addWidget(toggle_btn)

        log_btn = QPushButton("📋 로그")
        log_btn.setStyleSheet(
            "QPushButton { border: 1px solid #2a2a4a; border-radius: 4px; padding: 2px 8px; "
            "background-color: #16213e; }"
            "QPushButton:hover { border: 1px solid #4FC3F7; background-color: #1e2d50; }"
        )
        log_btn.clicked.connect(lambda: self._on_cron_log_click(job.id, job.name))
        top.addWidget(log_btn)
        layout.addLayout(top)

        sched_row = QHBoxLayout()
        sched_lbl = QLabel(f"주기: {job.schedule}")
        sched_lbl.setStyleSheet("color: #aaa; font-size: 11px; border: none;")
        sched_row.addWidget(sched_lbl)
        sched_row.addStretch()
        layout.addLayout(sched_row)

        info_row = QHBoxLayout()
        next_txt = job.next_run_at.strftime("%Y-%m-%d %H:%M") if job.next_run_at else "(비활성)"
        last_txt = job.last_run_at.strftime("%m-%d %H:%M") if job.last_run_at else "(없음)"
        info_lbl = QLabel(f"다음 실행: {next_txt}  │  마지막: {last_txt}")
        info_lbl.setStyleSheet("color: #666; font-size: 10px; border: none;")
        info_row.addWidget(info_lbl)
        info_row.addStretch()
        del_btn = QPushButton("🗑️")
        del_btn.setFixedSize(28, 28)
        del_btn.setStyleSheet(
            "QPushButton { border: 1px solid #2a2a4a; border-radius: 4px; "
            "background-color: #16213e; }"
            "QPushButton:hover { border: 1px solid #ff6b6b; background-color: #2a1a1a; }"
        )
        del_btn.clicked.connect(lambda: self._on_delete_cron(job.id))
        info_row.addWidget(del_btn)
        layout.addLayout(info_row)

        return frame

    def _style_toggle(self, btn: QPushButton, enabled: bool) -> None:
        if enabled:
            btn.setText("🔛 활성")
            btn.setStyleSheet(
                "QPushButton { background-color: #0a2a1a; color: #66BB6A; "
                "border: 1px solid #66BB6A; border-radius: 4px; padding: 2px 4px; }"
                "QPushButton:hover { background-color: #0d3a22; }"
            )
        else:
            btn.setText("🔴 비활성")
            btn.setStyleSheet(
                "QPushButton { background-color: #16213e; color: #888; "
                "border: 1px solid #2a2a4a; border-radius: 4px; padding: 2px 4px; }"
            )
        logger.debug("Cron job toggle: enabled=%s", enabled)

    def _on_toggle_cron(self, job_id: str, btn: QPushButton) -> None:
        async def _toggle() -> None:
            result = await self._db.toggle_cron_job(job_id)
            self._style_toggle(btn, result)

        _run_async_task(_toggle(), task_store=self._async_tasks)

    def _on_delete_cron(self, job_id: str) -> None:
        logger.debug("Cron job delete: %s", job_id)
        async def _delete() -> None:
            await self._db.delete_cron_job(job_id)
            self._load_cron_level1()

        _run_async_task(_delete(), task_store=self._async_tasks)

    def _on_cron_log_click(self, cron_job_id: str, job_name: str) -> None:
        self._cron_l2_title.setText(f"📋 {job_name}")
        self._cron_l2_cron_job_id = cron_job_id
        self._cron_l2_page = 1
        self._cron_l2_total_pages = 1
        self._load_cron_l2_sessions()
        self._cron_stacked.setCurrentIndex(1)

    def _load_cron_l2_sessions(self) -> None:
        self._clear_layout(self._cron_session_layout)

        cron_job_id = self._cron_l2_cron_job_id
        if not cron_job_id:
            return

        def _on_loaded(result: Any) -> None:
            sessions, total = result if result else ([], 0)
            page_size = 20
            self._cron_l2_total_pages = max(1, (total + page_size - 1) // page_size)
            self._update_pagination(
                self._cron_l2_pagination, self._cron_l2_page, self._cron_l2_total_pages
            )

            if not sessions:
                lbl = QLabel("실행 기록이 없습니다")
                lbl.setStyleSheet("color: #888; border: none;")
                lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                count = self._cron_session_layout.count()
                self._cron_session_layout.insertWidget(count - 1, lbl)
                return

            for s in sessions:
                row = self._make_session_row(s)
                count = self._cron_session_layout.count()
                self._cron_session_layout.insertWidget(count - 1, row)

        async def _fetch() -> Any:
            return await self._db.get_cron_session_logs(cron_job_id, page=self._cron_l2_page)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = asyncio.ensure_future(_fetch())
                task.add_done_callback(lambda t: _on_loaded(t.result() if not t.exception() else None))
                return
        except RuntimeError:
            pass

        result = _run_async(_fetch())
        _on_loaded(result)

    def _make_session_row(self, session: dict) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { border: 1px solid #2a2a4a; border-radius: 6px; padding: 8px; "
            "background-color: #1e1e3a; }"
            "QFrame:hover { background-color: #252545; border: 1px solid #4FC3F7; }"
        )
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(frame)

        created = session.get("created_at", "")
        if isinstance(created, str) and len(created) >= 16:
            time_txt = created[5:16].replace("T", " ")
        else:
            time_txt = str(created)[:16]
        time_lbl = QLabel(time_txt)
        time_lbl.setFixedWidth(100)
        time_lbl.setStyleSheet("border: none; color: #888;")
        layout.addWidget(time_lbl)

        sid = session.get("id", "")[:8]
        sid_lbl = QLabel(f"{sid}...")
        sid_lbl.setFixedWidth(80)
        sid_lbl.setStyleSheet("border: none; color: #666; font-size: 11px;")
        layout.addWidget(sid_lbl)

        resp = session.get("last_response", "") or ""
        resp_short = resp[:20] + ("..." if len(resp) > 20 else "")
        resp_lbl = QLabel(resp_short)
        resp_lbl.setStyleSheet("border: none; color: #ccc;")
        layout.addWidget(resp_lbl)

        frame.mousePressEvent = lambda e, sid=session.get("id"): self._on_l3_session_click(sid)
        return frame

    def _on_l3_session_click(self, session_id: str) -> None:
        def _on_loaded(messages: Any) -> None:
            if not messages:
                messages = []
            msg_dicts = [{"role": m.role, "content": m.content} for m in messages]
            self._cron_l3_chat.load_messages(msg_dicts)

            self._clear_layout(self._cron_l3_time_layout)
            self._cron_l3_time_list = []
            for m in messages:
                ts = getattr(m, "created_at", None)
                time_str = ts.strftime("%m-%d %H:%M") if isinstance(ts, datetime) else ""
                self._cron_l3_time_list.append({"time": time_str, "role": m.role, "content": m.content})

            for idx, item in enumerate(self._cron_l3_time_list):
                btn = QPushButton(f"{item['time']} [{item['role']}]")
                btn.setStyleSheet(
                    "QPushButton { text-align: left; border: 1px solid #2a2a4a; "
                    "border-radius: 4px; padding: 4px; font-size: 11px; "
                    "background-color: #16213e; }"
                    "QPushButton:hover { background-color: #1e2d50; border: 1px solid #4FC3F7; }"
                )
                btn.clicked.connect(lambda checked, i=idx: self._scroll_cron_l3_to(i))
                count = self._cron_l3_time_layout.count()
                self._cron_l3_time_layout.insertWidget(count - 1, btn)

            self._cron_stacked.setCurrentIndex(2)

        async def _fetch() -> Any:
            return await self._db.get_messages(session_id)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = asyncio.ensure_future(_fetch())
                task.add_done_callback(lambda t: _on_loaded(t.result() if not t.exception() else None))
                return
        except RuntimeError:
            pass

        messages = _run_async(_fetch()) or []
        _on_loaded(messages)

    def _scroll_cron_l3_to(self, idx: int) -> None:
        pass

    # ─── 로그 탭 (2단계 드릴다운) ─────────────────────────────────

    def _build_log_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        self._log_stacked = QStackedWidget()
        layout.addWidget(self._log_stacked)

        self._log_stacked.addWidget(self._build_log_level1())
        self._log_stacked.addWidget(self._build_log_level2())

        self._log_stacked.setCurrentIndex(0)
        return page

    def _build_log_level1(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        header = QHBoxLayout()
        header_lbl = QLabel("모든 세션")
        header_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        header.addWidget(header_lbl)
        header.addStretch()
        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._log_session_container = QWidget()
        self._log_session_layout = QVBoxLayout(self._log_session_container)
        self._log_session_layout.setContentsMargins(8, 8, 8, 8)
        self._log_session_layout.setSpacing(4)
        self._log_session_layout.addStretch()
        scroll.setWidget(self._log_session_container)
        layout.addWidget(scroll)

        self._log_l1_pagination = self._make_pagination(
            self._on_log_l1_prev, self._on_log_l1_next
        )
        layout.addLayout(self._log_l1_pagination)
        return w

    def _build_log_level2(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        header = QHBoxLayout()
        back_btn = QPushButton("◀ 세션 목록으로")
        back_btn.clicked.connect(lambda: self._log_stacked.setCurrentIndex(0))
        header.addWidget(back_btn)
        header.addStretch()
        close_btn = QPushButton("✕ 닫기")
        close_btn.clicked.connect(lambda: self._log_stacked.setCurrentIndex(0))
        header.addWidget(close_btn)
        layout.addLayout(header)

        split = QHBoxLayout()

        self._log_l2_time_container = QWidget()
        self._log_l2_time_container.setMaximumWidth(150)
        self._log_l2_time_layout = QVBoxLayout(self._log_l2_time_container)
        self._log_l2_time_layout.setContentsMargins(4, 4, 4, 4)
        self._log_l2_time_layout.setSpacing(2)
        self._log_l2_time_layout.addStretch()
        time_scroll = QScrollArea()
        time_scroll.setWidgetResizable(True)
        time_scroll.setWidget(self._log_l2_time_container)
        time_scroll.setMaximumWidth(160)
        split.addWidget(time_scroll)

        from src.ui.chat_widget import ChatWidget

        self._log_l2_chat = ChatWidget()
        split.addWidget(self._log_l2_chat)

        layout.addLayout(split)
        return w

    def _on_log_l1_prev(self) -> None:
        if self._log_l1_page > 1:
            self._log_l1_page -= 1
            self._load_log_level1()

    def _on_log_l1_next(self) -> None:
        if self._log_l1_page < self._log_l1_total_pages:
            self._log_l1_page += 1
            self._load_log_level1()

    def _load_log_level1(self) -> None:
        self._clear_layout(self._log_session_layout)

        async def _fetch() -> Any:
            return await self._db.list_sessions(page=self._log_l1_page)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = asyncio.ensure_future(_fetch())
                task.add_done_callback(lambda t: self._on_log_l1_loaded(
                    t.result() if not t.exception() else None
                ))
                return
        except RuntimeError:
            pass

        result = _run_async(_fetch())
        self._on_log_l1_loaded(result)

    def _on_log_l1_loaded(self, result) -> None:
        self._clear_layout(self._log_session_layout)
        sessions, total = result if result else ([], 0)
        page_size = 20
        self._log_l1_total_pages = max(1, (total + page_size - 1) // page_size)
        self._update_pagination(
            self._log_l1_pagination, self._log_l1_page, self._log_l1_total_pages
        )

        if not sessions:
            lbl = QLabel("세션이 없습니다")
            lbl.setStyleSheet("color: #888; border: none;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            count = self._log_session_layout.count()
            self._log_session_layout.insertWidget(count - 1, lbl)
            return

        for s in sessions:
            row = self._make_log_session_row(s)
            count = self._log_session_layout.count()
            self._log_session_layout.insertWidget(count - 1, row)

    def _make_log_session_row(self, session: dict) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { border: 1px solid #2a2a4a; border-radius: 6px; padding: 8px; "
            "background-color: #1e1e3a; }"
            "QFrame:hover { background-color: #252545; border: 1px solid #4FC3F7; }"
        )
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(frame)

        stype = session.get("type", "chat")
        type_icon = "💬 채팅" if stype == "chat" else "⏰ 크론"
        type_lbl = QLabel(type_icon)
        type_lbl.setFixedWidth(70)
        type_lbl.setStyleSheet("border: none; color: #888;")
        layout.addWidget(type_lbl)

        created = session.get("created_at", "")
        if isinstance(created, str) and len(created) >= 16:
            time_txt = created[5:16].replace("T", " ")
        else:
            time_txt = str(created)[:16]
        time_lbl = QLabel(time_txt)
        time_lbl.setFixedWidth(100)
        time_lbl.setStyleSheet("border: none; color: #888;")
        layout.addWidget(time_lbl)

        sid = session.get("id", "")[:8]
        sid_lbl = QLabel(f"{sid}...")
        sid_lbl.setFixedWidth(80)
        sid_lbl.setStyleSheet("border: none; color: #666; font-size: 11px;")
        layout.addWidget(sid_lbl)

        prompt = session.get("first_prompt", "") or ""
        prompt_short = prompt[:50] + ("..." if len(prompt) > 50 else "")
        if not prompt_short:
            prompt_short = "(내용 없음)"
        prompt_lbl = QLabel(prompt_short)
        prompt_lbl.setStyleSheet("border: none; color: #ccc;")
        layout.addWidget(prompt_lbl)

        frame.mousePressEvent = lambda e, sid=session.get("id"): self._on_log_l2_click(sid)
        return frame

    def _on_log_l2_click(self, session_id: str) -> None:
        async def _fetch() -> Any:
            return await self._db.get_messages(session_id)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = asyncio.ensure_future(_fetch())
                task.add_done_callback(lambda t: self._on_log_l2_loaded(
                    t.result() if not t.exception() else None
                ))
                return
        except RuntimeError:
            pass

        messages = _run_async(_fetch()) or []
        self._on_log_l2_loaded(messages)

    def _on_log_l2_loaded(self, messages) -> None:
        if not messages:
            messages = []
        msg_dicts = [{"role": m.role, "content": m.content} for m in messages]
        self._log_l2_chat.load_messages(msg_dicts)

        self._clear_layout(self._log_l2_time_layout)
        self._log_l2_time_list = []
        for m in messages:
            ts = getattr(m, "created_at", None)
            time_str = ts.strftime("%m-%d %H:%M") if isinstance(ts, datetime) else ""
            self._log_l2_time_list.append({"time": time_str, "role": m.role, "content": m.content})

        for idx, item in enumerate(self._log_l2_time_list):
            btn = QPushButton(f"{item['time']} [{item['role']}]")
            btn.setStyleSheet(
                "QPushButton { text-align: left; border: 1px solid #2a2a4a; "
                "border-radius: 4px; padding: 4px; font-size: 11px; "
                "background-color: #16213e; }"
                "QPushButton:hover { background-color: #1e2d50; border: 1px solid #4FC3F7; }"
            )
            btn.clicked.connect(lambda checked, i=idx: self._scroll_log_l2_to(i))
            count = self._log_l2_time_layout.count()
            self._log_l2_time_layout.insertWidget(count - 1, btn)

        self._log_stacked.setCurrentIndex(1)

    def _scroll_log_l2_to(self, idx: int) -> None:
        pass

    # ─── 설정 로드/저장 ───────────────────────────────────────────

    def _load_settings(self) -> None:
        c = self._config
        self._llm_url.setText(c.get("llm", "url", ""))
        self._llm_model.setText(c.get("llm", "model", ""))
        self._llm_api_key.setText(c.api_key)
        self._llm_max_tokens.setValue(c.get("llm", "max_tokens", 4096))
        self._llm_temperature.setValue(c.get("llm", "temperature", 0.0))
        self._llm_timeout.setValue(c.get("llm", "timeout", 60))

        self._cdp_host.setText(c.get("cdp", "host", "127.0.0.1"))
        self._cdp_port.setValue(c.get("cdp", "port", 9222))
        self._cdp_timeout.setValue(c.get("cdp", "connection_timeout", 1.0))

        self._chrome_path.setText(c.get("chrome", "path", ""))
        self._chrome_user_data.setText(c.get("chrome", "user_data_dir", ""))
        self._chrome_headless.setChecked(c.get("chrome", "headless", False))

        self._sqlite_path.setText(c.get("sqlite", "path", ""))
        self._ws_dir.setText(c.get("workspace", "workspace_dir", ""))

        self._agent_window.setValue(c.get("agent", "sliding_window_size", 30))
        self._agent_cron_timeout.setValue(c.get("agent", "cron_timeout", 300))

        lang_idx = self._ui_lang.findData(c.get("ui", "language", "ko"))
        if lang_idx >= 0:
            self._ui_lang.setCurrentIndex(lang_idx)
        self._ui_font_size.setValue(c.get("ui", "font_size", 14))
        self._ui_max_chat.setValue(c.get("ui", "max_chat_lines", 1000))

        log_idx = self._log_level_combo.findData(c.get("logging", "level", "disabled"))
        if log_idx >= 0:
            self._log_level_combo.setCurrentIndex(log_idx)
        self._log_path.setText(c.get("logging", "path", ""))

        self._cron_l1_page = 1
        self._cron_l1_total_pages = 1
        self._log_l1_page = 1
        self._log_l1_total_pages = 1
        logger.debug("Settings save requested")
        self._load_cron_level1()
        self._load_log_level1()

    def _save(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        api_key = self._llm_api_key.text().strip()
        model = self._llm_model.text().strip()
        if not api_key:
            QMessageBox.warning(self, "경고", "API 키를 입력해주세요.")
            return
        if not model:
            QMessageBox.warning(self, "경고", "모델명을 입력해주세요.")
            return

        c = self._config
        c.set("llm", "url", self._llm_url.text())
        c.set("llm", "model", model)
        c.api_key = api_key
        c.set("llm", "max_tokens", self._llm_max_tokens.value())
        c.set("llm", "temperature", self._llm_temperature.value())
        c.set("llm", "timeout", self._llm_timeout.value())

        c.set("cdp", "host", self._cdp_host.text())
        c.set("cdp", "port", self._cdp_port.value())
        c.set("cdp", "connection_timeout", self._cdp_timeout.value())

        c.set("chrome", "path", self._chrome_path.text())
        c.set("chrome", "user_data_dir", self._chrome_user_data.text())
        c.set("chrome", "headless", self._chrome_headless.isChecked())

        c.set("agent", "sliding_window_size", self._agent_window.value())
        c.set("agent", "cron_timeout", self._agent_cron_timeout.value())

        c.set("ui", "language", self._ui_lang.currentData())
        c.set("ui", "font_size", self._ui_font_size.value())
        c.set("ui", "max_chat_lines", self._ui_max_chat.value())

        c.set("logging", "level", self._log_level_combo.currentData())
        c.set("logging", "path", self._log_path.text())

        c.save()
        self.config_saved.emit()
        self.accept()

    # ─── 유틸리티 ────────────────────────────────────────────────

    def _make_section(self, title: str, parent: Optional[QWidget] = None) -> QFrame:
        """섹션 그룹을 생성하여 QFrame을 반환한다.

        반환된 QFrame의 레이아웃에 위젯/레이아웃을 추가하려면
        frame.layout()으로 QVBoxLayout을 얻어 사용한다.
        """
        frame = QFrame(parent)
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        # NOTE: 여기서 setStyleSheet를 호출하지 않음
        # → dialog 레벨(_apply_theme)의 QFrame/QLabel/... 규칙이
        #   자식 위젯까지 올바르게 적용됨
        layout = QVBoxLayout(frame)
        lbl = QLabel(title)
        lbl.setStyleSheet("font-weight: bold; font-size: 14px; border: none; color: #e0e0e0;")
        layout.addWidget(lbl)
        return frame

    def _row(
        self, label_text: str, widget_or_layout: Any, tooltip_key: str = ""
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setFixedWidth(150)
        lbl.setStyleSheet(
            "border: none; color: #000000; font-size: 13px; "
            "background-color: #FFFFFF; padding: 2px 6px; border-radius: 4px;"
        )
        row.addWidget(lbl)
        if tooltip_key and tooltip_key in FIELD_TOOLTIPS:
            row.addWidget(_make_tooltip_icon(FIELD_TOOLTIPS[tooltip_key]))
        if isinstance(widget_or_layout, QHBoxLayout):
            row.addLayout(widget_or_layout)
        else:
            row.addWidget(widget_or_layout)
        return row

    def _make_pagination(self, prev_fn: Any, next_fn: Any) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch()
        prev_btn = QPushButton("◀")
        prev_btn.setFixedSize(30, 30)
        prev_btn.clicked.connect(prev_fn)
        row.addWidget(prev_btn)
        page_lbl = QLabel("1 / 1")
        page_lbl.setStyleSheet("border: none; color: #aaa;")
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

    def _update_pagination(self, row: QHBoxLayout, current: int, total: int) -> None:
        row._page_lbl.setText(f"{current} / {total}")  # type: ignore
        row._prev_btn.setEnabled(current > 1)  # type: ignore
        row._next_btn.setEnabled(current < total)  # type: ignore

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count() > 1:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
