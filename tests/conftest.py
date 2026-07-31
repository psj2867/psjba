"""pytest 설정 — 공통 fixture.

UI 테스트는 QT_QPA_PLATFORM=offscreen 환경에서 헤드리스로 실행된다.
pytest-qt 의 qtbot fixture 를 사용하여 위젯/시그널/클릭을 시뮬레이션한다.
"""

import os
import tempfile
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

# 반드시 PySide6 import 전에 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """임시 디렉토리 fixture."""
    with tempfile.TemporaryDirectory() as d:
        old = os.getcwd()
        os.chdir(d)
        yield Path(d)
        os.chdir(old)


@pytest.fixture
def sample_ini_content() -> str:
    """샘플 config.ini 내용."""
    return """[llm]
api_key = gAAAAAB...encrypted...
model = gpt-4o
provider = openrouter
url = https://openrouter.ai/api/v1
max_tokens = 4096
temperature = 0.0
timeout = 60

[cdp]
host = 127.0.0.1
port = 9222
connection_timeout = 1.0

[chrome]
path = C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe
user_data_dir =
headless = false

[sqlite]
path = ./test_browser_agent.data

[agent]
sliding_window_size = 30
cron_timeout = 300

[workspace]
workspace_dir = ./psjba_temp

[ui]
font_size = 14
max_chat_lines = 1000
language = ko

[logging]
level = disabled
path = ./psjba.log
"""


@pytest.fixture
def mock_config() -> MagicMock:
    """Mock Config 객체."""
    cfg = MagicMock()
    cfg.get.side_effect = lambda section, key, default=None: {
        ("ui", "font_size"): 14,
        ("ui", "max_chat_lines"): 1000,
        ("ui", "language"): "ko",
        ("llm", "model"): "gpt-4o",
        ("cdp", "host"): "127.0.0.1",
        ("cdp", "port"): 9222,
        ("chrome", "headless"): False,
        ("chrome", "path"): "",
        ("chrome", "user_data_dir"): "",
        ("workspace", "workspace_dir"): "./psjba_temp",
        ("agent", "sliding_window_size"): 30,
        ("agent", "cron_timeout"): 300,
        ("logging", "level"): "disabled",
        ("logging", "path"): "./psjba.log",
        ("sqlite", "path"): "./test_browser_agent.data",
    }.get((section, key), default)
    cfg.api_key = "test-api-key"
    return cfg


@pytest.fixture
def mock_db() -> AsyncMock:
    """Mock Database 객체 (async)."""
    db = AsyncMock()
    db.get_current_session_id.return_value = None
    db.create_session.return_value = None
    db.set_current_session_id.return_value = None
    db.get_messages.return_value = []
    db.list_sessions.return_value = ([], 0)
    db.get_cron_jobs.return_value = ([], 0)
    return db


@pytest.fixture
def mock_manager() -> MagicMock:
    """Mock PlaywrightManager."""
    mgr = MagicMock()
    mgr.is_connected = False
    mgr.connect = AsyncMock(return_value="Chrome connected (mock)")
    mgr.disconnect = AsyncMock()
    mgr.update_config = MagicMock()
    # _launcher mock
    launcher = MagicMock()
    launcher.check_cdp_connection = AsyncMock(return_value=False)
    mgr._launcher = launcher
    return mgr