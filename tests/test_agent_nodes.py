"""tests/test_agent_nodes.py — LangGraph 노드 함수 테스트."""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.agent.nodes import (
    _is_tool_error,
    assess_error,
    execute_tool,
)


class TestIsToolError:
    def test_error_keywords(self):
        assert _is_tool_error("failed to find element") is True
        assert _is_tool_error("exception occurred") is True
        assert _is_tool_error("element not found") is True
        assert _is_tool_error("timeout exceeded") is True

    def test_success_messages(self):
        assert _is_tool_error("Navigated to https://example.com") is False
        assert _is_tool_error("Clicked: #button") is False
        assert _is_tool_error("hello world") is False
        assert _is_tool_error("") is False


@pytest.mark.asyncio
class TestExecuteTool:
    async def test_execute_success(self):
        tool_func = AsyncMock()
        tool_func.ainvoke.return_value = "Success result"
        tools = {"test_tool": tool_func}

        ai_msg = AIMessage(
            content="",
            tool_calls=[{"name": "test_tool", "args": {"arg1": "val1"}, "id": "call_1"}],
        )
        state = {"messages": [ai_msg], "error_count": 0}

        result = await execute_tool(state, tools)
        assert "messages" in result
        assert len(result["messages"]) == 1
        assert result["messages"][0].type == "tool"
        assert result["error_count"] == 0

    async def test_execute_unknown_tool(self):
        tools = {}
        ai_msg = AIMessage(
            content="",
            tool_calls=[{"name": "unknown_tool", "args": {}, "id": "call_1"}],
        )
        state = {"messages": [ai_msg], "error_count": 0}

        result = await execute_tool(state, tools)
        assert "messages" in result
        assert result["error_count"] > 0

    async def test_no_tool_calls(self):
        ai_msg = AIMessage(content="Just text response")
        state = {"messages": [ai_msg], "error_count": 0}

        result = await execute_tool(state, {})
        assert "messages" in result
        # ToolMessage with error
        assert result["messages"][0].type == "tool"

    async def test_tool_content_in_json(self):
        tool_func = AsyncMock()
        tool_func.ainvoke.return_value = "clicked"
        tools = {"browser_click": tool_func}

        ai_msg = AIMessage(
            content="",
            tool_calls=[{"name": "browser_click", "args": {"selector": "#btn"}, "id": "call_1"}],
        )
        state = {"messages": [ai_msg], "error_count": 0}

        result = await execute_tool(state, tools)
        content = json.loads(result["messages"][0].content)
        assert content["tool"] == "browser_click"
        assert content["result"] == "clicked"
        assert content["status"] == "success"
        assert "duration_ms" in content


class TestAssessError:
    """assess_error는 dict(state update) 반환. 라우팅은 graph.py의 conditional edge에서 수행.

    _shared_config가 없으면 _get_cfg 기본값 max_retries=3.
    """

    def test_no_error_continue(self):
        state = {"error_count": 0, "is_final": False}
        result = assess_error(state)
        assert result == {}

    def test_error_under_limit_retry(self):
        state = {"error_count": 1, "is_final": False}
        result = assess_error(state)
        assert result == {}

    def test_error_at_limit_give_up(self):
        state = {"error_count": 3, "is_final": False}
        result = assess_error(state)
        assert result["is_final"] is True
        assert "messages" in result

    def test_is_final_ends(self):
        state = {"error_count": 0, "is_final": True}
        result = assess_error(state)
        assert result == {}

    def test_error_just_below_limit(self):
        state = {"error_count": 2, "is_final": False}
        result = assess_error(state)
        assert result == {}