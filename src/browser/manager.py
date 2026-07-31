"""PlaywrightManager — Playwright 브라우저 제어 서비스 클래스 (핵심)."""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from src.browser.accessibility import get_accessibility_tree
from src.browser.self_healing import SelfHealingSelector
from src.chrome.launcher import ChromeLauncher

logger = logging.getLogger("psjba.browser.manager")

SCREENSHOT_DATE_FMT = "%Y%m%d-%H%M%S"


class _TabInfo:
    """내부 탭 정보 저장소."""

    def __init__(self, page: Page, url: str = "about:blank") -> None:
        self.page = page
        self.url = url
        self.title: str = ""

    async def update_info(self) -> None:
        try:
            self.url = self.page.url
            self.title = await self.page.title()
        except Exception:
            pass


class PlaywrightManager:
    """Playwright를 통한 브라우저 제어를 담당하는 서비스 클래스.

    LangGraph 노드에서 직접 import하여 호출한다.
    """

    def __init__(
        self,
        cdp_host: str = "127.0.0.1",
        cdp_port: int = 9222,
        connection_timeout: float = 1.0,
        chrome_path: str = "",
        user_data_dir: str = "",
        headless: bool = False,
        workspace_dir: str = "./psjba_temp",
    ) -> None:
        self._cdp_host = cdp_host
        self._cdp_port = cdp_port
        self._connection_timeout = connection_timeout
        self._workspace_dir = workspace_dir

        self._launcher = ChromeLauncher(
            host=cdp_host,
            port=cdp_port,
            chrome_path=chrome_path,
            user_data_dir=user_data_dir,
            headless=headless,
        )

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._tabs: list[_TabInfo] = []
        self._focused_tab_index: int = 0
        self._connected: bool = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def focused_page(self) -> Optional[Page]:
        if 0 <= self._focused_tab_index < len(self._tabs):
            return self._tabs[self._focused_tab_index].page
        return None

    def update_config(
        self,
        cdp_host: Optional[str] = None,
        cdp_port: Optional[int] = None,
        chrome_path: Optional[str] = None,
        user_data_dir: Optional[str] = None,
        headless: Optional[bool] = None,
        workspace_dir: Optional[str] = None,
    ) -> None:
        if cdp_host is not None:
            self._cdp_host = cdp_host
        if cdp_port is not None:
            self._cdp_port = cdp_port
        if chrome_path is not None:
            self._launcher.update_settings(chrome_path=chrome_path)
        if user_data_dir is not None:
            self._launcher.update_settings(user_data_dir=user_data_dir)
        if headless is not None:
            self._launcher.update_settings(headless=headless)
        if workspace_dir is not None:
            self._workspace_dir = workspace_dir

    # ─── 연결/해제 ──────────────────────────────────────────

    async def connect(self, launch_if_needed: bool = False) -> str:
        """CDP에 연결한다.

        Args:
            launch_if_needed: 연결 실패 시 Chrome을 자동 실행할지 여부

        Returns:
            상태 메시지
        """
        if self._connected:
            return "Already connected"

        quick_ok = await self._launcher.quick_check()
        if not quick_ok:
            if launch_if_needed:
                launched = await self._launcher.launch_chrome()
                if not launched:
                    return "Chrome connection failed"
            else:
                return "Chrome not connected"

        try:
            self._playwright = await async_playwright().start()
            ws_url = f"ws://{self._cdp_host}:{self._cdp_port}"
            self._browser = await self._playwright.chromium.connect_over_cdp(ws_url)

            contexts = self._browser.contexts
            if contexts and contexts[0].pages:
                self._context = contexts[0]
                self._tabs = [_TabInfo(p) for p in self._context.pages]
                for tab in self._tabs:
                    await tab.update_info()
                self._focused_tab_index = 0
            else:
                self._context = (
                    contexts[0]
                    if contexts
                    else await self._browser.new_context()
                )
                page = await self._context.new_page()
                self._tabs = [_TabInfo(page)]
                self._focused_tab_index = 0

            self._connected = True
            url = self.focused_page.url if self.focused_page else "about:blank"
            logger.info(f"Connected to Chrome CDP: {url}")
            return "Chrome connected"
        except Exception as e:
            logger.error(f"CDP connection error: {e}")
            self._cleanup_state()
            return f"Connection error: {e}"

    async def disconnect(self) -> str:
        """CDP 연결만 해제한다 (Chrome 프로세스는 유지)."""
        if not self._connected:
            return "Not connected"

        try:
            for tab in self._tabs:
                try:
                    await tab.page.close()
                except Exception:
                    pass
            self._tabs.clear()

            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.error(f"Disconnect error: {e}")
        finally:
            self._cleanup_state()

        logger.info("Disconnected from Chrome CDP")
        return "Disconnected"

    def _cleanup_state(self) -> None:
        self._browser = None
        self._playwright = None
        self._context = None
        self._tabs.clear()
        self._focused_tab_index = 0
        self._connected = False

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("Not connected to Chrome. Call connect() first.")

    def _ensure_page(self) -> Page:
        self._ensure_connected()
        page = self.focused_page
        if page is None:
            raise RuntimeError("No active page")
        return page

    # ─── 탭 관리 ────────────────────────────────────────────

    async def browser_new_tab(self, url: Optional[str] = None) -> str:
        """새 탭을 열고 해당 탭으로 전환한다."""
        self._ensure_connected()
        assert self._context is not None
        page = await self._context.new_page()
        if url:
            await page.goto(url, wait_until="domcontentloaded")
        tab = _TabInfo(page, url or "about:blank")
        await tab.update_info()
        self._tabs.append(tab)
        self._focused_tab_index = len(self._tabs) - 1
        return f"New tab opened: {tab.url} ({tab.title})"

    async def browser_close_tab(self) -> str:
        """현재 focused tab을 닫고 이전 탭으로 전환한다."""
        self._ensure_connected()
        if not self._tabs:
            return "No tabs to close"

        tab = self._tabs[self._focused_tab_index]
        url = tab.url
        try:
            await tab.page.close()
        except Exception:
            pass

        self._tabs.pop(self._focused_tab_index)

        if not self._tabs:
            page = await self._context.new_page()
            self._tabs.append(_TabInfo(page))
            self._focused_tab_index = 0
        elif self._focused_tab_index >= len(self._tabs):
            self._focused_tab_index = len(self._tabs) - 1

        focused = self._tabs[self._focused_tab_index]
        await focused.update_info()
        return f"Closed tab: {url}. Now on: {focused.url} ({focused.title})"

    async def browser_switch_tab(self, target: str) -> str:
        """탭을 전환한다. target은 인덱스(0-based) 또는 URL 문자열."""
        self._ensure_connected()

        try:
            idx = int(target)
            if 0 <= idx < len(self._tabs):
                self._focused_tab_index = idx
                tab = self._tabs[idx]
                await tab.update_info()
                return f"Switched to tab {idx}: {tab.url} ({tab.title})"
            return f"Invalid tab index: {idx} (have {len(self._tabs)} tabs)"
        except ValueError:
            for i, tab in enumerate(self._tabs):
                if tab.url == target or target in tab.url:
                    self._focused_tab_index = i
                    await tab.update_info()
                    return f"Switched to tab {i}: {tab.url} ({tab.title})"
            return f"Tab not found: {target}"

    async def browser_list_tabs(self) -> str:
        """현재 열린 탭 목록을 반환한다."""
        self._ensure_connected()
        lines: list[str] = []
        for i, tab in enumerate(self._tabs):
            await tab.update_info()
            marker = " ◀" if i == self._focused_tab_index else ""
            lines.append(f"  [{i}] {tab.url} - {tab.title}{marker}")
        return "Open tabs:\n" + "\n".join(lines) if lines else "No tabs open"

    async def browser_get_focused_tab(self) -> str:
        """현재 focused tab 정보를 반환한다."""
        self._ensure_connected()
        tab = self._tabs[self._focused_tab_index]
        await tab.update_info()
        return f"[{self._focused_tab_index}] {tab.url} ({tab.title})"

    # ─── 네비게이션 ──────────────────────────────────────────

    async def browser_navigate(self, url: str) -> str:
        """URL로 이동한다."""
        page = self._ensure_page()
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            tab = self._tabs[self._focused_tab_index]
            await tab.update_info()
            status = response.status if response else "unknown"
            return f"Navigated to {tab.url} (status: {status}, title: {tab.title})"
        except Exception as e:
            return f"Navigation error: {e}"

    async def browser_go_back(self) -> str:
        """브라우저 뒤로 가기."""
        page = self._ensure_page()
        try:
            await page.go_back(wait_until="domcontentloaded")
            tab = self._tabs[self._focused_tab_index]
            await tab.update_info()
            return f"Went back to {tab.url} ({tab.title})"
        except Exception as e:
            return f"Go back error: {e}"

    async def browser_get_url(self) -> str:
        """현재 URL을 반환한다."""
        page = self._ensure_page()
        return page.url

    async def browser_get_title(self) -> str:
        """페이지 제목을 반환한다."""
        page = self._ensure_page()
        try:
            return await page.title()
        except Exception:
            return ""

    # ─── 요소 조작 ──────────────────────────────────────────

    async def browser_click(
        self,
        selector: str,
        text: Optional[str] = None,
        aria_label: Optional[str] = None,
        role: Optional[str] = None,
    ) -> str:
        """선택자로 요소를 찾아 클릭한다 (self-healing 포함)."""
        page = self._ensure_page()
        healing = SelfHealingSelector(page)
        locator = await healing.find_element(
            selector, text=text, aria_label=aria_label, role=role
        )
        if locator is None:
            return f"Click failed: element not found ({selector})"

        try:
            await locator.click(timeout=5000)
            return f"Clicked: {selector}"
        except Exception as e:
            return f"Click error: {e}"

    async def browser_type(
        self,
        selector: str,
        text: str,
        clear: bool = False,
    ) -> str:
        """선택자로 찾은 입력 필드에 텍스트를 입력한다."""
        page = self._ensure_page()
        healing = SelfHealingSelector(page)
        locator = await healing.find_element(selector)
        if locator is None:
            return f"Type failed: element not found ({selector})"

        try:
            if clear:
                await locator.clear()
            await locator.fill(text)
            return f"Typed into {selector}: {text[:50]}{'...' if len(text) > 50 else ''}"
        except Exception as e:
            return f"Type error: {e}"

    async def browser_select_option(
        self,
        selector: str,
        value: str,
    ) -> str:
        """드롭다운에서 옵션을 선택한다."""
        page = self._ensure_page()
        healing = SelfHealingSelector(page)
        locator = await healing.find_element(selector)
        if locator is None:
            return f"Select failed: element not found ({selector})"

        try:
            await locator.select_option(value=value)
            return f"Selected option '{value}' in {selector}"
        except Exception as e:
            return f"Select error: {e}"

    async def browser_hover(self, selector: str) -> str:
        """요소 위에 마우스를 호버한다."""
        page = self._ensure_page()
        healing = SelfHealingSelector(page)
        locator = await healing.find_element(selector)
        if locator is None:
            return f"Hover failed: element not found ({selector})"

        try:
            await locator.hover(timeout=5000)
            return f"Hovered: {selector}"
        except Exception as e:
            return f"Hover error: {e}"

    async def browser_scroll(
        self,
        direction: str = "down",
        amount: Optional[int] = None,
    ) -> str:
        """페이지를 스크롤한다.

        Args:
            direction: up, down, top, bottom
            amount: 스크롤 픽셀 양 (지정하지 않으면 기본값 사용)
        """
        page = self._ensure_page()
        try:
            if direction == "top":
                await page.evaluate("window.scrollTo(0, 0)")
            elif direction == "bottom":
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            else:
                px = amount or 500
                if direction == "up":
                    px = -px
                await page.evaluate(f"window.scrollBy(0, {px})")
            return f"Scrolled {direction}"
        except Exception as e:
            return f"Scroll error: {e}"

    async def browser_focus(self, selector: str) -> str:
        """요소에 포커스를 맞춘다."""
        page = self._ensure_page()
        healing = SelfHealingSelector(page)
        locator = await healing.find_element(selector)
        if locator is None:
            return f"Focus failed: element not found ({selector})"

        try:
            await locator.focus(timeout=5000)
            return f"Focused: {selector}"
        except Exception as e:
            return f"Focus error: {e}"

    async def browser_press_key(self, key: str) -> str:
        """키보드 키를 입력한다 (Enter, Tab, Escape 등)."""
        page = self._ensure_page()
        try:
            await page.keyboard.press(key)
            return f"Pressed key: {key}"
        except Exception as e:
            return f"Press key error: {e}"

    async def browser_wait(self, milliseconds: int) -> str:
        """지정된 시간 동안 대기한다."""
        try:
            await asyncio.sleep(milliseconds / 1000)
            return f"Waited {milliseconds}ms"
        except Exception as e:
            return f"Wait error: {e}"

    # ─── 정보 조회 ──────────────────────────────────────────

    async def chrome_health(self) -> str:
        """Chrome/CDP 연결 상태 및 브라우저 종합 정보를 반환한다.

        연결 여부, 탭 개수, 포커스된 탭 정보, CDP 설정,
        Chrome 경로 등 브라우저 상태를 한 번에 파악할 수 있다.
        연결이 안 된 상태에서도 안전하게 호출 가능하다.
        """
        info: dict[str, Any] = {
            "connected": self._connected,
            "cdp_host": self._cdp_host,
            "cdp_port": self._cdp_port,
            "headless": self._launcher._headless,
        }

        if self._connected:
            info["tab_count"] = len(self._tabs)

            focused = self._tabs[self._focused_tab_index]
            await focused.update_info()
            info["focused_tab"] = {
                "index": self._focused_tab_index,
                "url": focused.url,
                "title": focused.title,
            }

            tabs_list = []
            for i, tab in enumerate(self._tabs):
                await tab.update_info()
                tabs_list.append({
                    "index": i,
                    "url": tab.url,
                    "title": tab.title,
                    "focused": i == self._focused_tab_index,
                })
            info["tabs"] = tabs_list

        chrome_pid = self._launcher.get_chrome_pid()
        if chrome_pid is not None:
            info["chrome_pid"] = chrome_pid

        return json.dumps(info, indent=2, ensure_ascii=False)

    async def chrome_start(self) -> str:
        """Chrome/CDP에 연결을 시도하고, 연결이 안 되어 있으면 Chrome을 실행한 후 연결한다.

        Returns:
            연결 결과 메시지
        """
        if self._connected:
            return "Already connected"

        result = await self.connect(launch_if_needed=True)
        if self._connected:
            return f"Chrome started and connected successfully.\n{await self.chrome_health()}"
        else:
            return f"Failed to start Chrome: {result}"

    async def browser_snapshot(self) -> str:
        """현재 페이지의 접근성 트리를 반환한다."""
        page = self._ensure_page()
        return await get_accessibility_tree(page)

    async def browser_get_dom(self) -> str:
        """현재 페이지의 DOM HTML을 반환한다 (간략화)."""
        page = self._ensure_page()
        try:
            html = await page.content()
            if len(html) > 50000:
                html = html[:50000] + "\n... (truncated)"
            return html
        except Exception as e:
            return f"DOM error: {e}"

    async def browser_screenshot(
        self,
        full_page: bool = False,
        filename: Optional[str] = None,
    ) -> str:
        """스크린샷을 워크스페이스 폴더에 PNG 파일로 저장한다.

        파일명: {yyyyMMdd-HHmmss}.png
        저장 경로: {workspace_dir}/screenshots/
        """
        page = self._ensure_page()

        screenshots_dir = Path(self._workspace_dir) / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        if filename is None:
            filename = datetime.now().strftime(SCREENSHOT_DATE_FMT) + ".png"

        filepath = screenshots_dir / filename
        try:
            await page.screenshot(path=str(filepath), full_page=full_page)
            return str(filepath.resolve())
        except Exception as e:
            return f"Screenshot error: {e}"

    # ─── 스크립트 실행 ──────────────────────────────────────

    async def browser_execute_script(self, script: str) -> str:
        """JavaScript를 실행하고 결과를 반환한다."""
        page = self._ensure_page()
        try:
            result = await page.evaluate(script)
            return str(result) if result is not None else "(undefined)"
        except Exception as e:
            return f"Script error: {e}"

    # ─── 크론 도구 (스텁 — 실제 구현은 cron/ 레이어에서) ────

    async def cron_add(
        self, name: str, schedule: str, prompt: str
    ) -> str:
        """새 크론 작업을 등록한다 (3차 레이어에서 구현 예정)."""
        return "cron_add: not yet implemented (layer 3)"

    async def cron_list(self) -> str:
        """등록된 크론 작업 목록을 조회한다."""
        return "cron_list: not yet implemented (layer 3)"

    async def cron_edit(
        self,
        job_id: str,
        name: Optional[str] = None,
        schedule: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """크론 작업을 수정한다."""
        return "cron_edit: not yet implemented (layer 3)"

    async def cron_toggle(self, job_id: str) -> str:
        """크론 작업 활성/비활성을 전환한다."""
        return "cron_toggle: not yet implemented (layer 3)"

    async def cron_remove(self, job_id: str) -> str:
        """크론 작업을 삭제한다."""
        return "cron_remove: not yet implemented (layer 3)"

    # ─── 메모리 검색 (스텁 — database.py FTS5 활용) ────────

    async def memory_search(self, query: str, session_id: str = "") -> str:
        """세션 내 과거 대화를 키워드로 검색한다.

        database.py의 search_messages를 활용하여 FTS5 검색을 수행한다.
        5차 레이어에서 Database 인스턴스를 주입받아 사용한다.
        """
        return f"memory_search: not yet integrated (query={query})"
