"""tests/test_agent_prompts.py — 시스템 프롬프트 테스트.

툴 설명은 bind_tools()의 API schema로 전달되므로,
시스템 프롬프트는 행동 규칙만 담고 있어야 한다.
"""

from src.agent.prompts import SYSTEM_PROMPT, build_system_prompt


class TestSystemPrompt:
    def test_constant_name(self):
        """SYSTEM_PROMPT 상수가 존재하고 문자열이다."""
        assert isinstance(SYSTEM_PROMPT, str)
        assert len(SYSTEM_PROMPT) > 100

    def test_build_system_prompt_returns_same(self):
        """build_system_prompt()은 항상 SYSTEM_PROMPT를 반환한다."""
        assert build_system_prompt() is SYSTEM_PROMPT

    def test_no_tool_descriptions(self):
        """툴 설명 섹션(## Available Tools)이 없어야 함 (bind_tools에 위임).

        규칙에서 tool 이름을 언급하는 건 OK (예: "browser_click(ref)"),
        별도의 툴 설명 목록 섹션이 없어야 한다.
        """
        assert "## Available Tools" not in SYSTEM_PROMPT
        assert "### Browser Tools" not in SYSTEM_PROMPT
        assert "### Cron Tools" not in SYSTEM_PROMPT

    def test_contains_rules(self):
        """핵심 행동 규칙이 포함되어야 함."""
        assert "browser automation agent" in SYSTEM_PROMPT.lower()
        assert "Max 3 retries" in SYSTEM_PROMPT or "3 retries" in SYSTEM_PROMPT