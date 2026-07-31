"""tests/test_database.py — SQLite 데이터베이스 테스트."""

from pathlib import Path

import pytest

from src.database import Database
from src.models.schemas import CronJob, Message, Session


@pytest.fixture
async def db(temp_dir: Path) -> Database:
    """임시 DB fixture."""
    db_path = str(temp_dir / "test.db")
    db = Database(db_path)
    await db.connect()
    await db.initialize()
    yield db
    await db.close()


@pytest.mark.asyncio
class TestDatabaseInit:
    async def test_initialize_creates_tables(self, temp_dir: Path):
        db = Database(str(temp_dir / "test.db"))
        await db.connect()
        await db.initialize()

        # 테이블 존재 확인
        cursor = await db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in await cursor.fetchall()]
        assert "psjba_meta" in tables
        assert "psjba_session" in tables
        assert "psjba_message" in tables
        assert "psjba_cron_job" in tables
        assert "psjba_message_fts" in tables

        await db.close()

    async def test_schema_version_mismatch(self, temp_dir: Path):
        """schema_version 불일치 시 RuntimeError."""
        db = Database(str(temp_dir / "test.db"))
        await db.connect()
        await db.initialize()
        # 강제로 버전 변경
        await db.set_meta("schema_version", "999")
        await db.close()

        db2 = Database(str(temp_dir / "test.db"))
        await db2.connect()
        with pytest.raises(RuntimeError, match="Schema version mismatch"):
            await db2.initialize()
        await db2.close()

    async def test_schema_version_match(self, temp_dir: Path):
        """schema_version 일치 시 정상."""
        db = Database(str(temp_dir / "test.db"))
        await db.connect()
        await db.initialize()
        await db.close()

        db2 = Database(str(temp_dir / "test.db"))
        await db2.connect()
        await db2.initialize()  # raise 안 해야 함
        await db2.close()


@pytest.mark.asyncio
class TestSessionCRUD:
    async def test_create_session(self, db: Database):
        s = Session(title="My Session")
        created = await db.create_session(s)
        assert created.id == s.id
        assert created.title == "My Session"

        loaded = await db.get_session(s.id)
        assert loaded is not None
        assert loaded.title == "My Session"
        assert loaded.type == "chat"
        assert loaded.status == "active"

    async def test_archive_session(self, db: Database):
        s = Session()
        await db.create_session(s)
        await db.archive_session(s.id)
        loaded = await db.get_session(s.id)
        assert loaded is not None
        assert loaded.status == "archived"

    async def test_get_current_session_id_no_active(self, db: Database):
        """활성 세션이 없으면 None."""
        result = await db.get_current_session_id()
        assert result is None

    async def test_get_current_session_id_active(self, db: Database):
        s = Session()
        await db.create_session(s)
        await db.set_current_session_id(s.id)
        result = await db.get_current_session_id()
        assert result == s.id

    async def test_get_current_session_id_archived(self, db: Database):
        """archived 세션은 current로 반환 안 함."""
        s = Session(status="active")
        await db.create_session(s)
        await db.archive_session(s.id)
        await db.set_current_session_id(s.id)
        result = await db.get_current_session_id()
        assert result is None

    async def test_update_session_title(self, db: Database):
        s = Session(title="Old Title")
        await db.create_session(s)
        await db.update_session_title(s.id, "New Title")
        loaded = await db.get_session(s.id)
        assert loaded is not None
        assert loaded.title == "New Title"

    async def test_list_sessions(self, db: Database):
        sessions = [
            Session(title="Session 1", type="chat"),
            Session(title="Session 2", type="chat"),
            Session(title="Session 3", type="cron"),
        ]
        for s in sessions:
            await db.create_session(s)

        result, total = await db.list_sessions(page=1, page_size=20)
        assert total == 3
        assert len(result) == 3

        # 타입 필터
        result2, total2 = await db.list_sessions(session_type="cron")
        assert total2 == 1
        assert len(result2) == 1

    async def test_list_sessions_pagination(self, db: Database):
        for i in range(5):
            await db.create_session(Session(title=f"S{i}"))
        result, total = await db.list_sessions(page=1, page_size=2)
        assert total == 5
        assert len(result) == 2

        result2, total2 = await db.list_sessions(page=3, page_size=2)
        assert len(result2) == 1


@pytest.mark.asyncio
class TestMessageCRUD:
    async def test_save_and_get_messages(self, db: Database):
        s = Session()
        await db.create_session(s)

        msgs = [
            Message(session_id=s.id, role="user", content="Hello"),
            Message(session_id=s.id, role="assistant", content="Hi there!"),
        ]
        for m in msgs:
            await db.save_message(m)

        loaded = await db.get_messages(s.id)
        assert len(loaded) == 2
        assert loaded[0].content == "Hello"
        assert loaded[1].content == "Hi there!"

    async def test_get_message_count(self, db: Database):
        s = Session()
        await db.create_session(s)
        for i in range(3):
            await db.save_message(Message(session_id=s.id, role="user", content=f"msg{i}"))
        assert await db.get_message_count(s.id) == 3

    async def test_messages_offset_limit(self, db: Database):
        s = Session()
        await db.create_session(s)
        for i in range(10):
            await db.save_message(
                Message(session_id=s.id, role="user", content=f"msg{i}")
            )
        loaded = await db.get_messages(s.id, limit=3, offset=7)
        assert len(loaded) == 3
        assert loaded[0].content == "msg7"

    async def test_search_messages_fts(self, db: Database):
        s = Session()
        await db.create_session(s)
        msgs = [
            Message(session_id=s.id, role="user", content="오늘 날씨 좋다"),
            Message(session_id=s.id, role="assistant", content="네, 맑습니다"),
            Message(session_id=s.id, role="user", content="내일 주식 시장"),
        ]
        for m in msgs:
            await db.save_message(m)

        results = await db.search_messages(s.id, "날씨")
        assert len(results) >= 1

        results2 = await db.search_messages(s.id, "주식")
        assert len(results2) >= 1

        results3 = await db.search_messages(s.id, "없는검색어")
        assert len(results3) == 0


@pytest.mark.asyncio
class TestCronJobCRUD:
    async def test_create_cron_job(self, db: Database):
        c = CronJob(name="test", schedule="*/5 * * * *", prompt="hello")
        created = await db.create_cron_job(c)
        assert created.id == c.id
        assert created.status == "enable"

        loaded = await db.get_cron_job(c.id)
        assert loaded is not None
        assert loaded.name == "test"
        assert loaded.schedule == "*/5 * * * *"

    async def test_update_cron_job(self, db: Database):
        c = CronJob(name="old", schedule="0 * * * *", prompt="old prompt")
        await db.create_cron_job(c)
        c.name = "new"
        c.schedule = "*/10 * * * *"
        await db.update_cron_job(c)
        loaded = await db.get_cron_job(c.id)
        assert loaded is not None
        assert loaded.name == "new"
        assert loaded.schedule == "*/10 * * * *"

    async def test_list_cron_jobs(self, db: Database):
        jobs = [
            CronJob(name="A", schedule="* * * * *", prompt="a"),
            CronJob(name="B", schedule="* * * * *", prompt="b"),
        ]
        for j in jobs:
            await db.create_cron_job(j)

        all_jobs = await db.list_cron_jobs()
        assert len(all_jobs) == 2

    async def test_list_cron_jobs_exclude_deleted(self, db: Database):
        j1 = CronJob(name="A", schedule="* * * * *", prompt="a")
        await db.create_cron_job(j1)
        j2 = CronJob(name="B", schedule="* * * * *", prompt="b")
        await db.create_cron_job(j2)

        # 삭제 (status=deleted)
        await db.delete_cron_job(j1.id)
        all_jobs = await db.list_cron_jobs()
        assert len(all_jobs) == 1
        assert all_jobs[0].id == j2.id

        # include_deleted=True
        all_with_deleted = await db.list_cron_jobs(include_deleted=True)
        assert len(all_with_deleted) == 2

    async def test_toggle_cron_job(self, db: Database):
        c = CronJob(name="tog", schedule="* * * * *", prompt="t")
        await db.create_cron_job(c)
        result = await db.toggle_cron_job(c.id)
        assert result is False  # disable로 전환

        loaded = await db.get_cron_job(c.id)
        assert loaded is not None
        assert loaded.status == "disable"

        result2 = await db.toggle_cron_job(c.id)
        assert result2 is True  # enable로 전환

        loaded2 = await db.get_cron_job(c.id)
        assert loaded2 is not None
        assert loaded2.status == "enable"

    async def test_toggle_nonexistent(self, db: Database):
        result = await db.toggle_cron_job("nonexistent")
        assert result is None

    async def test_get_enabled_cron_jobs(self, db: Database):
        j1 = CronJob(name="A", schedule="* * * * *", prompt="a", status="enable")
        j2 = CronJob(name="B", schedule="* * * * *", prompt="b", status="disable")
        await db.create_cron_job(j1)
        await db.create_cron_job(j2)

        enabled = await db.get_enabled_cron_jobs()
        assert len(enabled) == 1
        assert enabled[0].id == j1.id

    async def test_delete_cron_job(self, db: Database):
        c = CronJob(name="del", schedule="* * * * *", prompt="d")
        await db.create_cron_job(c)
        await db.delete_cron_job(c.id)
        loaded = await db.get_cron_job(c.id)
        assert loaded is not None
        assert loaded.status == "deleted"

    async def test_cron_session_logs(self, db: Database):
        c = CronJob(name="log-test", schedule="* * * * *", prompt="test")
        await db.create_cron_job(c)
        s = Session(type="cron", cron_job_id=c.id)
        await db.create_session(s)

        logs, total = await db.get_cron_session_logs(c.id)
        assert total == 1
        assert len(logs) == 1

    async def test_count_cron_sessions(self, db: Database):
        c = CronJob(name="cnt", schedule="* * * * *", prompt="c")
        await db.create_cron_job(c)
        for _ in range(3):
            s = Session(type="cron", cron_job_id=c.id)
            await db.create_session(s)
        assert await db.count_cron_sessions(c.id) == 3

    async def test_archive_cron_sessions(self, db: Database):
        c = CronJob(name="arch", schedule="* * * * *", prompt="a")
        await db.create_cron_job(c)
        s = Session(type="cron", cron_job_id=c.id, status="active")
        await db.create_session(s)
        count = await db.archive_cron_sessions(c.id)
        assert count >= 1
        loaded = await db.get_session(s.id)
        assert loaded is not None
        assert loaded.status == "archived"


@pytest.mark.asyncio
class TestMeta:
    async def test_get_set_meta(self, db: Database):
        # initialize에서 schema_version이 설정되므로, 추가 키 테스트
        await db.set_meta("test_key", "test_value")
        val = await db.get_meta("test_key")
        assert val == "test_value"

    async def test_update_last_run_at(self, db: Database):
        await db.update_last_run_at()
        val = await db.get_meta("last_run_at")
        assert val is not None
        assert len(val) > 0


@pytest.mark.asyncio
class TestErrors:
    async def test_get_session_not_found(self, db: Database):
        loaded = await db.get_session("nonexistent-id")
        assert loaded is None

    async def test_get_cron_job_not_found(self, db: Database):
        loaded = await db.get_cron_job("nonexistent-id")
        assert loaded is None

    async def test_database_not_connected(self):
        db = Database(":memory:")
        with pytest.raises(RuntimeError, match="Database not connected"):
            _ = db.conn