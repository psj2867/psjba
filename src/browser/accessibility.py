"""접근성 트리 포맷팅 — Playwright accessibility snapshot → 계층 텍스트."""

import logging
from typing import Any, Optional

from playwright.async_api import Page

logger = logging.getLogger("psjba.browser.accessibility")

MAX_TOKEN_ESTIMATE = 4000
CHARS_PER_TOKEN = 4

# ref 번호 부여를 위한 전역 카운터 (포맷 시에만 사용)
_REF_COUNTER = {"n": 0}


def _reset_ref_counter() -> None:
    """ref 번호 카운터를 초기화한다."""
    _REF_COUNTER["n"] = 0


def _next_ref() -> int:
    """다음 ref 번호를 발급한다."""
    _REF_COUNTER["n"] += 1
    return _REF_COUNTER["n"]


def _is_interactive(role: str) -> bool:
    """해당 role 이 상호작용 가능한 요소인지 판단한다."""
    interactive_roles = {
        "button", "link", "textbox", "combobox", "checkbox", "radio",
        "menuitem", "tab", "switch", "slider", "spinbutton", "searchbox",
        "listbox", "option", "treeitem", "gridcell", "row",
    }
    return role in interactive_roles


def _estimate_tokens(text: str) -> int:
    """텍스트의 토큰 수를 추정한다 (대략 4자 = 1토큰)."""
    return len(text) // CHARS_PER_TOKEN + 1


def _format_node(
    node: dict[str, Any],
    depth: int = 0,
    indent: str = "  ",
    refs: Optional[dict[int, dict[str, Any]]] = None,
) -> str:
    """단일 노드를 계층 텍스트로 포맷팅한다.

    Args:
        node: 접근성 노드 딕셔너리
        depth: 들여쓰기 깊이
        indent: 들여쓰기 문자열
        refs: ref 번호 → 노드 정보 매핑 (수집용, mutable)

    Returns:
        포맷된 텍스트 라인
    """
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

    # 상호작용 가능한 요소에 ref 번호 부여
    if refs is not None and _is_interactive(role):
        ref = _next_ref()
        label_parts.append(f"[#{ref}]")
        refs[ref] = {
            "role": role,
            "name": name,
            "value": value,
            "description": description,
        }

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
    refs: Optional[dict[int, dict[str, Any]]] = None,
) -> list[str]:
    """접근성 스냅샷을 계층적 텍스트 라인 리스트로 변환한다."""
    if lines is None:
        lines = []

    formatted = _format_node(snapshot, depth, indent, refs=refs)
    if formatted.strip():
        lines.append(formatted)

    children = snapshot.get("children", [])
    for child in children:
        _format_tree(child, depth + 1, lines, indent, refs=refs)

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
    refs: Optional[dict[int, dict[str, Any]]] = None,
) -> dict[str, Any]:
    """토큰 제한을 초과하면 불필요한 노드를 반복 제거한다."""
    tree = snapshot
    for _ in range(5):
        text = "\n".join(_format_tree(tree, refs=refs))
        if _estimate_tokens(text) <= max_tokens:
            return tree
        tree = _prune_tree(tree) or {"role": "Root", "children": []}
    return tree


def format_accessibility_tree(
    snapshot: Optional[dict[str, Any]],
    max_tokens: int = MAX_TOKEN_ESTIMATE,
    *,  # refs는 키워드 전용
    refs: Optional[dict[int, dict[str, Any]]] = None,
) -> str:
    """Playwright accessibility snapshot을 LLM 컨텍스트용 텍스트로 변환한다.

    Args:
        snapshot: Playwright page.accessibility.snapshot() 결과
        max_tokens: 최대 토큰 제한 (기본 4000)
        refs: ref 번호 → 노드 정보 매핑 (수집용, mutable)

    Returns:
        계층적 텍스트 문자열
    """
    if snapshot is None:
        return "(No accessibility tree available)"

    _reset_ref_counter()
    tree = _prune_tree(snapshot) or {"role": "Root", "children": []}
    tree = _token_prune(tree, max_tokens, refs=refs)

    lines = _format_tree(tree, refs=refs)
    text = "\n".join(lines)

    token_count = _estimate_tokens(text)
    if token_count > max_tokens:
        truncated_lines = lines
        while truncated_lines and _estimate_tokens("\n".join(truncated_lines)) > max_tokens:
            truncated_lines.pop()
        text = "\n".join(truncated_lines)
        text += f"\n... (truncated, ~{_estimate_tokens(text)} tokens)"

    return text


def format_accessibility_tree_with_refs(
    snapshot: Optional[dict[str, Any]],
    max_tokens: int = MAX_TOKEN_ESTIMATE,
) -> tuple[str, dict[int, dict[str, Any]]]:
    """접근성 트리를 포맷하고 ref 번호 → 노드 정보 매핑도 함께 반환한다.

    Returns:
        (포맷된 텍스트, {ref: {role, name, value, description}})
    """
    refs: dict[int, dict[str, Any]] = {}
    text = format_accessibility_tree(snapshot, max_tokens, refs=refs)
    return text, refs


async def get_accessibility_tree(page: Page) -> str:
    """페이지 접근성 트리를 가져온다.

    1순위: ``page.accessibility.snapshot()`` (Playwright 네이티브)
    2순위: CDP ``Accessibility`` domain 직접 활성화 + ``getFullAXTree``
    3순위: JS ``page.evaluate()`` 폴백

    CDP 연결 모드에서는 Playwright가 ``Accessibility`` domain을
    자동 활성화하지 않으므로 직접 enable 후 조회한다.

    Returns:
        포맷팅된 접근성 트리 텍스트
    """
    text, refs = await get_accessibility_tree_with_refs(page)
    return text


async def get_accessibility_tree_with_refs(
    page: Page,
) -> tuple[str, dict[int, dict[str, Any]]]:
    """접근성 트리 텍스트와 ref 매핑을 함께 가져온다.

    Returns:
        (포맷팅된 텍스트, {ref: {role, name, value, description}})
    """
    from playwright.async_api import Error as PlaywrightError

    try:
        snapshot = await page.accessibility.snapshot()
        return format_accessibility_tree_with_refs(snapshot)
    except (AttributeError, PlaywrightError):
        pass
    except Exception as e:
        logger.warning("accessibility.snapshot() failed: %s", e)

    # CDP Accessibility domain 직접 활성화 + 조회
    try:
        return await _get_accessibility_via_cdp_with_refs(page)
    except Exception as e:
        logger.warning("CDP accessibility failed: %s", e)

    # 최종 폴백: JS evaluate (ref 매핑 없음)
    text = await _fallback_via_evaluate(page)
    return text, {}


async def _get_accessibility_via_cdp(page: Page) -> str:
    """CDP Accessibility domain 을 직접 활성화하고 트리를 조회한다."""
    text, _refs = await _get_accessibility_via_cdp_with_refs(page)
    return text


async def _get_accessibility_via_cdp_with_refs(
    page: Page,
) -> tuple[str, dict[int, dict[str, Any]]]:
    """CDP 로 접근성 트리를 조회하고 ref 매핑도 함께 반환한다."""
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Accessibility.enable")
    result = await cdp.send("Accessibility.getFullAXTree", {})
    raw_nodes = result.get("nodes", [])
    if not raw_nodes:
        return "(No accessibility nodes via CDP)", {}

    # CDP node → format_accessibility_tree 호환 dict 로 변환
    snapshot = _cdp_nodes_to_snapshot(raw_nodes)
    return format_accessibility_tree_with_refs(snapshot)


def _cdp_nodes_to_snapshot(nodes: list[dict]) -> dict[str, Any]:
    """CDP Accessibility.getFullAXTree 노드 리스트를
    Playwright accessibility.snapshot() 호환 단일 dict 로 변환."""
    node_map: dict[str, dict] = {}
    for n in nodes:
        node_map[n["nodeId"]] = n

    def _to_playwright(node_id: str) -> dict[str, Any]:
        cdp_node = node_map.get(node_id, {})
        role = cdp_node.get("role", {}).get("value", "")
        name = ""
        value = ""
        description = ""
        checked: Optional[bool] = None
        disabled: Optional[bool] = None
        focused: Optional[bool] = None
        expanded: Optional[bool] = None
        selected: Optional[bool] = None

        for prop in cdp_node.get("properties", []):
            pname = prop.get("name", "")
            pval = prop.get("value", {})
            pvalue = pval.get("value", "")
            if pname == "name":
                name = str(pvalue)
            elif pname == "value":
                value = str(pvalue)
            elif pname == "description":
                description = str(pvalue)
            elif pname == "checked":
                checked = pvalue in ("true", True)
            elif pname == "disabled":
                disabled = pvalue in ("true", True)
            elif pname == "focused":
                focused = pvalue in ("true", True)
            elif pname == "expanded":
                expanded = pvalue in ("true", True)
            elif pname == "selected":
                selected = pvalue in ("true", True)

        children = []
        for cid in cdp_node.get("childIds", []):
            if cid in node_map:
                children.append(_to_playwright(cid))

        node: dict[str, Any] = {
            "role": role,
            "name": name,
            "children": children,
        }
        if value:
            node["value"] = value
        if description:
            node["description"] = description
        if checked is not None:
            node["checked"] = checked
        if disabled is not None:
            node["disabled"] = disabled
        if focused is not None:
            node["focused"] = focused
        if expanded is not None:
            node["expanded"] = expanded
        if selected is not None:
            node["selected"] = selected
        return node

    root_ids = [n["nodeId"] for n in nodes
                if not n.get("parentId") or n["parentId"] not in node_map]
    if not root_ids:
        root_ids = [nodes[0]["nodeId"]]

    snapshot = _to_playwright(root_ids[0])
    return snapshot


async def _fallback_via_evaluate(page: Page) -> str:
    """page.evaluate() 로 DOM 기반 접근성 정보를 추출한다."""
    try:
        js = r"""
        () => {
            const parts = [];

            // 1) title
            parts.push("=== Page ===");
            parts.push(document.title || "(no title)");
            parts.push("URL: " + (location.href || ""));

            // 2) landmarks & interactive elements
            const selectors = [
                "nav", "main", "section", "article", "aside",
                "header", "footer", "form", "search",
                "h1", "h2", "h3",
                "a[href]", "button", "select", "textarea",
                "input:not([type=hidden])",
                '[tabindex]:not([tabindex="-1"])',
                '[role="button"]', '[role="link"]', '[role="combobox"]',
                '[role="dialog"]', '[role="alert"]', '[role="menu"]',
            ];
            const seen = new Set();
            parts.push("\n=== Interactive Elements ===");
            document.querySelectorAll(selectors.join(",")).forEach(el => {
                const tag = el.tagName.toLowerCase();
                const text = (el.textContent || "").trim().slice(0, 100);
                const aria = el.getAttribute("aria-label") || "";
                const label = aria || text;
                if (!label || seen.has(label)) return;
                seen.add(label);
                const role = el.getAttribute("role") || tag;
                parts.push(`  [${role}] "${label}"`);
            });

            // 3) visible body text (truncated)
            parts.push("\n=== Body Text ===");
            const bodyText = (document.body && document.body.innerText || "").trim();
            parts.push(bodyText.slice(0, 3000) + (bodyText.length > 3000 ? "\n... (truncated)" : ""));

            return parts.join("\n");
        }
        """
        text = await page.evaluate(js)
        return text or "(Empty page)"
    except Exception as e:
        logger.error("Fallback evaluate failed: %s", e)
        return f"(Error getting page structure: {e})"
