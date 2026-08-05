"""LangGraph 그래프 정의 — 에이전트 실행 흐름 구성."""

import logging
from functools import partial
from typing import Any, Callable, Optional, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph

from src.agent.nodes import assess_error, call_model, execute_tool
from src.agent.state import AgentState
from src.agent.tools import create_all_tools
from src.browser.manager import PlaywrightManager
from src.config import Config
from src.database import Database

logger = logging.getLogger("psjba.agent.graph")


def _should_continue(state: AgentState) -> str:
    """call_model 이후 라우팅: 마지막 AIMessage에 tool_calls 있으면 execute_tool, 없으면 END."""
    messages = state.get("messages", [])
    # 가장 마지막 AI 메시지만 확인 (이전 루프의 tool_calls는 무시)
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                return "execute_tool"
            return "__end__"
    return "__end__"


def create_agent_graph(
    config: Config,
    manager: PlaywrightManager,
    db: Optional[Database] = None,
    session_id: str = "",
    llm: Optional[BaseChatModel] = None,
    max_context_messages: int = 10,
    max_retries: int = 3,
    max_tool_calls: int = 30,
    job_scheduler: Any = None,
) -> StateGraph:
    """LangGraph 에이전트 그래프를 생성하고 컴파일한다.

    Args:
        config: 설정 인스턴스 (LLM 설정 포함)
        manager: 연결된 PlaywrightManager 인스턴스
        db: Database 인스턴스 (memory_search용)
        session_id: 현재 세션 ID
        llm: 사전 생성된 LLM 인스턴스 (None이면 config로 생성)
        max_context_messages: 슬라이딩 윈도우 크기
        max_retries: 최대 오류 재시도 횟수

    Returns:
        컴파일된 StateGraph
    """
    from src.agent.llm_client import create_llm

    if llm is None:
        llm = create_llm(config)

    tools = create_all_tools(manager, db=db, session_id=session_id, job_scheduler=job_scheduler)

    tool_dict: dict[str, Any] = {}
    for t in tools:
        tool_dict[t.name] = t

    bound_llm = llm.bind_tools(tools, tool_choice="auto")

    graph = StateGraph(AgentState)

    graph.add_node(
        "call_model",
        partial(
            call_model,
            llm=bound_llm,
            max_context_messages=max_context_messages,
            max_tool_calls=max_tool_calls,
        ),
    )
    graph.add_node(
        "execute_tool",
        partial(execute_tool, tools=tool_dict, manager=manager),
    )
    graph.add_node(
        "assess_error",
        partial(assess_error, max_retries=max_retries),
    )

    graph.set_entry_point("call_model")

    graph.add_conditional_edges(
        "call_model",
        _should_continue,
        {
            "execute_tool": "execute_tool",
            "__end__": END,
        },
    )

    graph.add_edge("execute_tool", "assess_error")

    graph.add_conditional_edges(
        "assess_error",
        lambda state: "call_model" if not state.get("is_final") else "__end__",
        {
            "call_model": "call_model",
            "__end__": END,
        },
    )

    compiled = graph.compile()
    logger.info(
        f"Agent graph compiled: {len(tools)} tools, "
        f"window={max_context_messages}, retries={max_retries}"
    )
    return compiled
