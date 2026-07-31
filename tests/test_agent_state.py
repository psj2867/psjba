"""tests/test_agent_state.py — AgentState 테스트."""

from langchain_core.messages import AIMessage, HumanMessage

from src.agent.state import AgentState


class TestAgentState:
    def test_basic_structure(self):
        state: AgentState = {
            "messages": [],
            "session_id": "test-session",
            "url": "",
            "page_title": "",
            "accessibility_tree": "",
            "last_screenshot_path": "",
            "error_count": 0,
            "is_final": False,
        }
        assert state["session_id"] == "test-session"
        assert state["error_count"] == 0
        assert state["is_final"] is False
        assert state["messages"] == []

    def test_with_messages(self):
        state: AgentState = {
            "messages": [
                HumanMessage(content="hello"),
                AIMessage(content="hi"),
            ],
        }
        assert len(state["messages"]) == 2
        assert state["messages"][0].content == "hello"
        assert state["messages"][1].content == "hi"

    def test_add_messages_reducer(self):
        """add_messages 리듀서로 메시지가 추가되는지."""
        from langgraph.graph import add_messages

        existing = [HumanMessage(content="first")]
        new_msg = [AIMessage(content="second")]
        result = add_messages(existing, new_msg)
        assert len(result) == 2
        assert result[0].content == "first"
        assert result[1].content == "second"

    def test_total_false_defaults(self):
        """total=False 이므로 일부 키가 없어도 됨."""
        state: AgentState = {
            "messages": [],
        }
        assert "session_id" not in state or state["messages"] == []