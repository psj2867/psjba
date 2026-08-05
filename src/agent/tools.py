"""LangChain tool bindings — PlaywrightManager + Database 메서드를 tool로 래핑."""

import json
import logging
from typing import Optional

from langchain_core.tools import tool

from src.browser.manager import PlaywrightManager
from src.database import Database

logger = logging.getLogger("psjba.agent.tools")


def create_browser_tools(
    manager: PlaywrightManager,
) -> list:
    """PlaywrightManager 메서드를 LangChain tool 리스트로 생성한다.

    Args:
        manager: 연결된 PlaywrightManager 인스턴스

    Returns:
        @tool 데코레이터가 적용된 비동기 함수 리스트
    """

    @tool
    async def browser_navigate(url: str) -> str:
        """Navigate to a URL in the browser."""
        return await manager.browser_navigate(url)

    @tool
    async def browser_click(
        selector: str,
        text: Optional[str] = None,
        aria_label: Optional[str] = None,
        role: Optional[str] = None,
    ) -> str:
        """Click an element by CSS selector. Supports self-healing fallback (XPath, text, aria-label, role)."""
        return await manager.browser_click(
            selector, text=text, aria_label=aria_label, role=role
        )

    @tool
    async def browser_type(selector: str, text: str, clear: bool = False) -> str:
        """Type text into an input field found by CSS selector."""
        return await manager.browser_type(selector, text, clear=clear)

    @tool
    async def browser_select_option(selector: str, value: str) -> str:
        """Select an option from a dropdown by value."""
        return await manager.browser_select_option(selector, value)

    @tool
    async def browser_hover(selector: str) -> str:
        """Hover over an element found by CSS selector."""
        return await manager.browser_hover(selector)

    @tool
    async def browser_scroll(direction: str = "down", amount: Optional[int] = None) -> str:
        """Scroll the page. direction: up, down, top, bottom. amount: pixels (optional)."""
        return await manager.browser_scroll(direction, amount)

    @tool
    async def browser_focus(selector: str) -> str:
        """Focus on an element found by CSS selector."""
        return await manager.browser_focus(selector)

    @tool
    async def browser_press_key(key: str) -> str:
        """Press a keyboard key (Enter, Tab, Escape, ArrowDown, etc.)."""
        return await manager.browser_press_key(key)

    @tool
    async def browser_wait(milliseconds: int) -> str:
        """Wait for specified time in milliseconds."""
        return await manager.browser_wait(milliseconds)

    @tool
    async def browser_new_tab(url: Optional[str] = None) -> str:
        """Open a new browser tab. Optionally navigate to a URL."""
        return await manager.browser_new_tab(url)

    @tool
    async def browser_close_tab(target: str = "") -> str:
        """Close a tab. If target is empty, close the current active tab. Otherwise target can be a tab index (0-based) or a URL."""
        return await manager.browser_close_tab(target)

    @tool
    async def browser_switch_tab(target: str) -> str:
        """Switch to a tab by index (0-based) or URL."""
        return await manager.browser_switch_tab(target)

    @tool
    async def browser_list_tabs() -> str:
        """List all open browser tabs with index, URL, and title."""
        return await manager.browser_list_tabs()

    @tool
    async def browser_get_focused_tab() -> str:
        """Get info about the currently active tab (index, URL, title)."""
        return await manager.browser_get_focused_tab()

    @tool
    async def browser_screenshot(full_page: bool = False, tab_index: Optional[int] = None) -> str:
        """Take a screenshot and save it. Only use when the user explicitly requests a screenshot.

        Args:
            full_page: Capture full page if True
            tab_index: Target tab index (default: focused tab)
        """
        return await manager.browser_screenshot(full_page=full_page, tab_index=tab_index)

    @tool
    async def browser_snapshot(tab_index: Optional[int] = None) -> str:
        """Get the accessibility tree of a page. Use this to understand the page structure before taking actions.

        Args:
            tab_index: Target tab index (default: focused tab)
        """
        return await manager.browser_snapshot(tab_index)

    @tool
    async def chrome_health() -> str:
        """Check Chrome/CDP connection health and get comprehensive browser status. Returns connection status, tab count, focused tab info, CDP settings, headless mode, and Chrome PID if available. Safe to call even when not connected."""
        return await manager.chrome_health()

    @tool
    async def chrome_start() -> str:
        """Connect to Chrome CDP, or launch Chrome and connect if not already running. Use this when you need to interact with the browser but chrome_health() shows not connected. Returns connection result with health info on success."""
        return await manager.chrome_start()

    @tool
    async def browser_execute_script(script: str, tab_index: Optional[int] = None) -> str:
        """Execute JavaScript on a page and return the result.

        Args:
            script: JavaScript code to execute
            tab_index: Target tab index (default: focused tab)
        """
        return await manager.browser_execute_script(script, tab_index)

    @tool
    async def browser_snapshot_detail(ref: int, tab_index: Optional[int] = None) -> str:
        """Get a detailed subtree view for a specific ref from the accessibility tree. Use this when you need more detail about a particular interactive element — it shows the element's children up to 3 levels deep with full tag/attribute info (id, class, type, placeholder), without token truncation. Call browser_snapshot() first to get ref numbers.

        Args:
            ref: The [#N] ref number from the accessibility tree
            tab_index: Target tab index (default: focused tab)
        """
        return await manager.browser_snapshot_detail(ref, tab_index)

    @tool
    async def browser_click_by_ref(ref: int, tab_index: Optional[int] = None) -> str:
        """Click an element by its accessibility tree ref number. Use this after calling browser_snapshot() to see the [#N] ref numbers next to interactive elements. This is more reliable than guessing a CSS selector.

        Args:
            ref: The [#N] ref number from the accessibility tree
            tab_index: Target tab index (default: focused tab)
        """
        return await manager.browser_click_by_ref(ref, tab_index)

    @tool
    async def workspace_write(filename: str, content: str) -> str:
        """Save content to a file in the workspace exports folder. Use this to save generated data, reports, or any text output the user might want to keep. Only allows safe filenames (letters, numbers, dots, dashes, underscores)."""
        return await manager.workspace_write(filename, content)

    @tool
    async def current_datetime() -> str:
        """Get the current date and time. Returns the current local date and time with timezone info. Use this when you need to know the current time, date, or timestamp."""
        from datetime import datetime
        import time
        now = time.localtime()
        tz_name = time.tzname[1 if now.tm_isdst > 0 else 0]
        return datetime.now().strftime(f"%Y-%m-%d %H:%M:%S ({tz_name})")

    return [
        chrome_health,
        chrome_start,
        browser_navigate,
        browser_click,
        browser_type,
        browser_select_option,
        browser_hover,
        browser_scroll,
        browser_focus,
        browser_press_key,
        browser_wait,
        browser_new_tab,
        browser_close_tab,
        browser_switch_tab,
        browser_list_tabs,
        browser_get_focused_tab,
        browser_screenshot,
        browser_snapshot,
        browser_snapshot_detail,
        browser_execute_script,
        browser_click_by_ref,
        workspace_write,
        current_datetime,
    ]


def create_cron_tools(manager: PlaywrightManager, db: Any = None, job_scheduler: Any = None) -> list:
    """크론 + 1회성 job 도구 리스트를 생성한다."""

    @tool
    async def cron_add(name: str, schedule: str, prompt: str) -> str:
        """Add a new cron job. schedule is a cron expression (5 fields)."""
        return await manager.cron_add(name, schedule, prompt)

    @tool
    async def cron_list() -> str:
        """List all registered cron jobs."""
        return await manager.cron_list()

    @tool
    async def cron_edit(
        job_id: str,
        name: Optional[str] = None,
        schedule: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """Edit a cron job. Provide job_id and any fields to change."""
        return await manager.cron_edit(job_id, name=name, schedule=schedule, prompt=prompt)

    @tool
    async def cron_toggle(job_id: str) -> str:
        """Toggle a cron job between enabled and disabled."""
        return await manager.cron_toggle(job_id)

    @tool
    async def cron_remove(job_id: str) -> str:
        """Remove (soft-delete) a cron job."""
        return await manager.cron_remove(job_id)

    @tool
    async def cron_to_session(message: str) -> str:
        """Send a message from a cron job to the main chat session so the user sees it.
        Use this to report progress, results, or ask the user something."""
        return await manager.add_main_session_message(message)

    @tool
    async def job_add(name: str, time_str: str, prompt: str) -> str:
        """Add a one-time scheduled job. time_str is a datetime in ISO format (e.g. '2026-07-30T15:30:00' or '2026-07-30 15:30:00')."""
        from datetime import datetime
        from src.models.schemas import ScheduledJob
        parsed = time_str.replace("T", " ").strip()
        dt = datetime.fromisoformat(parsed)
        if dt <= datetime.now():
            return f"job_add error: time must be in the future (now: {datetime.now().isoformat()[:19]})"
        job = ScheduledJob(
            name=name,
            scheduled_at=dt,
            prompt=prompt,
        )
        if job_scheduler is not None:
            await job_scheduler.add_job(job)
        elif db is not None:
            await db.create_scheduled_job(job)
        else:
            return "job_add error: no database or scheduler available"
        return (
            f"Scheduled one-time job '{name}' created: "
            f"will run at {dt.isoformat()[:19]} (ID: {job.id})"
        )

    return [cron_add, cron_list, cron_edit, cron_toggle, cron_remove, cron_to_session, job_add]


def create_memory_tools(db: Database, session_id: str) -> list:
    """메모리 검색 도구 리스트를 생성한다.

    Args:
        db: 연결된 Database 인스턴스
        session_id: 현재 대화 세션 ID
    """

    @tool
    async def memory_search(query: str) -> str:
        """Search past messages in this session by keyword. Useful for recalling earlier conversation context that may have been removed from the sliding window."""
        try:
            results = await db.search_messages(session_id, query)
            if not results:
                return f"No results found for '{query}'"
            lines = []
            for r in results:
                role = r.get("role", "unknown")
                content = r.get("content", "")
                created = r.get("created_at", "")
                preview = content[:200] + ("..." if len(content) > 200 else "")
                lines.append(f"[{role}] ({created}) {preview}")
            return f"Found {len(results)} results:\n" + "\n".join(lines)
        except Exception as e:
            return f"Memory search error: {e}"

    return [memory_search]


def create_all_tools(
    manager: PlaywrightManager,
    db: Optional[Database] = None,
    session_id: str = "",
    job_scheduler: Any = None,
) -> list:
    """모든 도구를 하나의 리스트로 생성한다.

    Args:
        manager: PlaywrightManager 인스턴스
        db: Database 인스턴스 (memory_search용, None이면 memory_search 제외)
        session_id: 현재 세션 ID
        job_scheduler: JobScheduler 인스턴스 (job_add용)

    Returns:
        모든 LangChain tool 리스트
    """
    tools = create_browser_tools(manager)

    tools.extend(create_cron_tools(manager, db=db, job_scheduler=job_scheduler))
    if db and session_id:
        tools.extend(create_memory_tools(db, session_id))
    return tools
