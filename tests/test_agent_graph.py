"""tests/test_agent_graph.py — LangGraph 그래프 테스트."""

from langchain_core.messages import AIMessage

from src.agent.graph import _should_continue


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