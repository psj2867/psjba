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
from pathlib import Path

import qasync

from src.config import Config
from src.database import Database
from src.browser.manager import PlaywrightManager
from src.cron.scheduler import CronScheduler
from src.cron.executor import CronExecutor
from src.ui.main_window import MainWindow

logger = logging.getLogger("psjba")

CONFIG_PATH = "config.ini"


def setup_logging(config: Config) -> None:
    level_str = config.get("logging", "level", "disabled")
    log_path = config.get("logging", "path", "./psjba.log")

    root_logger = logging.getLogger("psjba")
    root_logger.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root_logger.addHandler(console)

    if level_str and level_str.lower() != "disabled":
        level = getattr(logging, level_str.upper(), None)
        if level is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
                )
            )
            root_logger.addHandler(file_handler)
            root_logger.setLevel(level)
            logger.info("Logging enabled: level=%s path=%s", level_str, log_path)
    else:
        logger.info("Logging disabled (console only)")


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

    logger.info("Step 1/10: Config loaded (path=%s)", CONFIG_PATH)

    # DB
    db_path = config.get("sqlite", "path", "./browser_agent.data")
    db = Database(db_path)
    await db.connect()
    await db.initialize()
    logger.info("Step 2/10: Database ready (path=%s)", db_path)

    # 워크스페이스
    workspace_dir = config.get("workspace", "workspace_dir", "./psjba_temp")
    Path(workspace_dir).mkdir(parents=True, exist_ok=True)
    Path(workspace_dir, "screenshots").mkdir(parents=True, exist_ok=True)
    Path(workspace_dir, "downloads").mkdir(parents=True, exist_ok=True)
    Path(workspace_dir, "exports").mkdir(parents=True, exist_ok=True)
    logger.info("Step 3/10: Workspace ready (dir=%s)", workspace_dir)

    # PlaywrightManager
    manager = PlaywrightManager(
        cdp_host=config.get("cdp", "host", "127.0.0.1"),
        cdp_port=config.get("cdp", "port", 9222),
        connection_timeout=config.get("cdp", "connection_timeout", 1.0),
        chrome_path=config.get("chrome", "path", ""),
        user_data_dir=config.get("chrome", "user_data_dir", ""),
        headless=config.get("chrome", "headless", False),
        workspace_dir=workspace_dir,
    )
    logger.info("Step 4/10: PlaywrightManager created (cdp=%s:%s, headless=%s)",
                config.get("cdp", "host", "127.0.0.1"),
                config.get("cdp", "port", 9222),
                config.get("chrome", "headless", False))

    # Cron
    cron_executor = CronExecutor(config=config, db=db, manager=manager)
    cron_scheduler = CronScheduler()
    cron_scheduler.set_dependencies(db=db, executor=cron_executor)
    await cron_scheduler.start()
    logger.info("Step 5/10: CronScheduler started (%d jobs)",
                cron_scheduler.registered_count)

    # MainWindow
    window = MainWindow(config=config, db=db, manager=manager)
    logger.info("Step 6/10: MainWindow created")

    # 시그널
    window.prompt_submitted.connect(
        lambda text: asyncio.ensure_future(
            _handle_prompt(config, db, manager, window, text)
        )
    )
    window.session_reset_requested.connect(
        lambda: asyncio.ensure_future(_handle_session_reset(db, window))
    )
    setup_signal_handlers(shutdown_event=shutdown_event)
    logger.info("Step 7/10: Signals connected")

    # 창 닫힘 → shutdown trigger
    window.closed.connect(shutdown_event.set)
    logger.info("Step 7b/10: Window closed signal connected")

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
        from src.models.schemas import Session
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
    model = config.get("llm", "model", "")
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
        if manager.is_connected:
            await manager.disconnect()
        await db.close()
        # QApplication 정리 — Qt 이벤트 루프가 아직 활성 상태이므로 quit()이 유효함
        app = qasync.QApplication.instance()
        if app:
            app.quit()
        logger.info("Application stopped")


async def _handle_prompt(
    config: Config,
    db: Database,
    manager: PlaywrightManager,
    window: MainWindow,
    text: str,
) -> None:
    logger.info("User prompt: %s", text[:80])
    from langchain_core.messages import HumanMessage, AIMessage
    from src.agent.graph import create_agent_graph
    from src.models.schemas import Message

    session_id = await db.get_current_session_id()
    if not session_id:
        from src.models.schemas import Session
        session = Session(title="새 대화", type="chat")
        await db.create_session(session)
        await db.set_current_session_id(session.id)
        session_id = session.id

    user_msg = Message(session_id=session_id, role="user", content=text)
    await db.save_message(user_msg)

    if not manager.is_connected:
        result = await manager.connect(launch_if_needed=True)
        if "Chrome connected" not in result:
            logger.error("Chrome not connected, cannot run agent")
            window._prompt.set_running(False)
            return

    graph = create_agent_graph(
        config=config,
        manager=manager,
        db=db,
        session_id=session_id,
        max_context_messages=config.get("agent", "sliding_window_size", 30),
    )

    history = await db.get_messages(session_id, limit=60)
    messages = [HumanMessage(content=text)]

    state = {
        "messages": messages,
        "session_id": session_id,
        "url": "",
        "page_title": "",
        "accessibility_tree": "",
        "last_screenshot_path": "",
        "error_count": 0,
        "is_final": False,
    }

    try:
        result = await graph.ainvoke(state)
        final_messages = result.get("messages", [])
        for msg in final_messages:
            if hasattr(msg, "content") and msg.content:
                role = "assistant" if msg.type == "ai" else msg.type
                if role in ("user", "assistant", "tool", "system"):
                    tool_calls = None
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        tool_calls = [
                            {
                                "name": tc.get("name", ""),
                                "args": tc.get("args", {}),
                                "id": tc.get("id", ""),
                            }
                            for tc in msg.tool_calls
                        ]
                    db_msg = Message(
                        session_id=session_id,
                        role=role,
                        content=msg.content if isinstance(msg.content, str) else str(msg.content),
                    )
                    await db.save_message(db_msg)

                    if role == "assistant":
                        window.add_ai_response(msg.content, tool_calls)

    except Exception as e:
        logger.error(f"Agent execution failed: {e}", exc_info=True)
        window.add_ai_response(f"오류: {e}")
    finally:
        window._prompt.set_running(False)


async def _handle_session_reset(db: Database, window: MainWindow) -> None:
    logger.info("Session reset requested")
    from src.models.schemas import Session

    current_id = await db.get_current_session_id()
    if current_id:
        await db.archive_session(current_id)

    session = Session(title="새 대화", type="chat")
    await db.create_session(session)
    await db.set_current_session_id(session.id)

    window._chat.clear()
    logger.info(f"Session reset: new session {session.id}")


def main() -> None:
    config = Config(CONFIG_PATH)
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

    try:
        with loop:
            try:
                loop.run_until_complete(run(config))
            except RuntimeError as e:
                # 창을 닫아 QApplication.exec() 가 종료된 경우
                if "Event loop stopped before Future completed" in str(e):
                    logger.info("Application window closed")
                else:
                    raise
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except asyncio.CancelledError:
        pass

    logger.info("PSJBA exited.")

    # run() finally 에서 모든 자원 정리 + QApplication.quit() 완료
    # 그래도 QApplication 싱글톤이 프로세스를 잡고 있으면 os._exit(0) 로 강제 종료
    os._exit(0)


if __name__ == "__main__":
    main()
