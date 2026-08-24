"""CronExecutor — 크론 작업 실행기 (세션 생성 + LangGraph 실행)."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from src.config import Config
from src.database import Database
from src.models.schemas import CronJob, Message, Session
from src.browser.manager import set_a11y_session_id as _set_session_context

logger = logging.getLogger("psjba.cron.executor")

DEFAULT_CRON_TIMEOUT = 300


class CronExecutor:
    """크론 실행기 — 새 세션 생성 후 LangGraph 에이전트 실행.

    - 실행 전 active 상태의 이전 크론 세션은 archive 처리
    - 동일 cron_job_id의 실행 중인 태스크가 있으면 스킵
    - cron_timeout 적용 (config의 agent.cron_timeout, 기본 300초)
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
        self._running: dict[str, asyncio.Task] = {}
        # AgentGraph는 크론에서 llm 없이 생성 → _run에서 LLMClient로 llm 생성 후 주입
        self._agent: Any = None

    @property
    def running_jobs(self) -> list[str]:
        return [jid for jid, t in self._running.items() if not t.done()]

    async def execute(self, job: CronJob) -> None:
        if job.id in self._running and not self._running[job.id].done():
            logger.warning(
                f"Cron '{job.name}' ({job.id}) already running, skipping"
            )
            return

        task = asyncio.ensure_future(self._run(job))
        self._running[job.id] = task

    async def _run(self, job: CronJob) -> None:
        cron_timeout = self._config.get("agent", "cron_timeout")
        session: Optional[Session] = None

        try:
            await self._db.archive_cron_sessions(job.id)

            session = Session(
                title=f"[크론] {job.name}",
                type="cron",
                cron_job_id=job.id,
            )
            await self._db.create_session(session)
            logger.info(
                f"Cron session created: {session.id} for job '{job.name}'"
            )

            if not self._manager.is_connected:
                result = await self._manager.connect(launch_if_needed=True)
                if "Chrome connected" not in result:
                    logger.error(f"Cron '{job.name}': Chrome not connected")
                    return

            await self._save_message(
                session.id, "user", job.prompt
            )

            # 크론 notes → system 메시지로 추가
            notes = job.notes.strip()
            if notes:
                await self._save_message(
                    session.id, "system",
                    f"[크론 메모 - 이전 실행에서 저장한 내용]\n{notes}",
                )
                # notes 소비 (빈 문자열로 초기화)
                job.notes = ""
                await self._db.update_cron_job(job)

            _set_session_context(session.id)

            # AgentGraph lazy init (첫 크론 실행 시)
            if self._agent is None:
                from src.agent.graph import AgentGraph
                self._agent = AgentGraph(
                    manager=self._manager, db=self._db, config=self._config,
                )

            from src.agent.llm_client import LLMClient
            llm_client = LLMClient(self._config)
            graph = self._agent.compiled(
                session_id=session.id, llm=llm_client.llm, cron_job_id=job.id,
            )
            from langchain_core.messages import HumanMessage, SystemMessage

            # manager → browser_context + current_date
            browser_context = await self._manager.get_browser_context()
            from datetime import datetime
            current_date = datetime.now().strftime("%Y-%m-%d %H:%M")
            info_parts = [f"[Current Date]\n{current_date}"]
            if browser_context and browser_context != "[Browser: not connected]":
                info_parts.append(f"[Browser Context]\n{browser_context}")

            state_messages: list = [HumanMessage(content=job.prompt)]
            # info (current_date + browser_context) → developer 메시지
            state_messages.insert(0, HumanMessage(
                content="\n\n".join(info_parts),
                additional_kwargs={"role": "developer"},
            ))
            if notes:
                state_messages.append(
                    SystemMessage(
                        content=f"[크론 메모 - 이전 실행에서 저장한 내용]\n{notes}"
                    )
                )

            state: dict[str, Any] = {
                "messages": state_messages,
                "session_id": session.id,
                "url": "",
                "page_title": "",
                "accessibility_tree": "",
                "last_screenshot_path": "",
                "error_count": 0,
                "is_final": False,
                "intervention_queue": [],
            }

            try:
                result = await asyncio.wait_for(
                    graph.ainvoke(state),
                    timeout=cron_timeout,
                )
            except asyncio.TimeoutError:
                logger.error(
                    f"Cron '{job.name}' timed out after {cron_timeout}s"
                )
                await self._save_message(
                    session.id,
                    "system",
                    f"[크론] 실행 시간 초과 ({cron_timeout}초)",
                )
                return

            messages = result.get("messages", [])
            for msg in messages:
                has_content = bool(getattr(msg, "content", None))
                has_tool_calls = bool(getattr(msg, "tool_calls", None))
                if not has_content and not has_tool_calls:
                    continue
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
                    tool_call_id = getattr(msg, "tool_call_id", "") if role == "tool" else ""
                    await self._save_message(
                        session.id,
                        role,
                        msg.content if isinstance(msg.content, str) else str(msg.content or ""),
                        tool_calls=tool_calls,
                        tool_call_id=tool_call_id,
                    )

            now = datetime.now()
            job.last_run_at = now
            await self._db.update_cron_job(job)

            logger.info(f"Cron '{job.name}' completed: session {session.id}")

        except asyncio.CancelledError:
            logger.info(f"Cron '{job.name}' cancelled")
        except Exception as e:
            logger.error(f"Cron '{job.name}' failed: {e}", exc_info=True)
            if session:
                await self._save_message(
                    session.id,
                    "system",
                    f"[크론] 실행 오류: {e}",
                )
        finally:
            self._running.pop(job.id, None)

    async def _save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: Optional[list[dict[str, Any]]] = None,
        tool_call_id: str = "",
    ) -> Message:
        tool_calls_str = None
        if tool_calls:
            tool_calls_str = json.dumps(tool_calls, ensure_ascii=False)

        msg = Message(
            session_id=session_id,
            role=role,
            content=content,
            tool_calls=tool_calls_str,
            tool_call_id=tool_call_id or None,
        )
        return await self._db.save_message(msg)

    async def cancel(self, job_id: str) -> None:
        task = self._running.get(job_id)
        if task and not task.done():
            task.cancel()
            logger.info(f"Cron task cancelled: {job_id}")
        self._running.pop(job_id, None)

    def cancel_all(self) -> None:
        for job_id in list(self._running.keys()):
            task = self._running.get(job_id)
            if task and not task.done():
                task.cancel()
        self._running.clear()
