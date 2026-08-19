"""PlaywrightManager — Playwright 브라우저 제어 서비스 클래스 (핵심)."""

import asyncio
import contextvars
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

from src.browser.accessibility import (
    get_accessibility_tree_with_refs,
    get_subtree_detail,
)
from src.browser.self_healing import SelfHealingSelector, _SAFE_CLASS_RE
from src.chrome.launcher import ChromeLauncher

logger = logging.getLogger("psjba.browser.manager")

SCREENSHOT_DATE_FMT = "%Y%m%d-%H%M%S"

# asyncio task별 현재 세션 ID (execute_tool에서 설정, cron/agent 간 격리)
_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar("session_id", default="")


def set_session_id(sid: str) -> None:
    """현재 asyncio task에 세션 ID를 설정한다 (a11y refs 격리용)."""
    _current_session_id.set(sid)


class _TabInfo:
    """내부 탭 정보 저장소."""

    def __init__(self, page: Page, url: str = "about:blank") -> None:
        self.page = page
        self.url = url
        self.title: str = ""
        self.dialog: Optional[dict] = None  # {type, message, default} — override dialog

    async def update_info(self) -> None:
        try:
            self.url = self.page.url
            self.title = await self.page.title()
        except Exception as e:
            logger.debug("TabInfo.update_info failed: %s", e)

    async def check_dialog(self) -> Optional[dict]:
        """Override된 dialog 정보를 page에서 읽어온다."""
        try:
            d = await self.page.evaluate("window.__psjba_dialog")
            if d and not d.get("_handled"):
                await self.page.evaluate("window.__psjba_dialog._handled = true")
                self.dialog = d
                return d
        except Exception as e:
            logger.debug("TabInfo.check_dialog failed: %s", e)
        return None

    async def clear_dialog(self) -> None:
        self.dialog = None
        try:
            await self.page.evaluate("window.__psjba_dialog = null")
        except Exception as e:
            logger.debug("TabInfo.clear_dialog failed: %s", e)


class ExtensionError(RuntimeError):
    """확장 프로그램 관련 오류."""
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
        # session_id → refs 매핑 (cron/agent 간 격리)
        self._a11y_refs: dict[str, dict[int, dict[str, Any]]] = {}
        self._db: Optional[Any] = None
        self._cron_scheduler: Optional[Any] = None
        # 확장 프로그램 (포커스 탭 감지)
        self._ext_id: Optional[str] = None
        self._ext_available: bool = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def focused_page(self) -> Optional[Page]:
        if 0 <= self._focused_tab_index < len(self._tabs):
            return self._tabs[self._focused_tab_index].page
        return None

    def _get_session_refs(self, session_id: str = "") -> dict[int, dict[str, Any]]:
        """현재 task의 session에 귀속된 a11y refs를 반환한다."""
        sid = session_id or _current_session_id.get()
        return self._a11y_refs.get(sid, {})

    def _set_session_refs(self, refs: dict[int, dict[str, Any]], session_id: str = "") -> None:
        """현재 task의 session에 a11y refs를 저장한다."""
        sid = session_id or _current_session_id.get()
        self._a11y_refs[sid] = refs

    def _clear_session_refs(self, session_id: str = "") -> None:
        """현재 task의 session a11y refs를 제거한다 (메모리 관리)."""
        sid = session_id or _current_session_id.get()
        self._a11y_refs.pop(sid, None)

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

    async def _inject_dialog_override(self) -> None:
        """모든 context/tab에 dialog override init script를 주입한다.
        
        CDP가 native alert/confirm을 supress하기 때문에, JS 레벨에서
        window.confirm/window.alert/window.prompt를 오버라이드하여
        호출 내용을 window.__psjba_dialog에 기록한다.
        """
        script = """
        (function() {
          if (window.__psjba_injected) return;
          window.__psjba_injected = true;
          window.__psjba_dialog = null;
          window.__psjba_confirm_result = null;   // null = use default (true)
          window.__psjba_prompt_result = null;     // null = use default (defaultVal)
          window.alert = function(msg) {
            window.__psjba_dialog = {type:'alert',message:String(msg)};
          };
          window.confirm = function(msg) {
            window.__psjba_dialog = {type:'confirm',message:String(msg)};
            var r = window.__psjba_confirm_result;
            if (r !== null) {
              window.__psjba_confirm_result = null;
              return r;
            }
            return true;
          };
          window.prompt = function(msg,defaultVal) {
            window.__psjba_dialog = {type:'prompt',message:String(msg),default:String(defaultVal)};
            var pv = window.__psjba_prompt_result;
            if (pv !== null) {
              window.__psjba_prompt_result = null;
              return pv;
            }
            return defaultVal;
          };
        })();
        """
        if self._context:
            await self._context.add_init_script(script)
        for tab in self._tabs:
            try:
                await tab.page.evaluate(script)
            except Exception as e:
                logger.debug("_inject_init_script_to_all_tabs failed for tab %s: %s", tab.index, e)

    async def connect(self, launch_if_needed: bool = False) -> str:
        """CDP에 연결하고 확장 프로그램을 설정한다.

        확장 프로그램이 로드되지 않았으면 launcher가 설정된 경우
        Chrome을 재시작하여 --load-extension을 적용한다.

        Raises:
            ExtensionError: Chrome/확장 연결 실패
        """
        if self._connected:
            logger.debug("connect() called but already connected")
            return "Already connected"

        logger.info("Connecting to Chrome CDP (host=%s, port=%s)...",
                    self._cdp_host, self._cdp_port)

        try:
            quick_ok = await self._launcher.quick_check()
            if not quick_ok:
                if launch_if_needed:
                    logger.info("Chrome not detected, launching...")
                    launched = await self._launcher.launch_chrome()
                    if not launched:
                        raise ExtensionError("Chrome을 실행할 수 없습니다.")
                else:
                    raise ExtensionError("Chrome이 실행 중이지 않습니다.")

            await self._connect_cdp()

            if not self._ext_available:
                if self._launcher.chrome_path:
                    logger.warning("Extension not found — restarting Chrome")
                    await self.disconnect()
                    await self._launcher.kill_chrome()
                    launched = await self._launcher.launch_chrome()
                    if not launched:
                        raise ExtensionError(
                            "Chrome 재시작 후에도 확장 프로그램을 불러올 수 없습니다.\n"
                            "Chrome을 최신 버전으로 업데이트한 후 다시 시도해 주세요."
                        )
                    await self._connect_cdp()
                else:
                    raise ExtensionError(
                        "Chrome 확장 프로그램(PSJBA Focus Tracker)을 불러올 수 없습니다.\n"
                        "Chrome을 최신 버전으로 업데이트한 후 다시 시도해 주세요."
                    )

            return "Chrome connected"

        except ExtensionError:
            self._cleanup_state()
            raise
        except Exception as e:
            logger.error("Chrome connection error: %s [%s]", e, type(e).__name__)
            self._cleanup_state()
            raise ExtensionError(f"Chrome 연결 중 오류 발생: {e}") from e

    async def _connect_cdp(self) -> None:
        """Playwright로 CDP WebSocket에 연결하고 탭/확장을 설정한다."""
        self._playwright = await async_playwright().start()
        loop = asyncio.get_event_loop()
        ws_url = await loop.run_in_executor(None, self._launcher.get_cdp_ws_endpoint)
        logger.info("Connecting to CDP via WebSocket: %s", ws_url)
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

        await self._inject_dialog_override()
        self._connected = True
        self._ext_id = await self._setup_extension()
        self._ext_available = self._ext_id is not None

        url = self.focused_page.url if self.focused_page else "about:blank"
        logger.info("CDP connected (%d tabs, url=%s, ext=%s)",
                    len(self._tabs), url, self._ext_available)

    async def disconnect(self) -> str:
        """CDP 연결만 해제한다 (Chrome 프로세스는 유지).

        연결 종료 시 --load-extension으로 로드된 확장도 제거한다.
        """
        if not self._connected:
            logger.debug("disconnect() called but not connected")
            return "Not connected"

        # 연결 종료 전에 --load-extension으로 로드된 확장 제거
        if self._ext_available and self._ext_id:
            try:
                browser_cdp = await self._browser.new_browser_cdp_session()
                await browser_cdp.send("Extensions.uninstall", {
                    "id": self._ext_id,
                })
                logger.info("Extension uninstalled: id=%s", self._ext_id)
            except Exception as e:
                logger.debug("Extensions.uninstall failed: %s", e)

        logger.info("Disconnecting from Chrome CDP...")
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.error("Disconnect error: %s", e)
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
        self._ext_id = None
        self._ext_available = False

    async def _setup_extension(self) -> Optional[str]:
        """확장 프로그램을 로드하여 포커스 탭 감지를 설정한다.

        Fallback 순서:
          1. --load-extension으로 이미 로드된 확장 확인
          2. Extensions.loadUnpacked로 런타임 로드
          3. 둘 다 실패 → 경고 (포커스 탐지 불가)

        Returns:
            확장 ID (로드 성공) 또는 None (실패)
        """
        ext_path = str(
            Path(__file__).resolve().parent.parent.parent / "chrome" / "extension"
        )
        ext_path = os.path.normpath(ext_path)

        if not os.path.isdir(ext_path):
            logger.warning("Extension directory not found: %s", ext_path)
            return None

        try:
            browser_cdp = await self._browser.new_browser_cdp_session()
        except Exception as e:
            logger.warning("Extension setup: CDP session unavailable (%s)", e)
            return None

        # 1) 이미 로드된 unpacked 확장 중에서 찾기
        try:
            exts = await browser_cdp.send("Extensions.getExtensions")
            for ext in exts.get("extensions", []):
                ep = os.path.normpath(ext.get("path", ""))
                if ep == ext_path:
                    ext_id = ext["id"]
                    logger.info("Extension already loaded: id=%s path=%s", ext_id, ext_path)
                    return ext_id
        except Exception as e:
            logger.debug("Extensions.getExtensions not available: %s", e)

        # 2) Extensions.loadUnpacked로 런타임 로드
        try:
            result = await browser_cdp.send("Extensions.loadUnpacked", {
                "path": ext_path,
            })
            ext_id = result.get("id")
            logger.info("Extension loaded via loadUnpacked: id=%s", ext_id)
            return ext_id
        except Exception as e:
            logger.warning("Extension load failed — focus detection disabled: %s", e)
            return None

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("Not connected to Chrome. Call connect() first.")

    def _ensure_page(self, tab_index: Optional[int] = None) -> Page:
        self._ensure_connected()
        idx = tab_index if tab_index is not None else self._focused_tab_index
        if 0 <= idx < len(self._tabs):
            return self._tabs[idx].page
        raise RuntimeError(f"Invalid tab index: {idx} (focused: {self._focused_tab_index})")

    # ─── 탭 관리 ────────────────────────────────────────────

    async def browser_new_tab(self, url: Optional[str] = None) -> str:
        logger.info("browser_new_tab: url=%s", url)
        """새 탭을 열고 해당 탭으로 전환한다."""
        self._ensure_connected()
        assert self._context is not None
        page = await self._context.new_page()
        await self._inject_dialog_override()
        if url:
            await page.goto(url, wait_until="domcontentloaded")
        tab = _TabInfo(page, url or "about:blank")
        await tab.update_info()
        self._tabs.append(tab)
        self._focused_tab_index = len(self._tabs) - 1
        return f"New tab opened: {tab.url} ({tab.title})"

    async def browser_close_tab(self, target: str = "") -> str:
        logger.info("browser_close_tab: target=%s", target)
        """탭을 닫는다. target이 없으면 현재 탭. target은 인덱스(0-base) 또는 URL."""
        self._ensure_connected()
        if not self._tabs:
            return "No tabs to close"

        if target:
            target_stripped = target.strip()
            try:
                idx = int(target_stripped)
                if not (0 <= idx < len(self._tabs)):
                    return f"Invalid tab index: {idx} (have {len(self._tabs)} tabs)"
            except ValueError:
                idx = -1
                for i, tab in enumerate(self._tabs):
                    if tab.url == target_stripped or target_stripped in tab.url:
                        idx = i
                        break
                if idx < 0:
                    return f"Tab not found: {target}"
        else:
            idx = self._focused_tab_index

        tab = self._tabs[idx]
        url = tab.url
        try:
            await tab.page.close()
        except Exception as e:
            logger.debug("close_tab: page close failed for tab %s: %s", idx, e)

        self._tabs.pop(idx)

        if not self._tabs:
            page = await self._context.new_page()
            self._tabs.append(_TabInfo(page))
            self._focused_tab_index = 0
        elif idx <= self._focused_tab_index and self._focused_tab_index > 0:
            self._focused_tab_index -= 1
        elif self._focused_tab_index >= len(self._tabs):
            self._focused_tab_index = len(self._tabs) - 1

        focused = self._tabs[self._focused_tab_index]
        await focused.update_info()
        return f"Closed tab: {url}. Now on: {focused.url} ({focused.title})"

    async def browser_switch_tab(self, target: str) -> str:
        logger.info("browser_switch_tab: target=%s", target)
        """탭을 전환한다. target은 인덱스(0-based) 또는 URL 문자열."""
        self._ensure_connected()

        try:
            idx = int(target)
            if 0 <= idx < len(self._tabs):
                self._focused_tab_index = idx
                tab = self._tabs[idx]
                await tab.page.bring_to_front()
                await tab.update_info()
                return f"Switched to tab {idx}: {tab.url} ({tab.title})"
            return f"Invalid tab index: {idx} (have {len(self._tabs)} tabs)"
        except ValueError:
            for i, tab in enumerate(self._tabs):
                if tab.url == target or target in tab.url:
                    self._focused_tab_index = i
                    await tab.page.bring_to_front()
                    await tab.update_info()
                    return f"Switched to tab {i}: {tab.url} ({tab.title})"
            return f"Tab not found: {target}"

    async def _refresh_tabs(self) -> None:
        """CDP에서 현재 페이지 목록을 다시 읽어 self._tabs를 재구성한다.

        외부에서 열린 탭(context.on("page")을 못 탄 경우)이나
        닫힌 탭을 반영하기 위해 매번 CDP에 직접 쿼리한다.
        기존 _TabInfo의 dialog override 상태는 보존한다.
        """
        if not self._context:
            return
        current_pages = self._context.pages
        old_by_page = {id(t.page): t for t in self._tabs}
        new_tabs: list[_TabInfo] = []
        for p in current_pages:
            tid = id(p)
            if tid in old_by_page:
                new_tabs.append(old_by_page[tid])
            else:
                new_tabs.append(_TabInfo(p))
        self._tabs = new_tabs
        if self._focused_tab_index >= len(self._tabs):
            self._focused_tab_index = max(0, len(self._tabs) - 1)

        # 확장 프로그램 content script DOM attribute로 포커스 보정
        if self._ext_available:
            for i, tab in enumerate(self._tabs):
                try:
                    active = await tab.page.evaluate(
                        "document.documentElement.getAttribute('data-psjba-active')"
                    )
                    if active == "true":
                        self._focused_tab_index = i
                        break
                except Exception as e:
                    logger.debug("_refresh_tabs: tab %d ext attribute eval failed: %s", i, e)
                    continue

    async def browser_list_tabs(self) -> str:
        logger.info("browser_list_tabs called")
        """현재 열린 탭 목록을 반환한다."""
        self._ensure_connected()
        await self._refresh_tabs()
        lines: list[str] = []
        for i, tab in enumerate(self._tabs):
            await tab.update_info()
            if i == self._focused_tab_index:
                lines.append(f"  [{i}] {tab.url} - {tab.title} ◀ FOCUSED")
            else:
                lines.append(f"  [{i}] {tab.url} - {tab.title}")
        return "Open tabs:\n" + "\n".join(lines) if lines else "No tabs open"

    # ─── 네비게이션 ──────────────────────────────────────────

    async def browser_navigate(self, url: str) -> str:
        logger.info("browser_navigate: %s", url)
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
        logger.info("browser_go_back called")
        """브라우저 뒤로 가기."""
        page = self._ensure_page()
        try:
            await page.go_back(wait_until="domcontentloaded")
            tab = self._tabs[self._focused_tab_index]
            await tab.update_info()
            return f"Went back to {tab.url} ({tab.title})"
        except Exception as e:
            return f"Go back error: {e}"

    # ─── 헬퍼: CDP querySelector 로 노드 재발견 ──────────
    @staticmethod
    def _build_click_selector(node: dict[str, Any]) -> str:
        """ref 노드 정보로 CDP DOM.querySelector용 CSS 선택자를 만든다.

        Fallback chain (높은 정확도 순):
          1. tag#id                        — id가 가장 정확
          2. tag[role="X"]:nth-of-type(N)  — role + nth-of-type으로 unique
          3. tag[attr="val"]                — aria-label, placeholder, name, type (중복 많음)
          4. tag.safe-class[role="X"]       — 위험 문자 없는 클래스만
        """
        tag = node.get("tag", "") or node.get("_tag", "")
        nid = node.get("_id", "")
        aria_label = node.get("_aria_label", "")
        nclass = node.get("_class", "")
        ntype = node.get("_type", "")
        name = node.get("name", "")
        role = node.get("role", "")
        placeholder = node.get("_placeholder", "")
        form_name = node.get("_form_name", "")
        nth = node.get("_nth_of_type", 0)

        if not tag:
            return ""

        # 1차: id
        if nid:
            return f"{tag}#{nid}"

        # 2차: role + nth-of-type (aria/name/type보다 nth가 더 unique)
        if role:
            parts = [tag]
            if nclass:
                safe_classes = [
                    c for c in nclass.split()
                    if _SAFE_CLASS_RE.match(c)
                ]
                if safe_classes:
                    parts.append("." + ".".join(safe_classes))
            parts.append(f'[role="{role}"]')
            if nth > 0:
                parts.append(f":nth-of-type({nth})")
            return "".join(parts)

        # 3차: HTML attribute (중복 가능성 높음, selector 검증 시 refined)
        if aria_label:
            escaped = aria_label.replace('"', '\\"').replace("'", "\\'")
            return f'{tag}[aria-label="{escaped}"]'
        if placeholder:
            return f'{tag}[placeholder="{placeholder}"]'
        if form_name:
            return f'{tag}[name="{form_name}"]'
        if name:
            escaped = name.replace('"', '\\"').replace("'", "\\'")
            return f'{tag}[aria-label="{escaped}"]'
        if ntype:
            return f'{tag}[type="{ntype}"]'

        # 4차: tag + safe class만 (role 없음, attr 없음)
        parts = [tag]
        if nclass:
            safe_classes = [
                c for c in nclass.split()
                if _SAFE_CLASS_RE.match(c)
            ]
            if safe_classes:
                parts.append("." + ".".join(safe_classes))
        return "".join(parts)

    # ─── 요소 조작 ──────────────────────────────────────────

    async def browser_type(
        self,
        selector: str,
        text: str,
        clear: bool = False,
    ) -> str:
        logger.info("browser_type: selector=%s, text=%s", selector, text[:50])
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
            result = f"Typed into {selector}: {text[:50]}{'...' if len(text) > 50 else ''}"
        except Exception as e:
            result = f"Type error: {e}"

        d = await self._tabs[self._focused_tab_index].check_dialog()
        if d:
            result += f"\n⚠️ Dialog recorded: [{d['type']}] {d.get('message','')}"
        return result

    async def browser_select_option(
        self,
        selector: str,
        value: str,
    ) -> str:
        logger.info("browser_select_option: selector=%s, value=%s", selector, value)
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
        logger.info("browser_hover: %s", selector)
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
        logger.info("browser_scroll: direction=%s, amount=%s", direction, amount)
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
        logger.info("browser_focus: %s", selector)
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
        logger.info("browser_press_key: %s", key)
        """키보드 키를 입력한다 (Enter, Tab, Escape 등)."""
        page = self._ensure_page()
        try:
            await page.keyboard.press(key)
            await self._tabs[self._focused_tab_index].update_info()
            tab = self._tabs[self._focused_tab_index]
            return f"Pressed key: {key} [url={tab.url}, title={tab.title}]"
        except Exception as e:
            return f"Press key error: {e}"

    async def browser_wait(self, milliseconds: int) -> str:
        logger.info("browser_wait: %dms", milliseconds)
        """지정된 시간 동안 대기한다."""
        try:
            await asyncio.sleep(milliseconds / 1000)
            return f"Waited {milliseconds}ms"
        except Exception as e:
            return f"Wait error: {e}"

    # ─── Dialog override 처리 ──────────────────────────────────

    async def check_all_tabs_dialog(self) -> list[dict]:
        """모든 탭의 override된 dialog 정보를 수집한다.

        Returns:
            [{tab_index, type, message, default}, ...]
        """
        results = []
        for i, tab in enumerate(self._tabs):
            d = await tab.check_dialog()
            if d:
                results.append({
                    "tab_index": i,
                    "tab_url": tab.url,
                    "tab_title": tab.title,
                    "type": d.get("type"),
                    "message": d.get("message"),
                    "default": d.get("default"),
                })
        return results

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

    async def get_browser_context(self) -> str:
        """현재 브라우저 상태를 사람이 읽기 쉬운 문자열로 반환한다.

        프롬프트에 자동 첨부되어 LLM 에 컨텍스트를 제공한다.
        연결이 안 된 상태에서는 기본 메시지만 반환한다.
        """
        if not self._connected or not self._tabs:
            return "[Browser: not connected]"

        await self._refresh_tabs()
        parts = [f"[Browser: connected ({len(self._tabs)} tabs)]"]
        for i, tab in enumerate(self._tabs):
            await tab.update_info()
            marker = " ◀ FOCUSED" if i == self._focused_tab_index else ""
            parts.append(f"  Tab {i}: {tab.url}{' (' + tab.title + ')' if tab.title else ''}{marker}")
            # pending dialog 정보도 표시
            if tab.dialog:
                d = tab.dialog
                parts.append(f"       ⚠️ Dialog: [{d.get('type')}] {d.get('message', '')}")
        return "\n".join(parts)

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

    async def browser_snapshot(self, tab_index: Optional[int] = None,
                              ref: Optional[int] = None) -> str:
        logger.info("browser_snapshot called (ref=%s)", ref)
        """특정 탭의 접근성 트리를 반환하고 ref 매핑을 저장한다.

        ref가 주어지면 해당 ref의 상세 서브트리(3 depth, detail 모드)를 반환한다.
        ref가 없는 전체 트리 요청 시에는 이전 refs + counter를 초기화한다 (메모리 관리).

        Args:
            tab_index: 대상 탭 인덱스 (None이면 포커스 탭)
            ref: 상세 조회할 [#N] ref 번호 (None이면 전체 트리)
        """
        if ref is not None:
            session_refs = self._get_session_refs()
            if not session_refs:
                return ("No a11y refs available. "
                        "Call browser_snapshot() first to get the accessibility tree.")
            page = self._ensure_page(tab_index)
            return await get_subtree_detail(page, ref, session_refs)

        # 전체 트리 요청: 이전 refs + counter 초기화
        self._clear_session_refs()
        from src.browser.accessibility import _reset_ref_counter
        _reset_ref_counter()

        page = self._ensure_page(tab_index)
        text, refs = await get_accessibility_tree_with_refs(page)
        if refs:
            logger.info("a11y snapshot: %d interactive refs", len(refs))
            # selector는 _build_click_selector로 동기 계산 (page.evaluate 없음)
            # 중복 해소는 browser_click 등 사용 시점에 CDP로 refined
            for ref, node in refs.items():
                sel = self._build_click_selector(node)
                if sel:
                    node["_selector"] = sel

        self._set_session_refs(refs)
        return text

    async def browser_click(self, ref: int, tab_index: Optional[int] = None) -> str:
        """접근성 트리 ref 번호로 요소를 찾아 클릭한다.

        backendNodeId → CDP getBoxModel + dispatchMouseEvent 로 정확히 클릭.
        CDP 실패 시 Playwright get_by_role 로 폴백.

        Args:
            ref: browser_snapshot() 결과에 표시된 [#N] 번호
            tab_index: 대상 탭 인덱스 (None이면 포커스 탭)
        """
        import time
        t0 = time.monotonic()
        session_refs = self._get_session_refs()
        node = session_refs.get(ref)
        if node is None:
            return (f"Click by ref failed: ref #{ref} not found. "
                    "Call browser_snapshot() first to refresh the tree.")

        page = self._ensure_page(tab_index)
        idx = tab_index if tab_index is not None else self._focused_tab_index
        role = node.get("role", "")
        name = node.get("name", "")
        tag = node.get("tag", "") or node.get("_tag", "")
        nid = node.get("_id", "")
        backend_id = node.get("_backendNodeId")

        click_desc = f"ref #{ref} ({role}"
        if name:
            click_desc += f" \"{name}\""
        if tag:
            click_desc += f" <{tag}>"
        if nid:
            click_desc += f" #{nid}"
        click_desc += ")"

        # ─── CDP click helper ──────────────────────────────
        async def _cdp_dispatch(cdp_session, nid: int) -> tuple[bool, float, float]:
            """CDP nodeId로 scrollIntoView + getBoxModel + mouse click.
            Returns (success, cx, cy)."""
            try:
                await cdp_session.send("DOM.scrollIntoViewIfNeeded", {"nodeId": nid})
                box = await cdp_session.send("DOM.getBoxModel", {"nodeId": nid})
                model = box.get("model", {})
                content = model.get("content", [])
                if len(content) < 4:
                    return False, 0, 0
                cx = (content[0] + content[4]) / 2 if len(content) >= 8 else content[0] + content[2] / 2
                cy = (content[1] + content[5]) / 2 if len(content) >= 8 else content[1] + content[3] / 2

                # 디버그: 누르려는 노드 vs 실제 (cx,cy) 위치의 노드 비교
                if logger.isEnabledFor(logging.DEBUG):
                    try:
                        target = await cdp_session.send("DOM.describeNode", {"nodeId": nid})
                        # raw_content는 getNodeForLocation 실패와 무관하게 먼저 로그
                        logger.debug("_cdp_dispatch: target=%s raw_content=%s center=(%.0f,%.0f) viewport=(%s,%s)",
                                     target.get("node", {}).get("nodeName", "?"),
                                     content,
                                     cx, cy,
                                     model.get("width", "?"), model.get("height", "?"))
                    except Exception as e:
                        logger.debug("_cdp_dispatch describeNode failed: %s", e)
                    # 별도 try: getNodeForLocation은 실패할 수 있음 (뷰포트 밖 좌표 등)
                    try:
                        hit = await cdp_session.send("DOM.getNodeForLocation", {
                            "x": int(cx), "y": int(cy),
                            "includeUserAgentShadowDOM": True,
                            "ignorePointerEventsNone": False,
                        })
                        hit_desc = await cdp_session.send("DOM.describeNode", {
                            "nodeId": hit.get("nodeId")
                        }) if hit.get("nodeId") else {"node": {"nodeName": "(none)"}}
                        logger.debug("_cdp_dispatch: hit at (%.0f,%.0f) = %s",
                                     cx, cy,
                                     hit_desc.get("node", {}).get("nodeName", "?"))
                    except Exception as e:
                        logger.debug("_cdp_dispatch: getNodeForLocation at (%.0f,%.0f) failed: %s",
                                     cx, cy, e)

                await cdp_session.send("Input.dispatchMouseEvent", {
                    "type": "mousePressed", "x": cx, "y": cy,
                    "button": "left", "clickCount": 1,
                })
                await cdp_session.send("Input.dispatchMouseEvent", {
                    "type": "mouseReleased", "x": cx, "y": cy,
                    "button": "left", "clickCount": 1,
                })
                return True, cx, cy
            except Exception as e:
                logger.warning("CDP dispatch failed for nodeId=%s: %s", nid, e)
                return False, 0, 0

        cdp = None

        # 1차: CDP backendNodeId 기반 정밀 클릭
        if backend_id is not None:
            try:
                cdp = await page.context.new_cdp_session(page)
                # pushNodesByBackendIdsToFrontend는 DOM document 필요
                await cdp.send("DOM.getDocument", {})
                pn = await cdp.send("DOM.pushNodesByBackendIdsToFrontend", {
                    "backendNodeIds": [backend_id],
                })
                nids = pn.get("nodeIds", [])
                node_id = nids[0] if nids else None
                if node_id:
                    ok, cx, cy = await _cdp_dispatch(cdp, node_id)
                    if ok:
                        await self._tabs[idx].update_info()
                        tab = self._tabs[idx]
                        elapsed = int((time.monotonic() - t0) * 1000)
                        logger.info("browser_click: clicked %s via CDP backendNodeId=%s (%.0f,%.0f) %dms",
                                     click_desc, backend_id, cx, cy, elapsed)
                        result = (f"Clicked {click_desc} [CDP] "
                                  f"[url={tab.url}, title={tab.title}]")
                        d = await self._tabs[idx].check_dialog()
                        if d:
                            result += f"\n⚠️ Dialog recorded: [{d['type']}] {d.get('message','')}"
                        return result
            except Exception as e:
                logger.warning("CDP pushNodesByBackendIdsToFrontend for %s failed: %s. → CDP re-discovery", click_desc, e)

        # 1.5차: CSS selector로 CDP 재발견 → CDP 클릭 (backendNodeId=null or stale 모두)
        try:
            selector = node.get("_selector", "")
            if selector:
                if cdp is None:
                    cdp = await page.context.new_cdp_session(page)
                doc = await cdp.send("DOM.getDocument", {})
                root_id = doc.get("root", {}).get("nodeId")
                if root_id:
                    # 중복 선택자 해소 (CDP DOM.querySelectorAll 로 eval 없이)
                    if selector:
                        try:
                            selector = await SelfHealingSelector(page).make_unique_selector(
                                cdp, root_id, selector, node,
                            )
                        except Exception as e:
                            logger.debug("browser_click: make_unique_selector failed: %s", e)
                    found = await cdp.send("DOM.querySelector", {
                        "nodeId": root_id, "selector": selector,
                    })
                    found_node_id = found.get("nodeId")
                    if found_node_id and found_node_id != 0:
                        ok, cx, cy = await _cdp_dispatch(cdp, found_node_id)
                        if ok:
                            await self._tabs[idx].update_info()
                            tab = self._tabs[idx]
                            elapsed = int((time.monotonic() - t0) * 1000)
                            logger.info("browser_click: clicked %s via CDP querySelector=%s (%.0f,%.0f) %dms",
                                         click_desc, selector, cx, cy, elapsed)
                            result = (f"Clicked {click_desc} [CDP:querySelector] "
                                      f"[url={tab.url}, title={tab.title}"
                                      f", selector={selector}]")
                            d = await self._tabs[idx].check_dialog()
                            if d:
                                result += f"\n⚠️ Dialog recorded: [{d['type']}] {d.get('message','')}"
                            return result
        except Exception as e:
            logger.warning("CDP re-discovery for %s failed: %s. → PW fallback", click_desc, e)

        # 2차: Playwright locator (CSS selector 먼저, 없으면 get_by_role)
        try:
            t1 = time.monotonic()
            locator = None
            # selector가 있으면 정확한 요소 지정 가능
            if selector:
                locator = page.locator(selector)
            elif name:
                locator = page.get_by_role(role, name=name, exact=False)
            if locator is not None:
                await locator.first.click(timeout=5000)
                await self._tabs[idx].update_info()
                tab = self._tabs[idx]
                pw_elapsed = int((time.monotonic() - t1) * 1000)
                total_elapsed = int((time.monotonic() - t0) * 1000)
                pw_method = "PW:locator" if selector else "PW:get_by_role"
                logger.info("browser_click: clicked %s via %s (%dms, total=%dms)",
                             click_desc, pw_method, pw_elapsed, total_elapsed)
                result = (f"Clicked {click_desc} [{pw_method}] "
                          f"[url={tab.url}, title={tab.title}]")
                d = await self._tabs[idx].check_dialog()
                if d:
                    result += f"\n⚠️ Dialog recorded: [{d['type']}] {d.get('message','')}"
                return result
        except Exception as e:
            logger.debug("browser_click: PW locator failed for %s: %s", click_desc, e)

        # 3차: SelfHealingSelector (name이 있어야 의미 있음)
        try:
            t1 = time.monotonic()
            healing = SelfHealingSelector(page)
            locator2 = None
            if name:
                locator2 = await healing.find_element(
                    "", text=name, role=role, aria_label=name,
                )
            elif selector:
                locator2 = await healing.find_element(
                    selector, role=role, text=node.get("_text_content", ""),
                )
            if locator2 is None:
                total_elapsed = int((time.monotonic() - t0) * 1000)
                logger.error("browser_click: all strategies failed for %s (%dms)",
                              click_desc, total_elapsed)
                return (f"Click by {click_desc} failed: could not uniquely identify element "
                        f"(role={role!r}, name={name!r}). "
                        "Try browser_snapshot() again to get a fresh ref.")
            await locator2.click(timeout=5000)
            await self._tabs[idx].update_info()
            tab = self._tabs[idx]
            heal_elapsed = int((time.monotonic() - t1) * 1000)
            total_elapsed = int((time.monotonic() - t0) * 1000)
            logger.info("browser_click: clicked %s via self-healing (%dms, total=%dms)",
                         click_desc, heal_elapsed, total_elapsed)
            result = (f"Clicked {click_desc} [heal] "
                      f"[url={tab.url}, title={tab.title}]")
            d = await self._tabs[idx].check_dialog()
            if d:
                result += f"\n⚠️ Dialog recorded: [{d['type']}] {d.get('message','')}"
            return result
        except Exception as e:
            total_elapsed = int((time.monotonic() - t0) * 1000)
            logger.error("browser_click: self-healing failed for %s (%dms): %s",
                          click_desc, total_elapsed, e)
            return f"Click by {click_desc} failed: {e}"

    async def browser_get_text(self, ref: int, tab_index: Optional[int] = None) -> str:
        """접근성 트리 ref 번호로 요소의 DOM 텍스트를 가져온다.

        접근성 트리에 표시되지 않는 보이는 텍스트를 추출할 때 사용한다.
        aria-label 등 accessible name이 비어있어도 실제 DOM 텍스트를 반환한다.
        CDP backendNodeId로 정확한 요소를 찾는다.

        Args:
            ref: browser_snapshot() 결과에 표시된 [#N] 번호
            tab_index: 대상 탭 인덱스 (None이면 포커스 탭)
        """
        session_refs = self._get_session_refs()
        node = session_refs.get(ref)
        if node is None:
            return (f"Get text by ref failed: ref #{ref} not found. "
                    "Call browser_snapshot() first to refresh the tree.")

        page = self._ensure_page(tab_index)
        role = node.get("role", "")
        name = node.get("name", "")
        backend_id = node.get("_backendNodeId")

        # backendNodeId가 있으면 CDP getOuterHTML로 정확히 찾기
        if backend_id is not None:
            try:
                cdp = await page.context.new_cdp_session(page)
                result = await cdp.send("DOM.getOuterHTML", {
                    "backendNodeId": backend_id,
                })
                outer = result.get("outerHTML", "")
                if outer:
                    import re
                    text = re.sub(r'<[^>]+>', ' ', outer).strip()
                    text = ' '.join(text.split())[:5000]
                    if text:
                        return (f"Text for ref #{ref} ({role} \"{name}\"):\n{text}\n"
                                "(truncated — use browser_snapshot() for full tree)")
            except Exception as e:
                logger.warning("CDP getOuterHTML for ref %d failed: %s", ref, e)

        # fallback 1: _text_content (CDP enrichment에서 저장된 값)
        text_content = node.get("_text_content", "")
        if text_content:
            return (f"Text for ref #{ref} ({role} \"{name}\"):\n"
                    f"{text_content.strip()[:5000]}\n"
                    "(truncated — use browser_snapshot() for full tree)")

        # fallback 2: page.evaluate 로 DOM textContent 직접 추출 (CSS selector)
        try:
            selector = node.get("_selector", "")
            if selector:
                script = (
                    f"(document.querySelector('{selector}')"
                    f"?.textContent || '').trim().substring(0, 5000)"
                )
                text = await page.evaluate(script)
                if text:
                    return (f"Text for ref #{ref} ({role} \"{name}\"):\n{text}\n"
                            "(truncated — use browser_snapshot() for full tree)")

            # 최후: role+name 기반 selector (name이 있을 때만)
            if name:
                escaped_role = role.replace("'", "\\'")
                escaped_name = name.replace("'", "\\'")
                if tag:
                    sel = f'{tag}[role="{escaped_role}"][aria-label="{escaped_name}"]'
                else:
                    sel = f'[role="{escaped_role}"][aria-label="{escaped_name}"]'
                script = (
                    f"(document.querySelector('{sel}')"
                    f"?.textContent || '').trim().substring(0, 5000)"
                )
                text = await page.evaluate(script)
                if text:
                    return (f"Text for ref #{ref} ({role} \"{name}\"):\n{text}\n"
                            "(truncated — use browser_snapshot() for full tree)")
            return f"Text for ref #{ref} ({role} \"{name}\"): (no text found)"
        except Exception as e:
            return f"Get text by ref #{ref} failed: {e}"

    async def browser_get_by_ref(self, ref: int, tab_index: Optional[int] = None) -> str:
        """접근성 트리 ref 번호로 요소의 전체 DOM outerHTML을 가져온다.

        디버깅/테스트 용도: ref가 가리키는 요소의 정확한 DOM 구조를 확인한다.
        CDP backendNodeId로 정확한 요소를 찾는다.

        Args:
            ref: browser_snapshot() 결과에 표시된 [#N] 번호
            tab_index: 대상 탭 인덱스 (None이면 포커스 탭)
        """
        session_refs = self._get_session_refs()
        node = session_refs.get(ref)
        if node is None:
            return (f"Get by ref failed: ref #{ref} not found. "
                    "Call browser_snapshot() first to refresh the tree.")

        page = self._ensure_page(tab_index)
        role = node.get("role", "")
        name = node.get("name", "")
        tag = node.get("tag", "") or node.get("_tag", "")
        nid = node.get("_id", "")
        backend_id = node.get("_backendNodeId")

        desc = f"ref #{ref} ({role}"
        if name:
            desc += f" \"{name}\""
        if tag:
            desc += f" <{tag}>"
        if nid:
            desc += f" #{nid}"
        desc += ")"

        if backend_id is not None:
            try:
                cdp = await page.context.new_cdp_session(page)
                result = await cdp.send("DOM.getOuterHTML", {
                    "backendNodeId": backend_id,
                })
                outer = result.get("outerHTML", "")
                if outer:
                    if len(outer) > 10000:
                        return (f"DOM for {desc}:\n{outer[:10000]}\n"
                                "... (truncated — use browser_snapshot() for full tree)")
                    return f"DOM for {desc}:\n{outer}"
            except Exception as e:
                logger.warning("CDP getOuterHTML for ref %d failed: %s", ref, e)

        # fallback: CSS selector로 outerHTML 추출
        try:
            selector = node.get("_selector", "")
            if selector:
                script = (
                    f"(document.querySelector('{selector}')?.outerHTML || '')"
                )
                outer = await page.evaluate(script)
                if outer:
                    if len(outer) > 10000:
                        return (f"DOM for {desc} (via selector):\n{outer[:10000]}\n"
                                "... (truncated — use browser_snapshot() for full tree)")
                    return f"DOM for {desc} (via selector):\n{outer}"
        except Exception as e:
            logger.warning("Selector fallback for ref %d failed: %s", ref, e)

        return f"DOM for {desc}: (element not found)"

    async def browser_screenshot(
        self,
        full_page: bool = False,
        filename: Optional[str] = None,
        tab_index: Optional[int] = None,
    ) -> str:
        logger.info("browser_screenshot: full_page=%s, filename=%s", full_page, filename)
        """스크린샷을 워크스페이스 폴더에 PNG 파일로 저장한다.

        파일명: {yyyyMMdd-HHmmss}.png
        저장 경로: {workspace_dir}/screenshots/

        Args:
            full_page: 전체 페이지 스크린샷 여부
            filename: 파일명 (None이면 타임스탬프)
            tab_index: 대상 탭 인덱스 (None이면 포커스 탭)
        """
        page = self._ensure_page(tab_index)

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

    async def browser_execute_script(self, script: str, tab_index: Optional[int] = None) -> str:
        logger.info("browser_execute_script: script=%s", script[:80])
        """JavaScript를 실행하고 결과를 반환한다.

        Args:
            script: 실행할 JavaScript
            tab_index: 대상 탭 인덱스 (None이면 포커스 탭)
        """
        page = self._ensure_page(tab_index)
        try:
            result = await page.evaluate(script)
            return f"Script returned: {result}" if result is not None else "Script returned: (no value)"
        except Exception as e:
            return f"Script error: {e}"

    def set_cron_dependencies(self, db: Any, cron_scheduler: Any) -> None:
        """크론 작업에 필요한 DB와 스케줄러를 주입한다."""
        self._db = db
        self._cron_scheduler = cron_scheduler

    def set_main_window(self, window: Any) -> None:
        """MainWindow 참조를 주입한다 (cron_to_session UI 업데이트용)."""
        self._main_window = window

    # ─── 크론 도구 구현 ────────────────────────────────────────

    async def cron_add(self, name: str, schedule: str, prompt: str) -> str:
        """새 크론 작업을 등록한다."""
        from datetime import datetime as _dt
        from src.models.schemas import CronJob

        if not name or not schedule or not prompt:
            return "cron_add error: name, schedule, prompt are required"
        if self._db is None:
            return "cron_add error: database not initialized"

        now = _dt.now()
        job = CronJob(
            name=name,
            schedule=schedule,
            prompt=prompt,
            status="enable",
            created_at=now,
            updated_at=now,
        )
        await self._db.create_cron_job(job)
        if self._cron_scheduler:
            await self._cron_scheduler.add_job(job)
        return f"cron_add: job '{name}' created (id={job.id}, schedule={schedule})"

    async def cron_list(self) -> str:
        """등록된 크론 작업 목록을 조회한다."""
        if self._db is None:
            return "cron_list error: database not initialized"

        jobs = await self._db.list_cron_jobs()
        if not jobs:
            return "cron_list: no cron jobs registered"

        lines = ["**Cron Jobs:**"]
        for j in jobs:
            status_icon = "🟢" if j.status == "enable" else "🔴"
            next_run = j.next_run_at.strftime("%Y-%m-%d %H:%M") if j.next_run_at else "-"
            lines.append(
                f"- {status_icon} **{j.name}** (id={j.id}) | "
                f"schedule=`{j.schedule}` | prompt=`{j.prompt[:60]}` | "
                f"next={next_run}"
            )
        return "\n".join(lines)

    async def cron_edit(
        self,
        job_id: str,
        name: Optional[str] = None,
        schedule: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """크론 작업을 수정한다."""
        if self._db is None:
            return "cron_edit error: database not initialized"

        job = await self._db.get_cron_job(job_id)
        if not job:
            return f"cron_edit error: job '{job_id}' not found"

        if name is not None:
            job.name = name
        if schedule is not None:
            job.schedule = schedule
        if prompt is not None:
            job.prompt = prompt
        job.updated_at = datetime.now()

        await self._db.update_cron_job(job)
        if self._cron_scheduler:
            await self._cron_scheduler.update_job(job)

        return f"cron_edit: job '{job.name}' (id={job_id}) updated"

    async def cron_toggle(self, job_id: str) -> str:
        """크론 작업 활성/비활성을 전환한다."""
        if self._db is None:
            return "cron_toggle error: database not initialized"

        job = await self._db.get_cron_job(job_id)
        if not job:
            return f"cron_toggle error: job '{job_id}' not found"

        new_status = await self._db.toggle_cron_job(job_id)
        if new_status is None:
            return f"cron_toggle error: failed to toggle job '{job_id}'"

        if self._cron_scheduler:
            await self._cron_scheduler.toggle_job(job_id, new_status)

        status_text = "enabled" if new_status else "disabled"
        return f"cron_toggle: job '{job.name}' (id={job_id}) is now {status_text}"

    async def cron_remove(self, job_id: str) -> str:
        """크론 작업을 삭제한다."""
        if self._db is None:
            return "cron_remove error: database not initialized"

        job = await self._db.get_cron_job(job_id)
        if not job:
            return f"cron_remove error: job '{job_id}' not found"

        await self._db.delete_cron_job(job_id)
        if self._cron_scheduler:
            await self._cron_scheduler.remove_job(job_id)

        return f"cron_remove: job '{job.name}' (id={job_id}) removed"

    async def add_main_session_message(self, message: str) -> str:
        """메인 세션 채팅창에 메시지를 표시한다."""
        if hasattr(self, "_main_window") and self._main_window is not None:
            try:
                self._main_window._chat.add_ai_block(f"[크론→메인] {message}")
                return f"add_main_session_message: displayed '{message[:60]}'"
            except Exception as e:
                logger.warning("Failed to display main window chat: %s", e)
                return f"add_main_session_message error: {e}"
        return "add_main_session_message error: main window not available"

    # ─── 메모리 검색 (스텁 — database.py FTS5 활용) ────────

    async def memory_search(self, query: str, session_id: str = "") -> str:
        """세션 내 과거 대화를 키워드로 검색한다.

        database.py의 search_messages를 활용하여 FTS5 검색을 수행한다.
        5차 레이어에서 Database 인스턴스를 주입받아 사용한다.
        """
        return f"memory_search: not yet integrated (query={query})"

    # ─── 파일 쓰기 ──────────────────────────────────────────

    async def workspace_write(self, filename: str, content: str) -> str:
        """워크스페이스 exports 폴더에 파일을 저장한다. (기존 파일 덮어쓰기 방지)

        Args:
            filename: 저장할 파일명 (영문, 숫자, . - _ 만 허용)
            content: 저장할 내용

        Returns:
            저장된 파일의 절대 경로 또는 오류 메시지
        """
        import re
        if not re.match(r'^[\w.\-]+$', filename):
            return f"Invalid filename: '{filename}'. Use only letters, numbers, dots, dashes, and underscores."

        exports_dir = Path(self._workspace_dir) / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        filepath = exports_dir / filename
        if filepath.exists():
            return (f"File already exists: '{filename}'. "
                    f"Please choose a different filename and try again.")

        try:
            filepath.write_text(content, encoding="utf-8")
            logger.info("workspace_write: saved %s (%d bytes)", filepath, len(content))
            return str(filepath.resolve())
        except Exception as e:
            return f"File write error: {e}"
