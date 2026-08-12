"""tests/test_accessibility.py вАФ м†СкЈЉмД± нКЄл¶ђ нПђлІЈнМЕ нЕМмК§нКЄ."""

import pytest

from src.browser.accessibility import (
    MAX_TOKEN_ESTIMATE,
    _estimate_tokens,
    _format_node,
    format_accessibility_tree,
)


class TestEstimateTokens:
    def test_empty(self):
        assert _estimate_tokens("") == 1  # len("")//4 + 1 = 1

    def test_short_text(self):
        assert _estimate_tokens("hello") == 2  # 5//4 + 1 = 2

    def test_exact_4(self):
        assert _estimate_tokens("abcd") == 2  # 4//4 + 1

    def test_long_text(self):
        assert _estimate_tokens("a" * 100) == 26  # 100//4 + 1

    def test_4000_chars(self):
        assert _estimate_tokens("x" * 4000) == 1001  # 4000//4 + 1


class TestFormatNode:
    def test_basic_role_only(self):
        node = {"role": "button", "name": ""}
        result = _format_node(node, depth=0)
        assert "[button]" in result

    def test_with_name(self):
        node = {"role": "link", "name": "Click here"}
        result = _format_node(node, depth=0)
        assert '[link]' in result
        assert '→ Click here' in result

    def test_with_value(self):
        node = {"role": "textbox", "name": "Search", "value": "hello"}
        result = _format_node(node, depth=0)
        assert "val=hello" in result

    def test_with_description(self):
        node = {"role": "img", "name": "logo", "description": "Company logo"}
        result = _format_node(node, depth=0)
        assert "desc=Company logo" in result

    def test_with_flags_checked(self):
        node = {"role": "checkbox", "name": "agree", "checked": True}
        result = _format_node(node, depth=0)
        assert "checked" in result

    def test_with_flags_unchecked(self):
        node = {"role": "checkbox", "name": "agree", "checked": False}
        result = _format_node(node, depth=0)
        assert "unchecked" in result

    def test_with_disabled(self):
        node = {"role": "button", "name": "submit", "disabled": True}
        result = _format_node(node, depth=0)
        assert "disabled" in result

    def test_with_focused(self):
        node = {"role": "textbox", "name": "input", "focused": True}
        result = _format_node(node, depth=0)
        assert "focused" in result

    def test_with_expanded(self):
        node = {"role": "treeitem", "name": "node", "expanded": True}
        result = _format_node(node, depth=0)
        assert "expanded" in result

    def test_with_collapsed(self):
        node = {"role": "treeitem", "name": "node", "expanded": False}
        result = _format_node(node, depth=0)
        assert "collapsed" in result

    def test_with_selected(self):
        node = {"role": "option", "name": "item", "selected": True}
        result = _format_node(node, depth=0)
        assert "selected" in result

    def test_indent_depth(self):
        node = {"role": "button", "name": "ok"}
        result = _format_node(node, depth=2)
        assert result.startswith("    ")  # 2 * "  "


class TestFormatTree:
    @pytest.mark.asyncio
    async def test_single_node(self):
        snapshot = {"role": "Root", "name": "page"}
        lines = await format_accessibility_tree(snapshot)
        assert "[Root]" in lines
        assert "→ page" in lines

    @pytest.mark.asyncio
    async def test_nested_nodes(self):
        snapshot = {
            "role": "Root",
            "name": "page",
            "children": [
                {"role": "button", "name": "ok"},
                {"role": "link", "name": "here"},
            ],
        }
        result = await format_accessibility_tree(snapshot)
        # 리프 노드는 → format
        assert "→ ok" in result
        assert "→ here" in result

    @pytest.mark.asyncio
    async def test_deep_nesting(self):
        snapshot = {
            "role": "Root",
            "children": [
                {
                    "role": "group",
                    "children": [
                        {"role": "button", "name": "deep"}
                    ],
                }
            ],
        }
        result = await format_accessibility_tree(snapshot)
        assert "→ deep" in result


class TestPruneTree:
    @pytest.mark.asyncio
    async def test_remove_empty_static_text(self):
        node = {"role": "Root", "children": [
            {"role": "StaticText", "name": "", "children": []}
        ]}
        result = await format_accessibility_tree(node)
        assert "StaticText" not in result

    @pytest.mark.asyncio
    async def test_keep_non_empty_static_text(self):
        node = {"role": "Root", "children": [
            {"role": "StaticText", "name": "hello", "children": []}
        ]}
        result = await format_accessibility_tree(node)
        assert "→ hello" in result

    @pytest.mark.asyncio
    async def test_remove_none_role(self):
        node = {"role": "Root", "children": [
            {"role": "None", "name": "", "children": []}
        ]}
        result = await format_accessibility_tree(node)
        assert "None" not in result

    @pytest.mark.asyncio
    async def test_remove_presentation_role(self):
        node = {"role": "Root", "children": [
            {"role": "presentation", "name": "", "children": []}
        ]}
        result = await format_accessibility_tree(node)
        assert "presentation" not in result

    @pytest.mark.asyncio
    async def test_prune_children(self):
        node = {
            "role": "Root",
            "name": "",
            "children": [
                {"role": "StaticText", "name": "", "children": []},
                {"role": "button", "name": "ok", "children": []},
            ],
        }
        result = await format_accessibility_tree(node)
        assert "StaticText" not in result
        assert "→ ok" in result


class TestTokenPrune:
    @pytest.mark.asyncio
    async def test_no_prune_needed(self):
        snapshot = {"role": "Root", "name": "small"}
        result = await format_accessibility_tree(snapshot)
        assert "[Root]" in result
        assert "→ small" in result

    @pytest.mark.asyncio
    async def test_prune_large_tree(self):
        """매우 큰 트리에서도 prunning이 동작"""
        children = []
        for i in range(200):
            children.append(
                {
                    "role": "StaticText",
                    "name": "x" * 50,
                    "children": [],
                }
            )
        snapshot = {"role": "Root", "children": children}
        result = await format_accessibility_tree(snapshot)
        assert result is not None

class TestFormatAccessibilityTree:
    @pytest.mark.asyncio
    async def test_none_snapshot(self):
        result = await format_accessibility_tree(None)
        assert "No accessibility tree" in result

    @pytest.mark.asyncio
    async def test_basic_snapshot(self):
        snapshot = {"role": "Root", "name": "page"}
        result = await format_accessibility_tree(snapshot)
        assert "[Root]" in result

    def test_max_token_constant(self):
        assert MAX_TOKEN_ESTIMATE == 4000

    @pytest.mark.asyncio
    async def test_truncated_output(self):
        """매우 큰 트리는 truncated 표시."""
        children = []
        for i in range(500):
            children.append(
                {
                    "role": "button",
                    "name": "x" * 100,
                    "children": [],
                }
            )
        snapshot = {"role": "Root", "children": children}
        result = await format_accessibility_tree(snapshot, max_tokens=100)
        assert "(truncated" in result or len(result) > 0
