"""AgentState — LangGraph 에이전트 상태 정의."""

from typing import Annotated, Any, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    """에이전트가 그래프를 순회하며 유지하는 상태 정보."""

    messages: Annotated[list[BaseMessage], add_messages]
    session_id: str
    url: str
    page_title: str
    accessibility_tree: str
    last_screenshot_path: str
    error_count: int
    is_final: bool
