"""Agent 실행기 — 프롬프트 제출 → LangGraph 그래프 스트리밍 → 결과 저장."""

import asyncio
import logging
import time
from contextlib import aclosing
from typing import Any, AsyncIterator, Optional

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)

from src.agent.graph import AgentGraph
from src.agent.llm_client import LLMClient
from src.models.schemas import Message
from src.ui.helpers import finish_prompt_cancelled, show_error_in_ui

logger = logging.getLogger("psjba")

_DEFAULT_AGENT_TIMEOUT = 300  # 메인 세션 그래프 타임아웃 (초)


async def async_iter_timeout(
    aiterable: Any,
    timeout: float,
    timeout_msg: str = "Async iteration timed out",
) -> AsyncIterator[Any]:
    """aysnc generator/async iterable에 타임아웃을 적용한다.

    각 yield 사이의 간격만 타임아웃 체크 (전체 실행 시간이 아님).
    """
    it = aiterable.__aiter__()
    while True:
        try:
            val = await asyncio.wait_for(it.__anext__(), timeout=timeout)
            yield val
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            logger.error(timeout_msg)
            raise


async def handle_prompt(
    config: Any,
    db: Any,
    manager: Any,
    window: Any,
    text: str,
    job_scheduler: Optional[Any] = None,
    agent: Optional[AgentGraph] = None,
) -> None:
    """사용자 프롬프트를 LangGraph 에이전트로 실행하고 결과를 UI/DB에 반영한다."""
    logger.info("User prompt: %s", text[:80])
    _t = time.monotonic()

    session_id = await db.get_current_session_id()
    if not session_id:
        from src.models.schemas import Session
        session = Session(title="새 대화", type="chat")
        await db.create_session(session)
        await db.set_current_session_id(session.id)
        session_id = session.id

    user_msg = Message(session_id=session_id, role="user", content=text)
    await db.save_message(user_msg)
    logger.debug("[TIMING] session/DB: %.3fs", time.monotonic() - _t)

    llm_client = None
    try:
        _t2 = time.monotonic()
        llm_client = LLMClient(config)
        logger.debug("[TIMING] LLMClient(): %.3fs", time.monotonic() - _t2)

        _t3 = time.monotonic()
        graph = (agent or AgentGraph(
            manager=manager, db=db, job_scheduler=job_scheduler,
            max_context_messages=int(config.get("agent", "sliding_window_size")),
            max_tool_calls=int(config.get("agent", "max_tool_calls")),
        )).compiled(session_id=session_id, llm=llm_client.llm)
        logger.debug("[TIMING] create_agent_graph: %.3fs", time.monotonic() - _t3)

        _t4 = time.monotonic()
        browser_context = await manager.get_browser_context()
        logger.debug("[TIMING] get_browser_context: %.3fs", time.monotonic() - _t4)
        logger.info("Browser context:\n%s", browser_context)

        state = {
            "session_id": session_id,
            "user_text": text,
            "browser_context": browser_context,
            "intervention_queue": window.intervention_queue,
            "url": "",
            "page_title": "",
            "accessibility_tree": "",
            "last_screenshot_path": "",
            "error_count": 0,
            "is_final": False,
            "tool_call_count": 0,
        }

        _t5 = time.monotonic()
        window.show_ai_thinking("🤖 응답 대기 중...")

        try:
            final_state = None
            async with aclosing(
                async_iter_timeout(
                    graph.astream(state, stream_mode="values"),
                    timeout=_DEFAULT_AGENT_TIMEOUT,
                    timeout_msg="Agent graph execution timed out",
                )
            ) as stream:
                async for event in stream:
                    if final_state is None:
                        logger.debug("[TIMING] first astream event: %.3fs", time.monotonic() - _t5)
                    final_state = event
                    try:
                        event_msgs = event.get("messages", [])
                        if event_msgs:
                            for m in reversed(event_msgs):
                                if m.type != "ai":
                                    continue
                                content = str(m.content or "").strip()
                                if content:
                                    window._chat.update_ai_content(content)
                                break
                    except Exception as e:
                        logger.debug("Real-time content update failed: %s", e)
                    await asyncio.sleep(0)
                    if window.stop_requested:
                        logger.warning("Stop requested during agent execution, breaking loop")
                        break

            final_messages = final_state.get("messages", []) if final_state else []
        except asyncio.CancelledError:
            logger.warning("Agent execution cancelled (graph astream)")
            finish_prompt_cancelled(window)
            return
        except asyncio.TimeoutError:
            logger.error("Agent graph execution timed out")
            show_error_in_ui(window, "⏱️ 실행 시간 초과 (300초)")
            return
        except Exception as e:
            logger.error("Agent execution failed", exc_info=True)
            show_error_in_ui(window, "⚠️ 에이전트 실행 중 오류", e)
            return

        if window.stop_requested:
            finish_prompt_cancelled(window)
            return

        try:
            assistant_msgs = [m for m in final_messages if m.type == "ai"]
            if assistant_msgs:
                last = assistant_msgs[-1]
                tool_calls = None
                if hasattr(last, "tool_calls") and last.tool_calls:
                    tool_calls = [
                        {"name": tc.get("name", ""), "args": tc.get("args", {}),
                         "id": tc.get("id", "")}
                        for tc in last.tool_calls
                    ]
                window._chat.finalize_ai_response(str(last.content), tool_calls=tool_calls)

            for msg in final_messages:
                try:
                    role = "assistant" if msg.type == "ai" else msg.type
                    if role not in ("user", "assistant", "tool", "system"):
                        continue
                    if role == "user" and isinstance(msg, HumanMessage) and \
                       msg.additional_kwargs.get("role") == "developer":
                        continue
                    db_msg = Message(
                        session_id=session_id,
                        role=role,
                        content=msg.content if isinstance(msg.content, str) else str(msg.content),
                    )
                    if role == "tool":
                        db_msg.tool_call_id = getattr(msg, "tool_call_id", "") or ""
                    await db.save_message(db_msg)
                except Exception as e:
                    logger.error("Error saving message (role=%s): %s", role, e)

        except asyncio.CancelledError:
            logger.warning("Result processing cancelled — shutdown in progress")
            raise
        except Exception as e:
            logger.error("Error processing agent response: %s", e)
        finally:
            window._prompt.set_running(False)
    except asyncio.CancelledError:
        logger.warning("Agent initialization cancelled — shutdown in progress")
        raise
    except asyncio.TimeoutError:
        logger.error("Agent LLM initialization timed out")
        show_error_in_ui(window, "⏱️ LLM 초기화 시간 초과")
    except Exception as e:
        logger.error(f"Agent initialization failed: {e}", exc_info=True)
        show_error_in_ui(window, "⚠️ 에이전트 초기화 중 오류", e)
    finally:
        if llm_client is not None:
            await llm_client.close()