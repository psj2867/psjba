from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import uuid4


@dataclass
class Meta:
    key: str
    value: str


@dataclass
class Session:
    id: str = field(default_factory=lambda: str(uuid4()))
    title: str = "새 대화"
    type: str = "chat"
    cron_job_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    status: str = "active"


@dataclass
class Message:
    id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str = ""
    role: str = ""
    content: str = ""
    token_usage: Optional[str] = None
    model: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class CronJob:
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    schedule: str = ""
    prompt: str = ""
    status: str = "enable"
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)