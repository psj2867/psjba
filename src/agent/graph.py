"""LangGraph 그래프 정의 — 에이전트 실행 흐름 구성.

Architecture
────────────
  AgentGraph (러너/크론이 사용)
    │
    ├─ compiled(session_id, llm, cron_job_id)
    │     → _build_cached(session_id, …)    ← @lru_cache(maxsize=32)
    │     → node.set_llm(bound_llm)         ← bind_tools + 주입 (호출 직전)
    │     → compiled graph 반환
    │
    └─ _build_cached (module-level, hashable param만)
          → tools 생성
          → _CallModelNode + StateGraph.compile
          → (compiled_graph, call_node, tools) 반환
"""

import functools
import logging
import time
from functools import partial
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import END, StateGraph

from src.agent.nodes import (
    _shared_config as _set_nodes_config,
    assess_error,
    call_model_cron,
    call_model_user,
    execute_tool,
)
from src.agent.state import AgentState
from src.agent.tools import create_all_tools

logger = logging.getLogger("psjba.agent.graph")


# ── 앱 lifetime singleton (AgentGraph.__init__에서 1회 설정) ────────
_shared_manager: Any = None
_shared_db: Any = None
_shared_scheduler: Any = None


# ── 라우팅 ─────────────────────────────────────────────────────────

def _should_continue(state: AgentState) -> str:
    """call_model 이후 라우팅: 마지막 AIMessage에 tool_calls 있으면 execute_tool, 없으면 END."""
    messages = state.get("messages", [])
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                return "execute_tool"
            return "__end__"
    return "__end__"


# ── 동적 LLM 주입용 call_model 노드 ────────────────────────────────

class _CallModelNode:
    """call_model 노드 — partial 대신 사용, LLM을 동적으로 주입.

    _build_cached에서 compiled graph에 박히고,
    AgentGraph.compiled()에서 set_llm()으로 LLM을 주입한다.
    """

    def __init__(self, is_cron: bool) -> None:
        self._is_cron = is_cron
        self._llm: Any = None  # set_llm()으로 주입

    def set_llm(self, llm: Any) -> None:
        self._llm = llm

    async def __call__(self, state: AgentState) -> dict:
        fn = call_model_cron if self._is_cron else call_model_user
        return await fn(state, llm=self._llm, db=_shared_db)


# ── module-level lru_cache 빌드 ────────────────────────────────────

@functools.lru_cache(maxsize=32)
def _build_cached(
    session_id: str,
    is_cron: bool,
    cron_job_id: str,
) -> tuple:
    """(compiled_graph, call_node, tool_dict) 를 캐싱하여 반환.

    Hashable param만 받고, 나머지(app-lifetime singleton)는 module global 참조.
    설정값(max_tool_calls, sliding_window_size 등)은 _shared_config에서 실시간 읽음.
    LLM은 포함되지 않음 → AgentGraph.compiled()에서 set_llm()으로 주입.
    """
    _t0 = time.monotonic()

    tools = create_all_tools(
        _shared_manager, db=_shared_db, session_id=session_id,
        job_scheduler=_shared_scheduler, cron_job_id=cron_job_id,
    )
    logger.debug("[TIMING] create_all_tools: %.3fs", time.monotonic() - _t0)

    tool_dict: dict[str, Any] = {t.name: t for t in tools}
    tool_list: list = tools  # bind_tools용 list

    call_node = _CallModelNode(is_cron)

    _t1 = time.monotonic()
    graph = StateGraph(AgentState)

    graph.add_node("call_model", call_node)
    graph.add_node(
        "execute_tool",
        partial(execute_tool, tools=tool_dict, manager=_shared_manager),
    )
    graph.add_node("assess_error", assess_error)

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
    logger.debug("[TIMING] graph.compile: %.3fs", time.monotonic() - _t1)
    logger.info(f"Agent graph compiled: {len(tools)} tools")
    return compiled, call_node, tool_list


# ── AgentGraph ─────────────────────────────────────────────────────

class AgentGraph:
    """LangGraph 에이전트 — LRU 캐싱 + LLM 동적 주입.

    사용법:
        agent = AgentGraph(config=config, manager=..., db=..., ...)

        # 매 프롬프트
        graph = agent.compiled(
            session_id=session_id,
            llm=llm_client.llm,
        )
        async for event in graph.astream(state):
            ...
    """

    def __init__(
        self,
        *,
        config: Any,
        manager: Any,
        db: Any = None,
        job_scheduler: Any = None,
    ) -> None:
        global _shared_manager, _shared_db, _shared_scheduler
        _shared_manager = manager
        _shared_db = db
        _shared_scheduler = job_scheduler
        # nodes.py에도 config 전파 (실시간 읽기용)
        _set_nodes_config(config)

    # ── public ─────────────────────────────────────────────────

    def compiled(
        self,
        session_id: str,
        llm: BaseChatModel,
        cron_job_id: str = "",
    ) -> Any:
        """캐싱된 compiled graph를 반환한다 (LLM은 호출 직전 주입)."""
        is_cron = bool(cron_job_id)
        compiled, call_node, tools = _build_cached(
            session_id, is_cron, cron_job_id,
        )
        # bind_tools + LLM 주입 (호출 직전, ~0.028s)
        bound_llm = llm.bind_tools(tools, tool_choice="auto")
        call_node.set_llm(bound_llm)
        return compiled
