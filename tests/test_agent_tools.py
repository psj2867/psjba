"""tests/test_agent_tools.py — LangChain tool bindings 테스트."""

from unittest.mock import AsyncMock

import pytest

from src.agent.tools import create_all_tools, create_browser_tools, create_cron_tools, create_memory_tools


class TestCreateBrowserTools:
    def test_returns_list(self):
        manager = AsyncMock()
        tools = create_browser_tools(manager)
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_tool_names(self):
        manager = AsyncMock()
        tools = create_browser_tools(manager)
        names = [t.name for t in tools]
        assert "browser_navigate" in names
        assert "browser_click" in names
        assert "browser_type" in names
        assert "browser_snapshot" in names
        assert "browser_screenshot" in names
        assert "browser_new_tab" in names
        assert "browser_close_tab" in names
        assert "browser_switch_tab" in names
        assert "browser_list_tabs" in names
        assert "browser_get_focused_tab" in names
        assert "browser_get_url" in names
        assert "browser_get_title" in names
        assert "browser_execute_script" in names
        assert "browser_scroll" in names
        assert "browser_focus" in names
        assert "browser_press_key" in names
        assert "browser_wait" in names
        assert "browser_select_option" in names
        assert "browser_hover" in names
        assert "browser_get_dom" in names

    @pytest.mark.asyncio
    async def test_browser_navigate_calls_manager(self):
        manager = AsyncMock()
        manager.browser_navigate.return_value = "Navigated"
        tools = create_browser_tools(manager)
        nav = [t for t in tools if t.name == "browser_navigate"][0]
        result = await nav.ainvoke({"url": "https://example.com"})
        assert result == "Navigated"
        manager.browser_navigate.assert_called_once_with("https://example.com")


class TestCreateCronTools:
    def test_returns_list(self):
        manager = AsyncMock()
        tools = create_cron_tools(manager)
        assert isinstance(tools, list)
        names = [t.name for t in tools]
        assert "cron_add" in names
        assert "cron_list" in names
        assert "cron_edit" in names
        assert "cron_toggle" in names
        assert "cron_remove" in names


class TestCreateMemoryTools:
    @pytest.mark.asyncio
    async def test_memory_search(self):
        db = AsyncMock()
        db.search_messages.return_value = [
            {"role": "user", "content": "hello", "created_at": "2026-01-01"}
        ]
        tools = create_memory_tools(db, "session-123")
        assert len(tools) == 1
        assert tools[0].name == "memory_search"
        result = await tools[0].ainvoke({"query": "hello"})
        assert "hello" in result
        db.search_messages.assert_called_once_with("session-123", "hello")

    @pytest.mark.asyncio
    async def test_memory_search_no_results(self):
        db = AsyncMock()
        db.search_messages.return_value = []
        tools = create_memory_tools(db, "session-123")
        result = await tools[0].ainvoke({"query": "nothing"})
        assert "No results" in result


class TestCreateAllTools:
    def test_all_tools_with_db(self):
        manager = AsyncMock()
        db = AsyncMock()
        tools = create_all_tools(manager, db=db, session_id="sess-1")
        names = [t.name for t in tools]
        # browser 20 + cron 5 + memory 1 = 26
        assert len(names) >= 26
        assert "memory_search" in names

    def test_all_tools_without_db(self):
        manager = AsyncMock()
        tools = create_all_tools(manager)
        names = [t.name for t in tools]
        assert "memory_search" not in names
        assert len(names) == 25  # 20 browser + 5 cron