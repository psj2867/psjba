"""PSJBA — Browser Agent 진입점.

11단계 시작 시나리오:
1. Config 로드  2. 로깅 설정  3. 워크스페이스 생성  4. DB 연결/초기화
5. PlaywrightManager  6. CronExecutor/CronScheduler  7. MainWindow
8. 시그널 연결  9. 세션 복원  10. 필수값 확인  11. qasync 이벤트 루프
"""

import asyncio
import logging
import os
import signal
import sys
import traceback
from contextlib import aclosing
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import qasync

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from src.agent.graph import create_agent_graph
from src.agent.llm_client import LLMClient
from src.config import Config
from src.database import Database
from src.browser.manager import PlaywrightManager
from src.cron.scheduler import CronScheduler
from src.cron.executor import CronExecutor
from src.cron.job_scheduler import JobScheduler
from src.models.schemas import Message, Session
from src.ui.main_window import MainWindow

logger = logging.getLogger("psjba")

CONFIG_PATH = "config.ini"

_CANCELLED_MSG = "⚠️ 사용자가 실행을 취소했습니다."

_DEFAULT_AGENT_TIMEOUT = 300  # 메인 세션 그래프 타임아웃 (초)


def _finish_prompt_cancelled(window: "MainWindow") -> None:
    """stop/cancel 시 UI 정리: 스켈론 교체 + 입력창 복구."""
    window._chat.replace_thinking_skeleton(_CANCELLED_MSG)
    window._prompt.set_running(False)


def _show_error_in_ui(window: "MainWindow", title: str, exc: Optional[BaseException]) -> None:
    """에러 메시지를 UI 채팅에 표시하고 입력창을 복구한다."""
    detail = f"({type(exc).__name__}: {exc})" if exc else ""
    msg = f"{title} {detail}" if detail else title
    window._chat.replace_thinking_skeleton(msg)
    window._prompt.set_running(False)

# ─── 크래시 진단 후크 ───────────────────────────────────────

_ORIG_EXCEPTHOOK: object | None = None
_main_window_ref: Optional["MainWindow"] = None


def notify_user_critical(title: str, message: str) -> None:
    """MainWindow를 통해 사용자에게 치명적 오류를 알린다."""
    win = _main_window_ref
    if win is None:
        return
    try:
        msg = f"⚠️ **{title}**\n\n{message[:300]}"
        win._chat.add_user_message(msg)
    except Exception:
        logger.exception("notify_user_critical failed")


def _setup_crash_hooks() -> None:
    """전역 예외 후크를 등록하여 잡히지 않은 예외를 로그에 기록한다."""
    # 1. sys.excepthook — 동기 코드의 잡히지 않은 예외
    global _ORIG_EXCEPTHOOK
    _ORIG_EXCEPTHOOK = sys.excepthook

    def excepthook(exc_type, exc_value, exc_tb) -> None:
        logger.critical(
            "Unhandled exception (sys.excepthook): %s: %s\n%s",
            exc_type.__name__, exc_value,
            "".join(traceback.format_tb(exc_tb)),
        )
        notify_user_critical(
            exc_type.__name__, f"{exc_value}"
        )
        if _ORIG_EXCEPTHOOK:
            _ORIG_EXCEPTHOOK(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook


def parse_args() -> str:
    """명령줄 인자를 파싱하여 설정 파일 경로를 반환한다."""
    import argparse
    parser = argparse.ArgumentParser(description="PSJBA — Browser Agent")
    parser.add_argument(
        "-c", "--config",
        default=CONFIG_PATH,
        help=f"설정 파일 경로 (기본: {CONFIG_PATH})",
    )
    args, _ = parser.parse_known_args()
    return args.config


def setup_logging(config: Config) -> None:
    level_str = config.get("logging", "level")
    log_path = config.get("logging", "path")

    root_logger = logging.getLogger("psjba")
    level = getattr(logging, level_str.upper(), logging.WARNING)
    root_logger.setLevel(level)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if log_path and log_path.strip():
        # 파일만
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.NOTSET)
        fh.setFormatter(fmt)
        root_logger.addHandler(fh)
        logger.info("Logging to file: level=%s path=%s", level_str, log_path)
    else:
        # 콘솔만 (기본)
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.NOTSET)
        ch.setFormatter(fmt)
        root_logger.addHandler(ch)


def setup_signal_handlers(
    shutdown_event: asyncio.Event,
) -> None:
    """시그널 핸들러 등록.

    ``shutdown_event.set()`` 만 호출하여 ``run()`` 의 ``await shutdown_event.wait()``
    이후 정리 로직이 실행되도록 한다.
    """
    def shutdown(signum, frame):
        logger.info(f"Signal {signum} received, shutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)


async def run(config: Config) -> None:
    shutdown_event = asyncio.Event()
    _setup_crash_hooks()

    logger.info("Step 1/10: Config loaded (path=%s)", CONFIG_PATH)

    # DB
    db_path = config.get("sqlite", "path")
    db = Database(db_path)
    await db.connect()
    await db.initialize()
    logger.info("Step 2/10: Database ready (path=%s)", db_path)

    # 워크스페이스
    workspace_dir = config.get("workspace", "workspace_dir")
    Path(workspace_dir).mkdir(parents=True, exist_ok=True)
    Path(workspace_dir, "screenshots").mkdir(parents=True, exist_ok=True)
    Path(workspace_dir, "downloads").mkdir(parents=True, exist_ok=True)
    Path(workspace_dir, "exports").mkdir(parents=True, exist_ok=True)
    logger.info("Step 3/10: Workspace ready (dir=%s)", workspace_dir)

    # PlaywrightManager
    manager = PlaywrightManager(
        cdp_host=config.get("cdp", "host"),
        cdp_port=config.get("cdp", "port"),
        connection_timeout=config.get("cdp", "connection_timeout"),
        chrome_path=config.get("chrome", "path"),
        user_data_dir=config.get("chrome", "user_data_dir"),
        headless=config.get("chrome", "headless"),
        workspace_dir=workspace_dir,
    )
    logger.info("Step 4/10: PlaywrightManager created (cdp=%s:%s, headless=%s)",
                config.get("cdp", "host"),
                config.get("cdp", "port"),
                config.get("chrome", "headless"))

    # Cron
    cron_executor = CronExecutor(config=config, db=db, manager=manager)
    cron_scheduler = CronScheduler()
    cron_scheduler.set_dependencies(db=db, executor=cron_executor)
    await cron_scheduler.start()
    logger.info("Step 5/10: CronScheduler started (%d jobs)",
                cron_scheduler.registered_count)

    # 1회성 JobScheduler
    job_scheduler = JobScheduler(config=config, db=db, manager=manager)
    await job_scheduler.start()
    logger.info("Step 5a/10: JobScheduler started")

    # 모든 크론/예약 job 비활성화 (설정 UI에서 수동 활성화 필요)
    await db.conn.execute(
        "UPDATE psjba_cron_job SET status = 'disable', updated_at = datetime('now') "
        "WHERE status = 'enable'"
    )
    await db.conn.execute(
        "UPDATE psjba_scheduled_job SET status = 'cancelled' "
        "WHERE status = 'pending'"
    )
    await db.conn.commit()
    logger.info("Step 5c/10: All cron/scheduled jobs disabled at startup")

    # manager에 크론 의존성 주입
    manager.set_cron_dependencies(db=db, cron_scheduler=cron_scheduler)
    logger.info("Step 5b/10: Cron dependencies injected into manager")

    # MainWindow
    window = MainWindow(config=config, db=db, manager=manager)
    _main_window_ref = window
    manager.set_main_window(window)
    logger.info("Step 6/10: MainWindow created")

    # 시그널
    def _on_prompt(text: str) -> None:
        """프롬프트 제출 처리 — 큐 기반, 큐 소비 루프."""
        async def _run_loop() -> None:
            try:
                while window.intervention_queue:
                    current_text = window.intervention_queue.pop(0)
                    window._prompt.set_running(True)
                    window.stop_requested = False
                    await _handle_prompt(config, db, manager, window, current_text, job_scheduler)
                    await asyncio.sleep(0)
            finally:
                window._queue_processor_running = False

        task = asyncio.ensure_future(_run_loop())
        window._agent_task = task
        def _log_task_error(t: asyncio.Task) -> None:
            window._queue_processor_running = False
            if t.cancelled():
                logger.warning("Task _run_loop was cancelled")
                window._agent_task = None
                window._prompt.set_running(False)
                return
            exc = t.exception()
            if exc is not None:
                logger.error(
                    "Task in _run_loop failed: %s: %s\n%s",
                    type(exc).__name__, exc,
                    "".join(traceback.format_tb(exc.__traceback__)),
                )
            window._agent_task = None
            window._prompt.set_running(False)
        task.add_done_callback(_log_task_error)

    window.prompt_submitted.connect(_on_prompt)
    window.session_reset_requested.connect(
        lambda: asyncio.ensure_future(_handle_session_reset(db, window))
    )
    setup_signal_handlers(shutdown_event=shutdown_event)
    logger.info("Step 7/10: Signals connected")

    # 창 닫힘 → shutdown trigger
    window.closed.connect(shutdown_event.set)
    logger.info("Step 7b/10: Window closed signal connected")

    # Qt aboutToQuit — app.quit() 호출 또는 외부 요인으로 앱 종료 시 로그
    app = qasync.QApplication.instance()
    if app:
        app.aboutToQuit.connect(lambda: logger.info("QApplication aboutToQuit"))
    logger.info("Step 7c/10: aboutToQuit logger connected")

    # 세션 복원
    session_id = await db.get_current_session_id()
    if session_id:
        messages = await db.get_messages(session_id, limit=100)
        if messages:
            msg_dicts = [
                {"role": m.role, "content": m.content}
                for m in messages
            ]
            window._chat.load_messages(msg_dicts)
            logger.info("Step 8/10: Session restored (id=%s, %d msgs)",
                        session_id, len(messages))
    else:
        session = Session(title="새 대화", type="chat")
        await db.create_session(session)
        await db.set_current_session_id(session.id)
        session_id = session.id
        logger.info("Step 8/10: New session created (id=%s)", session_id)

    # UI 표시
    logger.info("Step 9/10: Showing window")
    window.show()

    # 필수값 확인
    api_key = config.api_key
    model = config.get("llm", "model")
    if not api_key or not model:
        logger.warning("Step 10/10: api_key or model not set, prompting user")
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(
            window,
            "설정 필요",
            "API 키와 모델을 설정(⚙️)에서 설정해주세요.",
        )
        window._open_settings()
    else:
        logger.info("Step 10/10: api_key and model configured")

    logger.info("Application started successfully")

    try:
        # run_forever 대신 shutdown_event 를 기다림
        # (qasync 의 run_until_complete 가 내부에서 Qt 이벤트 루프를 실행)
        await shutdown_event.wait()
    except asyncio.CancelledError:
        logger.info("Application cancelled")
    finally:
        # 종료 정리
        cron_scheduler.stop_all()
        cron_executor.cancel_all()
        await job_scheduler.stop()
        if manager.is_connected:
            await manager.disconnect()
        await db.close()
        # QApplication 정리 — Qt 이벤트 루프가 아직 활성 상태이므로 quit()이 유효함
        app = qasync.QApplication.instance()
        if app:
            app.quit()
        logger.info("Application stopped")


async def _async_iter_timeout(
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


async def _handle_prompt(
    config: Config,
    db: Database,
    manager: PlaywrightManager,
    window: MainWindow,
    text: str,
    job_scheduler: Optional[Any] = None,
) -> None:
    logger.info("User prompt: %s", text[:80])

    session_id = await db.get_current_session_id()
    if not session_id:
        session = Session(title="새 대화", type="chat")
        await db.create_session(session)
        await db.set_current_session_id(session.id)
        session_id = session.id

    user_msg = Message(session_id=session_id, role="user", content=text)
    await db.save_message(user_msg)

    # 슬라이딩 윈도우: user 프롬프트 단위로 리밋, 사이의 모든 메시지 포함
    slide_window = config.get("agent", "sliding_window_size")
    history_rows = await db.get_messages(session_id, limit=999999)  # 일단 전체 로드
    # 방금 저장한 현재 user 메시지는 enriched_text 로 대체하므로 제외
    if history_rows and history_rows[-1].role == "user" and history_rows[-1].content == text:
        history_rows = history_rows[:-1]
    # user 메시지 기준으로 슬라이딩 (가장 최근 N개 user 블록 유지)
    user_indices = [i for i, r in enumerate(history_rows) if r.role == "user"]
    if len(user_indices) > slide_window:
        keep_from = user_indices[-slide_window]
        history_rows = history_rows[keep_from:]

    history_messages: list = []
    for row in history_rows:
        if row.role == "user":
            history_messages.append(HumanMessage(content=row.content))
        elif row.role == "assistant":
            history_messages.append(AIMessage(content=row.content))
        elif row.role == "tool":
            history_messages.append(ToolMessage(content=row.content, tool_call_id=""))
        elif row.role == "system":
            history_messages.append(SystemMessage(content=row.content))

    # LLMClient 로 httpx 세션 관리 (Python 3.14 GeneratorExit 방지)
    llm_client = None
    try:
        llm_client = LLMClient(config)
        graph = create_agent_graph(
            config=config,
            manager=manager,
            db=db,
            session_id=session_id,
            llm=llm_client.llm,
            max_context_messages=int(config.get("agent", "sliding_window_size")),
            max_tool_calls=int(config.get("agent", "max_tool_calls")),
            job_scheduler=job_scheduler,
        )

        # 브라우저 현재 상태 수집 (연결/탭/포커스) -> developer 메시지로 추가
        browser_ctx = await manager.get_browser_context()
        logger.info("Browser context:\n%s", browser_ctx)

        history_developer_msg = None
        if browser_ctx and browser_ctx != "[Browser: not connected]":
            # developer role 로 브라우저 컨텍스트 전달 (system 과 user 사이, 로그 제외)
            history_developer_msg = HumanMessage(
                content=f"[Browser Context]\n{browser_ctx}",
                additional_kwargs={"role": "developer"},
            )

        # developer 메시지가 있으면 history 와 user 사이에 넣고,
        # 없으면 history 만 사용
        if history_developer_msg:
            messages = [*history_messages, history_developer_msg, HumanMessage(content=text)]
        else:
            messages = [*history_messages, HumanMessage(content=text)]

        logger.info("User prompt: %s", text[:80])

        state = {
            "messages": messages,
            "session_id": session_id,
            "intervention_queue": window.intervention_queue,
            "url": "",
            "page_title": "",
            "accessibility_tree": "",
            "last_screenshot_path": "",
            "error_count": 0,
            "is_final": False,
            "tool_call_count": 0,
        }

        # 응답 시작 전에 skeleton 표시
        window.show_ai_thinking("🤖 응답 대기 중...")

        # 입력 메시지 개수를 미리 저장 (스트리밍 중 이전 응답을 걸러내기 위함)
        input_msg_count = len(state["messages"])

        try:
            # astream 사용 + 타임아웃 (메인 세션도 무한 대기 방지)
            # aclosing으로 break 시에도 generator 정리 보장
            final_state = None
            async with aclosing(
                _async_iter_timeout(
                    graph.astream(state, stream_mode="values"),
                    timeout=_DEFAULT_AGENT_TIMEOUT,
                    timeout_msg="Agent graph execution timed out",
                )
            ) as stream:
                async for event in stream:
                    final_state = event
                    # 실시간 AI content 표시 (툴 콜 없이 알려주는 텍스트)
                    try:
                        event_msgs = event.get("messages", [])
                        if event_msgs and len(event_msgs) > input_msg_count:
                            # 이번 입력 이후에 추가된 메시지만 고려
                            new_msgs = event_msgs[input_msg_count:]
                            for m in reversed(new_msgs):
                                if m.type != "ai":
                                    continue
                                content = str(m.content or "").strip()
                                if content:
                                    window._chat.update_ai_content(content)
                                break
                    except Exception as e:
                        logger.debug("Real-time content update failed: %s", e)
                    await asyncio.sleep(0)  # Qt 이벤트 루프에 명시적 양보
                    # stop 플래그 체크 (CancelledError 가 안 먹히는 엔진 대비)
                    if window.stop_requested:
                        logger.warning("Stop requested during agent execution, breaking loop")
                        break
            input_count = len(state["messages"])
            final_messages = final_state.get("messages", []) if final_state else []
        except asyncio.CancelledError:
            logger.warning("Agent execution cancelled (graph astream)")
            _finish_prompt_cancelled(window)
            return
        except asyncio.TimeoutError:
            logger.error("Agent graph execution timed out")
            _show_error_in_ui(window, "⏱️ 실행 시간 초과 (300초)", None)
            return
        except Exception as e:
            logger.error("Agent execution failed", exc_info=True)
            _show_error_in_ui(window, "⚠️ 에이전트 실행 중 오류", e)
            return

        # 사용자가 stop 요청한 경우 → 부분 결과 처리 안 함
        if window.stop_requested:
            _finish_prompt_cancelled(window)
            return

        # 새 메시지만 처리 (그래프가 생성한 메시지 = input 이후)
        try:
            new_messages = final_messages[input_count:]

            # 새 assistant 메시지 중 마지막 1개를 UI에 표시
            assistant_msgs = [m for m in new_messages if m.type == "ai"]
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

            # 모든 새 메시지 DB 저장 (단, developer role 메시지는 저장 안 함)
            for msg in new_messages:
                try:
                    role = "assistant" if msg.type == "ai" else msg.type
                    if role not in ("user", "assistant", "tool", "system"):
                        continue
                    # developer role 메시지는 DB 저장 안 함 (동적 컨텍스트, 로그 불필요)
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
        _show_error_in_ui(window, "⏱️ LLM 초기화 시간 초과", None)
    except Exception as e:
        logger.error(f"Agent initialization failed: {e}", exc_info=True)
        _show_error_in_ui(window, "⚠️ 에이전트 초기화 중 오류", e)
    finally:
        if llm_client is not None:
            await llm_client.close()


async def _handle_session_reset(db: Database, window: MainWindow) -> None:
    logger.info("Session reset requested")

    current_id = await db.get_current_session_id()
    if current_id:
        await db.archive_session(current_id)

    session = Session(title="새 대화", type="chat")
    await db.create_session(session)
    await db.set_current_session_id(session.id)

    window._chat.clear()
    logger.info(f"Session reset: new session {session.id}")


def main() -> None:
    config_path = parse_args()
    config = Config(config_path)
    config.load()

    setup_logging(config)

    logger.info("PSJBA starting...")

    # QApplication 은 QEventLoop 생성 전에 반드시 존재해야 함
    if not qasync.QApplication.instance():
        qasync.QApplication(sys.argv)
    # 툴팁 표시 지연을 0.2초로 단축 (지원하지 않는 Qt 버전에서는 무시)
    try:
        from PySide6.QtWidgets import QToolTip as QtToolTip
        QtToolTip.setShowDelay(200)
    except AttributeError:
        pass
    loop = qasync.QEventLoop()
    asyncio.set_event_loop(loop)

    # Python 3.14+ Windows ProactorEventLoop 호환:
    # ensure_future 로 만든 Task 가 async I/O _OverlappedFuture 충돌로
    # "Cannot enter into task while another task is being executed" RuntimeError 가
    # 발생할 수 있음 → 예외 핸들러에서 debug log 로 전환
    _original_handler = loop.get_exception_handler()

    def _safe_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        msg = context.get("message", "")
        exc = context.get("exception")
        if isinstance(exc, RuntimeError) and "Cannot enter into task" in str(exc):
            logger.debug("Ignored nested task wakeup collision: %s", exc)
            return
        # Critical error → log + user notification
        logger.critical("Unhandled asyncio exception: %s", msg)
        if exc:
            tb = "".join(traceback.format_tb(exc.__traceback__))
            logger.critical("%s: %s\n%s", type(exc).__name__, exc, tb)
        notify_user_critical(
            type(exc).__name__ if exc else "AsyncioError",
            f"{msg}" if not exc else f"{msg}\n\n{exc}",
        )
        if _original_handler:
            _original_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_safe_exception_handler)

    try:
        loop.run_until_complete(run(config))
    except RuntimeError as e:
        if "Event loop stopped before Future completed" in str(e):
            logger.info("Application window closed")
        else:
            raise
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except asyncio.CancelledError:
        pass

    logger.info("PSJBA exited.")
    os._exit(0)


if __name__ == "__main__":
    main()
