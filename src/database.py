import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import aiosqlite

from src.models.schemas import CronJob, Message, Meta, ScheduledJob, Session

logger = logging.getLogger("psjba.database")

SCHEMA_VERSION = "2"
APP_VERSION = "0.1.0"

CREATE_TABLE_META = """
CREATE TABLE IF NOT EXISTS psjba_meta (
    key   TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL
);
"""

CREATE_TABLE_SESSION = """
CREATE TABLE IF NOT EXISTS psjba_session (
    id          TEXT PRIMARY KEY NOT NULL,
    title       TEXT NOT NULL DEFAULT '새 대화',
    type        TEXT NOT NULL DEFAULT 'chat' CHECK(type IN ('chat', 'cron')),
    cron_job_id TEXT REFERENCES psjba_cron_job(id),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived', 'deleted'))
);
"""

CREATE_TABLE_MESSAGE = """
CREATE TABLE IF NOT EXISTS psjba_message (
    id          TEXT PRIMARY KEY NOT NULL,
    session_id  TEXT NOT NULL REFERENCES psjba_session(id),
    role        TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'tool', 'system')),
    content     TEXT NOT NULL,
    token_usage TEXT,
    model       TEXT,
    created_at  TEXT NOT NULL
);
"""

CREATE_TABLE_CRON_JOB = """
CREATE TABLE IF NOT EXISTS psjba_cron_job (
    id          TEXT PRIMARY KEY NOT NULL,
    name        TEXT NOT NULL,
    schedule    TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'enable' CHECK(status IN ('enable', 'disable', 'deleted')),
    notes       TEXT NOT NULL DEFAULT '',
    last_run_at TEXT,
    next_run_at TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""

CREATE_TABLE_SCHEDULED_JOB = """
CREATE TABLE IF NOT EXISTS psjba_scheduled_job (
    id            TEXT PRIMARY KEY NOT NULL,
    name          TEXT NOT NULL,
    scheduled_at  TEXT NOT NULL,
    prompt        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'running', 'done', 'cancelled', 'error')),
    session_id    TEXT,
    error_message TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

CREATE_MESSAGE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_message_session ON psjba_message(session_id, created_at);
"""

CREATE_FTS5_TABLE = """
CREATE VIRTUAL TABLE IF NOT EXISTS psjba_message_fts USING fts5(
    content,
    session_id UNINDEXED,
    content='psjba_message',
    content_rowid='rowid'
);
"""

FTS5_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS psjba_message_ai AFTER INSERT ON psjba_message BEGIN
        INSERT INTO psjba_message_fts(rowid, content, session_id)
        VALUES (new.rowid, new.content, new.session_id);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS psjba_message_ad AFTER DELETE ON psjba_message BEGIN
        INSERT INTO psjba_message_fts(psjba_message_fts, rowid, content, session_id)
        VALUES ('delete', old.rowid, old.content, old.session_id);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS psjba_message_au AFTER UPDATE ON psjba_message BEGIN
        INSERT INTO psjba_message_fts(psjba_message_fts, rowid, content, session_id)
        VALUES ('delete', old.rowid, old.content, old.session_id);
        INSERT INTO psjba_message_fts(rowid, content, session_id)
        VALUES (new.rowid, new.content, new.session_id);
    END;
    """,
]


class Database:
    def __init__(self, db_path: str = "./browser_agent.db"):
        self._db_path = str(Path(db_path).resolve())
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA foreign_keys=ON;")

    async def close(self) -> None:
        if self._conn:
            try:
                await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                await self._conn.commit()
            except Exception:
                pass
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    async def initialize(self) -> None:
        await self.conn.execute(CREATE_TABLE_META)
        await self.conn.execute(CREATE_TABLE_CRON_JOB)
        await self.conn.execute(CREATE_TABLE_SCHEDULED_JOB)
        await self.conn.execute(CREATE_TABLE_SESSION)
        await self.conn.execute(CREATE_TABLE_MESSAGE)
        await self.conn.execute(CREATE_MESSAGE_INDEX)
        await self.conn.execute(CREATE_FTS5_TABLE)
        for trigger in FTS5_TRIGGERS:
            await self.conn.execute(trigger)
        await self.conn.commit()

        stored_version = await self._get_meta("schema_version")
        if stored_version is None:
            await self._set_meta("schema_version", SCHEMA_VERSION)
            await self._set_meta("app_version", APP_VERSION)
        elif stored_version != SCHEMA_VERSION:
            if stored_version == "1" and SCHEMA_VERSION == "2":
                # v1 → v2: psjba_cron_job 에 notes 컬럼 추가
                await self._migrate_v1_to_v2()
                await self._set_meta("schema_version", SCHEMA_VERSION)
            else:
                raise RuntimeError(
                    f"Schema version mismatch: DB={stored_version}, code={SCHEMA_VERSION}"
                )

    async def _migrate_v1_to_v2(self) -> None:
        """v1 → v2: psjba_cron_job.notes 컬럼 추가 (기존 row는 빈 문자열)."""
        columns = await self.conn.execute_fetchall("PRAGMA table_info(psjba_cron_job)")
        col_names = {c["name"] for c in columns}
        if "notes" not in col_names:
            await self.conn.execute(
                "ALTER TABLE psjba_cron_job ADD COLUMN notes TEXT NOT NULL DEFAULT ''"
            )
        await self.conn.commit()
        logger.info("DB migrated v1 → v2 (cron_job.notes added)")

    async def _get_meta(self, key: str) -> Optional[str]:
        cursor = await self.conn.execute(
            "SELECT value FROM psjba_meta WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return row["value"] if row else None

    async def _set_meta(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT OR REPLACE INTO psjba_meta(key, value) VALUES (?, ?)",
            (key, value),
        )

    async def get_meta(self, key: str) -> Optional[str]:
        return await self._get_meta(key)

    async def set_meta(self, key: str, value: str) -> None:
        await self._set_meta(key, value)
        await self.conn.commit()

    async def get_current_session_id(self) -> Optional[str]:
        val = await self._get_meta("current_session_id")
        if val:
            cursor = await self.conn.execute(
                "SELECT id FROM psjba_session WHERE id = ? AND status = 'active'",
                (val,),
            )
            row = await cursor.fetchone()
            if row:
                return val
        return None

    async def set_current_session_id(self, session_id: str) -> None:
        await self._set_meta("current_session_id", session_id)
        await self.conn.commit()

    async def update_last_run_at(self) -> None:
        now = datetime.now().isoformat()
        await self._set_meta("last_run_at", now)
        await self.conn.commit()

    async def create_session(self, session: Session) -> Session:
        await self.conn.execute(
            """INSERT INTO psjba_session(id, title, type, cron_job_id, created_at, updated_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session.id,
                session.title,
                session.type,
                session.cron_job_id,
                session.created_at.isoformat(),
                session.updated_at.isoformat(),
                session.status,
            ),
        )
        await self.conn.commit()
        return session

    async def archive_session(self, session_id: str) -> None:
        now = datetime.now().isoformat()
        await self.conn.execute(
            "UPDATE psjba_session SET status = 'archived', updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        await self.conn.commit()

    async def get_session(self, session_id: str) -> Optional[Session]:
        cursor = await self.conn.execute(
            "SELECT * FROM psjba_session WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        return self._row_to_session(row) if row else None

    async def update_session_title(
        self, session_id: str, title: str
    ) -> None:
        now = datetime.now().isoformat()
        await self.conn.execute(
            "UPDATE psjba_session SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, session_id),
        )
        await self.conn.commit()

    async def list_sessions(
        self,
        page: int = 1,
        page_size: int = 20,
        session_type: Optional[str] = None,
    ) -> tuple[list[dict[str, Any]], int]:
        conditions = ["s.status != 'deleted'"]
        params: list[Any] = []
        if session_type:
            conditions.append("s.type = ?")
            params.append(session_type)
        where = " AND ".join(conditions)

        count_cursor = await self.conn.execute(
            f"SELECT COUNT(*) FROM psjba_session s WHERE {where}", params
        )
        count_row = await count_cursor.fetchone()
        total = count_row[0]

        offset = (page - 1) * page_size
        cursor = await self.conn.execute(
            f"""SELECT s.*, 
                       (SELECT content FROM psjba_message 
                        WHERE session_id = s.id AND role = 'user' 
                        ORDER BY created_at ASC LIMIT 1) as first_prompt
                FROM psjba_session s
                WHERE {where}
                ORDER BY s.created_at DESC
                LIMIT ? OFFSET ?""",
            (*params, page_size, offset),
        )
        rows = await cursor.fetchall()
        sessions = [dict(r) for r in rows]
        return sessions, total

    async def count_cron_sessions(self, cron_job_id: str) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM psjba_session WHERE cron_job_id = ? AND status != 'deleted'",
            (cron_job_id,),
        )
        row = await cursor.fetchone()
        return row[0]

    async def archive_cron_sessions(self, cron_job_id: str) -> int:
        now = datetime.now().isoformat()
        cursor = await self.conn.execute(
            """UPDATE psjba_session 
               SET status = 'archived', updated_at = ? 
               WHERE cron_job_id = ? AND status = 'active'""",
            (now, cron_job_id),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def save_message(self, message: Message) -> Message:
        await self.conn.execute(
            """INSERT INTO psjba_message(id, session_id, role, content, token_usage, model, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                message.id,
                message.session_id,
                message.role,
                message.content,
                message.token_usage,
                message.model,
                message.created_at.isoformat(),
            ),
        )
        await self.conn.commit()
        return message

    async def get_messages(
        self,
        session_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Message]:
        cursor = await self.conn.execute(
            """SELECT * FROM psjba_message 
               WHERE session_id = ? 
               ORDER BY created_at ASC
               LIMIT ? OFFSET ?""",
            (session_id, limit, offset),
        )
        rows = await cursor.fetchall()
        return [self._row_to_message(r) for r in rows]

    async def get_message_count(self, session_id: str) -> int:
        cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM psjba_message WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return row[0]

    async def search_messages(
        self, session_id: str, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        cursor = await self.conn.execute(
            """SELECT m.id, m.role, m.content, m.created_at
               FROM psjba_message_fts fts
               JOIN psjba_message m ON m.rowid = fts.rowid
               WHERE fts.session_id = ? AND fts.content MATCH ?
               ORDER BY m.created_at DESC
               LIMIT ?""",
            (session_id, query, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def create_cron_job(self, job: CronJob) -> CronJob:
        await self.conn.execute(
            """INSERT INTO psjba_cron_job(id, name, schedule, prompt, status, notes, last_run_at, next_run_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.id,
                job.name,
                job.schedule,
                job.prompt,
                job.status,
                job.notes,
                job.last_run_at.isoformat() if job.last_run_at else None,
                job.next_run_at.isoformat() if job.next_run_at else None,
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
            ),
        )
        await self.conn.commit()
        return job

    async def get_cron_job(self, job_id: str) -> Optional[CronJob]:
        cursor = await self.conn.execute(
            "SELECT * FROM psjba_cron_job WHERE id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        return self._row_to_cron_job(row) if row else None

    async def update_cron_job(self, job: CronJob) -> None:
        await self.conn.execute(
            """UPDATE psjba_cron_job 
               SET name=?, schedule=?, prompt=?, status=?, notes=?,
                   last_run_at=?, next_run_at=?, updated_at=?
               WHERE id=?""",
            (
                job.name,
                job.schedule,
                job.prompt,
                job.status,
                job.notes,
                job.last_run_at.isoformat() if job.last_run_at else None,
                job.next_run_at.isoformat() if job.next_run_at else None,
                datetime.now().isoformat(),
                job.id,
            ),
        )
        await self.conn.commit()

    async def list_cron_jobs(
        self, include_deleted: bool = False
    ) -> list[CronJob]:
        if include_deleted:
            cursor = await self.conn.execute(
                "SELECT * FROM psjba_cron_job ORDER BY created_at DESC"
            )
        else:
            cursor = await self.conn.execute(
                "SELECT * FROM psjba_cron_job WHERE status != 'deleted' ORDER BY created_at DESC"
            )
        rows = await cursor.fetchall()
        return [self._row_to_cron_job(r) for r in rows]

    async def get_enabled_cron_jobs(self) -> list[CronJob]:
        cursor = await self.conn.execute(
            "SELECT * FROM psjba_cron_job WHERE status = 'enable'"
        )
        rows = await cursor.fetchall()
        return [self._row_to_cron_job(r) for r in rows]

    async def delete_cron_job(self, job_id: str) -> None:
        now = datetime.now().isoformat()
        await self.conn.execute(
            "UPDATE psjba_cron_job SET status = 'deleted', updated_at = ? WHERE id = ?",
            (now, job_id),
        )
        await self.conn.commit()

    async def toggle_cron_job(self, job_id: str) -> Optional[bool]:
        job = await self.get_cron_job(job_id)
        if not job:
            return None
        new_status = "disable" if job.status == "enable" else "enable"
        await self.conn.execute(
            "UPDATE psjba_cron_job SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, datetime.now().isoformat(), job_id),
        )
        await self.conn.commit()
        return new_status == "enable"

    async def update_cron_notes(self, job_id: str, notes: str) -> None:
        """크론 job 의 notes 필드를 갱신한다 (크론 메시지 전달용)."""
        await self.conn.execute(
            "UPDATE psjba_cron_job SET notes = ?, updated_at = ? WHERE id = ?",
            (notes, datetime.now().isoformat(), job_id),
        )
        await self.conn.commit()

    async def get_cron_notes(self, job_id: str) -> str:
        """크론 job 의 notes 필드를 읽는다. (없으면 빈 문자열)"""
        cursor = await self.conn.execute(
            "SELECT notes FROM psjba_cron_job WHERE id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        return row["notes"] if row else ""

    async def get_cron_session_logs(
        self,
        cron_job_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        count_cursor = await self.conn.execute(
            "SELECT COUNT(*) FROM psjba_session WHERE cron_job_id = ? AND status != 'deleted'",
            (cron_job_id,),
        )
        total = (await count_cursor.fetchone())[0]

        offset = (page - 1) * page_size
        cursor = await self.conn.execute(
            """SELECT s.id, s.created_at, s.title,
                      (SELECT content FROM psjba_message 
                       WHERE session_id = s.id AND role = 'assistant' 
                       ORDER BY created_at DESC LIMIT 1) as last_response
               FROM psjba_session s
               WHERE s.cron_job_id = ? AND s.status != 'deleted'
               ORDER BY s.created_at DESC
               LIMIT ? OFFSET ?""",
            (cron_job_id, page_size, offset),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows], total

    def _row_to_session(self, row: aiosqlite.Row) -> Session:
        return Session(
            id=row["id"],
            title=row["title"],
            type=row["type"],
            cron_job_id=row["cron_job_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            status=row["status"],
        )

    def _row_to_message(self, row: aiosqlite.Row) -> Message:
        return Message(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            token_usage=row["token_usage"],
            model=row["model"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _row_to_cron_job(self, row: aiosqlite.Row) -> CronJob:
        return CronJob(
            id=row["id"],
            name=row["name"],
            schedule=row["schedule"],
            prompt=row["prompt"],
            status=row["status"],
            notes=row["notes"] if "notes" in row.keys() else "",
            last_run_at=datetime.fromisoformat(row["last_run_at"])
            if row["last_run_at"]
            else None,
            next_run_at=datetime.fromisoformat(row["next_run_at"])
            if row["next_run_at"]
            else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_scheduled_job(self, row: aiosqlite.Row) -> ScheduledJob:
        return ScheduledJob(
            id=row["id"],
            name=row["name"],
            scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
            prompt=row["prompt"],
            status=row["status"],
            session_id=row["session_id"],
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ─── 1회성 ScheduledJob ──────────────────────────────────────

    async def create_scheduled_job(self, job: ScheduledJob) -> ScheduledJob:
        await self.conn.execute(
            """INSERT INTO psjba_scheduled_job
               (id, name, scheduled_at, prompt, status, session_id, error_message, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job.id,
                job.name,
                job.scheduled_at.isoformat(),
                job.prompt,
                job.status,
                job.session_id,
                job.error_message,
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
            ),
        )
        await self.conn.commit()
        return job

    async def get_scheduled_job(self, job_id: str) -> Optional[ScheduledJob]:
        cursor = await self.conn.execute(
            "SELECT * FROM psjba_scheduled_job WHERE id = ?", (job_id,)
        )
        row = await cursor.fetchone()
        return self._row_to_scheduled_job(row) if row else None

    async def update_scheduled_job(self, job: ScheduledJob) -> None:
        await self.conn.execute(
            """UPDATE psjba_scheduled_job
               SET name=?, scheduled_at=?, prompt=?, status=?,
                   session_id=?, error_message=?, updated_at=?
               WHERE id=?""",
            (
                job.name,
                job.scheduled_at.isoformat(),
                job.prompt,
                job.status,
                job.session_id,
                job.error_message,
                datetime.now().isoformat(),
                job.id,
            ),
        )
        await self.conn.commit()

    async def list_scheduled_jobs(
        self,
        statuses: Optional[list[str]] = None,
        limit: int = 100,
    ) -> list[ScheduledJob]:
        """ScheduledJob 목록을 반환한다. statuses가 None이면 전부."""
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            cursor = await self.conn.execute(
                f"SELECT * FROM psjba_scheduled_job WHERE status IN ({placeholders}) "
                "ORDER BY scheduled_at ASC LIMIT ?",
                (*statuses, limit),
            )
        else:
            cursor = await self.conn.execute(
                "SELECT * FROM psjba_scheduled_job ORDER BY scheduled_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [self._row_to_scheduled_job(r) for r in rows]

    async def get_due_scheduled_jobs(self) -> list[ScheduledJob]:
        """실행 시각이 지난 pending job 목록을 반환한다."""
        cursor = await self.conn.execute(
            "SELECT * FROM psjba_scheduled_job "
            "WHERE status = 'pending' AND scheduled_at <= ? "
            "ORDER BY scheduled_at ASC",
            (datetime.now().isoformat(),),
        )
        rows = await cursor.fetchall()
        return [self._row_to_scheduled_job(r) for r in rows]

    async def delete_scheduled_job(self, job_id: str) -> None:
        await self.conn.execute(
            "UPDATE psjba_scheduled_job SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(), job_id),
        )
        await self.conn.commit()