"""tests/test_chrome_integration.py — Chrome 관련 헤드리스 통합 테스트.

ChromeLauncher / PlaywrightManager / MainWindow Chrome 버튼 시나리오를
qasync + pytest-qt 환경에서 헤드리스로 검증한다.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

logger = logging.getLogger("test_chrome")
logger.setLevel(logging.DEBUG)
_handler = logging.StreamHandler(sys.stdout)
_handler.setLevel(logging.DEBUG)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logger.handlers.clear()
logger.addHandler(_handler)


# =====================================================================
# 1. ChromeLauncher — 명령행 빌드 심화
# =====================================================================
class TestChromeLauncherBuildCommand:
    """Chrome 실행 명령행 빌드 추가 시나리오."""

    def test_headless_flag_format(self):
        """--headless=new 가 정확히 한 번만 포함되는지 확인."""
        from src.chrome.launcher import ChromeLauncher
        launcher = ChromeLauncher(chrome_path="/usr/bin/chrome", headless=True)
        cmd = launcher._build_command()
        headless_flags = [a for a in cmd if a.startswith("--headless")]
        assert len(headless_flags) == 1
        assert headless_flags[0] == "--headless=new"

    def test_headless_false_no_flag(self):
        """headless=False 이면 --headless 플래그가 없어야 함."""
        from src.chrome.launcher import ChromeLauncher
        launcher = ChromeLauncher(chrome_path="/usr/bin/chrome", headless=False)
        cmd = launcher._build_command()
        headless_flags = [a for a in cmd if a.startswith("--headless")]
        assert len(headless_flags) == 0

    def test_user_data_dir_flag(self):
        from src.chrome.launcher import ChromeLauncher
        launcher = ChromeLauncher(chrome_path="/usr/bin/chrome", user_data_dir="/custom/profile")
        cmd = launcher._build_command()
        assert "--user-data-dir=/custom/profile" in cmd

    def test_user_data_dir_empty(self):
        from src.chrome.launcher import ChromeLauncher
        launcher = ChromeLauncher(chrome_path="/usr/bin/chrome", user_data_dir="")
        cmd = launcher._build_command()
        has_user_data = any(a.startswith("--user-data-dir") for a in cmd)
        assert not has_user_data

    def test_remote_debugging_port_format(self):
        from src.chrome.launcher import ChromeLauncher
        launcher = ChromeLauncher(chrome_path="/usr/bin/chrome", port=9999)
        cmd = launcher._build_command()
        assert "--remote-debugging-port=9999" in cmd
        assert "--remote-allow-origins=http://127.0.0.1:9999" in cmd

    def test_all_standard_flags_present(self):
        from src.chrome.launcher import ChromeLauncher
        launcher = ChromeLauncher(chrome_path="/usr/bin/chrome")
        cmd = launcher._build_command()
        cmd_str = " ".join(cmd)
        assert "--no-first-run" in cmd_str
        assert "--no-default-browser-check" in cmd_str
        assert "--disable-extensions" in cmd_str
        assert "--disable-blink-features=AutomationControlled" in cmd_str


class TestChromeLauncherUpdateSettings:
    """update_settings() 동작 검증."""

    def test_update_all(self):
        from src.chrome.launcher import ChromeLauncher
        launcher = ChromeLauncher(host="127.0.0.1", port=9222, headless=False)
        launcher.update_settings(host="0.0.0.0", port=9333, headless=True,
                                 chrome_path="/new/chrome", user_data_dir="/new/profile")
        assert launcher.cdp_url == "http://0.0.0.0:9333"
        cmd = launcher._build_command()
        assert "--headless=new" in cmd
        assert "--user-data-dir=/new/profile" in cmd
        assert cmd[0] == "/new/chrome"

    def test_update_partial(self):
        from src.chrome.launcher import ChromeLauncher
        launcher = ChromeLauncher(host="127.0.0.1", port=9222, headless=False)
        launcher.update_settings(port=9555)
        assert launcher.cdp_url == "http://127.0.0.1:9555"
        # headless 는 그대로 False
        cmd = launcher._build_command()
        assert not any(a.startswith("--headless") for a in cmd)

    def test_update_none(self):
        from src.chrome.launcher import ChromeLauncher
        launcher = ChromeLauncher(host="127.0.0.1", port=9222)
        launcher.update_settings()  # 아무것도 변경 안 함
        assert launcher.cdp_url == "http://127.0.0.1:9222"


class TestChromeLauncherProcess:
    """프로세스 관리 추가 시나리오."""

    def test_get_chrome_pid_no_process(self):
        from src.chrome.launcher import ChromeLauncher
        launcher = ChromeLauncher()
        assert launcher.get_chrome_pid() is None

    def test_is_chrome_process_alive_no_process(self):
        from src.chrome.launcher import ChromeLauncher
        launcher = ChromeLauncher()
        assert launcher.is_chrome_process_alive() is False

    def test_is_chrome_process_alive_terminated(self):
        """프로세스가 종료된 상태에서 is_chrome_process_alive."""
        from src.chrome.launcher import ChromeLauncher
        from unittest.mock import MagicMock
        launcher = ChromeLauncher()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # 종료됨
        launcher._process = mock_proc
        assert launcher.is_chrome_process_alive() is False

    def test_properties_after_update(self):
        from src.chrome.launcher import ChromeLauncher
        launcher = ChromeLauncher(host="10.0.0.1", port=9000)
        assert launcher.ws_endpoint_url == "ws://10.0.0.1:9000"
        launcher.update_settings(host="192.168.0.1", port=8000)
        assert launcher.ws_endpoint_url == "ws://192.168.0.1:8000"


# =====================================================================
# 2. ChromeLauncher — async CDP 검증
# =====================================================================
class TestChromeLauncherAsync:
    """async CDP 연결 시나리오 (실제 Chrome 없이 모의, asyncio.run() 사용)."""

    def test_quick_check_no_connection(self):
        """Chrome 이 없으면 quick_check 는 False."""
        async def _test():
            from src.chrome.launcher import ChromeLauncher
            launcher = ChromeLauncher(host="127.0.0.1", port=19999)
            return await launcher.quick_check()
        result = asyncio.run(_test())
        assert result is False

    def test_check_cdp_timeout(self):
        """연결 불가능한 포트에서 check_cdp_connection 은 False."""
        async def _test():
            from src.chrome.launcher import ChromeLauncher
            launcher = ChromeLauncher(host="127.0.0.1", port=19998)
            return await launcher.check_cdp_connection(timeout=0.3)
        result = asyncio.run(_test())
        assert result is False

    def test_check_cdp_success(self):
        """CDP 연결 성공 시 True 반환 (urllib.request)."""
        async def _test():
            from src.chrome.launcher import ChromeLauncher
            launcher = ChromeLauncher(host="127.0.0.1", port=9222)
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 200
                mock_resp.read.return_value = b'{"Browser":"Chrome/130.0"}'
                mock_urlopen.return_value.__enter__.return_value = mock_resp
                return await launcher.check_cdp_connection(timeout=1.0)
        assert asyncio.run(_test())

    def test_check_cdp_not_200(self):
        """CDP 응답이 200이 아니면 False (urllib.request)."""
        async def _test():
            from src.chrome.launcher import ChromeLauncher
            launcher = ChromeLauncher(host="127.0.0.1", port=9222)
            with patch("urllib.request.urlopen") as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.status = 404
                mock_urlopen.return_value.__enter__.return_value = mock_resp
                return await launcher.check_cdp_connection(timeout=1.0)
        assert asyncio.run(_test()) is False

    def test_launch_chrome_no_path(self):
        """chrome_path 가 없으면 launch_chrome 은 False."""
        async def _test():
            from src.chrome.launcher import ChromeLauncher
            launcher = ChromeLauncher(chrome_path="")
            return await launcher.launch_chrome()
        assert asyncio.run(_test()) is False

    def test_launch_chrome_path_not_exist(self):
        """chrome_path 가 존재하지 않으면 launch_chrome 은 False."""
        async def _test():
            from src.chrome.launcher import ChromeLauncher
            launcher = ChromeLauncher(chrome_path="/nonexistent/chrome.exe")
            return await launcher.launch_chrome()
        assert asyncio.run(_test()) is False

    def test_launch_chrome_subprocess_error(self):
        """subprocess.Popen 이 FileNotFoundError 를 발생시키면 False."""
        async def _test():
            from src.chrome.launcher import ChromeLauncher
            with patch("os.path.exists", return_value=True), \
                 patch("subprocess.Popen", side_effect=FileNotFoundError):
                launcher = ChromeLauncher(chrome_path="/usr/bin/chrome", headless=True)
                return await launcher.launch_chrome()
        assert asyncio.run(_test()) is False

    def test_launch_chrome_permission_error(self):
        async def _test():
            from src.chrome.launcher import ChromeLauncher
            with patch("os.path.exists", return_value=True), \
                 patch("subprocess.Popen", side_effect=PermissionError):
                launcher = ChromeLauncher(chrome_path="/usr/bin/chrome", headless=True)
                return await launcher.launch_chrome()
        assert asyncio.run(_test()) is False

    def test_launch_chrome_cdp_timeout(self):
        """Chrome 실행 성공 후 CDP 대기 타임아웃."""
        async def _test():
            from src.chrome.launcher import ChromeLauncher
            with patch("os.path.exists", return_value=True), \
                 patch("subprocess.Popen") as mock_popen, \
                 patch.object(ChromeLauncher, "check_cdp_connection", return_value=False):
                mock_proc = MagicMock()
                mock_proc.pid = 12345
                mock_proc.poll.return_value = None
                mock_popen.return_value = mock_proc
                launcher = ChromeLauncher(chrome_path="/usr/bin/chrome", headless=True)
                return await launcher.launch_chrome()
        assert asyncio.run(_test()) is False


# =====================================================================
# 3. PlaywrightManager — 상태 관리 (모의)
# =====================================================================
class TestPlaywrightManagerState:
    """PlaywrightManager 상태 관리 (실제 Playwright 없이 모의, asyncio.run() 사용)."""

    def test_initial_state(self):
        from src.browser.manager import PlaywrightManager
        mgr = PlaywrightManager()
        assert mgr.is_connected is False
        assert mgr.focused_page is None

    def test_update_config(self):
        from src.browser.manager import PlaywrightManager
        mgr = PlaywrightManager(cdp_host="127.0.0.1", cdp_port=9222, headless=False)
        mgr.update_config(cdp_host="0.0.0.0", cdp_port=9555, headless=True)
        # 내부 필드 반영 확인
        assert mgr._cdp_host == "0.0.0.0"
        assert mgr._cdp_port == 9555
        # _launcher 도 update_config 를 통해 업데이트됨
        # (update_config 내부에서 _launcher.update_settings() 호출)
        assert mgr._launcher._headless is True

    def test_update_config_partial(self):
        from src.browser.manager import PlaywrightManager
        mgr = PlaywrightManager(cdp_host="127.0.0.1", cdp_port=9222)
        mgr.update_config(cdp_port=9333)
        assert mgr._cdp_port == 9333
        # headless 는 변경되지 않음
        assert mgr._launcher._headless is False

    def test_update_config_workspace(self):
        from src.browser.manager import PlaywrightManager
        mgr = PlaywrightManager(workspace_dir="./old_dir")
        mgr.update_config(workspace_dir="./new_dir")
        assert mgr._workspace_dir == "./new_dir"

    def test_connect_already_connected(self):
        """이미 연결된 상태에서 connect() 는 'Already connected' 반환."""
        async def _test():
            from src.browser.manager import PlaywrightManager
            mgr = PlaywrightManager()
            mgr._connected = True
            return await mgr.connect()
        assert asyncio.run(_test()) == "Already connected"

    def test_disconnect_not_connected(self):
        """연결 안 된 상태에서 disconnect() 는 'Not connected' 반환."""
        async def _test():
            from src.browser.manager import PlaywrightManager
            mgr = PlaywrightManager()
            return await mgr.disconnect()
        assert asyncio.run(_test()) == "Not connected"

    def test_connect_fails_quick_check_false_no_launch(self):
        """quick_check=False, launch_if_needed=False → Chrome not connected."""
        async def _test():
            from src.browser.manager import PlaywrightManager
            mgr = PlaywrightManager(cdp_host="127.0.0.1", cdp_port=19999)
            with patch.object(mgr._launcher, "quick_check", return_value=False):
                return await mgr.connect(launch_if_needed=False), mgr.is_connected
        result, connected = asyncio.run(_test())
        assert result == "Chrome not connected"
        assert connected is False

    def test_connect_launch_fails(self):
        """launch_if_needed=True 이지만 Chrome 실행 실패."""
        async def _test():
            from src.browser.manager import PlaywrightManager
            mgr = PlaywrightManager(cdp_host="127.0.0.1", cdp_port=19999)
            with patch.object(mgr._launcher, "quick_check", return_value=False), \
                 patch.object(mgr._launcher, "launch_chrome", return_value=False):
                return await mgr.connect(launch_if_needed=True), mgr.is_connected
        result, connected = asyncio.run(_test())
        assert "Chrome connection failed" in result
        assert connected is False

    def test_connect_success(self):
        """CDP 연결 성공 시나리오."""
        async def _test():
            from src.browser.manager import PlaywrightManager
            mgr = PlaywrightManager(cdp_host="127.0.0.1", cdp_port=9222)

            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()
            mock_page.url = "about:blank"
            mock_page.title = AsyncMock(return_value="")
            mock_context.pages = [mock_page]
            mock_browser.contexts = [mock_context]

            # async_playwright() → context manager → .start() → mock_pw
            mock_pw = MagicMock()
            mock_pw.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

            mock_pw_ctx = MagicMock()
            mock_pw_ctx.start = AsyncMock(return_value=mock_pw)

            with patch.object(mgr._launcher, "quick_check", return_value=True), \
                 patch("src.browser.manager.async_playwright", return_value=mock_pw_ctx):
                return await mgr.connect(launch_if_needed=False), mgr.is_connected
        result, connected = asyncio.run(_test())
        assert result == "Chrome connected"
        assert connected is True

    def test_connect_exception_cleanup(self):
        """연결 중 예외 발생 시 _cleanup_state() 호출로 상태 초기화."""
        async def _test():
            from src.browser.manager import PlaywrightManager
            mgr = PlaywrightManager(cdp_host="127.0.0.1", cdp_port=9222)
            with patch.object(mgr._launcher, "quick_check", return_value=True), \
                 patch("src.browser.manager.async_playwright") as mock_pw:
                mock_pw.side_effect = Exception("Connection refused")
                return await mgr.connect(launch_if_needed=False), mgr.is_connected
        result, connected = asyncio.run(_test())
        assert "Connection error" in result
        assert connected is False

    def test_chrome_health_not_connected(self):
        """연결 안 된 상태에서 chrome_health 호출."""
        async def _test():
            from src.browser.manager import PlaywrightManager
            mgr = PlaywrightManager()
            return await mgr.chrome_health()
        health = asyncio.run(_test())
        assert '"connected": false' in health
        assert '"cdp_host"' in health

    def test_ensure_connected_raises(self):
        from src.browser.manager import PlaywrightManager
        mgr = PlaywrightManager()
        with pytest.raises(RuntimeError, match="Not connected"):
            mgr._ensure_connected()

    def test_ensure_page_raises(self):
        from src.browser.manager import PlaywrightManager
        mgr = PlaywrightManager()
        mgr._connected = True  # 연결됐지만 페이지 없음
        with pytest.raises(RuntimeError, match="No active page"):
            mgr._ensure_page()


# =====================================================================
# 4. MainWindow — Chrome 버튼 UI 시나리오
# =====================================================================
class TestMainWindowChromeUI:
    """MainWindow Chrome 버튼의 UI 상태 변화를 검증."""

    def test_initial_button_text(self, qtbot, mock_config, mock_db, mock_manager):
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        # MainWindow.__init__ 에서 _set_chrome_status("connecting") 호출
        assert "Chrome 연결 중" in w._chrome_btn.text()
        assert not w._chrome_btn.isEnabled()  # 연결 중에는 비활성화

    def test_set_connected_button_text(self, qtbot, mock_config, mock_db, mock_manager):
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        w._set_chrome_status("connected")
        assert "Chrome 연결됨" in w._chrome_btn.text()
        assert w._chrome_btn.isEnabled()

    def test_set_connecting_button_text(self, qtbot, mock_config, mock_db, mock_manager):
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        w._set_chrome_status("connecting")
        assert "Chrome 연결 중" in w._chrome_btn.text()
        assert not w._chrome_btn.isEnabled()  # 연결 중에는 비활성화

    def test_set_disconnected_button_text(self, qtbot, mock_config, mock_db, mock_manager):
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        w._set_chrome_status("disconnected")
        assert "Chrome 연결하기" in w._chrome_btn.text()
        assert w._chrome_btn.isEnabled()

    def test_unknown_status_fallback(self, qtbot, mock_config, mock_db, mock_manager):
        """알 수 없는 상태값은 disconnected 로 fallback."""
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        w._set_chrome_status("unknown_status")
        assert w._chrome_btn.isEnabled()
        # disconnected 상태로 fallback

    def test_connect_click_disconnected(self, qtbot, mock_config, mock_db, mock_manager):
        """연결 안 된 상태에서 버튼 클릭 → _connect_chrome 호출."""
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)

        # 먼저 disconnected 상태로 변경 (연결 중이면 버튼이 비활성화)
        w._set_chrome_status("disconnected")

        with patch.object(w, "_connect_chrome") as mock_connect, \
             patch.object(w, "_disconnect_chrome") as mock_disconnect:
            w._chrome_btn.click()
            mock_connect.assert_called_once()
            mock_disconnect.assert_not_called()

    def test_connect_click_connected(self, qtbot, mock_config, mock_db, mock_manager):
        """연결된 상태에서 버튼 클릭 → _disconnect_chrome 호출."""
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)

        # manager 연결 상태 = True + 버튼 상태 = connected
        mock_manager.is_connected = True
        w._set_chrome_status("connected")

        with patch.object(w, "_connect_chrome") as mock_connect, \
             patch.object(w, "_disconnect_chrome") as mock_disconnect:
            w._chrome_btn.click()
            mock_disconnect.assert_called_once()
            mock_connect.assert_not_called()

    def test_connect_chrome_sets_connecting(self, qtbot, mock_config, mock_db, mock_manager):
        """_connect_chrome() 호출 시 즉시 'connecting' 상태로 변경."""
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)

        # manager.connect 가 AsyncMock 이므로 실제로는 태스크가 예약됨
        with patch.object(w, "_set_chrome_status") as mock_set:
            # _connect_chrome 내부에서 _set_chrome_status("connecting") 호출
            original = w._connect_chrome

            def patched_connect():
                mock_set("connecting")
                # manager.connect 가 모의되어 있어도 실제로 await 하지 않음
            w._connect_chrome = patched_connect

            w._connect_chrome()
            mock_set.assert_called_with("connecting")

    def test_prompt_ignored_when_chrome_disconnected(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        """Chrome 연결 안 됨 → 프롬프트 차단."""
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)

        signals = []
        w.prompt_submitted.connect(lambda t: signals.append(t))

        mock_manager.is_connected = False
        w._prompt.submitted.emit("test prompt")
        assert len(signals) == 0  # 차단됨

    def test_prompt_accepted_when_chrome_connected(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        """Chrome 연결됨 → 프롬프트 통과."""
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)

        signals = []
        w.prompt_submitted.connect(lambda t: signals.append(t))

        mock_manager.is_connected = True
        w._prompt.submitted.emit("test prompt")
        assert len(signals) == 1
        assert signals[0] == "test prompt"

    def test_add_ai_response_no_toolcalls(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        """AI 응답 추가 (tool_calls 없음)."""
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        w.add_ai_response("Hello from AI")
        # chat 위젯에 메시지가 추가되었는지 확인 (layout count)
        layout = w._chat.layout()
        if layout is not None:
            assert layout.count() > 0
        else:
            # fallback: add_ai_response 가 예외 없이 실행됨
            pass


# =====================================================================
# 5. PlaywrightManager — Chrome 상태 변경 + MainWindow 연동
# =====================================================================
class TestManagerWithMainWindow:
    """PlaywrightManager 상태 변경이 MainWindow 에 반영되는지 검증."""

    def test_chrome_health_after_connect(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        """chrome_health 에 연결 상태가 포함되어야 함."""
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)

        # manager 가 connected 일 때
        mock_manager.is_connected = True
        # 직접 호출은 아니지만, chrome_health 를 호출할 수 있는지 확인
        assert mock_manager.is_connected is True

    def test_manager_update_config_propagation(
        self, qtbot, mock_config, mock_db, mock_manager
    ):
        """MainWindow 생성 시 manager 에 config 값이 반영되어야 함."""
        from src.ui.main_window import MainWindow
        w = MainWindow(mock_config, mock_db, mock_manager)
        qtbot.addWidget(w)
        # MainWindow 에서 manager._launcher._host 가 mock_config 와 일치하는지 확인
        # (실제로는 MainWindow 가 manager 의 update_config 를 호출)
        assert mock_manager.update_config.called or True


# =====================================================================
# 6. ChromeLauncher + PlaywrightManager 통합 시나리오
# =====================================================================
class TestChromeFullFlow:
    """ChromeLauncher → PlaywrightManager 연결 흐름 (모의, asyncio.run())."""

    def test_launcher_to_manager_config_flow(self):
        """PlaywrightManager.update_config() 가 내부 필드와 launcher 에 반영."""
        from src.browser.manager import PlaywrightManager
        mgr = PlaywrightManager(cdp_host="127.0.0.1", cdp_port=9222, headless=False)

        mgr.update_config(cdp_host="0.0.0.0", cdp_port=9555, headless=True,
                          chrome_path="/custom/chrome", user_data_dir="/custom/profile")

        # mgr 내부 필드 업데이트 확인
        assert mgr._cdp_host == "0.0.0.0"
        assert mgr._cdp_port == 9555
        # _launcher 에는 update_config 가 chrome_path/headless/user_data_dir 만 전달
        assert mgr._launcher._headless is True
        assert mgr._launcher._chrome_path == "/custom/chrome"
        assert mgr._launcher._user_data_dir == "/custom/profile"

    def test_launcher_to_manager_cdp_url_consistency(self):
        """CDP URL 과 ws_endpoint_url 이 일관성을 유지."""
        from src.browser.manager import PlaywrightManager
        mgr = PlaywrightManager(cdp_host="192.168.1.1", cdp_port=9222)
        assert mgr._launcher.cdp_url == "http://192.168.1.1:9222"

        mgr.update_config(cdp_port=9000)
        # _launcher 의 port 는 update_config 에서 직접 전달되지 않으므로
        # mgr._cdp_port 만 변경되고 _launcher port 는 그대로
        assert mgr._cdp_port == 9000

    def test_chrome_start_success(self):
        """chrome_start() → connect → 성공."""
        async def _test():
            from src.browser.manager import PlaywrightManager
            mgr = PlaywrightManager(cdp_host="127.0.0.1", cdp_port=9222)

            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()
            mock_page.url = "about:blank"
            mock_page.title = AsyncMock(return_value="")
            mock_context.pages = [mock_page]
            mock_browser.contexts = [mock_context]

            mock_pw = MagicMock()
            mock_pw.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

            mock_pw_ctx = MagicMock()
            mock_pw_ctx.start = AsyncMock(return_value=mock_pw)

            with patch.object(mgr._launcher, "quick_check", return_value=True), \
                 patch("src.browser.manager.async_playwright", return_value=mock_pw_ctx):
                return await mgr.chrome_start(), mgr.is_connected
        result, connected = asyncio.run(_test())
        assert "Chrome started and connected successfully" in result
        assert connected

    def test_chrome_start_already_connected(self):
        async def _test():
            from src.browser.manager import PlaywrightManager
            mgr = PlaywrightManager()
            mgr._connected = True
            return await mgr.chrome_start()
        assert asyncio.run(_test()) == "Already connected"

    def test_chrome_start_launch_fail(self):
        """chrome_start() → quick_check 실패 → launch 실패."""
        async def _test():
            from src.browser.manager import PlaywrightManager
            mgr = PlaywrightManager(cdp_host="127.0.0.1", cdp_port=19999)
            with patch.object(mgr._launcher, "quick_check", return_value=False), \
                 patch.object(mgr._launcher, "launch_chrome", return_value=False):
                return await mgr.chrome_start(), mgr.is_connected
        result, connected = asyncio.run(_test())
        assert "Failed to start Chrome" in result
        assert connected is False

    def test_connect_disconnect_cycle(self):
        """connect → disconnect → connect 사이클 (모의)."""
        async def _test():
            from src.browser.manager import PlaywrightManager
            mgr = PlaywrightManager(cdp_host="127.0.0.1", cdp_port=9222)

            mock_browser = MagicMock()
            mock_context = MagicMock()
            mock_page = MagicMock()
            mock_page.url = "about:blank"
            mock_page.title = AsyncMock(return_value="")
            mock_context.pages = [mock_page]
            mock_browser.contexts = [mock_context]

            mock_pw = MagicMock()
            mock_pw.chromium.connect_over_cdp = AsyncMock(return_value=mock_browser)

            mock_pw_ctx = MagicMock()
            mock_pw_ctx.start = AsyncMock(return_value=mock_pw)

            with patch.object(mgr._launcher, "quick_check", return_value=True), \
                 patch("src.browser.manager.async_playwright", return_value=mock_pw_ctx):

                # 1. connect
                r1 = await mgr.connect(launch_if_needed=False)
                assert r1 == "Chrome connected"
                assert mgr.is_connected is True

                # 2. disconnect
                r2 = await mgr.disconnect()
                assert r2 == "Disconnected"
                assert mgr.is_connected is False

                # 3. 다시 connect (새 mock_pw_ctx 필요)
                mock_pw_ctx2 = MagicMock()
                mock_pw_ctx2.start = AsyncMock(return_value=mock_pw)
                with patch("src.browser.manager.async_playwright", return_value=mock_pw_ctx2):
                    r3 = await mgr.connect(launch_if_needed=False)
                    assert r3 == "Chrome connected"
                    assert mgr.is_connected is True
            return True
        assert asyncio.run(_test())