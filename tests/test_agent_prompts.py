"""tests/test_agent_prompts.py — 시스템 프롬프트 테스트."""

from src.agent.prompts import SYSTEM_PROMPT_TEMPLATE, build_system_prompt


class TestSystemPrompt:
    def test_template_contains_placeholders(self):
        assert "{accessibility_tree}" in SYSTEM_PROMPT_TEMPLATE

    def test_build_system_prompt_default(self):
        prompt = build_system_prompt()
        assert "(No page loaded)" in prompt
        assert "browser automation agent" in prompt.lower()

    def test_build_system_prompt_with_tree(self):
        tree = "[button] \"Click me\"\n  [link] \"here\""
        prompt = build_system_prompt(accessibility_tree=tree)
        assert "[button]" in prompt
        assert "\"Click me\"" in prompt
        assert "[link]" in prompt

    def test_contains_tool_list(self):
        prompt = build_system_prompt()
        assert "browser_navigate" in prompt
        assert "browser_click" in prompt
        assert "browser_snapshot" in prompt
        assert "browser_screenshot" in prompt
        assert "cron_add" in prompt
        assert "memory_search" in prompt

    def test_contains_rules(self):
        prompt = build_system_prompt()
        assert "CSS selectors" in prompt
        assert "self-healing" in prompt
        assert "Max 3 retries" in prompt or "3 retries" in prompt
        assert "memory_search" in prompt