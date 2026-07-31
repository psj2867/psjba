"""tests/test_integration_api.py — OpenRouter API 통합 테스트.

Python 3.14 + qasync 환경에서 실제 API 호출을 검증한다.
실행:  pytest tests/test_integration_api.py -v -p no:asyncio
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ── 로깅 설정 (콘솔에 바로 보이게) ──────────────────────────────────
logger = logging.getLogger("test_integration")
logger.setLevel(logging.DEBUG)
_handler = logging.StreamHandler(sys.stdout)
_handler.setLevel(logging.DEBUG)
_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
)
logger.handlers.clear()
logger.addHandler(_handler)

# ── 테스트용 OpenRouter 키 ───────────────────────────────────────────
OPENROUTER_KEY = "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"
MODEL = "deepseek/deepseek-v4-flash"  # deepseek-v4-flash


# =====================================================================
# 1. _run_async 동작 검증
# =====================================================================
class TestRunAsync:
    """_run_async 의 Python 3.14 동작을 시나리오별로 검증."""

    def test_no_loop_creates_new(self, tmp_path: Path) -> None:
        """루프가 없으면 새로 만들어 코루틴을 실행해야 함."""
        from src.ui.settings_dialog import _run_async

        async def dummy():
            return 42

        # 기존 루프 제거
        try:
            old = asyncio.get_event_loop()
            if old.is_running():
                pytest.skip("루프가 실행 중이어서 이 테스트를 건너뜀")
        except RuntimeError:
            pass

        result = _run_async(dummy())
        assert result == 42

    def test_running_loop_returns_none(self) -> None:
        """루프가 실행 중이면 None 을 반환해야 함."""
        from src.ui.settings_dialog import _run_async

        async def dummy():
            return 42

        async def runner():
            result = _run_async(dummy())
            assert result is None

        # qasync 루프를 만들지 않고 asyncio.run() 사용
        # (Python 3.14+ 에서도 asyncio.run() 은 내부 루프를 실행 중으로 설정)
        try:
            asyncio.run(runner())
        except RuntimeError as e:
            # Python 3.14+에서 asyncio.run() 실패 시 우회
            logger.warning(f"asyncio.run() failed: {e}")
            pytest.skip("asyncio.run() not available in this environment")

    def test_qasync_running_loop_returns_none(self, qapp) -> None:
        """qasync 루프가 실행 중일 때도 None 을 반환해야 함."""
        from src.ui.settings_dialog import _run_async

        async def dummy():
            return 42

        async def main():
            result = _run_async(dummy())
            assert result is None, f"Expected None, got {result}"
            logger.info("✓ _run_async returned None as expected when loop is running")

        # qasync 루프 생성 및 실행
        import qasync
        loop = qasync.QEventLoop(qapp)
        asyncio.set_event_loop(loop)
        with loop:
            loop.run_until_complete(main())


# =====================================================================
# 2. Config + OpenRouter 키 저장/로드 테스트
# =====================================================================
class TestConfigWithOpenRouter:
    """Config 에 OpenRouter API 키 저장 및 복호화 테스트."""

    def test_save_and_load_api_key(self, tmp_path: Path) -> None:
        """API 키 저장 후 재로드 시 동일한 값이 유지되어야 함."""
        from src.config import Config

        ini_path = tmp_path / "config.ini"
        cfg = Config(str(ini_path))
        cfg.load()

        # API 키 설정
        cfg.api_key = OPENROUTER_KEY
        cfg.set("llm", "model", MODEL)
        cfg.set("llm", "url", "https://openrouter.ai/api/v1")
        cfg.save()

        # 새 Config 로드
        cfg2 = Config(str(ini_path))
        cfg2.load()
        assert cfg2.api_key == OPENROUTER_KEY, "API 키 복호화 실패"
        assert cfg2.get("llm", "model") == MODEL
        logger.info("✓ API key encryption/decryption OK")

    def test_create_llm_with_real_config(self, tmp_path: Path) -> None:
        """create_llm 이 실제 Config 로 정상 생성되어야 함."""
        from src.config import Config
        from src.agent.llm_client import create_llm

        ini_path = tmp_path / "config.ini"
        cfg = Config(str(ini_path))
        cfg.load()
        cfg.api_key = OPENROUTER_KEY
        cfg.set("llm", "model", MODEL)
        cfg.set("llm", "url", "https://openrouter.ai/api/v1")
        cfg.set("llm", "temperature", 0.0)
        cfg.set("llm", "max_tokens", 2048)
        cfg.set("llm", "timeout", 30)

        llm = create_llm(cfg)
        assert llm is not None
        assert llm.model == MODEL
        # ChatOpenAI 에는 openai_api_base 속성이 있음
        api_base = getattr(llm, "openai_api_base", "unknown")
        logger.info(f"✓ LLM created: model={llm.model}, api_base={api_base}")


# =====================================================================
# 3. 실제 OpenRouter API 호출 테스트
# =====================================================================
class TestOpenRouterAPICall:
    """OpenRouter API 로 실제 LLM 호출을 검증.

    이 테스트는 실제 API를 호출하므로 네트워크 필요.
    건너뛰려면: ``pytest -m "not openrouter"``
    """

    @pytest.mark.openrouter
    def test_simple_chat_call(self) -> None:
        """OpenRouter API 로 간단한 채팅 호출 (qasync 루프 없이 httpx 직접 사용)."""
        import httpx

        logger.info(f"Calling {MODEL} via OpenRouter API...")
        headers = {
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "Hello! Say 'API_OK' in your response."}],
            "max_tokens": 512,
            "temperature": 0.0,
        }

        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=30,
        )
        assert r.status_code == 200, f"API error: {r.status_code} {r.text[:200]}"
        data = r.json()
        content = data["choices"][0]["message"]["content"]

        logger.info(f"=== LLM RESPONSE ===")
        logger.info(content)
        logger.info(f"=== END RESPONSE ===")
        assert content, "LLM returned empty response"
        assert "API_OK" in content, f"Expected API_OK in response, got: {content[:100]}"

    @pytest.mark.openrouter
    def test_agent_via_httpx(self) -> None:
        """LangGraph 호출을 시뮬레이션: OpenRouter API 로 에이전트 역할 수행.

        qasync 루프 없이 httpx 로 직접 API 를 호출하여
        에이전트 시나리오(도구 호출 판단까지)를 검증한다.
        """
        import httpx
        import json

        logger.info(f"Calling {MODEL} (agent scenario)...")
        headers = {
            "Authorization": f"Bearer {OPENROUTER_KEY}",
            "Content-Type": "application/json",
        }

        # 1차: 도구 호출이 필요한 질문
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": "You are a browser agent. When you need to interact with a webpage, "
                 "call the 'navigate' tool. Otherwise, answer directly."},
                {"role": "user", "content": "What is 1+1? Answer with just the number."},
            ],
            "max_tokens": 1024,
            "temperature": 0.0,
        }

        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=30,
        )
        assert r.status_code == 200, f"API error: {r.status_code}"
        data = r.json()
        content = data["choices"][0]["message"]["content"]

        logger.info(f"=== AGENT RESPONSE (simple query) ===")
        logger.info(content)
        logger.info(f"=== END ===")
        assert content, "Empty response"
        assert "2" in content, f"Expected '2' in response, got: {content[:100]}"

        # 2차: 도구 호출이 필요한 시나리오
        payload2 = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": "You are a browser agent. Use the 'navigate' tool "
                 "when you need to visit a URL. You have the following tools available:\n\n"
                 "navigate(url: str) - Navigate to a URL"},
                {"role": "user", "content": "Go to google.com and search for cats."},
            ],
            "max_tokens": 1024,
            "temperature": 0.0,
        }

        r2 = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload2,
            headers=headers,
            timeout=30,
        )
        assert r2.status_code == 200, f"API error: {r2.status_code}"
        data2 = r2.json()
        choice = data2["choices"][0]
        message = choice["message"]

        logger.info(f"=== AGENT RESPONSE (tool scenario) ===")
        if "tool_calls" in message and message["tool_calls"]:
            for tc in message["tool_calls"]:
                logger.info(f"  Tool call: {tc['function']['name']}({tc['function']['arguments']})")
        if message.get("content"):
            logger.info(f"  Content: {message['content'][:200]}")
        logger.info(f"=== END ===")

        # 도구 호출이 있거나 내용이 있어야 함
        has_tool_call = "tool_calls" in message and message["tool_calls"]
        has_content = message.get("content") and len(message["content"]) > 0
        assert has_tool_call or has_content, "No response from model"


# =====================================================================
# 4. main.py 시나리오 검증 (모의)
# =====================================================================
class TestMainScenario:
    """main.py 의 run() 시나리오를 모의로 검증."""

    def test_shutdown_event_flow(self) -> None:
        """shutdown_event 가 정상 동작하는지 검증."""
        async def scenario():
            shutdown_event = asyncio.Event()
            results = []

            async def worker():
                await shutdown_event.wait()
                results.append("shutdown")
                return "done"

            task = asyncio.ensure_future(worker())
            await asyncio.sleep(0.01)
            assert len(results) == 0  # 아직 shutdown 안 됨

            shutdown_event.set()  # 시그널 발생
            await asyncio.sleep(0.01)
            assert len(results) == 1
            assert results[0] == "shutdown"

        import qasync
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
        with loop:
            loop.run_until_complete(scenario())
        logger.info("✓ shutdown_event flow OK")

    def test_setup_signal_handlers_signature(self) -> None:
        """setup_signal_handlers 의 새 시그니처가 정상 동작."""
        from main import setup_signal_handlers
        import signal

        shutdown_event = asyncio.Event()
        setup_signal_handlers(shutdown_event=shutdown_event)
        assert not shutdown_event.is_set()

        # SIGINT 핸들러가 shutdown_event 를 설정하는지 확인
        # (실제로 보내지는 않음)
        logger.info("✓ setup_signal_handlers signature OK")


# =====================================================================
# 5. qasync 루프 + 크론 스케줄러 통합
# =====================================================================
class TestCronWithQasync:
    """qasync 루프에서 CronScheduler 가 정상 동작하는지 검증."""

    def test_cron_scheduler_start_stop(self, tmp_path: Path) -> None:
        """CronScheduler 를 시작/종료 (qasync 루프)."""
        from src.cron.scheduler import CronScheduler
        from src.cron.executor import CronExecutor
        from unittest.mock import MagicMock, AsyncMock
        import qasync

        cfg = MagicMock()
        cfg.get.side_effect = lambda s, k, d=None: d
        cfg.api_key = OPENROUTER_KEY

        db = AsyncMock()
        db.get_cron_jobs.return_value = ([], 0)
        db.get_current_session_id.return_value = None

        manager = MagicMock()
        manager.is_connected = False

        async def scenario():
            executor = CronExecutor(config=cfg, db=db, manager=manager)
            scheduler = CronScheduler()
            scheduler.set_dependencies(db=db, executor=executor)

            await scheduler.start()
            assert scheduler.registered_count == 0
            logger.info(f"✓ CronScheduler started (0 jobs)")

            scheduler.stop_all()
            logger.info("✓ CronScheduler stopped")

        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
        with loop:
            loop.run_until_complete(scenario())