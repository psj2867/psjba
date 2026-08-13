"""LLM 클라이언트 — ChatOpenAI 래퍼 (OpenRouter/OpenAI 호환)."""

import logging

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from src.config import Config

logger = logging.getLogger("psjba.agent.llm")

MAX_RETRIES = 3

# config에 model 값이 비어있을 때 사용할 fallback
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"


class LLMClient:
    """ChatOpenAI 인스턴스 생성 및 정리 담당.

    close() 는 향후 httpx 세션 관리 확장을 위한 인터페이스.
    """

    def __init__(self, config: Config) -> None:
        api_key = config.api_key
        model = str(config.get("llm", "model", "") or "").strip()
        base_url = config.get("llm", "url", "https://openrouter.ai/api/v1")
        temperature = config.get("llm", "temperature", 0.0)
        max_tokens = config.get("llm", "max_tokens", 4096)
        timeout = config.get("llm", "timeout", 60)

        # model이 비어있으면 fallback (OpenRouter는 model="" 이면 자동 라우팅 → 다른 모델이 응답함)
        if not model:
            logger.warning("LLM model is empty in config — falling back to %s. "
                           "Set [llm] model in config.ini or settings dialog.",
                           DEFAULT_MODEL)
            model = DEFAULT_MODEL

        if not api_key:
            logger.warning("API key is empty, LLM calls will fail")
            api_key = "sk-placeholder"

        # http_client를 생략하면 ChatOpenAI가 내부에서 httpx 세션을 자체 관리
        self.llm: BaseChatModel = ChatOpenAI(
            model=str(model),
            base_url=str(base_url),
            api_key=api_key,
            temperature=float(temperature),
            max_tokens=int(max_tokens),
            timeout=int(timeout),
            max_retries=MAX_RETRIES,
        )

        logger.info(f"LLM created: model={model}, base_url={base_url}")

    async def close(self) -> None:
        """리소스를 정리한다. (현재 httpx는 ChatOpenAI 내부 관리)"""
        pass


def create_llm(config: Config) -> BaseChatModel:
    """Config 설정으로 ChatOpenAI 인스턴스를 생성한다. (단순 호환용)

    주의: 이 함수는 httpx 세션 관리를 하지 않는다.
    세션 생명주기가 필요한 경우 ``LLMClient`` 를 사용할 것.

    Args:
        config: 암호화된 API 키가 이미 복호화된 Config 인스턴스

    Returns:
        ChatOpenAI 인스턴스
    """
    return LLMClient(config).llm
