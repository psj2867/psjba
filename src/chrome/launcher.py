"""Chrome 프로세스 관리자 — CDP 연결 확인 및 Chrome 실행."""

import asyncio
import logging
import os
import platform
import subprocess
from typing import Optional

import aiohttp

logger = logging.getLogger("psjba.chrome.launcher")

CDP_CHECK_TIMEOUT = 5.0
CDP_QUICK_TIMEOUT = 1.0
CHROME_POLL_INTERVAL = 0.5
CHROME_POLL_MAX_WAIT = 5.0


class ChromeLauncher:
    """Chrome 프로세스 실행 및 CDP 연결 상태 확인."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9222,
        chrome_path: str = "",
        user_data_dir: str = "",
        headless: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._chrome_path = chrome_path
        self._user_data_dir = user_data_dir
        self._headless = headless
        self._process: Optional[subprocess.Popen] = None

    @property
    def cdp_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    @property
    def ws_endpoint_url(self) -> str:
        return f"ws://{self._host}:{self._port}"

    def update_settings(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        chrome_path: Optional[str] = None,
        user_data_dir: Optional[str] = None,
        headless: Optional[bool] = None,
    ) -> None:
        if host is not None:
            self._host = host
        if port is not None:
            self._port = port
        if chrome_path is not None:
            self._chrome_path = chrome_path
        if user_data_dir is not None:
            self._user_data_dir = user_data_dir
        if headless is not None:
            self._headless = headless

    async def check_cdp_connection(self, timeout: float = CDP_CHECK_TIMEOUT) -> bool:
        """CDP 포트에 연결 가능한지 확인."""
        url = f"{self.cdp_url}/json/version"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        browser_version = data.get("Browser", "unknown")
                        logger.info(f"Chrome connected: {browser_version}")
                        return True
                    return False
        except Exception:
            return False

    async def quick_check(self) -> bool:
        """1초 타임아웃으로 빠른 CDP 연결 확인 (버튼 클릭 시 사용)."""
        return await self.check_cdp_connection(timeout=CDP_QUICK_TIMEOUT)

    async def launch_chrome(self) -> bool:
        """Chrome 프로세스를 실행하고 CDP 연결이 되될 때까지 폴링."""
        if not self._chrome_path:
            logger.error("Chrome path not configured")
            return False

        if not os.path.exists(self._chrome_path):
            logger.error(f"Chrome executable not found: {self._chrome_path}")
            return False

        cmd = self._build_command()
        logger.info(f"Launching Chrome: {' '.join(cmd)}")

        try:
            creationflags = 0
            if platform.system() == "Windows":
                creationflags = subprocess.CREATE_NO_WINDOW

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            logger.info(f"Chrome PID: {self._process.pid}")
        except Exception as e:
            logger.error(f"Failed to launch Chrome: {e}")
            return False

        return await self._wait_for_cdp()

    def _build_command(self) -> list[str]:
        """Chrome 실행 명령어 리스트 생성."""
        cmd = [self._chrome_path]
        cmd.append(f"--remote-debugging-port={self._port}")
        cmd.append(f"--remote-allow-origins=http://127.0.0.1:{self._port}")
        cmd.append("--no-first-run")
        cmd.append("--no-default-browser-check")
        cmd.append("--disable-extensions")
        cmd.append("--disable-blink-features=AutomationControlled")

        if self._user_data_dir:
            cmd.append(f"--user-data-dir={self._user_data_dir}")

        if self._headless:
            cmd.append("--headless=new")

        return cmd

    async def _wait_for_cdp(self) -> bool:
        """CDP 연결이 가능해질 때까지 폴링 (최대 5초)."""
        elapsed = 0.0
        while elapsed < CHROME_POLL_MAX_WAIT:
            if await self.check_cdp_connection(timeout=1.0):
                logger.info("Chrome CDP connection established")
                return True
            await asyncio.sleep(CHROME_POLL_INTERVAL)
            elapsed += CHROME_POLL_INTERVAL

        logger.error("Chrome CDP connection timeout after launch")
        return False

    def get_chrome_pid(self) -> Optional[int]:
        """실행된 Chrome 프로세스 PID 반환."""
        if self._process and self._process.poll() is None:
            return self._process.pid
        return None

    def is_chrome_process_alive(self) -> bool:
        """Chrome 프로세스가 아직 살아있는지 확인."""
        if self._process is None:
            return False
        return self._process.poll() is None
