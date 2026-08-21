"""JobScheduler — AsyncIOScheduler + DateTrigger 기반 1회성 job."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from src.config import Config
from src.database import Database
from src.models.schemas import Message, ScheduledJob, Session

logger = logging.getLogger("psjba.cron.job_scheduler")


class JobScheduler:
    """AsyncIOScheduler 로 1회성 예약 job을 관리한다.

    - start() 시 DB에서 pending job을 읽어 APScheduler 에 등록
    - job_add() 툴로 새 job 추가 시 즉시 APScheduler 등록
    - job 실행 시 새 세션 생성 → LangGraph 실행 후 done 처리
    """

    def __init__(
        self,
        config: Config,
        db: Database,
        manager: Any,
    ) -> None:
        self._config = config
        self._db = db
        self._manager = manager
        self._scheduler = AsyncIOScheduler()
        self._running: dict[str, asyncio.Task] = {}
        self._agent: Any = None

    @property
    def running_jobs(self) -> list[str]:
        return [jid for jid, t in self._running.items() if not t.done()]

    async def start(self) -> None:
        # DB에서 pending job 로드 안 함 — 모두 비활성화 상태로 관리
        self._scheduler.start()
        logger.info("JobScheduler started (no pending jobs loaded, all disabled)")

    async def stop(self) -> None:
        self._scheduler.shutdown(wait=False)
        for jid in list(self._running.keys()):
            await self._cancel_job(jid)
        logger.info("JobScheduler stopped")

    def _schedule_one(self, job: ScheduledJob) -> None:
        """APScheduler 에 job 등록 (DateTrigger)."""
        if job.scheduled_at <= datetime.now():
            logger.warning(
                "ScheduledJob '%s' (%s) already past due, skipping",
                job.name, job.id,
            )
            return

        self._scheduler.add_job(
            self._execute_wrapper(job),
            trigger=DateTrigger(run_date=job.scheduled_at),
            id=job.id,
            name=job.name,
            replace_existing=True,
        )
        logger.debug(
            "ScheduledJob '%s' registered: %s", job.name, job.scheduled_at
        )

    def _execute_wrapper(self, job: ScheduledJob) -> Any:
        """APScheduler 용 래퍼 — 내부에서 async task 생성."""

        async def _run() -> None:
            if job.id in self._running and not self._running[job.id].done():
                logger.warning(
                    "ScheduledJob '%s' already running, skipping", job.name
                )
                return
            task = asyncio.ensure_future(self._execute(job))
            self._running[job.id] = task
            try:
                await task
            finally:
                self._running.pop(job.id, None)

        return _run

    async def add_job(self, job: ScheduledJob) -> None:
        """새 job 추가 — DB 저장 후 APScheduler 등록."""
        await self._db.create_scheduled_job(job)
        self._schedule_one(job)
        logger.info("ScheduledJob added: %s (%s)", job.name, job.id)

    async def remove_job(self, job_id: str) -> None:
        """job 제거 — APScheduler 취소 + DB 상태 변경."""
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
        await self._db.delete_scheduled_job(job_id)
        await self._cancel_job(job_id)
        logger.info("ScheduledJob removed: %s", job_id)

    async def _execute(self, job: ScheduledJob) -> None:
        session: Optional[Session] = None
        try:
            job.status = "running"
            await self._db.update_scheduled_job(job)

            session = Session(
                title=f"[예약] {job.name}", type="chat"
            )
            await self._db.create_session(session)
            job.session_id = session.id
            await self._db.update_scheduled_job(job)

            logger.info(
                "ScheduledJob '%s' (%s) executing, session=%s",
                job.name, job.id, session.id,
            )

            if not self._manager.is_connected:
                result = await self._manager.connect(launch_if_needed=True)
                if "Chrome connected" not in result:
                    raise RuntimeError("Chrome not connected")

            await self._db.save_message(
                Message(
                    session_id=session.id, role="user", content=job.prompt
                )
            )

            # AgentGraph lazy init
            if self._agent is None:
                from src.agent.graph import AgentGraph
                self._agent = AgentGraph(
                    manager=self._manager, db=self._db, job_scheduler=self,
                    config=self._config,
                )

            from src.agent.llm_client import LLMClient
            llm_client = LLMClient(self._config)
            graph = self._agent.compiled(session_id=session.id, llm=llm_client.llm)
            from langchain_core.messages import HumanMessage

            state: dict[str, Any] = {
                "messages": [HumanMessage(content=job.prompt)],
                "session_id": session.id,
                "url": "",
                "page_title": "",
                "accessibility_tree": "",
                "last_screenshot_path": "",
                "error_count": 0,
                "is_final": False,
                "intervention_queue": [],
            }

            timeout = self._config.get("agent", "cron_timeout")
            result = await asyncio.wait_for(
                graph.ainvoke(state), timeout=timeout
            )

            for msg in result.get("messages", []):
                if hasattr(msg, "content") and msg.content:
                    role = "assistant" if msg.type == "ai" else msg.type
                    if role in ("user", "assistant", "tool", "system"):
                        await self._db.save_message(
                            Message(
                                session_id=session.id,
                                role=role,
                                content=(
                                    msg.content
                                    if isinstance(msg.content, str)
                                    else str(msg.content)
                                ),
                            )
                        )

            job.status = "done"
            await self._db.update_scheduled_job(job)
            logger.info("ScheduledJob '%s' completed", job.name)

        except asyncio.TimeoutError:
            logger.error("ScheduledJob '%s' timed out", job.name)
            job.status = "error"
            job.error_message = f"Timeout ({timeout}s)"
            await self._db.update_scheduled_job(job)
            if session:
                await self._db.save_message(
                    Message(
                        session_id=session.id,
                        role="system",
                        content=f"[예약 job] 실행 시간 초과 ({timeout}초)",
                    )
                )
        except asyncio.CancelledError:
            logger.info("ScheduledJob '%s' cancelled", job.name)
            job.status = "cancelled"
            await self._db.update_scheduled_job(job)
        except Exception as e:
            logger.error(
                "ScheduledJob '%s' failed: %s", job.name, e, exc_info=True
            )
            job.status = "error"
            job.error_message = str(e)
            await self._db.update_scheduled_job(job)
            if session:
                await self._db.save_message(
                    Message(
                        session_id=session.id,
                        role="system",
                        content=f"[예약 job] 실행 오류: {e}",
                    )
                )
        finally:
            self._running.pop(job.id, None)

    async def _cancel_job(self, job_id: str) -> None:
        task = self._running.get(job_id)
        if task and not task.done():
            task.cancel()
        self._running.pop(job_id, None)