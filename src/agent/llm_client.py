"""LLM 클라이언트 — ChatOpenAI 래퍼 (OpenRouter/OpenAI 호환)."""

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from src.config import Config

logger = logging.getLogger("psjba.agent.llm")

MAX_RETRIES = 3


def create_llm(config: Config) -> BaseChatModel:
    """Config 설정으로 ChatOpenAI 인스턴스를 생성한다.

    OpenRouter, OpenAI, Together, Groq 등 OpenAI 호환 API를 지원한다.

    Args:
        config: 암호화된 API 키가 이미 복호화된 Config 인스턴스

    Returns:
        ChatOpenAI 인스턴스
    """
    api_key = config.api_key
    model = config.get("llm", "model", "qwen/qwen3.6-27b")
    base_url = config.get("llm", "url", "https://openrouter.ai/api/v1")
    temperature = config.get("llm", "temperature", 0.0)
    max_tokens = config.get("llm", "max_tokens", 4096)
    timeout = config.get("llm", "timeout", 60)

    if not api_key:
        logger.warning("API key is empty, LLM calls will fail")
        api_key = "sk-placeholder"

    llm = ChatOpenAI(
        model=str(model),
        base_url=str(base_url),
        api_key=api_key,
        temperature=float(temperature),
        max_tokens=int(max_tokens),
        timeout=int(timeout),
        max_retries=MAX_RETRIES,
    )

    logger.info(f"LLM created: model={model}, base_url={base_url}")
    return llm
