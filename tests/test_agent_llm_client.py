"""tests/test_agent_llm_client.py — LLM 클라이언트 테스트."""

from unittest.mock import patch

import pytest

from src.agent.llm_client import create_llm


class TestCreateLLM:
    def test_create_llm_with_default_config(self, temp_dir):
        """기본 설정으로 LLM 클라이언트 생성."""
        from src.config import Config

        ini_path = temp_dir / "config.ini"
        cfg = Config(str(ini_path))
        cfg.load()
        cfg.api_key = "sk-test-key"
        with patch("src.agent.llm_client.ChatOpenAI") as mock:
            mock_instance = mock.return_value
            mock_instance.invoke.return_value = "test"
            llm = create_llm(cfg)
            assert llm is not None
            mock.assert_called_once()
            kwargs = mock.call_args.kwargs
            assert kwargs["model"] == "gpt-4o"  # 기본값
            assert kwargs["temperature"] == 0.0

    def test_create_llm_with_custom_config(self, temp_dir):
        """커스텀 설정으로 LLM 클라이언트 생성."""
        from src.config import Config

        ini_path = temp_dir / "config.ini"
        cfg = Config(str(ini_path))
        cfg.load()
        cfg.api_key = "sk-test-key"
        cfg.set("llm", "model", "claude-sonnet-4")
        cfg.set("llm", "temperature", 0.5)
        cfg.set("llm", "max_tokens", 2048)
        cfg.set("llm", "timeout", 30)
        cfg.set("llm", "url", "https://api.anthropic.com/v1")

        with patch("src.agent.llm_client.ChatOpenAI") as mock:
            llm = create_llm(cfg)
            assert llm is not None
            kwargs = mock.call_args.kwargs
            assert kwargs["model"] == "claude-sonnet-4"
            assert kwargs["temperature"] == 0.5
            assert kwargs["max_tokens"] == 2048
            assert kwargs["timeout"] == 30

    def test_create_llm_function_exists(self):
        """create_llm 함수가 존재하고 호출 가능."""
        assert callable(create_llm)