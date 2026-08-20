"""PSJBA — Browser Agent 진입점.

역할: 전역 설정 로드, 초기화, 실행, 종료.
UI/프롬프트 실행 로직은 각각 ui/helpers.py, agent/runner.py 에 위임.
"""

import asyncio
import logging
import os
import signal
import sys
import traceback
from pathlib import Path

import qasync

from src.agent.runner import handle_prompt
from src.config import Config
from src.database import Database
from src.browser.manager import PlaywrightManager
from src.cron.scheduler import CronScheduler
from src.cron.executor import CronExecutor
from src.cron.job_scheduler import JobScheduler
from src.models.schemas import Session
from src.ui.helpers import (
    finish_prompt_cancelled,
    handle_session_reset,
    notify_user_critical,
    set_main_window_ref,
    setup_crash_hooks,
)
from src.ui.main_window import MainWindow

logger = logging.getLogger("psjba")

CONFIG_PATH = "config.ini"


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
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(logging.NOTSET)
        fh.setFormatter(fmt)
        root_logger.addHandler(fh)
        logger.info("Logging to file: level=%s path=%s", level_str, log_path)
    else:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(logging.NOTSET)
        ch.setFormatter(fmt)
        root_logger.addHandler(ch)


def setup_signal_handlers(shutdown_event: asyncio.Event) -> None:
    def shutdown(signum, frame):
        logger.info(f"Signal {signum} received, shutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)


async def run(config: Config) -> None:
    shutdown_event = asyncio.Event()
    setup_crash_hooks()

    logger.info("Step 1/10: Config loaded (path=%s)", CONFIG_PATH)

    db_path = config.get("sqlite", "path")
    db = Database(db_path)
    await db.connect()
    await db.initialize()
    logger.info("Step 2/10: Database ready (path=%s)", db_path)

    workspace_dir = config.get("workspace", "workspace_dir")
    Path(workspace_dir).mkdir(parents=True, exist_ok=True)
    Path(workspace_dir, "screenshots").mkdir(parents=True, exist_ok=True)
    Path(workspace_dir, "downloads").mkdir(parents=True, exist_ok=True)
    Path(workspace_dir, "exports").mkdir(parents=True, exist_ok=True)
    logger.info("Step 3/10: Workspace ready (dir=%s)", workspace_dir)

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

    cron_executor = CronExecutor(config=config, db=db, manager=manager)
    cron_scheduler = CronScheduler()
    cron_scheduler.set_dependencies(db=db, executor=cron_executor)
    await cron_scheduler.start()
    logger.info("Step 5/10: CronScheduler started (%d jobs)",
                cron_scheduler.registered_count)

    job_scheduler = JobScheduler(config=config, db=db, manager=manager)
    await job_scheduler.start()
    logger.info("Step 5a/10: JobScheduler started")

    await db.disable_all_cron_jobs()
    await db.cancel_all_scheduled_jobs()
    logger.info("Step 5c/10: All cron/scheduled jobs disabled at startup")

    manager.set_cron_dependencies(db=db, cron_scheduler=cron_scheduler)
    logger.info("Step 5b/10: Cron dependencies injected into manager")

    window = MainWindow(config=config, db=db, manager=manager)
    set_main_window_ref(window)
    manager.set_main_window(window)
    logger.info("Step 6/10: MainWindow created")

    # 프롬프트 제출 → 큐 소비 루프
    def _on_prompt(text: str) -> None:
        async def _run_loop() -> None:
            try:
                while window.intervention_queue:
                    current_text = window.intervention_queue.pop(0)
                    window._prompt.set_running(True)
                    window.stop_requested = False
                    await handle_prompt(
                        config, db, manager, window, current_text,
                        job_scheduler, agent=agent,
                    )
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
        lambda: asyncio.ensure_future(handle_session_reset(db, window))
    )
    setup_signal_handlers(shutdown_event=shutdown_event)
    logger.info("Step 7/10: Signals connected")

    window.closed.connect(shutdown_event.set)
    logger.info("Step 7b/10: Window closed signal connected")

    app = qasync.QApplication.instance()
    if app:
        app.aboutToQuit.connect(lambda: logger.info("QApplication aboutToQuit"))
    logger.info("Step 7c/10: aboutToQuit logger connected")

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

    logger.info("Step 9/10: Showing window")
    window.show()

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

    # AgentGraph 생성 (LLM 주입 + 캐싱)
    from src.agent.graph import AgentGraph
    from src.agent.llm_client import LLMClient
    llm_client = LLMClient(config)
    agent = AgentGraph(
        config=config, manager=manager, db=db,
        llm=llm_client.llm, job_scheduler=job_scheduler,
        max_context_messages=int(config.get("agent", "sliding_window_size")),
        max_tool_calls=int(config.get("agent", "max_tool_calls")),
    )

    logger.info("Application started successfully")

    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        logger.info("Application cancelled")
    finally:
        cron_scheduler.stop_all()
        cron_executor.cancel_all()
        await job_scheduler.stop()
        if manager.is_connected:
            await manager.disconnect()
        await db.close()
        app = qasync.QApplication.instance()
        if app:
            app.quit()
        logger.info("Application stopped")


def main() -> None:
    config_path = parse_args()
    config = Config(config_path)
    config.load()

    setup_logging(config)

    logger.info("PSJBA starting...")

    if not qasync.QApplication.instance():
        qasync.QApplication(sys.argv)
    try:
        from PySide6.QtWidgets import QToolTip as QtToolTip
        QtToolTip.setShowDelay(200)
    except AttributeError:
        pass
    loop = qasync.QEventLoop()
    asyncio.set_event_loop(loop)

    _original_handler = loop.get_exception_handler()

    def _safe_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        msg = context.get("message", "")
        exc = context.get("exception")
        if isinstance(exc, RuntimeError) and "Cannot enter into task" in str(exc):
            logger.debug("Ignored nested task wakeup collision: %s", exc)
            return
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
