"""Chrome 프로세스 관리자 — CDP 연결 확인 및 Chrome 실행."""

import asyncio
import json
import logging
import os
import platform
import subprocess
import tempfile
import urllib.error
import urllib.request
from typing import Optional

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
        """CDP 포트에 연결 가능한지 확인.

        Python 3.14+ Windows ProactorEventLoop 호환:
        aiohttp 대신 urllib.request 를 run_in_executor 로 실행하여
        _OverlappedFuture 충돌을 회피한다.
        """
        url = f"{self.cdp_url}/json/version"

        def _sync_check() -> bool:
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        data = resp.read().decode("utf-8", errors="replace")
                        logger.info("Chrome CDP detected: %s", data[:120])
                        return True
                    logger.debug("CDP check returned status %d", resp.status)
                    return False
            except urllib.error.URLError as e:
                logger.debug("CDP check failed: %s", e.reason)
                return False
            except (TimeoutError, OSError) as e:
                logger.debug("CDP check OS error: %s", e)
                return False

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_check)

    def get_cdp_ws_endpoint(self, timeout: float = 3.0) -> str:
        """/json/version 에서 webSocketDebuggerUrl 을 반환한다.

        Playwright connect_over_cdp 에 ws://host:port/ (root) 를 넘기면
        404 가 발생하므로, 정확한 browser endpoint URL 을 먼저 조회한다.
        """
        url = f"{self.cdp_url}/json/version"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                ws_url = data.get("webSocketDebuggerUrl", "")
                if ws_url:
                    logger.debug("CDP WebSocket endpoint: %s", ws_url)
                    return ws_url
        except Exception as e:
            logger.warning("Failed to get CDP WebSocket endpoint: %s", e)
        return f"ws://{self._host}:{self._port}/"

    async def quick_check(self) -> bool:
        """1초 타임아웃으로 빠른 CDP 연결 확인 (버튼 클릭 시 사용)."""
        return await self.check_cdp_connection(timeout=CDP_QUICK_TIMEOUT)

    async def launch_chrome(self) -> bool:
        """Chrome 프로세스를 실행하고 CDP 연결이 될 때까지 폴링."""
        if not self._chrome_path:
            logger.error("Chrome path not configured (config.ini [chrome] path=)")
            return False

        if not os.path.exists(self._chrome_path):
            logger.error("Chrome executable not found: path=%s, cwd=%s",
                         self._chrome_path, os.getcwd())
            return False

        cmd = self._build_command()
        cmd_str = " ".join(cmd)
        logger.info("Launching Chrome: %s", cmd_str)
        logger.info("Chrome config: host=%s port=%s headless=%s user_data=%s",
                    self._host, self._port, self._headless,
                    self._user_data_dir or "(default)")

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
            logger.info("Chrome process started: PID=%s, path=%s", self._process.pid, self._chrome_path)
        except FileNotFoundError:
            logger.error("Chrome binary not found (even though os.path.exists passed): %s", self._chrome_path)
            return False
        except PermissionError:
            logger.error("Permission denied launching Chrome: %s", self._chrome_path)
            return False
        except OSError as e:
            logger.error("OS error launching Chrome (path=%s): %s", self._chrome_path, e)
            return False
        except Exception as e:
            logger.error("Unexpected error launching Chrome (path=%s): %s [%s:%s]",
                         self._chrome_path, e, type(e).__name__, e)
            return False

        return await self._wait_for_cdp()

    def _get_user_data_dir(self) -> str:
        """사용자 데이터 디렉토리 경로 반환.

        config 에 설정된 값이 있으면 그것을 사용하고,
        없으면 temp/psjba/chrome/ 을 생성하여 사용한다.
        """
        if self._user_data_dir:
            return self._user_data_dir
        d = os.path.join(tempfile.gettempdir(), "psjba", "chrome")
        os.makedirs(d, exist_ok=True)
        return d

    def _build_command(self) -> list[str]:
        """Chrome 실행 명령어 리스트 생성."""
        cmd = [self._chrome_path]

        cmd.append(f"--remote-debugging-port={self._port}")
        cmd.append(f"--remote-allow-origins=http://127.0.0.1:{self._port}")
        cmd.append("--no-first-run")
        cmd.append("--no-default-browser-check")
        cmd.append("--disable-blink-features=AutomationControlled")

        # 포커스 감지 확장 프로그램 로드
        ext_path = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "chrome", "extension")
        )
        if os.path.isdir(ext_path):
            cmd.append(f"--load-extension={ext_path}")
            logger.info("Loading extension from: %s", ext_path)
        else:
            logger.warning("Extension directory not found: %s", ext_path)

        ud = self._get_user_data_dir()
        cmd.append(f"--user-data-dir={ud}")

        if self._headless:
            cmd.append("--headless=new")

        return cmd

    async def _wait_for_cdp(self) -> bool:
        """CDP 연결이 가능해질 때까지 폴링 (최대 5초)."""
        elapsed = 0.0
        while elapsed < CHROME_POLL_MAX_WAIT:
            if await self.check_cdp_connection(timeout=1.0):
                logger.info("Chrome CDP ready after %.1fs", elapsed)
                return True
            await asyncio.sleep(CHROME_POLL_INTERVAL)
            elapsed += CHROME_POLL_INTERVAL

        logger.error("Chrome CDP not ready after %.1fs", elapsed)
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
