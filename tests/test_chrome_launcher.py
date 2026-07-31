"""tests/test_chrome_launcher.py — ChromeLauncher 테스트."""

from pathlib import Path

import pytest

from src.chrome.launcher import ChromeLauncher


class TestChromeLauncherInit:
    def test_default_values(self):
        launcher = ChromeLauncher()
        assert launcher.cdp_url == "http://127.0.0.1:9222"
        assert launcher.ws_endpoint_url == "ws://127.0.0.1:9222"

    def test_custom_values(self):
        launcher = ChromeLauncher(
            host="0.0.0.0",
            port=9999,
            chrome_path="/custom/chrome",
            user_data_dir="/custom/profile",
            headless=True,
        )
        assert launcher.cdp_url == "http://0.0.0.0:9999"
        assert launcher.ws_endpoint_url == "ws://0.0.0.0:9999"

    def test_update_settings(self):
        launcher = ChromeLauncher()
        launcher.update_settings(host="192.168.1.1", port=9333, headless=True)
        assert launcher.cdp_url == "http://192.168.1.1:9333"


class TestBuildCommand:
    def test_basic_command(self):
        launcher = ChromeLauncher(
            chrome_path="C:\\chrome.exe",
            port=9222,
        )
        cmd = launcher._build_command()
        assert cmd[0] == "C:\\chrome.exe"
        assert "--remote-debugging-port=9222" in cmd
        assert "--remote-allow-origins=http://127.0.0.1:9222" in cmd
        assert "--no-first-run" in cmd
        assert "--no-default-browser-check" in cmd
        assert "--disable-extensions" in cmd
        assert "--disable-blink-features=AutomationControlled" in cmd

    def test_with_user_data_dir(self):
        launcher = ChromeLauncher(
            chrome_path="/usr/bin/chrome",
            user_data_dir="/tmp/profile",
        )
        cmd = launcher._build_command()
        assert "--user-data-dir=/tmp/profile" in cmd

    def test_without_user_data_dir(self):
        launcher = ChromeLauncher(chrome_path="/usr/bin/chrome")
        cmd = launcher._build_command()
        has_user_data = any("--user-data-dir" in arg for arg in cmd)
        assert not has_user_data

    def test_headless_mode(self):
        launcher = ChromeLauncher(
            chrome_path="/usr/bin/chrome",
            headless=True,
        )
        cmd = launcher._build_command()
        assert "--headless=new" in cmd

    def test_non_headless(self):
        launcher = ChromeLauncher(chrome_path="/usr/bin/chrome", headless=False)
        cmd = launcher._build_command()
        has_headless = any("--headless" in arg for arg in cmd)
        assert not has_headless

    def test_remote_allow_origins_format(self):
        launcher = ChromeLauncher(host="127.0.0.1", port=9222)
        cmd = launcher._build_command()
        assert "--remote-allow-origins=http://127.0.0.1:9222" in cmd


class TestProcessManagement:
    def test_get_chrome_pid_no_process(self):
        launcher = ChromeLauncher()
        assert launcher.get_chrome_pid() is None

    def test_is_chrome_process_alive_no_process(self):
        launcher = ChromeLauncher()
        assert launcher.is_chrome_process_alive() is False


@pytest.mark.asyncio
class TestAsyncMethods:
    async def test_quick_check_not_connected(self):
        """연결 안 된 상태에서 quick_check는 False."""
        launcher = ChromeLauncher(host="127.0.0.1", port=19999)
        result = await launcher.quick_check()
        assert result is False

    async def test_check_cdp_connection_not_connected(self):
        launcher = ChromeLauncher(host="127.0.0.1", port=19999)
        result = await launcher.check_cdp_connection(timeout=0.5)
        assert result is False

    async def test_launch_chrome_no_path(self):
        launcher = ChromeLauncher(chrome_path="")
        result = await launcher.launch_chrome()
        assert result is False

    async def test_launch_chrome_path_not_exist(self):
        launcher = ChromeLauncher(chrome_path="/nonexistent/chrome.exe")
        result = await launcher.launch_chrome()
        assert result is False