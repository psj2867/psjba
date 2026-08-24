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
from src.agent.nodes import request_abort, reset_abort
from src.agent.llm_client import LLMClient
from src.models.schemas import Message
from src.ui.helpers import finish_prompt_cancelled, show_error_in_ui

logger = logging.getLogger("psjba")

_DEFAULT_AGENT_TIMEOUT = 300  # 메인 세션 그래프 타임아웃 (초)


async def build_initial_messages(
    db: Any,
    session_id: str,
    user_text: str,
    browser_context: str,
    config: Any = None,
) -> list:
    """DB history + browser_context + user_text로 초기 messages 리스트를 구성한다.

    runner.py의 handle_prompt에서 state.messages에 세팅하기 위해 사용.
    """
    from langchain_core.messages import (
        AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage,
    )

    max_context_messages = 5  # 기본값
    if config:
        try:
            max_context_messages = int(getattr(getattr(config, "agent", None), "sliding_window_size", 5))
        except (TypeError, ValueError):
            pass

    _t_load = time.monotonic()

    try:
        history_rows = await db.get_messages(session_id, limit=999999)
    except Exception as e:
        logger.error("build_initial_messages: DB load failed: %s", e)
        return []

    # 방금 저장된 user 메시지는 user_text로 대체
    if history_rows and history_rows[-1].role == "user":
        history_rows = history_rows[:-1]

    # user 메시지 기준 슬라이딩
    user_indices = [i for i, r in enumerate(history_rows) if r.role == "user"]
    skipped_count = 0
    if len(user_indices) > max_context_messages:
        keep_from = user_indices[-max_context_messages]
        skipped_count = len(user_indices) - max_context_messages
        history_rows = history_rows[keep_from:]

    messages: list[BaseMessage] = []
    for row in history_rows:
        if row.role == "user":
            messages.append(HumanMessage(content=row.content))
        elif row.role == "assistant":
            messages.append(AIMessage(content=row.content))
        elif row.role == "tool":
            messages.append(ToolMessage(content=row.content, tool_call_id=""))
        elif row.role == "system":
            messages.append(SystemMessage(content=row.content))

    # 생략된 user 프롬프트 수 안내
    if skipped_count > 0:
        messages.insert(0, SystemMessage(
            content=f"[시스템] 이전 {skipped_count}개의 사용자 요청은 컨텍스트 제한으로 생략되었습니다. "
                    f"필요 시 memory_search 도구로 과거 내용을 검색할 수 있습니다."
        ))

    # browser context + current date → developer message
    from datetime import datetime
    current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    info_parts = [f"[Current Date]\n{current_date}"]
    if browser_context and browser_context != "[Browser: not connected]":
        info_parts.append(f"[Browser Context]\n{browser_context}")
    messages.append(HumanMessage(
        content="\n\n".join(info_parts),
        additional_kwargs={"role": "developer"},
    ))

    # 현재 user text
    if user_text:
        messages.append(HumanMessage(content=user_text))

    logger.debug("[TIMING] build_initial_messages: %.3fs (%d msgs)",
                 time.monotonic() - _t_load, len(messages))
    return messages


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

    reset_abort()  # 이전 실행의 중단 플래그 초기화

    llm_client = None
    try:
        _t2 = time.monotonic()
        llm_client = LLMClient(config)
        logger.debug("[TIMING] LLMClient(): %.3fs", time.monotonic() - _t2)

        _t3 = time.monotonic()
        graph = (agent or AgentGraph(
            manager=manager, db=db, job_scheduler=job_scheduler, config=config,
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

        # 초기 메시지 구성: DB history + browser_context + user_text
        initial_msgs = await build_initial_messages(
            db, session_id, text, browser_context, config,
        )
        if initial_msgs:
            state["messages"] = initial_msgs
            initial_msg_count = len(initial_msgs)
        else:
            # DB 로드 실패 시에도 user_text는 보존
            state["messages"] = [HumanMessage(content=text)]
            initial_msg_count = 1

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
                        if event_msgs and event_msgs[-1].type == "ai":
                            content = str(event_msgs[-1].content or "").strip()
                            if content:
                                window._chat.update_ai_content(content)
                    except Exception as e:
                        logger.debug("Real-time content update failed: %s", e)
                    await asyncio.sleep(0)
                    if window.stop_requested:
                        request_abort()
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
            request_abort()
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

            for msg in final_messages[initial_msg_count:]:
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