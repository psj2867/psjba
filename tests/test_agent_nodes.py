"""tests/test_agent_nodes.py — LangGraph 노드 함수 테스트."""

import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.agent.nodes import (
    _apply_sliding_window,
    _is_tool_error,
    assess_error,
    call_model,
    execute_tool,
)


class TestApplySlidingWindow:
    def test_no_trimming_needed(self):
        msgs = [HumanMessage(content="a"), HumanMessage(content="b")]
        result = _apply_sliding_window(msgs, max_context=10)
        assert len(result) == 2

    def test_trim_excess(self):
        msgs = [HumanMessage(content=f"msg{i}") for i in range(10)]
        result = _apply_sliding_window(msgs, max_context=3)
        assert len(result) <= 3 + 1  # 3 user + 비율 보정

    def test_system_message_preserved(self):
        msgs = [SystemMessage(content="system"), HumanMessage(content="a"),
                HumanMessage(content="b"), HumanMessage(content="c")]
        result = _apply_sliding_window(msgs, max_context=2)
        assert any(isinstance(m, SystemMessage) for m in result)

    def test_empty_messages(self):
        result = _apply_sliding_window([], max_context=10)
        assert result == []

    def test_only_system_messages(self):
        msgs = [SystemMessage(content="sys")]
        result = _apply_sliding_window(msgs, max_context=5)
        assert len(result) == 1

    def test_min_keep_messages(self):
        """min_keep개의 최근 user 메시지는 유지."""
        msgs = [HumanMessage(content=f"msg{i}") for i in range(10)]
        result = _apply_sliding_window(msgs, max_context=3, min_keep=2)
        # 적어도 2개의 최근 메시지는 유지되어야 함
        assert len(result) >= 2

    def test_max_context_zero(self):
        msgs = [HumanMessage(content="a")]
        result = _apply_sliding_window(msgs, max_context=0)
        assert len(result) == 1  # 0이면 그대로


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
class TestCallModel:
    async def test_llm_response(self):
        llm = AsyncMock()
        llm.ainvoke.return_value = AIMessage(content="Test response")
        state = {
            "messages": [HumanMessage(content="hello")],
            "accessibility_tree": "[button] ok",
            "error_count": 0,
        }
        result = await call_model(state, llm, max_context_messages=30)
        assert "messages" in result
        msgs = result["messages"]
        assert len(msgs) == 1
        assert msgs[0].content == "Test response"

    async def test_llm_error_handling(self):
        llm = AsyncMock()
        llm.ainvoke.side_effect = Exception("API Timeout")
        state = {
            "messages": [HumanMessage(content="hello")],
            "error_count": 0,
        }
        result = await call_model(state, llm)
        assert "messages" in result
        assert "LLM error" in result["messages"][0].content
        assert result["error_count"] == 1


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
        assert result["messages"][0].role == "tool"
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
        assert result["messages"][0].role == "tool"

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
    def test_no_error_continue(self):
        state = {"error_count": 0, "is_final": False}
        assert assess_error(state) == "call_model"

    def test_error_under_limit_retry(self):
        state = {"error_count": 1, "is_final": False}
        assert assess_error(state, max_retries=3) == "call_model"

    def test_error_at_limit_give_up(self):
        state = {"error_count": 3, "is_final": False}
        result = assess_error(state, max_retries=3)
        assert result == "__end__"
        assert state["is_final"] is True

    def test_is_final_ends(self):
        state = {"error_count": 0, "is_final": True}
        assert assess_error(state) == "__end__"

    def test_error_just_below_limit(self):
        state = {"error_count": 2, "is_final": False}
        assert assess_error(state, max_retries=3) == "call_model"