"""tests/test_self_healing.py — Self-healing selector 테스트."""

import pytest

from src.browser.self_healing import (
    MAX_CONSECUTIVE_FAILURES,
    SelfHealingSelector,
    _css_to_possible_xpath,
)


class TestCssToXPath:
    def test_id_selector(self):
        assert _css_to_possible_xpath("#myid") == "//*[@id='myid']"

    def test_class_selector(self):
        result = _css_to_possible_xpath(".myclass")
        assert "contains(@class, 'myclass')" in result

    def test_tag_selector(self):
        assert _css_to_possible_xpath("div") == "//div"

    def test_tag_with_attr(self):
        result = _css_to_possible_xpath('input[type="text"]')
        assert "//input" in result
        assert "@type='text'" in result

    def test_tag_with_multiple_attrs(self):
        result = _css_to_possible_xpath('a[href="https://x.com"][target="_blank"]')
        assert "//a" in result
        assert "@href='https://x.com'" in result
        assert "@target='_blank'" in result

    def test_tag_with_bare_attr(self):
        result = _css_to_possible_xpath("input[required]")
        assert "//input" in result
        assert "@required" in result

    def test_plain_string_fallback(self):
        result = _css_to_possible_xpath("some-random-text")
        # fallback: contains class or tag
        assert result is not None


class TestSelfHealingSelector:
    def test_consecutive_failures_initial(self):
        """초기 연속 실패 횟수는 0."""
        # page mock은 필요 없음 — 실패 카운터만 테스트
        selector = SelfHealingSelector.__new__(SelfHealingSelector)
        selector._consecutive_failures = 0
        assert selector.consecutive_failures == 0

    def test_increment_and_reset(self):
        selector = SelfHealingSelector.__new__(SelfHealingSelector)
        selector._consecutive_failures = 0
        selector.increment_failure()
        assert selector.consecutive_failures == 1
        selector.increment_failure()
        assert selector.consecutive_failures == 2
        selector.reset_failures()
        assert selector.consecutive_failures == 0

    def test_should_give_up_false(self):
        selector = SelfHealingSelector.__new__(SelfHealingSelector)
        selector._consecutive_failures = 2
        assert selector.should_give_up() is False

    def test_should_give_up_true(self):
        selector = SelfHealingSelector.__new__(SelfHealingSelector)
        selector._consecutive_failures = 3
        assert selector.should_give_up() is True

    def test_max_consecutive_failures_constant(self):
        assert MAX_CONSECUTIVE_FAILURES == 3

    @pytest.mark.asyncio
    async def test_find_element_give_up(self):
        """should_give_up이 True면 바로 None 반환."""
        selector = SelfHealingSelector.__new__(SelfHealingSelector)
        selector._consecutive_failures = 3
        result = await selector.find_element("#anything")
        assert result is None