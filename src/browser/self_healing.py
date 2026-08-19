"""Self-healing selector — CSS 선택자 실패 시 대체 전략 자동 시도."""

import logging
import re
from typing import Any, Optional

from playwright.async_api import Locator, Page

logger = logging.getLogger("psjba.browser.self_healing")

MAX_CONSECUTIVE_FAILURES = 3

# CSS 클래스명으로 안전한 문자만: [a-zA-Z0-9_-]
_SAFE_CLASS_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


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

    async def make_unique_selector(
        self,
        cdp: Any,
        root_id: int,
        selector: str,
        name: str,
    ) -> str:
        """중복 CSS 선택자를 unique하게 만든다.

        CDP DOM.querySelectorAll 로 개수 체크 (eval 없음),
        1개 초과 시에만 1회 page.evaluate 로 textContent 비교.

        Args:
            cdp: CDP 세션
            root_id: DOM.getDocument 의 root nodeId
            selector: 중복 가능성이 있는 CSS 선택자
            name: 접근성 트리의 name (textContent 비교 기준)

        Returns:
            unique CSS 선택자 (중복이 없으면 원본 그대로)
        """
        try:
            qr = await cdp.send("DOM.querySelectorAll", {
                "nodeId": root_id, "selector": selector,
            })
            nids = qr.get("nodeIds", [])
        except Exception:
            return selector
        if len(nids) <= 1:
            return selector

        if not name:
            return selector

        try:
            escaped = name.replace("'", "\\'").replace("\\", "\\\\")
            idx = await self._page.evaluate(f"""(() => {{
                const t = '{escaped}';
                const nodes = document.querySelectorAll('{selector}');
                for (let i = 0; i < nodes.length; i++)
                    if ((nodes[i].textContent || '').trim() === t) return i;
                return -1;
            }})()""")
            if idx >= 0:
                return f"{selector}:nth-of-type({idx + 1})"
        except Exception:
            pass

        return selector

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

        # 1) CSS 먼저 시도
        try:
            css_locator = await self._try_css(selector, timeout)
            if css_locator is not None and await css_locator.count() > 0:
                self._consecutive_failures = 0
                logger.info("Self-healing: found element with CSS strategy")
                return css_locator.first
        except Exception as e:
            logger.debug("Self-healing: CSS failed: %s", e)

        # 2) CSS 실패 시에만 fallback 코루틴 생성 → 미소비 경고 방지
        try:
            xpath_locator = await self._try_xpath(selector, timeout)
            if xpath_locator is not None and await xpath_locator.count() > 0:
                self._consecutive_failures = 0
                logger.info("Self-healing: found element with XPath strategy")
                return xpath_locator.first
        except Exception as e:
            logger.debug("Self-healing: XPath failed: %s", e)

        if text:
            try:
                text_locator = await self._try_text(text, timeout)
                if text_locator is not None and await text_locator.count() > 0:
                    self._consecutive_failures = 0
                    logger.info("Self-healing: found element with Text strategy")
                    return text_locator.first
            except Exception as e:
                logger.debug("Self-healing: Text failed: %s", e)

        if aria_label:
            try:
                aria_locator = await self._try_aria_label(aria_label, timeout)
                if aria_locator is not None and await aria_locator.count() > 0:
                    self._consecutive_failures = 0
                    logger.info("Self-healing: found element with AriaLabel strategy")
                    return aria_locator.first
            except Exception as e:
                logger.debug("Self-healing: AriaLabel failed: %s", e)

        if role:
            try:
                role_locator = await self._try_role(role, timeout)
                if role_locator is not None and await role_locator.count() > 0:
                    self._consecutive_failures = 0
                    logger.info("Self-healing: found element with Role strategy")
                    return role_locator.first
            except Exception as e:
                logger.debug("Self-healing: Role failed: %s", e)

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
