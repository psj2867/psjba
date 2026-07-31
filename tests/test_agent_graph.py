"""tests/test_agent_graph.py — LangGraph 그래프 테스트."""

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.graph import _should_continue, create_agent_graph


class TestShouldContinue:
    def test_has_tool_calls(self):
        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "test", "args": {}, "id": "1"}],
                )
            ]
        }
        assert _should_continue(state) == "execute_tool"

    def test_no_tool_calls(self):
        state = {
            "messages": [
                AIMessage(content="Final answer.")
            ]
        }
        assert _should_continue(state) == "__end__"

    def test_empty_messages(self):
        state = {"messages": []}
        assert _should_continue(state) == "__end__"

    def test_tool_calls_in_earlier_message(self):
        """최신 AI 메시지에만 tool_calls가 있음."""
        state = {
            "messages": [
                AIMessage(content="first", tool_calls=[{"name": "t", "args": {}, "id": "1"}]),
                AIMessage(content="second"),
            ]
        }
        # reversed로 보므로 마지막=second가 먼저 확인됨 → tool_calls 없음
        assert _should_continue(state) == "__end__"


class TestCreateAgentGraph:
    def test_create_graph(self):
        config = AsyncMock()
        config.get.side_effect = lambda s, k, d=None: d
        config.api_key = "sk-test"

        manager = AsyncMock()

        with patch("src.agent.graph.create_llm") as mock_create_llm, \
             patch("src.agent.graph.create_all_tools") as mock_create_tools:
            mock_llm = AsyncMock()
            mock_create_llm.return_value = mock_llm
            mock_create_tools.return_value = []

            graph = create_agent_graph(config, manager)

            assert graph is not None

    def test_graph_nodes(self):
        """그래프에 필요한 노드가 있는지 확인."""
        config = AsyncMock()
        config.get.side_effect = lambda s, k, d=None: d
        config.api_key = "sk-test"
        manager = AsyncMock()

        with patch("src.agent.graph.create_llm") as mock_create_llm, \
             patch("src.agent.graph.create_all_tools") as mock_create_tools:
            mock_llm = AsyncMock()
            mock_create_llm.return_value = mock_llm
            mock_create_tools.return_value = []

            graph = create_agent_graph(config, manager)

            nodes = list(graph.nodes.keys())
            assert "call_model" in nodes
            assert "execute_tool" in nodes
            assert "assess_error" in nodes

    def test_graph_with_db(self):
        """DB와 session_id를 전달한 그래프 생성."""
        config = AsyncMock()
        config.get.side_effect = lambda s, k, d=None: d
        config.api_key = "sk-test"
        manager = AsyncMock()
        db = AsyncMock()

        with patch("src.agent.graph.create_llm") as mock_create_llm, \
             patch("src.agent.graph.create_all_tools") as mock_create_tools:
            mock_llm = AsyncMock()
            mock_create_llm.return_value = mock_llm
            mock_create_tools.return_value = []

            graph = create_agent_graph(config, manager, db=db, session_id="sess-1")

            assert graph is not None
            # session_id가 tools에 전달되었는지
            mock_create_tools.assert_called_once()
            _, kwargs = mock_create_tools.call_args
            assert kwargs["session_id"] == "sess-1"