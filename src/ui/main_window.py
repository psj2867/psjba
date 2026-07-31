"""MainWindow — 메인 윈도우 (⚙️ + 🔗 Chrome + 채팅 + 입력)."""

import asyncio
import logging

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.browser.manager import PlaywrightManager
from src.config import Config
from src.database import Database
from src.ui.chat_widget import ChatWidget
from src.ui.prompt_input import PromptInput
from src.ui.settings_dialog import SettingsDialog

logger = logging.getLogger("psjba.ui.main_window")

CHROME_BTN_STYLE_DISCONNECTED = (
    "QPushButton { background-color: #16213e; color: #e0e0e0; "
    "border: 1px solid #2a2a4a; border-radius: 6px; padding: 4px 10px; }"
    "QPushButton:hover { background-color: #1e2d50; border: 1px solid #4FC3F7; }"
)
CHROME_BTN_STYLE_CONNECTING = (
    "QPushButton { background-color: #16213e; color: #888; "
    "border: 1px dashed #2a2a4a; border-radius: 6px; padding: 4px 10px; }"
)
CHROME_BTN_STYLE_CONNECTED = (
    "QPushButton { background-color: #0a2a1a; color: #66BB6A; "
    "border: 1px solid #66BB6A; border-radius: 6px; padding: 4px 10px; }"
    "QPushButton:hover { background-color: #0d3a22; }"
)

STARTUP_CDP_TIMEOUT = 5.0


class MainWindow(QMainWindow):
    """메인 윈도우 — ⚙️ + 🔗 Chrome + 세션 로그 + 프롬프트 입력."""

    prompt_submitted = Signal(str)
    session_reset_requested = Signal()

    def __init__(
        self,
        config: Config,
        db: Database,
        manager: PlaywrightManager,
    ) -> None:
        super().__init__()
        self._config = config
        self._db = db
        self._manager = manager
        self._agent_task = None
        self._init_ui()
        self._startup_chrome_check()

    def _init_ui(self) -> None:
        self.setWindowTitle("Browser Agent")
        self.setMinimumSize(800, 600)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        top_bar = QHBoxLayout()

        self._settings_btn = QPushButton("⚙️")
        self._settings_btn.setFixedSize(36, 36)
        self._settings_btn.setStyleSheet(
            "QPushButton { border: 1px solid #2a2a4a; border-radius: 8px; "
            "background-color: #16213e; font-size: 18px; }"
            "QPushButton:hover { background-color: #1e2d50; border: 1px solid #4FC3F7; }"
        )
        self._settings_btn.clicked.connect(self._open_settings)
        top_bar.addWidget(self._settings_btn)

        top_bar.addStretch()

        self._chrome_btn = QPushButton("🔗 Chrome 연결 안 됨")
        self._chrome_btn.setStyleSheet(CHROME_BTN_STYLE_DISCONNECTED)
        self._chrome_btn.clicked.connect(self._on_chrome_btn_click)
        top_bar.addWidget(self._chrome_btn)

        layout.addLayout(top_bar)

        self._chat = ChatWidget()
        layout.addWidget(self._chat, stretch=1)

        self._prompt = PromptInput()
        self._prompt.submitted.connect(self._on_prompt_submitted)
        self._prompt.stopped.connect(self._on_stop_agent)
        self._prompt.session_reset.connect(self._on_session_reset)
        layout.addWidget(self._prompt)

    # ─── Chrome 연결 ──────────────────────────────────────────

    def _startup_chrome_check(self) -> None:
        """프로그램 시작 시 5초 타임아웃으로 CDP 테스트."""
        self._set_chrome_status("connecting")

        async def _check():
            ok = await self._manager._launcher.check_cdp_connection(
                timeout=STARTUP_CDP_TIMEOUT
            )
            if ok:
                result = await self._manager.connect()
                return "Chrome connected" in result or "Already" in result
            return False

        self._run_async_background(_check, self._on_startup_check_done)

    def _on_startup_check_done(self, connected: bool) -> None:
        if connected:
            self._set_chrome_status("connected")
        else:
            self._set_chrome_status("disconnected")

    def _on_chrome_btn_click(self) -> None:
        if self._manager.is_connected:
            self._disconnect_chrome()
        else:
            self._connect_chrome()

    def _connect_chrome(self) -> None:
        self._set_chrome_status("connecting")

        async def _do():
            result = await self._manager.connect(launch_if_needed=True)
            return "Chrome connected" in result or "Already" in result

        self._run_async_background(_do, self._on_connect_done)

    def _on_connect_done(self, connected: bool) -> None:
        if connected:
            self._set_chrome_status("connected")
        else:
            self._set_chrome_status("disconnected")

    def _disconnect_chrome(self) -> None:
        async def _do():
            await self._manager.disconnect()
            return False

        self._run_async_background(_do, self._on_connect_done)

    def _set_chrome_status(self, status: str) -> None:
        if status == "connected":
            self._chrome_btn.setText("🔗 Chrome 연결 됨")
            self._chrome_btn.setStyleSheet(CHROME_BTN_STYLE_CONNECTED)
            self._chrome_btn.setEnabled(True)
            self._prompt.set_running(False)
        elif status == "connecting":
            self._chrome_btn.setText("🔗 Chrome 연결 중...")
            self._chrome_btn.setStyleSheet(CHROME_BTN_STYLE_CONNECTING)
            self._chrome_btn.setEnabled(False)
        else:
            self._chrome_btn.setText("🔗 Chrome 연결 안 됨")
            self._chrome_btn.setStyleSheet(CHROME_BTN_STYLE_DISCONNECTED)
            self._chrome_btn.setEnabled(True)

    # ─── 설정 창 ──────────────────────────────────────────────

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._config, self._db, parent=self)
        dlg.config_saved.connect(self._on_config_saved)
        dlg.exec()

    def _on_config_saved(self) -> None:
        self._manager.update_config(
            cdp_host=self._config.get("cdp", "host", "127.0.0.1"),
            cdp_port=self._config.get("cdp", "port", 9222),
            chrome_path=self._config.get("chrome", "path", ""),
            user_data_dir=self._config.get("chrome", "user_data_dir", ""),
            headless=self._config.get("chrome", "headless", False),
            workspace_dir=self._config.get("workspace", "workspace_dir", "./psjba_temp"),
        )

    # ─── 프롬프트 처리 ────────────────────────────────────────

    def _on_prompt_submitted(self, text: str) -> None:
        if not self._manager.is_connected:
            return
        self._prompt.set_running(True)
        self._chat.add_user_message(text)
        self.prompt_submitted.emit(text)

    def _on_stop_agent(self) -> None:
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()
        self._prompt.set_running(False)

    def _on_session_reset(self) -> None:
        self._chat.clear()
        self.session_reset_requested.emit()

    def add_ai_response(
        self,
        content: str,
        tool_calls=None,
    ) -> None:
        """외부(Agent)에서 AI 응답을 채팅에 추가."""
        self._chat.add_ai_block(content, tool_calls)
        self._prompt.set_running(False)

    # ─── 비동기 유틸 ──────────────────────────────────────────

    def _run_async_background(self, coro, callback=None) -> None:
        """백그라운드에서 코루틴 실행 완료 후 콜백."""
        try:
            import qasync
        except ImportError:
            return

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return

        if not loop.is_running():
            return

        async def _wrapper():
            try:
                result = await coro
            except Exception:
                result = None
            if callback:
                from PySide6.QtCore import QTimer

                QTimer.singleShot(0, lambda r=result: callback(r))

        asyncio.ensure_future(_wrapper())
