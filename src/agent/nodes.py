"""LangGraph 노드 함수 — call_model, execute_tool, assess_error."""

import json
import logging
import re
import time
from typing import Any, Optional

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
from src.browser.manager import set_session_id as _set_current_session

logger = logging.getLogger("psjba.agent.nodes")

ERROR_KEYWORDS = ("error", "failed", "exception", "not found", "timeout")
MIN_KEEP_MESSAGES = 10

# browser_execute_script no-op 패턴: 실제 JS 문장이 없는 경우
_NOOP_SCRIPT_RE = re.compile(
    r'^\s*(?:(?:\/\/[^\n]*|/\*[\s\S]*?\*/|<!--[\s\S]*?-->|\s*;)*\s*)*$'
)


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

    첫 번째 user 메시지는 항상 유지 (툴 목록이 포함된 컨텍스트 유지).

    Args:
        messages: 전체 메시지 리스트
        max_context: 최대 user 메시지 수
        min_keep: 유지할 최소 최근 user 메시지 수

    Returns:
        정리된 메시지 리스트
    """
    try:
        max_context = int(max_context)
        min_keep = int(min_keep)
    except (TypeError, ValueError):
        max_context = 30
        min_keep = 10
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
        and m.additional_kwargs.get("role") != "developer"
    ]

    if len(user_indices) <= max_context:
        return messages

    cutoff_count = len(user_indices) - max_context
    cutoff_user_idx = user_indices[cutoff_count]

    keep_start = cutoff_user_idx + 1

    # 첫 번째 user 메시지는 항상 유지 (툴 목록이 포함된 컨텍스트)
    first_user_idx = user_indices[0]
    if keep_start > first_user_idx:
        keep_start = first_user_idx

    trimmed = non_system[keep_start:]

    if not trimmed:
        return messages

    result = system_msgs + trimmed
    return result


_TOOL_NAME_PATTERN = re.compile(
    r"^(browser_(?:navigate|click|type|select_option|hover|scroll|focus|"
    r"press_key|wait|new_tab|close_tab|switch_tab|list_tabs|"
    r"screenshot|snapshot|"
    r"execute_script|click_by_ref)"
    r"|chrome_(?:health|start)"
    r"|cron_(?:add|list|edit|remove|to_session)"
    r"|job_add"
    r"|memory_search|workspace_write|current_datetime)\(\)?$"
)


def _detect_text_tool_call(content: str) -> Optional[str]:
    """모델이 텍스트로 도구 이름만 출력했는지 감지한다.

    예: "browser_snapshot()" 또는 "chrome_health" 형태의 텍스트.
    tool_call 을 해야 하는데 텍스트로 출력한 경우를 식별.

    Returns:
        매칭된 도구 이름, 또는 None
    """
    clean = content.strip().rstrip(")")
    m = _TOOL_NAME_PATTERN.match(clean)
    return m.group(0) if m else None


def _extract_finish_reason(response: BaseMessage) -> str:
    """AIMessage.response_metadata 에서 finish_reason 추출.

    Returns:
        "finish_reason=stop" 형태, 또는 빈 문자열
    """
    meta = getattr(response, "response_metadata", {}) or {}
    finish_reason = meta.get("finish_reason") or meta.get("finish_details", {}).get("type")
    return f"finish_reason={finish_reason}" if finish_reason else ""


def _is_tool_error(result: str) -> bool:
    """도구 실행 결과가 오류인지 판단한다."""
    lower = result.lower()
    return any(kw in lower for kw in ERROR_KEYWORDS)


async def _invoke_llm(
    messages: list[BaseMessage],
    llm: BaseChatModel,
    max_context_messages: int = 30,
    injected_msgs: Optional[list[BaseMessage]] = None,
    state: Optional[AgentState] = None,
) -> dict[str, Any]:
    """LLM 호출 공통 코어: 슬라이딩 윈도우 → 시스템 프롬프트 → 호출 → 응답.

    Args:
        messages: 컨텍스트 메시지 (슬라이딩 윈도우 적용 전)
        llm: 바인딩된 ChatOpenAI 인스턴스
        max_context_messages: 슬라이딩 윈도우 크기
        injected_msgs: intervention 등 추가로 붙일 메시지
        state: 현재 상태 (에러 카운트 증가용, 없으면 0)

    Returns:
        상태 업데이트 딕셔너리 (messages + 추가)
    """
    messages = _apply_sliding_window(messages, max_context_messages)

    injected_msgs = injected_msgs or []
    if injected_msgs:
        messages = messages + injected_msgs

    # 첫 요청(call_model)에만 툴 목록 포함.
    # messages에 ToolMessage가 있으면 tool loop 안 → 툴 설명이 이미 컨텍스트에 있음
    has_tool_msgs = any(isinstance(m, ToolMessage) for m in messages)
    include_tools = not has_tool_msgs
    system_prompt = build_system_prompt(include_tools=include_tools)

    final_messages = [SystemMessage(content=system_prompt)] + messages

    logger.info(
        f"call_model: {len(final_messages)} messages "
        f"(window: {len(messages)} after sliding)"
    )

    # 마지막 메시지(사용자 입력 or 툴 결과) 요약 로그
    if messages:
        last = messages[-1]
        if isinstance(last, HumanMessage):
            logger.info(f"LLM request >> user: {last.content[:120]}")
        elif isinstance(last, ToolMessage):
            try:
                data = json.loads(last.content)
                logger.info(
                    f"LLM request >> tool result: "
                    f"{data.get('tool', '?')} | "
                    f"{(data.get('result', '') or '')[:100]}"
                )
            except (json.JSONDecodeError, TypeError):
                logger.info(f"LLM request >> tool result: {last.content[:120]}")
        elif isinstance(last, AIMessage):
            hints = ""
            if hasattr(last, "tool_calls") and last.tool_calls:
                hints = f" [{len(last.tool_calls)} tools]"
            logger.info(f"LLM request >> ai{hints}: {last.content[:120]}")

    call_start = time.monotonic()
    logger.debug("LLM call starting...")

    try:
        response = await llm.ainvoke(final_messages)
    except Exception as e:
        duration = time.monotonic() - call_start
        logger.error(f"LLM call failed after {duration:.1f}s: {e}")
        return {
            "messages": injected_msgs + [
                AIMessage(content=f"LLM error: {e}. Please try again.")
            ],
            "error_count": (state.get("error_count", 0) + 1) if state else 1,
        }

    call_duration = time.monotonic() - call_start
    finish_str = _extract_finish_reason(response)

    # token usage
    meta = getattr(response, "response_metadata", {}) or {}
    usage = meta.get("token_usage", {}) or {}
    tokens_str = ""
    if usage:
        parts = []
        if "prompt_tokens" in usage:
            parts.append(f"in={usage['prompt_tokens']}")
        if "completion_tokens" in usage:
            parts.append(f"out={usage['completion_tokens']}")
        if "total_tokens" in usage:
            parts.append(f"total={usage['total_tokens']}")
        if parts:
            tokens_str = " [" + ", ".join(parts) + "]"

    has_tool_calls = hasattr(response, "tool_calls") and response.tool_calls
    if has_tool_calls:
        tool_names = [tc["name"] for tc in response.tool_calls]
        log_msg = (
            f"LLM response ({call_duration:.1f}s){tokens_str}: "
            f"{len(response.tool_calls)} tool calls "
            f"→ {tool_names}"
        )
        if finish_str:
            log_msg += f" | {finish_str}"
        logger.info(log_msg)
    else:
        content = str(response.content or "").strip()
        log_msg = (
            f"LLM response ({call_duration:.1f}s){tokens_str}: "
            f"\"{content[:200]}\""
        )
        if finish_str:
            log_msg += f" | {finish_str}"
        logger.info(log_msg)
        # 모델이 텍스트로 도구 이름만 출력한 경우 경고 (tool_call 누락)
        text_tool_match = _detect_text_tool_call(content)
        if text_tool_match:
            warn_msg = (
                f"⚠️ STOP: model output '{text_tool_match}' as text "
                f"instead of a tool_call. "
                f"tool_choice='any' may not be supported by this model."
            )
            if finish_str:
                warn_msg += f" [{finish_str}]"
            logger.warning(warn_msg)

    return {"messages": injected_msgs + [response]}


async def call_model_user(
    state: AgentState,
    llm: BaseChatModel,
    max_context_messages: int = 30,
    max_tool_calls: int = 30,
) -> dict[str, Any]:
    """사용자 요청용 LLM 호출 — intervention/dialog/경고 포함."""
    # ── intervention 큐 확인: 실행 중 사용자 입력을 메시지로 주입 ──
    queue = state.get("intervention_queue")
    injected_msgs: list[BaseMessage] = []
    if queue:
        while queue:
            text = queue.pop(0)
            logger.info("Injecting intervention: %s", text[:80])
            injected_msgs.append(HumanMessage(content=text))

    # ── pending dialog 큐 확인 (execute_tool이 검출한 dialog override 정보) ──
    pending_dialogs: list[dict] = state.get("pending_dialogs", [])
    dialog_msgs: list[HumanMessage] = []
    if pending_dialogs:
        for d in pending_dialogs:
            tidx = d.get("tab_index", "?")
            dtype = d.get("type", "?")
            msg_text = d.get("message", "")
            dialog_msgs.append(HumanMessage(
                content=f"⚠️ Dialog pending on tab [{tidx}]: {dtype} '{msg_text}'",
                additional_kwargs={"role": "developer"},
            ))
        state["pending_dialogs"] = []  # 소비

    # 최대 tool call 횟수 초과 시 즉시 종료
    current_count = state.get("tool_call_count", 0)
    try:
        current_count = int(current_count)
        max_tool_calls = int(max_tool_calls)
    except (TypeError, ValueError):
        max_tool_calls = 30
    if current_count >= max_tool_calls:
        logger.warning(
            f"Tool call limit reached: {current_count}/{max_tool_calls}, ending"
        )
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"최대 tool call 횟수({max_tool_calls}회)에 도달했습니다. "
                        f"작업을 완료하지 못했습니다. "
                        f"더 간단한 작업으로 나누어 다시 시도해 주세요."
                    )
                )
            ],
            "is_final": True,
        }

    messages = list(state.get("messages", []))

    # ── 툴콜 한도 경고: max_tool_calls - 5 이하 남았을 때 system role 로 안내 ──
    remaining = max_tool_calls - current_count
    if 0 < remaining <= 5:
        warning = (
            f"[시스템 경고] 이번 작업의 tool call 가능 횟수가 {remaining}회 남았습니다. "
            f"최대 {max_tool_calls}회 제한 내에서 작업을 마무리해야 합니다. "
            f"남은 횟수 안에서 목표를 달성할 수 있도록 계획을 줄이고 행동을 최소화하세요."
        )
        logger.warning("Tool call warning: %s remaining", remaining)
        messages = [SystemMessage(content=warning)] + messages

    injected_all = injected_msgs + dialog_msgs

    return await _invoke_llm(
        messages, llm, max_context_messages, injected_msgs=injected_all, state=state,
    )


async def call_model_cron(
    state: AgentState,
    llm: BaseChatModel,
    max_context_messages: int = 30,
    max_tool_calls: int = 50,
) -> dict[str, Any]:
    """크론/잡 전용 LLM 호출 — intervention/dialog 없이 간결하게."""
    current_count = state.get("tool_call_count", 0)
    try:
        current_count = int(current_count)
        max_tool_calls = int(max_tool_calls)
    except (TypeError, ValueError):
        max_tool_calls = 50
    if current_count >= max_tool_calls:
        logger.warning(
            f"Cron: tool call limit reached: {current_count}/{max_tool_calls}, ending"
        )
        return {
            "messages": [
                AIMessage(
                    content=(
                        f"최대 tool call 횟수({max_tool_calls}회)에 도달했습니다. "
                        f"작업을 완료하지 못했습니다."
                    )
                )
            ],
            "is_final": True,
        }

    messages = list(state.get("messages", []))

    return await _invoke_llm(
        messages, llm, max_context_messages, injected_msgs=None, state=state,
    )


async def execute_tool(
    state: AgentState,
    tools: dict[str, Any],
    manager: Optional[Any] = None,
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
    all_pending_dialogs: list[dict] = []

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
                # session context 설정 (a11y refs session 격리)
                if manager is not None:
                    _set_current_session(state.get("session_id", ""))
                result = await tool_func.ainvoke(tool_args)
        except Exception as e:
            result = f"Tool execution error ({tool_name}): {e}"
            any_error = True
        duration_ms = int((time.monotonic() - start) * 1000)

        if logger.isEnabledFor(logging.DEBUG):
            logger.info(
                f"Tool {tool_name}({tool_args}) → "
                f"{result} "
                f"({duration_ms}ms)"
            )
        else:
            logger.info(
                f"Tool {tool_name}({tool_args}) → "
                f"{result[:200]}{'...' if len(str(result)) > 200 else ''} "
                f"({duration_ms}ms)"
            )

        if _is_tool_error(str(result)):
            any_error = True

        # no-op script reject (모델이 생각용으로 script를 사용하는 경우 방지)
        if tool_name == "browser_execute_script":
            script = tool_args.get("script", "")
            if _NOOP_SCRIPT_RE.match(script):
                result = (
                    "⚠️  No-op script rejected. This script contains only comments "
                    "and no executable JavaScript. "
                    "If you need to 'think', use your text output instead of "
                    "creating fake script calls. "
                    "browser_execute_script is for real JavaScript execution."
                )
                any_error = True

        # tool 실행 직후 dialog 체크 (override된 confirm/alert/prompt 기록)
        dialog_hint = ""
        if manager and hasattr(manager, "check_all_tabs_dialog"):
            try:
                dialogs = await manager.check_all_tabs_dialog()
                if dialogs:
                    for d in dialogs:
                        all_pending_dialogs.append(d)
                    dialog_hint = (
                        f" ⚠️Dialog: tab[{dialogs[0]['tab_index']}] "
                        f"{dialogs[0]['type']} '{dialogs[0].get('message','')}'"
                    )
            except Exception:
                pass

        tool_msg_content = json.dumps(
            {
                "tool": tool_name,
                "result": str(result) + dialog_hint,
                "duration_ms": duration_ms,
                "status": "error" if any_error else "success",
            },
            ensure_ascii=False,
        )
        tool_messages.append(
            ToolMessage(content=tool_msg_content, tool_call_id=tool_id)
        )

    new_error_count = error_count + 1 if any_error else 0

    # tool call 횟수 증가 및 로그
    current_tool_count = state.get("tool_call_count", 0)
    tool_call_count = current_tool_count + len(last_ai.tool_calls)
    logger.info(
        f"Tool call progress: {tool_call_count} "
        f"(last batch: {len(last_ai.tool_calls)})"
    )

    updates: dict[str, Any] = {
        "messages": tool_messages,
        "tool_call_count": tool_call_count,
        "pending_dialogs": all_pending_dialogs,
    }

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
) -> dict:
    """도구 실행 결과를 평가하여 상태를 업데이트한다.

    노드 반환값은 dict(state update) 여야 한다.
    라우팅은 graph.py 의 conditional edge(lambda state: ...) 가 담당한다.

    Args:
        state: 현재 에이전트 상태
        max_retries: 최대 재시도 횟수
    """
    error_count = state.get("error_count", 0)
    is_final = state.get("is_final", False)

    if is_final:
        logger.info("assess_error: is_final=True, ending")
        return {}

    if error_count == 0:
        logger.info("assess_error: no error, continue")
        return {}

    if error_count < max_retries:
        logger.info(
            "assess_error: error_count=%d, retrying (%d retries left)",
            error_count,
            max_retries - error_count,
        )
        return {}

    logger.warning(
        "assess_error: error_count=%d >= %d, giving up",
        error_count,
        max_retries,
    )
    return {
        "is_final": True,
        "messages": [
            AIMessage(
                content=(
                    f"I've encountered {error_count} consecutive errors and cannot "
                    f"complete the task. Please check the browser state and try again."
                )
            )
        ],
    }
