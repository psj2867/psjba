"""tests/test_schemas.py — 데이터 모델 테스트."""

from datetime import datetime
from uuid import UUID

from src.models.schemas import CronJob, Message, Meta, Session


class TestMeta:
    def test_create(self):
        m = Meta(key="k", value="v")
        assert m.key == "k"
        assert m.value == "v"

    def test_repr(self):
        m = Meta(key="k", value="v")
        assert "k" in repr(m)


class TestSession:
    def test_create_default(self):
        s = Session()
        # UUID 자동 생성
        assert UUID(s.id)
        assert s.title == "새 대화"
        assert s.type == "chat"
        assert s.cron_job_id is None
        assert isinstance(s.created_at, datetime)
        assert isinstance(s.updated_at, datetime)
        assert s.status == "active"

    def test_create_with_values(self):
        now = datetime.now()
        s = Session(
            id="my-id",
            title="Test Session",
            type="cron",
            cron_job_id="cron-123",
            created_at=now,
            updated_at=now,
            status="archived",
        )
        assert s.id == "my-id"
        assert s.title == "Test Session"
        assert s.type == "cron"
        assert s.cron_job_id == "cron-123"
        assert s.created_at == now
        assert s.updated_at == now
        assert s.status == "archived"

    def test_uuid_auto_generation(self):
        s1 = Session()
        s2 = Session()
        assert s1.id != s2.id


class TestMessage:
    def test_create_default(self):
        m = Message()
        assert UUID(m.id)
        assert m.session_id == ""
        assert m.role == ""
        assert m.content == ""
        assert m.token_usage is None
        assert m.model is None
        assert isinstance(m.created_at, datetime)

    def test_create_with_values(self):
        now = datetime.now()
        m = Message(
            id="msg-1",
            session_id="sess-1",
            role="user",
            content="hello",
            token_usage='{"prompt": 10}',
            model="gpt-4o",
            created_at=now,
        )
        assert m.id == "msg-1"
        assert m.session_id == "sess-1"
        assert m.role == "user"
        assert m.content == "hello"
        assert m.token_usage == '{"prompt": 10}'
        assert m.model == "gpt-4o"
        assert m.created_at == now


class TestCronJob:
    def test_create_default(self):
        c = CronJob()
        assert UUID(c.id)
        assert c.name == ""
        assert c.schedule == ""
        assert c.prompt == ""
        assert c.status == "enable"
        assert c.last_run_at is None
        assert c.next_run_at is None
        assert isinstance(c.created_at, datetime)
        assert isinstance(c.updated_at, datetime)

    def test_create_with_values(self):
        now = datetime.now()
        c = CronJob(
            id="cron-1",
            name="test job",
            schedule="*/5 * * * *",
            prompt="do something",
            status="disable",
            last_run_at=now,
            next_run_at=now,
            created_at=now,
            updated_at=now,
        )
        assert c.id == "cron-1"
        assert c.name == "test job"
        assert c.schedule == "*/5 * * * *"
        assert c.prompt == "do something"
        assert c.status == "disable"
        assert c.last_run_at == now
        assert c.next_run_at == now