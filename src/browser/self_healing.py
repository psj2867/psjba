"""Self-healing selector — CSS 선택자 실패 시 대체 전략 자동 시도."""

import logging
import re
from typing import Optional

from playwright.async_api import Locator, Page

logger = logging.getLogger("psjba.browser.self_healing")

MAX_CONSECUTIVE_FAILURES = 3


def _css_to_possible_xpath(css_selector: str) -> str:
    """간단한 CSS 선택자를 XPath로 변환."""
    s = css_selector.strip()

    if s.startswith("#"):
        el_id = s[1:]
        return f"//*[@id='{el_id}']"

    if s.startswith("."):
        cls = s[1:]
        return f"//*[contains(@class, '{cls}')]"

    tag_match = re.match(r"^(\w+)(.*)", s)
    if tag_match:
        tag = tag_match.group(1)
        rest = tag_match.group(2)
        if not rest:
            return f"//{tag}"
        attr_match = re.findall(r"\[([^\]=]+)(?:=['\"]([^'\"]*)['\"])?\]", rest)
        if attr_match:
            conditions = []
            for attr_name, attr_val in attr_match:
                if attr_val:
                    conditions.append(f"@{attr_name}='{attr_val}'")
                else:
                    conditions.append(f"@{attr_name}")
            xpath = f"//{tag}[{' and '.join(conditions)}]"
            return xpath

    return f"//*[contains(@class, '{css_selector}')]" or f"//{css_selector}"


class SelfHealingSelector:
    """CSS 선택자 실패 시 계층적 fallback으로 요소를 찾는다.

    순서: CSS → XPath → text → aria-label → role
    연속 3회 실패 시 포기.
    """

    def __init__(self, page: Page) -> None:
        self._page = page
        self._consecutive_failures = 0

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reset_failures(self) -> None:
        self._consecutive_failures = 0

    def increment_failure(self) -> None:
        self._consecutive_failures += 1

    def should_give_up(self) -> bool:
        return self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES

    async def find_element(
        self,
        selector: str,
        *,
        timeout: int = 5000,
        role: Optional[str] = None,
        text: Optional[str] = None,
        aria_label: Optional[str] = None,
    ) -> Optional[Locator]:
        """대체 전략으로 요소를 찾는다.

        Args:
            selector: 원본 CSS 선택자
            timeout: 각 전략의 타임아웃 (ms)
            role: role 기반 매칭에 사용할 ARIA role
            text: 텍스트 매칭에 사용할 텍스트
            aria_label: aria-label 매칭에 사용할 레이블

        Returns:
            찾은 Locator 또는 None
        """
        if self.should_give_up():
            logger.warning("Self-healing: giving up after 3 consecutive failures")
            return None

        strategies = [
            ("CSS", self._try_css(selector, timeout)),
            ("XPath", self._try_xpath(selector, timeout)),
        ]

        if text:
            strategies.append(("Text", self._try_text(text, timeout)))
        if aria_label:
            strategies.append(("AriaLabel", self._try_aria_label(aria_label, timeout)))
        if role:
            strategies.append(("Role", self._try_role(role, timeout)))

        if text and not any(s[0] == "Text" for s in strategies):
            strategies.append(("Text", self._try_text(text, timeout)))
        if aria_label and not any(s[0] == "AriaLabel" for s in strategies):
            strategies.append(("AriaLabel", self._try_aria_label(aria_label, timeout)))
        if role and not any(s[0] == "Role" for s in strategies):
            strategies.append(("Role", self._try_role(role, timeout)))

        for strategy_name, coro in strategies:
            try:
                locator = await coro
                if locator is not None:
                    count = await locator.count()
                    if count > 0:
                        self._consecutive_failures = 0
                        logger.info(
                            f"Self-healing: found element with {strategy_name} strategy"
                        )
                        return locator.first
            except Exception as e:
                logger.debug(f"Self-healing: {strategy_name} failed: {e}")
                continue

        self._consecutive_failures += 1
        logger.warning(
            f"Self-healing: all strategies failed "
            f"(consecutive failures: {self._consecutive_failures})"
        )
        return None

    async def _try_css(self, selector: str, timeout: int) -> Optional[Locator]:
        """CSS 선택자로 요소를 찾는다."""
        locator = self._page.locator(selector)
        return locator

    async def _try_xpath(self, selector: str, timeout: int) -> Optional[Locator]:
        """XPath로 요소를 찾는다."""
        xpath = _css_to_possible_xpath(selector)
        locator = self._page.locator(f"xpath={xpath}")
        return locator

    async def _try_text(self, text: str, timeout: int) -> Optional[Locator]:
        """텍스트 매칭으로 요소를 찾는다."""
        locator = self._page.get_by_text(text, exact=False)
        return locator

    async def _try_aria_label(self, label: str, timeout: int) -> Optional[Locator]:
        """aria-label 매칭으로 요소를 찾는다."""
        locator = self._page.get_by_label(label, exact=False)
        return locator

    async def _try_role(self, role: str, timeout: int) -> Optional[Locator]:
        """ARIA role 매칭으로 요소를 찾는다."""
        try:
            locator = self._page.get_by_role(role)
            return locator
        except Exception:
            return None
