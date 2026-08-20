"""LangGraph 그래프 정의 — 에이전트 실행 흐름 구성."""

import functools
import logging
import time
from functools import partial
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph

from src.agent.nodes import (
    assess_error,
    call_model_cron,
    call_model_user,
    execute_tool,
)
from src.agent.state import AgentState
from src.agent.tools import create_all_tools
from src.browser.manager import PlaywrightManager
from src.config import Config
from src.database import Database

logger = logging.getLogger("psjba.agent.graph")


def _should_continue(state: AgentState) -> str:
    """call_model 이후 라우팅: 마지막 AIMessage에 tool_calls 있으면 execute_tool, 없으면 END."""
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                return "execute_tool"
            return "__end__"
    return "__end__"


class AgentGraph:
    """LangGraph 에이전트 그래프 — 생성/컴파일 1회, LRU 캐싱 후 실행만 반복.

    사용법:
        agent = AgentGraph(config=..., manager=..., db=..., llm=..., ...)

        # 매 프롬프트
        graph = agent.compiled(session_id=session_id)
        async for event in graph.astream(state):
            ...
    """

    def __init__(
        self,
        *,
        config: Config,
        manager: PlaywrightManager,
        db: Optional[Database] = None,
        llm: BaseChatModel,
        job_scheduler: Any = None,
        max_context_messages: int = 10,
        max_retries: int = 3,
        max_tool_calls: int = 30,
    ) -> None:
        self._config = config
        self._manager = manager
        self._db = db
        self._llm = llm
        self._job_scheduler = job_scheduler
        self._max_context_messages = max_context_messages
        self._max_retries = max_retries
        self._max_tool_calls = max_tool_calls
        # lru_cache: (session_id, cron_job_id) → compiled graph (최대 32개)
        self._compiled = functools.lru_cache(maxsize=32)(self._build)

    # ── public ─────────────────────────────────────────────────

    def compiled(self, session_id: str, cron_job_id: str = "") -> Any:
        """세션에 대응하는 compiled graph를 반환한다 (lazy build + lru_cache)."""
        return self._compiled(session_id, cron_job_id)

    # ── build ──────────────────────────────────────────────────

    def _build(self, session_id: str, cron_job_id: str = "") -> Any:
        """실제 그래프를 생성하고 compile한다."""
        _t0 = time.monotonic()

        tools = create_all_tools(
            self._manager, db=self._db, session_id=session_id,
            job_scheduler=self._job_scheduler, cron_job_id=cron_job_id,
        )
        logger.debug("[TIMING] create_all_tools: %.3fs", time.monotonic() - _t0)

        _t1 = time.monotonic()
        tool_dict: dict[str, Any] = {t.name: t for t in tools}
        bound_llm = self._llm.bind_tools(tools, tool_choice="auto")
        logger.debug("[TIMING] bind_tools: %.3fs", time.monotonic() - _t1)

        _t2 = time.monotonic()
        graph = StateGraph(AgentState)

        graph.add_node(
            "call_model",
            partial(
                call_model_cron if cron_job_id else call_model_user,
                llm=bound_llm,
                max_context_messages=self._max_context_messages,
                max_tool_calls=self._max_tool_calls,
                db=self._db,
            ),
        )
        graph.add_node(
            "execute_tool",
            partial(execute_tool, tools=tool_dict, manager=self._manager),
        )
        graph.add_node(
            "assess_error",
            partial(assess_error, max_retries=self._max_retries),
        )

        graph.set_entry_point("call_model")
        graph.add_conditional_edges(
            "call_model",
            _should_continue,
            {"execute_tool": "execute_tool", "__end__": END},
        )
        graph.add_edge("execute_tool", "assess_error")
        graph.add_conditional_edges(
            "assess_error",
            lambda state: "call_model" if not state.get("is_final") else "__end__",
            {"call_model": "call_model", "__end__": END},
        )

        compiled = graph.compile()
        logger.debug("[TIMING] graph.compile: %.3fs", time.monotonic() - _t2)
        logger.info(
            f"Agent graph compiled: {len(tools)} tools, "
            f"window={self._max_context_messages}, retries={self._max_retries}"
        )
        return compiled
