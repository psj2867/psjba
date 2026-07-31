"""CronScheduler — aiocron 기반 크론 스케줄러 관리."""

import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional

import aiocron

from src.database import Database
from src.models.schemas import CronJob

logger = logging.getLogger("psjba.cron.scheduler")


class CronScheduler:
    """aiocron crontab 등록/해제/목록 관리.

    시작 시 DB에서 enabled 크론을 읽어 aiocron에 등록하고,
    런타임에 동적으로 추가/제거/수정한다.
    """

    def __init__(self) -> None:
        self._tabs: dict[str, Any] = {}
        self._db: Optional[Database] = None
        self._executor: Any = None

    def set_dependencies(self, db: Database, executor: Any) -> None:
        self._db = db
        self._executor = executor

    async def start(self) -> None:
        if self._db is None:
            return

        jobs = await self._db.get_enabled_cron_jobs()
        for job in jobs:
            self._register(job)

        logger.info(f"CronScheduler started: {len(jobs)} jobs registered")

    def _register(self, job: CronJob) -> None:
        if job.id in self._tabs:
            self._unregister(job.id)

        if job.status != "enable":
            return

        async def _run() -> None:
            if self._executor is None:
                return
            try:
                await self._executor.execute(job)
            except Exception as e:
                logger.error(f"Cron job '{job.name}' ({job.id}) failed: {e}")

        try:
            tab = aiocron.crontab(job.schedule, func=_run)
            tab.start()
            self._tabs[job.id] = tab
            logger.debug(f"Cron registered: {job.name} ({job.schedule})")
        except Exception as e:
            logger.error(f"Failed to register cron '{job.name}': {e}")

    def _unregister(self, job_id: str) -> None:
        tab = self._tabs.pop(job_id, None)
        if tab is not None:
            try:
                tab.stop()
            except Exception:
                pass
            logger.debug(f"Cron unregistered: {job_id}")

    async def add_job(self, job: CronJob) -> None:
        self._register(job)
        logger.info(f"Cron added: {job.name} ({job.id})")

    async def remove_job(self, job_id: str) -> None:
        self._unregister(job_id)
        logger.info(f"Cron removed: {job_id}")

    async def update_job(self, job: CronJob) -> None:
        self._unregister(job.id)
        if job.status == "enable":
            self._register(job)
        logger.info(f"Cron updated: {job.name} ({job.id})")

    async def toggle_job(self, job_id: str, enabled: bool) -> None:
        if self._db is None:
            return

        job = await self._db.get_cron_job(job_id)
        if not job:
            return

        if enabled:
            self._register(job)
        else:
            self._unregister(job_id)

        logger.info(f"Cron toggled: {job_id} -> {'enabled' if enabled else 'disabled'}")

    def stop_all(self) -> None:
        for job_id in list(self._tabs.keys()):
            self._unregister(job_id)
        logger.info("CronScheduler stopped: all jobs unregistered")

    @property
    def registered_count(self) -> int:
        return len(self._tabs)

    def is_registered(self, job_id: str) -> bool:
        return job_id in self._tabs
