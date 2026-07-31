"""접근성 트리 포맷팅 — Playwright accessibility snapshot → 계층 텍스트."""

import logging
from typing import Any, Optional

from playwright.async_api import Page

logger = logging.getLogger("psjba.browser.accessibility")

MAX_TOKEN_ESTIMATE = 4000
CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """텍스트의 토큰 수를 추정한다 (대략 4자 = 1토큰)."""
    return len(text) // CHARS_PER_TOKEN + 1


def _format_node(
    node: dict[str, Any],
    depth: int = 0,
    indent: str = "  ",
) -> str:
    """단일 노드를 계층 텍스트로 포맷팅한다."""
    parts: list[str] = []
    role = node.get("role", "")
    name = node.get("name", "")
    value = node.get("value", "")
    description = node.get("description", "")
    checked = node.get("checked")
    disabled = node.get("disabled")
    focused = node.get("focused")
    expanded = node.get("expanded")
    selected = node.get("selected")

    label_parts: list[str] = []
    if role:
        label_parts.append(f"[{role}]")
    if name:
        label_parts.append(f'"{name}"')
    if value:
        label_parts.append(f"val={value}")
    if description:
        label_parts.append(f"desc={description}")

    flags: list[str] = []
    if checked is True:
        flags.append("checked")
    elif checked is False:
        flags.append("unchecked")
    if disabled is True:
        flags.append("disabled")
    if focused is True:
        flags.append("focused")
    if expanded is True:
        flags.append("expanded")
    elif expanded is False:
        flags.append("collapsed")
    if selected is True:
        flags.append("selected")

    prefix = indent * depth
    label = " ".join(label_parts)
    if flags:
        label += f" [{', '.join(flags)}]"

    return f"{prefix}{label}"


def _format_tree(
    snapshot: dict[str, Any],
    depth: int = 0,
    lines: Optional[list[str]] = None,
    indent: str = "  ",
) -> list[str]:
    """접근성 스냅샷을 계층적 텍스트 라인 리스트로 변환한다."""
    if lines is None:
        lines = []

    formatted = _format_node(snapshot, depth, indent)
    if formatted.strip():
        lines.append(formatted)

    children = snapshot.get("children", [])
    for child in children:
        _format_tree(child, depth + 1, lines, indent)

    return lines


def _prune_tree(snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    """빈 StaticText 노드, 숨겨진 요소 등을 제거하여 트리를 축소한다."""
    role = snapshot.get("role", "")
    name = snapshot.get("name", "")
    children = snapshot.get("children", [])

    if role == "StaticText" and not name.strip():
        return None

    if role in ("None", "presentation") and not name and not children:
        return None

    pruned_children: list[dict[str, Any]] = []
    for child in children:
        pruned = _prune_tree(child)
        if pruned is not None:
            pruned_children.append(pruned)

    result = dict(snapshot)
    result["children"] = pruned_children
    return result


def _token_prune(
    snapshot: dict[str, Any],
    max_tokens: int = MAX_TOKEN_ESTIMATE,
) -> dict[str, Any]:
    """토큰 제한을 초과하면 불필요한 노드를 반복 제거한다."""
    tree = snapshot
    for _ in range(5):
        text = "\n".join(_format_tree(tree))
        if _estimate_tokens(text) <= max_tokens:
            return tree
        tree = _prune_tree(tree) or {"role": "Root", "children": []}
    return tree


def format_accessibility_tree(
    snapshot: Optional[dict[str, Any]],
    max_tokens: int = MAX_TOKEN_ESTIMATE,
) -> str:
    """Playwright accessibility snapshot을 LLM 컨텍스트용 텍스트로 변환한다.

    Args:
        snapshot: Playwright page.accessibility.snapshot() 결과
        max_tokens: 최대 토큰 제한 (기본 4000)

    Returns:
        계층적 텍스트 문자열
    """
    if snapshot is None:
        return "(No accessibility tree available)"

    tree = _prune_tree(snapshot) or {"role": "Root", "children": []}
    tree = _token_prune(tree, max_tokens)

    lines = _format_tree(tree)
    text = "\n".join(lines)

    token_count = _estimate_tokens(text)
    if token_count > max_tokens:
        truncated_lines = lines
        while truncated_lines and _estimate_tokens("\n".join(truncated_lines)) > max_tokens:
            truncated_lines.pop()
        text = "\n".join(truncated_lines)
        text += f"\n... (truncated, ~{_estimate_tokens(text)} tokens)"

    return text


async def get_accessibility_tree(page: Page) -> str:
    """페이지의 접근성 트리를 가져와 포맷팅된 텍스트로 반환한다.

    Args:
        page: Playwright Page 객체

    Returns:
        포맷팅된 접근성 트리 텍스트
    """
    try:
        snapshot = await page.accessibility.snapshot()
        return format_accessibility_tree(snapshot)
    except Exception as e:
        logger.error(f"Failed to get accessibility tree: {e}")
        return f"(Error getting accessibility tree: {e})"
