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
    closed = Signal()  # 창 닫힘 → shutdown trigger

    # ─── 정지 플래그 (CancelledError 가 안 먹히는 경우 대비) ─────
    stop_requested: bool = False

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
        self.intervention_queue: list[str] = []  # 실행 중 사용자 입력 큐
        self._queue_processor_running: bool = False
        self._init_ui()
        self._try_set_titlebar_color("#1a1a2e")
        self._startup_chrome_check()

    def closeEvent(self, event) -> None:
        """창 닫힘 → closed 시그널 발생."""
        logger.debug("Window close event")
        self.closed.emit()
        super().closeEvent(event)

    def _try_set_titlebar_color(self, color_hex: str) -> None:
        """Windows DWM API로 타이틀바 배경색 변경 (실패 시 조용히 무시)."""
        try:
            import ctypes
            from ctypes import wintypes
            r, g, b = bytes.fromhex(color_hex.lstrip("#"))
            color_ref = r | (g << 8) | (b << 16)
            DWMWA_CAPTION_COLOR = 35
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(int(self.winId())),
                DWMWA_CAPTION_COLOR,
                ctypes.byref(ctypes.c_int(color_ref)),
                ctypes.sizeof(ctypes.c_int),
            )
        except Exception:
            logger.debug("DWM titlebar color not supported on this system")

    def _init_ui(self) -> None:
        self.setWindowTitle("Browser Agent")
        self.setMinimumSize(600, 400)
        self.resize(900, 700)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

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

        self._chrome_btn = QPushButton("🔗 Chrome 연결하기")
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
        """시작 시 CDP 연결 확인 (완전 동기, async/Task 생성 안 함).

        Python 3.14+ Windows ProactorEventLoop 에서
        ensure_future + async I/O 시 "Cannot enter into task while another task
        is being executed" RuntimeError 가 발생한다.
        이 문제를 피하기 위해 QTimer + 동기 HTTP 요청으로 처리한다.

        API 키가 없으면 건너뛴다 (설정 저장 시 재시도).
        """
        api_key = self._config.api_key
        if not api_key:
            logger.debug("Skipping startup Chrome check: API key not configured yet")
            self._set_chrome_status("disconnected")
            return
        self._set_chrome_status("connecting")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(50, self._check_cdp_sync)

    def _check_cdp_sync(self) -> None:
        """동기 HTTP 요청으로 CDP 연결 확인. Task 생성 없음."""
        host = self._manager._launcher._host
        port = self._manager._launcher._port
        url = f"http://{host}:{port}/json/version"
        try:
            import urllib.error
            import urllib.request

            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    logger.debug("Chrome CDP detected on startup (%s:%s)", host, port)
                    # CDP 포트 확인됨 → Manager 실제 연결 (UI "connecting" 유지)
                    from PySide6.QtCore import QTimer
                    QTimer.singleShot(0, self._connect_chrome)
                    return
        except urllib.error.URLError as e:
            logger.debug("Chrome CDP not available on startup: %s", e.reason)
        except (TimeoutError, OSError) as e:
            logger.debug("Chrome CDP not available on startup: %s", e)
        except Exception as e:
            logger.debug("Chrome CDP check error: %s", e)
        self._set_chrome_status("disconnected")

    def _on_chrome_btn_click(self) -> None:
        logger.debug("Chrome button clicked")
        if self._manager.is_connected:
            self._disconnect_chrome()
        else:
            self._connect_chrome()

    def _connect_chrome(self) -> None:
        self._set_chrome_status("connecting")
        logger.debug("Chrome connect requested, launching...")

        async def _do():
            logger.debug("Calling manager.connect(launch_if_needed=True)...")
            result = await self._manager.connect(launch_if_needed=True)
            logger.debug("manager.connect returned: %s", result)
            return "Chrome connected" in result or "Already" in result

        self._run_async_background(_do(), self._on_connect_done)

    def _on_connect_done(self, connected: bool) -> None:
        logger.debug("Chrome connect result: connected=%s", connected)
        if connected:
            self._set_chrome_status("connected")
        else:
            self._set_chrome_status("disconnected")

    def _disconnect_chrome(self) -> None:
        async def _do():
            await self._manager.disconnect()
            return False

        self._run_async_background(_do(), self._on_connect_done)

    def _set_chrome_status(self, status: str) -> None:
        if status == "connected":
            self._chrome_btn.setText("🔗 Chrome 연결됨")
            self._chrome_btn.setStyleSheet(CHROME_BTN_STYLE_CONNECTED)
            self._chrome_btn.setEnabled(True)
            self._prompt.set_running(False)
        elif status == "connecting":
            self._chrome_btn.setText("🔗 Chrome 연결 중...")
            self._chrome_btn.setStyleSheet(CHROME_BTN_STYLE_CONNECTING)
            self._chrome_btn.setEnabled(False)
        else:
            self._chrome_btn.setText("🔗 Chrome 연결하기")
            self._chrome_btn.setStyleSheet(CHROME_BTN_STYLE_DISCONNECTED)
            self._chrome_btn.setEnabled(True)

    # ─── 설정 창 ──────────────────────────────────────────────

    def _open_settings(self) -> None:
        logger.debug("Settings dialog opened")
        dlg = SettingsDialog(self._config, self._db, parent=self)
        dlg.config_saved.connect(self._on_config_saved)
        dlg.exec()

    def _on_config_saved(self) -> None:
        logger.debug("Config saved")
        self._manager.update_config(
            cdp_host=self._config.get("cdp", "host", "127.0.0.1"),
            cdp_port=self._config.get("cdp", "port", 9222),
            chrome_path=self._config.get("chrome", "path", ""),
            user_data_dir=self._config.get("chrome", "user_data_dir", ""),
            headless=self._config.get("chrome", "headless", False),
            workspace_dir=self._config.get("workspace", "workspace_dir", "./psjba_temp"),
        )
        # 설정 저장 후 Chrome 연결 재시도 (API 키가 이제 설정됨)
        if not self._manager.is_connected:
            logger.debug("Config saved, retrying Chrome connection...")
            self._startup_chrome_check()

    # ─── 프롬프트 처리 ────────────────────────────────────────
    def _on_prompt_submitted(self, text: str) -> None:
        logger.debug("Prompt submitted: %s", text[:80])
        self._chat.add_user_message(text)
        # 모든 프롬프트는 큐 기반 처리
        self.intervention_queue.append(text)
        # 처리 중이 아니면 큐 프로세서 시작
        if not self._queue_processor_running:
            self._queue_processor_running = True
            self._prompt.set_running(True)
            self.stop_requested = False
            self.prompt_submitted.emit(text)

    def _on_stop_agent(self) -> None:
        logger.debug("Agent stop requested")
        self.stop_requested = True
        # 큐에 남은 항목을 DB에 로그로만 저장하고 비운다 (처리는 안 함)
        if self.intervention_queue:
            queued = list(self.intervention_queue)
            self.intervention_queue.clear()
            logger.debug("Draining %d queued item(s) to DB (no processing)", len(queued))
            self._run_async_background(self._save_queued_to_db(queued))

    def _on_session_reset(self) -> None:
        logger.debug("Session reset button clicked")
        self._chat.clear()
        self.session_reset_requested.emit()

    async def _save_queued_to_db(self, texts: list[str]) -> None:
        """정지 시 큐에 남은 사용자 입력을 DB에만 저장한다 (처리 안 함)."""
        from src.models.schemas import Message

        session_id = await self._db.get_current_session_id()
        if not session_id:
            logger.warning("No active session to save queued messages")
            return
        for text in texts:
            msg = Message(session_id=session_id, role="user", content=text)
            await self._db.save_message(msg)
        logger.debug("Saved %d queued message(s) to session %s", len(texts), session_id)

    def show_ai_thinking(self, text: str = "🤖 응답 중...") -> None:
        """AI 응답 대기 스켈레톤을 채팅에 추가한다."""
        self._chat.add_thinking_skeleton(text)

    def add_ai_response(
        self,
        content: str,
        tool_calls=None,
    ) -> None:
        """외부(Agent)에서 AI 응답을 채팅에 추가.

        첫 번째 호출은 thinking skeleton(있다면)을 교체하고,
        이후 호출은 새 블록으로 추가한다.
        """
        if self._chat._thinking_block:
            self._chat.replace_thinking_skeleton(content, tool_calls)
        else:
            self._chat.add_ai_block(content, tool_calls)
        self._prompt.set_running(False)

    # ─── 비동기 유틸 ──────────────────────────────────────────

    def _run_async_background(self, coro, callback=None) -> None:
        """백그라운드에서 코루틴 실행 완료 후 콜백.

        Note: Python 3.14+ Windows ProactorEventLoop 환경에서는
        이 Task 생성이 "Cannot enter into task while another task is being
        executed" RuntimeError 를 유발할 수 있다. 해당 예외는
        main.py 의 event loop exception handler 에서 debug log 로 전환된다.
        """
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
            except asyncio.CancelledError:
                logger.debug("Background task cancelled")
                result = None
            except Exception as e:
                logger.error("Background task error: %s [%s]", e, type(e).__name__)
                result = None
            if callback:
                from PySide6.QtCore import QTimer

                QTimer.singleShot(0, lambda r=result: callback(r))

        asyncio.ensure_future(_wrapper())
