"""LangGraph 노드 함수 — call_model, execute_tool, assess_error."""

import json
import logging
import time
from typing import Any, Literal, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.language_models import BaseChatModel

from src.agent.prompts import build_system_prompt
from src.agent.state import AgentState

logger = logging.getLogger("psjba.agent.nodes")

ERROR_KEYWORDS = ("error", "failed", "exception", "not found", "timeout")
MIN_KEEP_MESSAGES = 10


def _apply_sliding_window(
    messages: list[BaseMessage],
    max_context: int,
    min_keep: int = MIN_KEEP_MESSAGES,
) -> list[BaseMessage]:
    """슬라이딩 윈도우를 적용하여 messages를 정리한다.

    system 메시지는 항상 유지.
    user 메시지 기준으로 max_context 초과 시 오래된 것부터 제거.
    min_keep개의 최근 user 메시지는 절대 제거하지 않음.
    assistant 메시지는 user 메시지에 종속되어 함께 제거됨.

    Args:
        messages: 전체 메시지 리스트
        max_context: 최대 user 메시지 수
        min_keep: 유지할 최소 최근 user 메시지 수

    Returns:
        정리된 메시지 리스트
    """
    if max_context <= 0:
        return messages

    system_msgs: list[BaseMessage] = []
    non_system: list[BaseMessage] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            system_msgs.append(msg)
        else:
            non_system.append(msg)

    user_indices = [
        i for i, m in enumerate(non_system) if isinstance(m, HumanMessage)
    ]

    if len(user_indices) <= max_context:
        return messages

    cutoff_count = len(user_indices) - max_context
    cutoff_user_idx = user_indices[cutoff_count]

    keep_start = cutoff_user_idx + 1
    trimmed = non_system[keep_start:]

    if not trimmed:
        return messages

    result = system_msgs + trimmed
    return result


def _is_tool_error(result: str) -> bool:
    """도구 실행 결과가 오류인지 판단한다."""
    lower = result.lower()
    return any(kw in lower for kw in ERROR_KEYWORDS)


async def call_model(
    state: AgentState,
    llm: BaseChatModel,
    max_context_messages: int = 30,
) -> dict[str, Any]:
    """LLM을 호출하여 다음 액션을 결정한다.

    슬라이딩 윈도우를 적용하고, 시스템 프롬프트에 동적 a11y 트리를 주입한다.

    Args:
        state: 현재 에이전트 상태
        llm: ChatOpenAI 인스턴스
        max_context_messages: 슬라이딩 윈도우 크기

    Returns:
        상태 업데이트 딕셔너리 (messages 추가)
    """
    messages = list(state.get("messages", []))

    messages = _apply_sliding_window(messages, max_context_messages)

    a11y_tree = state.get("accessibility_tree", "(No page loaded)")
    system_prompt = build_system_prompt(a11y_tree)

    final_messages = [SystemMessage(content=system_prompt)] + messages

    logger.info(
        f"call_model: {len(final_messages)} messages "
        f"(window: {len(messages)} after sliding)"
    )

    try:
        response = await llm.ainvoke(final_messages)
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return {
            "messages": [
                AIMessage(content=f"LLM error: {e}. Please try again.")
            ],
            "error_count": state.get("error_count", 0) + 1,
        }

    has_tool_calls = hasattr(response, "tool_calls") and response.tool_calls
    if has_tool_calls:
        logger.info(
            f"LLM requested {len(response.tool_calls)} tool calls: "
            f"{[tc['name'] for tc in response.tool_calls]}"
        )

    return {"messages": [response]}


async def execute_tool(
    state: AgentState,
    tools: dict[str, Any],
) -> dict[str, Any]:
    """LLM이 요청한 도구 호출을 실행한다.

    Args:
        state: 현재 에이전트 상태
        tools: 도구 이름→callable 매핑 딕셔너리

    Returns:
        상태 업데이트 딕셔너리 (ToolMessage 추가, error_count 갱신)
    """
    messages = list(state.get("messages", []))
    error_count = state.get("error_count", 0)

    last_ai = None
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
            last_ai = msg
            break

    if last_ai is None:
        return {
            "messages": [
                ToolMessage(
                    content="No tool calls found in last AI message.",
                    tool_call_id="error",
                )
            ],
            "error_count": error_count + 1,
        }

    tool_messages: list[BaseMessage] = []
    any_error = False

    for tc in last_ai.tool_calls:
        tool_name = tc["name"]
        tool_args = tc.get("args", {})
        tool_id = tc["id"]

        start = time.monotonic()
        try:
            tool_func = tools.get(tool_name)
            if tool_func is None:
                result = f"Unknown tool: {tool_name}"
                any_error = True
            else:
                result = await tool_func.ainvoke(tool_args)
        except Exception as e:
            result = f"Tool execution error ({tool_name}): {e}"
            any_error = True
        duration_ms = int((time.monotonic() - start) * 1000)

        logger.info(
            f"Tool {tool_name}({tool_args}) → "
            f"{result[:200]}{'...' if len(str(result)) > 200 else ''} "
            f"({duration_ms}ms)"
        )

        if _is_tool_error(str(result)):
            any_error = True

        tool_msg_content = json.dumps(
            {
                "tool": tool_name,
                "result": str(result),
                "duration_ms": duration_ms,
                "status": "error" if any_error else "success",
            },
            ensure_ascii=False,
        )
        tool_messages.append(
            ToolMessage(content=tool_msg_content, tool_call_id=tool_id)
        )

    new_error_count = error_count + 1 if any_error else 0

    updates: dict[str, Any] = {"messages": tool_messages}

    if any_error:
        updates["error_count"] = new_error_count
    else:
        updates["error_count"] = 0

    screenshot_path = state.get("last_screenshot_path", "")
    if not any_error:
        for tm in tool_messages:
            try:
                data = json.loads(tm.content)
                if data.get("tool") == "browser_screenshot":
                    updates["last_screenshot_path"] = data.get("result", "")
            except (json.JSONDecodeError, TypeError):
                pass

    return updates


def assess_error(
    state: AgentState,
    max_retries: int = 3,
) -> Literal["call_model", "__end__"]:
    """도구 실행 결과를 평가하여 다음 라우팅을 결정한다.

    Args:
        state: 현재 에이전트 상태
        max_retries: 최대 재시도 횟수

    Returns:
        "call_model" (재시도 또는 정상继续) 또는 "__end__" (포기)
    """
    error_count = state.get("error_count", 0)
    is_final = state.get("is_final", False)

    if is_final:
        logger.info("assess_error: is_final=True, ending")
        return "__end__"

    if error_count == 0:
        logger.info("assess_error: no error, continue")
        return "call_model"

    if error_count < max_retries:
        logger.info(
            f"assess_error: error_count={error_count}, retrying "
            f"({max_retries - error_count} retries left)"
        )
        return "call_model"

    logger.warning(
        f"assess_error: error_count={error_count} >= {max_retries}, giving up"
    )

    state["messages"] = state.get("messages", []) + [
        AIMessage(
            content=(
                f"I've encountered {error_count} consecutive errors and cannot "
                f"complete the task. Please check the browser state and try again."
            )
        )
    ]
    state["is_final"] = True
    return "__end__"
