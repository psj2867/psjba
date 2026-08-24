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
    scheduled_job_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    status: str = "active"


@dataclass
class Message:
    id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str = ""
    role: str = ""
    content: str = ""
    tool_calls: Optional[str] = None      # assistant 전용: JSON string
    tool_call_id: Optional[str] = None    # tool 전용
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
    notes: str = ""
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class ScheduledJob:
    """1회성 예약 job — 지정된 시간에 한 번만 실행된다."""

    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    scheduled_at: datetime = field(default_factory=datetime.now)
    prompt: str = ""
    status: str = "pending"  # pending | running | done | cancelled | error
    session_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)