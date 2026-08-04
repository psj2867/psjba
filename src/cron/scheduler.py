"""CronScheduler — AsyncIOScheduler + CronTrigger 기반 크론 관리."""

import logging
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src.database import Database
from src.models.schemas import CronJob

logger = logging.getLogger("psjba.cron.scheduler")


class CronScheduler:
    """AsyncIOScheduler + CronTrigger 로 크론 job을 관리한다.

    시작 시 DB에서 enabled 크론을 읽어 APScheduler 에 등록하고,
    런타임에 동적으로 추가/제거/수정한다.
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
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

        self._scheduler.start()
        logger.info(f"CronScheduler started: {len(jobs)} jobs registered")

    def _register(self, job: CronJob) -> None:
        if self._scheduler.get_job(job.id):
            self._scheduler.remove_job(job.id)

        if job.status != "enable":
            return

        try:
            trigger = CronTrigger.from_crontab(job.schedule)
            self._scheduler.add_job(
                self._execute_wrapper(job),
                trigger=trigger,
                id=job.id,
                name=job.name,
                replace_existing=True,
            )
            logger.debug(
                "Cron registered: %s (%s)", job.name, job.schedule
            )
        except Exception as e:
            logger.error(
                "Failed to register cron '%s': %s", job.name, e
            )

    def _execute_wrapper(self, job: CronJob) -> Any:
        async def _run() -> None:
            if self._executor is None:
                return
            try:
                await self._executor.execute(job)
            except Exception as e:
                logger.error(
                    "Cron job '%s' (%s) failed: %s",
                    job.name, job.id, e,
                )
        return _run

    async def add_job(self, job: CronJob) -> None:
        self._register(job)
        logger.info("Cron added: %s (%s)", job.name, job.id)

    async def remove_job(self, job_id: str) -> None:
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
        logger.info("Cron removed: %s", job_id)

    async def update_job(self, job: CronJob) -> None:
        if self._scheduler.get_job(job.id):
            self._scheduler.remove_job(job.id)
        if job.status == "enable":
            self._register(job)
        logger.info("Cron updated: %s (%s)", job.name, job.id)

    async def toggle_job(self, job_id: str, enabled: bool) -> None:
        if self._db is None:
            return
        job = await self._db.get_cron_job(job_id)
        if not job:
            return
        if enabled:
            self._register(job)
        else:
            if self._scheduler.get_job(job_id):
                self._scheduler.remove_job(job_id)
        logger.info(
            "Cron toggled: %s -> %s", job_id,
            "enabled" if enabled else "disabled",
        )

    def stop_all(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("CronScheduler stopped: all jobs unregistered")

    @property
    def registered_count(self) -> int:
        return len(self._scheduler.get_jobs())

    def is_registered(self, job_id: str) -> bool:
        return self._scheduler.get_job(job_id) is not None
