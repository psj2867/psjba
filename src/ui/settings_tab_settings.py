"""SettingsTab — 일반 설정 (LLM, CDP, Chrome, Agent, UI, 로깅)."""

import logging
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.ui.settings_dialog import (
    LOG_LEVELS,
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
    _make_section,
    _make_tooltip_icon,
    _row,
)

logger = logging.getLogger("psjba.ui.settings_tab_settings")


class SettingsTab(QWidget):
    """일반 설정 탭 — LLM, CDP, Chrome, Agent, UI, 로깅 필드."""

    def __init__(self, dialog: Any, config: Any) -> None:
        super().__init__()
        self._dialog = dialog
        self._config = config
        self._init_ui()

    def _init_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        page = QWidget()
        main_layout = QVBoxLayout(page)

        # ── LLM ──
        llm_box = _make_section("LLM", parent=page)
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

        llm_layout.addLayout(_row("url:", self._llm_url, "url"))
        llm_layout.addLayout(_row("model:", self._llm_model, "model"))
        llm_layout.addLayout(_row("api_key:", api_key_row, "api_key"))
        llm_layout.addLayout(_row("max_tokens:", self._llm_max_tokens, "max_tokens"))
        llm_layout.addLayout(_row("temperature:", self._llm_temperature, "temperature"))
        llm_layout.addLayout(_row("timeout:", self._llm_timeout, "timeout"))
        main_layout.addWidget(llm_box)

        # ── CDP ──
        cdp_box = _make_section("CDP", parent=page)
        cdp_layout = cdp_box.layout()
        self._cdp_host = QLineEdit()
        self._cdp_port = NoWheelSpinBox()
        self._cdp_port.setRange(1024, 65535)
        self._cdp_timeout = NoWheelDoubleSpinBox()
        self._cdp_timeout.setRange(0.5, 30.0)
        self._cdp_timeout.setSingleStep(0.5)
        cdp_layout.addLayout(_row("host:", self._cdp_host, "host"))
        cdp_layout.addLayout(_row("port:", self._cdp_port, "port"))
        cdp_layout.addLayout(_row("connection_timeout:", self._cdp_timeout, "connection_timeout"))
        main_layout.addWidget(cdp_box)

        # ── Chrome ──
        chrome_box = _make_section("Chrome", parent=page)
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
        chrome_layout.addLayout(_row("path:", chrome_path_row, "path"))
        chrome_layout.addLayout(_row("user_data_dir:", user_data_row, "user_data_dir"))
        chrome_layout.addLayout(_row("headless:", self._chrome_headless, "headless"))
        main_layout.addWidget(chrome_box)

        # ── SQLite / Workspace ──
        path_box = _make_section("경로", parent=page)
        path_layout = path_box.layout()
        self._sqlite_path = QLineEdit()
        sqlite_path_row = QHBoxLayout()
        sqlite_path_row.addWidget(self._sqlite_path)
        browse_sql_btn = QPushButton("찾아보기")
        browse_sql_btn.clicked.connect(self._browse_sqlite_path)
        sqlite_path_row.addWidget(browse_sql_btn)
        self._ws_dir = QLineEdit()
        ws_dir_row = QHBoxLayout()
        ws_dir_row.addWidget(self._ws_dir)
        browse_ws_btn = QPushButton("찾아보기")
        browse_ws_btn.clicked.connect(self._browse_workspace_dir)
        ws_dir_row.addWidget(browse_ws_btn)
        path_layout.addLayout(_row("sqlite_path:", sqlite_path_row, "sqlite_path"))
        path_layout.addLayout(_row("workspace_dir:", ws_dir_row, "workspace_dir"))
        main_layout.addWidget(path_box)

        # ── Agent ──
        agent_box = _make_section("Agent", parent=page)
        agent_layout = agent_box.layout()
        self._agent_window = NoWheelSpinBox()
        self._agent_window.setRange(1, 200)
        self._agent_cron_timeout = NoWheelSpinBox()
        self._agent_cron_timeout.setRange(10, 3600)
        self._agent_cron_tool_calls = NoWheelSpinBox()
        self._agent_cron_tool_calls.setRange(1, 200)
        agent_layout.addLayout(_row("sliding_window_size:", self._agent_window, "sliding_window_size"))
        agent_layout.addLayout(_row("cron_timeout:", self._agent_cron_timeout, "cron_timeout"))
        agent_layout.addLayout(_row("cron_tool_calls:", self._agent_cron_tool_calls, "cron_tool_calls"))
        main_layout.addWidget(agent_box)

        # ── UI ──
        ui_box = _make_section("UI", parent=page)
        ui_layout = ui_box.layout()
        self._ui_font_size = NoWheelSpinBox()
        self._ui_font_size.setRange(8, 72)
        self._ui_max_chat = NoWheelSpinBox()
        self._ui_max_chat.setRange(10, 5000)
        ui_layout.addLayout(_row("font_size:", self._ui_font_size, "font_size"))
        ui_layout.addLayout(_row("max_chat_lines:", self._ui_max_chat, "max_chat_lines"))
        main_layout.addWidget(ui_box)

        # ── 로깅 ──
        log_box = _make_section("로깅", parent=page)
        log_layout = log_box.layout()
        self._log_level_combo = NoWheelComboBox()
        for v, k in LOG_LEVELS.items():
            self._log_level_combo.addItem(v, k)
        self._log_path = QLineEdit()
        log_layout.addLayout(_row("level:", self._log_level_combo, "logging_level"))
        log_layout.addLayout(_row("path:", self._log_path, "logging_path"))
        main_layout.addWidget(log_box)

        # ── 저장 버튼 ──
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton("저장")
        save_btn.clicked.connect(self._dialog._save)
        save_btn.setFixedSize(120, 36)
        btn_row.addWidget(save_btn)
        btn_row.addStretch()
        main_layout.addLayout(btn_row)

        main_layout.addStretch()
        scroll.setWidget(page)
        outer_layout.addWidget(scroll)

    # ─── 설정 로드 ────────────────────────────────────────────────

    def load_settings(self) -> None:
        c = self._config
        self._llm_url.setText(c.get("llm", "url"))
        self._llm_model.setText(c.get("llm", "model"))
        self._llm_api_key.setText(c.api_key)
        self._llm_max_tokens.setValue(int(c.get("llm", "max_tokens")))
        self._llm_temperature.setValue(float(c.get("llm", "temperature")))
        self._llm_timeout.setValue(int(c.get("llm", "timeout")))

        self._cdp_host.setText(c.get("cdp", "host"))
        self._cdp_port.setValue(int(c.get("cdp", "port")))
        self._cdp_timeout.setValue(float(c.get("cdp", "connection_timeout")))

        self._chrome_path.setText(c.get("chrome", "path"))
        self._chrome_user_data.setText(c.get("chrome", "user_data_dir"))
        self._chrome_headless.setChecked(bool(c.get("chrome", "headless")))

        self._sqlite_path.setText(c.get("sqlite", "path"))
        self._ws_dir.setText(c.get("workspace", "workspace_dir"))

        self._agent_window.setValue(int(c.get("agent", "sliding_window_size")))
        self._agent_cron_timeout.setValue(int(c.get("agent", "cron_timeout")))
        self._agent_cron_tool_calls.setValue(int(c.get("agent", "cron_tool_calls")))

        self._ui_font_size.setValue(int(c.get("ui", "font_size")))
        self._ui_max_chat.setValue(int(c.get("ui", "max_chat_lines")))

        log_idx = self._log_level_combo.findData(c.get("logging", "level"))
        if log_idx >= 0:
            self._log_level_combo.setCurrentIndex(log_idx)
        self._log_path.setText(c.get("logging", "path"))
        logger.debug("Settings loaded")

    def save_settings(self) -> tuple[Optional[str], Optional[str]]:
        """Returns (api_key, model) if valid, or (None, None). Validation messages via dialog warning."""
        from PySide6.QtWidgets import QMessageBox

        api_key = self._llm_api_key.text().strip()
        model = self._llm_model.text().strip()
        if not api_key:
            QMessageBox.warning(self._dialog, "경고", "API 키를 입력해주세요.")
            return None, None
        if not model:
            QMessageBox.warning(self._dialog, "경고", "모델명을 입력해주세요.")
            return None, None

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
        c.set("agent", "cron_tool_calls", self._agent_cron_tool_calls.value())

        c.set("ui", "font_size", self._ui_font_size.value())
        c.set("ui", "max_chat_lines", self._ui_max_chat.value())

        c.set("logging", "level", self._log_level_combo.currentData())
        c.set("logging", "path", self._log_path.text())

        # 로그 레벨 즉시 반영
        level_name = self._log_level_combo.currentData()
        logging.getLogger("psjba").setLevel(
            getattr(logging, level_name.upper(), logging.WARNING)
        )
        return api_key, model

    # ─── 헬퍼 ────────────────────────────────────────────────────

    def _toggle_api_key(self, btn: QPushButton) -> None:
        if self._llm_api_key.echoMode() == QLineEdit.EchoMode.Password:
            self._llm_api_key.setEchoMode(QLineEdit.EchoMode.Normal)
            btn.setText("숨김")
        else:
            self._llm_api_key.setEchoMode(QLineEdit.EchoMode.Password)
            btn.setText("표시")

    def _browse_chrome_path(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self._dialog, "Chrome 실행 파일 선택")
        if path:
            self._chrome_path.setText(path)

    def _browse_user_data(self) -> None:
        path = QFileDialog.getExistingDirectory(self._dialog, "사용자 데이터 디렉토리 선택")
        if path:
            self._chrome_user_data.setText(path)

    def _browse_sqlite_path(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self._dialog, "SQLite DB 파일 선택")
        if path:
            self._sqlite_path.setText(path)

    def _browse_workspace_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self._dialog, "작업 디렉토리 선택")
        if path:
            self._ws_dir.setText(path)