"""접근성 트리 포맷팅 — Playwright accessibility snapshot → 계층 텍스트 (고도화)."""

import logging
from typing import Any, Optional

from playwright.async_api import Page

logger = logging.getLogger("psjba.browser.accessibility")

MAX_TOKEN_ESTIMATE = 4000
CHARS_PER_TOKEN = 4

# ref 번호 부여를 위한 전역 카운터 (포맷 시에만 사용)
_REF_COUNTER = {"n": 0}

# a11y role 중 상호작용 가능한 것
_INTERACTIVE_ROLES = {
    "button", "link", "textbox", "combobox", "checkbox", "radio",
    "menuitem", "tab", "switch", "slider", "spinbutton", "searchbox",
    "listbox", "option", "treeitem", "gridcell", "row",
    "menuitemcheckbox", "menuitemradio",
    "progressbar", "scrollbar",
}

# 타이핑 가능한 role
_TYPE_ROLES = {"textbox", "searchbox", "combobox", "spinbutton"}


def _reset_ref_counter() -> None:
    """ref 번호 카운터를 초기화한다."""
    _REF_COUNTER["n"] = 0


def _next_ref() -> int:
    """다음 ref 번호를 발급한다."""
    _REF_COUNTER["n"] += 1
    return _REF_COUNTER["n"]


def _is_interactive(role: str) -> bool:
    return role in _INTERACTIVE_ROLES


def _is_type_role(role: str) -> bool:
    return role in _TYPE_ROLES


def _estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


def _format_node(
    node: dict[str, Any],
    depth: int = 0,
    indent: str = "  ",
    refs: Optional[dict[int, dict[str, Any]]] = None,
    detail: bool = False,
) -> str:
    """단일 노드를 계층 텍스트로 포맷팅한다.

    Args:
        node: 접근성 노드 딕셔너리
        depth: 들여쓰기 깊이
        indent: 들여쓰기 문자열
        refs: ref 번호 → 노드 정보 매핑 (수집용, mutable)
        detail: True면 태그/속성 등 상세 정보를 추가로 표시

    Returns:
        포맷된 텍스트 라인
    """
    role = node.get("role", "")
    name = node.get("name", "")
    value = node.get("value", "")

    label_parts: list[str] = []
    if role:
        label_parts.append(f"[{role}]")

    # 태그 정보 (detail 모드 또는 interactive 요소)
    tag = node.get("tag", "")
    if tag:
        attr_parts = []
        if tag:
            attr_parts.append(tag)
        nid = node.get("_id", "")
        if nid:
            attr_parts.append(f"#{nid}")
        cls = node.get("_class", "")
        if cls:
            attr_parts.append(f".{cls.replace(' ', '.')}"[:60])
        if attr_parts:
            label_parts.append(f"<{' '.join(attr_parts)}>")

    if name:
        label_parts.append(f'"{name}"')

    # 타이핑에 중요한 속성들
    input_type = node.get("_type", "")
    if input_type and input_type != "text":
        label_parts.append(f"type={input_type}")
    placeholder = node.get("_placeholder", "")
    if placeholder and placeholder != name:
        label_parts.append(f"ph={placeholder}")
    form_name = node.get("_form_name", "")
    if form_name and form_name != name:
        label_parts.append(f"name={form_name}")

    if value is not None and value != "":
        v = str(value)
        if len(v) > 60:
            v = v[:60] + "..."
        label_parts.append(f"val={v}")
    description = node.get("description", "")
    if description:
        d = description[:80]
        label_parts.append(f"desc={d}")

    # 접힌(collapsed) 서브트리 표시
    collapsed = node.get("_collapsed")
    if collapsed:
        count = collapsed.get("count", 0)
        label_parts.append(f"→ {count} children collapsed")

    # 상호작용 가능 요소에 ref 번호 부여
    if refs is not None and _is_interactive(role):
        ref = _next_ref()
        label_parts.append(f"[#{ref}]")
        refs[ref] = {
            "role": role,
            "name": name,
            "value": value,
            "description": description,
            "tag": tag,
            "_id": nid,
            "_class": cls,
            "_type": input_type,
            "_placeholder": placeholder,
            "_form_name": form_name,
            "_aria_label": node.get("_aria_label", ""),
        }

    # detail 모드에서 추가 정보
    auto = node.get("_autocomplete", "")
    if detail and auto:
        label_parts.append(f"autocomplete={auto}")
    if detail:
        aria_lbl = node.get("_aria_label", "")
        if aria_lbl and aria_lbl != name:
            label_parts.append(f"aria={aria_lbl}")

    # flags
    flags: list[str] = []
    checked = node.get("checked")
    disabled = node.get("disabled")
    focused = node.get("focused")
    expanded = node.get("expanded")
    selected = node.get("selected")
    required = node.get("required")
    readonly = node.get("readonly")
    multiline = node.get("multiline")

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
    if required is True:
        flags.append("required")
    if readonly is True:
        flags.append("readonly")
    if multiline is True:
        flags.append("multiline")

    level = node.get("level", 0)
    if detail and level > 0:
        label_parts.append(f"H{level}")

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
    detail: bool = False,
    max_depth: int = 999,
) -> list[str]:
    """접근성 스냅샷을 계층적 텍스트 라인 리스트로 변환한다."""
    if lines is None:
        lines = []

    if depth > max_depth:
        lines.append(f"{indent * depth}... (depth {depth})")
        return lines

    formatted = _format_node(snapshot, depth, indent, refs=refs, detail=detail)
    if formatted.strip():
        lines.append(formatted)

    children = snapshot.get("children", [])
    for child in children:
        _format_tree(child, depth + 1, lines, indent, refs=refs, detail=detail, max_depth=max_depth)

    return lines


def _prune_tree(snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    """불필요한 노드(빈 StaticText, 불필요한 구조 래퍼)를 제거한다.

    Single-child "generic"/"none"/"presentation"/"paragraph"/"group" 노드는
    interactive한 자식이나 name/description/value가 있는 경우가 아니면
    자신을 생략하고 자식을 바로 부모에 붙인다.
    """
    role = snapshot.get("role", "")
    name = snapshot.get("name", "")
    value = snapshot.get("value", "")
    description = snapshot.get("description", "")
    children = snapshot.get("children", [])

    # 내용 없는 노드 제거
    if role == "StaticText" and not name.strip():
        return None

    # 자식도 없고 이름도 없는 쓸모없는 컨테이너 제거
    wrapper_roles = {"None", "presentation", "generic", "paragraph", "group",
                     "Region", "region", "Section", "section",
                     "Div", "div", "Span", "span"}
    if role in wrapper_roles and not name and not value and not description:
        if not children:
            return None

    # 순수 레이아웃 div/span 컨테이너는 자식들을 한 단계 끌어올림
    # 조건: role=none/generic, tag=div/span, 이름/값/설명/id/class 없음, 자식 있음
    is_pure_container = (
        role in ("none", "None", "generic", "Generic")
        and not name and not value and not description
        and not snapshot.get("_id")
        and not snapshot.get("_class")
        and snapshot.get("tag", "") in ("div", "span")
        and bool(children)
    )
    if is_pure_container:
        # 자식들을 먼저 정리한 뒤, 컨테이너 자체는 생략하고 자식들만 반환
        pruned_all = []
        for child in children:
            pruned = _prune_tree(child)
            if pruned is not None:
                pruned_all.append(pruned)
        if len(pruned_all) == 1:
            return pruned_all[0]
        if pruned_all:
            # 자식이 여러 개면 synthetic none 노드로 감싸서 반환
            merged = {"role": "none", "children": pruned_all}
            return merged
        return None

    # 단일 자식만 가진 구조 래퍼는 펼치기 (interactive 노드면 보존)
    has_interactive_child = any(
        _is_interactive(c.get("role", ""))
        for c in children
    )

    if (
        role in wrapper_roles
        and not name and not value and not description
        and len(children) == 1
        and not has_interactive_child
    ):
        child = children[0]
        child_pruned = _prune_tree(child)
        if child_pruned is not None:
            return child_pruned
        return None

    pruned_children: list[dict[str, Any]] = []
    for child in children:
        pruned = _prune_tree(child)
        if pruned is not None:
            pruned_children.append(pruned)

    result = dict(snapshot)
    result["children"] = pruned_children
    return result


def _has_interactive_descendant(node: dict[str, Any]) -> bool:
    """노드의 서브트리에 상호작용 가능한 요소가 있는지 확인한다."""
    role = node.get("role", "")
    if _is_interactive(role):
        return True
    for child in node.get("children", []):
        if _has_interactive_descendant(child):
            return True
    return False


def _count_descendants(node: dict[str, Any]) -> int:
    """노드의 모든 직간접 자식 수를 센다 (node 자신 제외)."""
    total = 0
    for child in node.get("children", []):
        total += 1 + _count_descendants(child)
    return total


def _collapse_tree(
    tree: dict[str, Any],
    max_tokens: int = MAX_TOKEN_ESTIMATE,
    refs: Optional[dict[int, dict[str, Any]]] = None,
) -> dict[str, Any]:
    """비-인터랙티브 서브트리를 요약 라인으로 접어 토큰을 줄인다.
    
    _prune_tree 로 더 이상 줄일 수 없을 때 호출된다.
    interactive 요소가 전혀 없는 큰 서브트리를 찾아
    '[role] "name" → N children collapsed' 형태로 치환한다.
    """
    for _ in range(20):
        text = "\n".join(_format_tree(tree, refs=refs))
        if _estimate_tokens(text) <= max_tokens:
            return tree

        # collapse 후보 수집: interactive descendant 가 없고 자식이 2개 이상인 노드
        candidates: list[tuple[int, list[int], dict[str, Any]]] = []

        def _find_candidates(
            node: dict[str, Any],
            path: list[int],
        ) -> None:
            children = node.get("children", [])
            if not children:
                return
            # 자식을 먼저 방문 (bottom-up 우선)
            for i, child in enumerate(children):
                _find_candidates(child, path + [i])

            # 자기 자신 평가
            if node.get("_collapsed"):
                return
            if _has_interactive_descendant(node):
                return
            count = len(children)
            if count < 2:
                return
            savings = _count_descendants(node)
            candidates.append((savings, path, node))

        _find_candidates(tree, [])

        if not candidates:
            break  # 더 이상 접을 게 없음

        # savings 큰 순으로 정렬 (가장 큰 서브트리부터 collapse)
        candidates.sort(key=lambda x: x[0], reverse=True)
        _, path, node = candidates[0]

        child_count = len(node.get("children", []))
        node["_collapsed"] = {"count": child_count}
        node["children"] = []

    return tree


def _token_prune(
    snapshot: dict[str, Any],
    max_tokens: int = MAX_TOKEN_ESTIMATE,
    refs: Optional[dict[int, dict[str, Any]]] = None,
) -> dict[str, Any]:
    """토큰 제한을 초과하면 반복적으로 불필요한 노드를 제거한다.
    상호작용 가능한 노드는 최대한 보존한다."""
    tree = snapshot
    for _ in range(8):
        text = "\n".join(_format_tree(tree, refs=refs))
        if _estimate_tokens(text) <= max_tokens:
            return tree
        pruned = _prune_tree(tree) or {"role": "Root", "children": []}
        if pruned is tree:  # 더 이상 _prune_tree가 줄일 게 없으면 collapse 진입
            tree = _collapse_tree(tree, max_tokens, refs=refs)
            break
        tree = pruned
    # collapse 후에도 오버일 수 있으니 최종 체크
    text = "\n".join(_format_tree(tree, refs=refs))
    if _estimate_tokens(text) <= max_tokens:
        return tree
    for _ in range(10):
        text = "\n".join(_format_tree(tree, refs=refs))
        if _estimate_tokens(text) <= max_tokens:
            return tree
        tree = _collapse_tree(tree, max_tokens, refs=refs) or tree
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
        max_tokens: 최대 토큰 제한
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
        (포맷된 텍스트, {ref: {role, name, value, description, tag, ...}})
    """
    refs: dict[int, dict[str, Any]] = {}
    text = format_accessibility_tree(snapshot, max_tokens, refs=refs)
    return text, refs


# ─────────────────── DOM enrichment ───────────────────

_DOM_ENRICH_JS = r"""
() => {
    const results = {};
    const candidates = document.querySelectorAll(
        'button, a, input, select, textarea, [role], [tabindex]'
    );
    candidates.forEach(el => {
        const role = el.getAttribute('role') || el.tagName.toLowerCase();
        const ariaLabel = el.getAttribute('aria-label') || '';
        const placeholder = el.getAttribute('placeholder') || '';
        const text = (el.textContent || '').trim().slice(0, 100);
        const name = ariaLabel || placeholder || text;
        if (!name) return;
        const key = role + '|' + name;
        if (results[key]) return;  // first match wins
        results[key] = {
            tag: el.tagName.toLowerCase(),
            id: el.id || '',
            className: (el.className && typeof el.className === 'string')
                ? el.className.trim().slice(0, 100) : '',
            type: el.getAttribute('type') || '',
            placeholder: placeholder,
            name: el.getAttribute('name') || '',
            aria_label: ariaLabel,
        };
    });
    return results;
};
"""


async def enrich_with_dom_info(page: Page, refs: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """JS injection으로 DOM 속성(tag, id, class, type, placeholder 등)을
    refs 맵에 추가한다. (role, name) 쌍으로 매칭한다.

    Returns:
        동일한 refs 딕셔너리 (in-place + 반환)
    """
    try:
        dom_map: dict[str, dict] = await page.evaluate(_DOM_ENRICH_JS)
    except Exception as e:
        logger.warning("DOM enrichment JS failed: %s", e)
        return refs

    matched = 0
    for ref, info in refs.items():
        role = info.get("role", "")
        name = info.get("name", "")
        key = f"{role}|{name}"
        dom = dom_map.get(key)
        if dom:
            info["tag"] = dom.get("tag", "")
            info["_id"] = dom.get("id", "")
            info["_class"] = dom.get("className", "")
            info["_type"] = dom.get("type", "")
            info["_placeholder"] = dom.get("placeholder", "")
            info["_form_name"] = dom.get("name", "")
            info["_aria_label"] = dom.get("aria_label", "")
            matched += 1

    logger.debug("DOM enrichment: %d/%d refs matched", matched, len(refs))
    return refs


async def get_accessibility_tree(page: Page) -> str:
    """페이지 접근성 트리를 가져온다. (DOM 정보 포함)"""
    text, refs = await get_accessibility_tree_with_refs(page)
    return text


async def get_accessibility_tree_with_refs(
    page: Page,
) -> tuple[str, dict[int, dict[str, Any]]]:
    """접근성 트리 텍스트와 ref 매핑을 함께 가져온다.
    DOM 정보도 함께 수집하여 refs를 풍부하게 만든다.

    Returns:
        (포맷팅된 텍스트, {ref: {role, name, tag, _id, _class, ...}})
    """
    from playwright.async_api import Error as PlaywrightError

    try:
        snapshot = await page.accessibility.snapshot()
    except (AttributeError, PlaywrightError):
        snapshot = None
    except Exception as e:
        logger.warning("accessibility.snapshot() failed: %s", e)
        snapshot = None

    if snapshot is None:
        try:
            return await _get_accessibility_via_cdp_with_refs(page)
        except Exception as e:
            logger.warning("CDP accessibility failed: %s", e)
            text = await _fallback_via_evaluate(page)
            return text, {}

    text, refs = format_accessibility_tree_with_refs(snapshot)
    await enrich_with_dom_info(page, refs)
    return text, refs


# ─────────────────── Detail subtree ───────────────────

async def get_subtree_detail(page: Page, ref: int, refs: dict[int, dict[str, Any]],
                             max_depth: int = 3) -> str:
    """특정 ref의 서브트리를 깊이 3까지 상세 포맷으로 반환한다.

    Args:
        page: Playwright Page
        ref: 대상 ref 번호
        refs: 현재 a11y refs 맵 (browser_snapshot()에서 얻은 것)
        max_depth: 서브트리 최대 깊이

    Returns:
        포맷된 서브트리 텍스트 (태그/속성 등 상세 정보 포함)
    """
    from playwright.async_api import Error as PlaywrightError

    _reset_ref_counter()

    # 1) CDP full tree 얻기
    try:
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Accessibility.enable")
        full_result = await cdp.send("Accessibility.getFullAXTree", {})
        raw_nodes = full_result.get("nodes", [])
    except Exception as e:
        return f"Subtree detail error (CDP): {e}"

    if not raw_nodes:
        return "(No CDP nodes available for subtree detail)"

    # 2) node_map 구축
    node_map: dict[str, dict] = {}
    parent_map: dict[str, Optional[str]] = {}
    for n in raw_nodes:
        nid = n["nodeId"]
        node_map[nid] = n
        parent_map[nid] = n.get("parentId")

    # 3) ref에 해당하는 노드 찾기: refs[ref]의 role+name으로 CDP 노드 매칭
    ref_info = refs.get(ref)
    if not ref_info:
        return f"Subtree detail: ref #{ref} not found. Call browser_snapshot() first."

    target_role = ref_info.get("role", "")
    target_name = ref_info.get("name", "")

    target_node_id: Optional[str] = None
    for nid, n in node_map.items():
        nrole = n.get("role", {}).get("value", "")
        nname = ""
        for prop in n.get("properties", []):
            if prop.get("name") == "name":
                nname = str(prop.get("value", {}).get("value", ""))
                break
        if nrole == target_role and nname == target_name:
            target_node_id = nid
            break

    if not target_node_id:
        return (f"Subtree detail: could not locate ref #{ref} "
                f"({target_role} \"{target_name}\") in CDP tree. "
                "Try calling browser_snapshot() again.")

    # 4) 서브트리 추출
    subtree_snapshot = _cdp_subtree_to_snapshot(target_node_id, node_map)

    # 5) DOM enrichment - CDP DOM.describeNode로 태그/속성 정보 추가
    try:
        await _enrich_cdp_snapshot_with_dom(cdp, subtree_snapshot)
    except Exception as e:
        logger.debug("CDP DOM enrichment failed: %s", e)

    # 6) 포맷 (detail 모드, max_depth 제한)
    _reset_ref_counter()
    lines = _format_tree(subtree_snapshot, detail=True, max_depth=max_depth)
    text = "\n".join(lines)

    if ref_info:
        text = (f"--- Detail for ref #{ref} ({target_role} \"{target_name}\") "
                f"depth ≤ {max_depth} ---\n{text}")

    return text


def _cdp_subtree_to_snapshot(node_id: str, node_map: dict[str, dict]) -> dict[str, Any]:
    """CDP node_id 기준으로 서브트리를 snapshot dict로 변환."""
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
    required: Optional[bool] = None
    readonly: Optional[bool] = None
    multiline: Optional[bool] = None
    level: int = 0

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
        elif pname == "required":
            required = pvalue in ("true", True)
        elif pname == "readonly":
            readonly = pvalue in ("true", True)
        elif pname == "multiline":
            multiline = pvalue in ("true", True)
        elif pname == "level":
            try:
                level = int(pvalue)
            except (ValueError, TypeError):
                pass

    children = []
    for cid in cdp_node.get("childIds", []):
        if cid in node_map:
            children.append(_cdp_subtree_to_snapshot(cid, node_map))

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
    if required is not None:
        node["required"] = required
    if readonly is not None:
        node["readonly"] = readonly
    if multiline is not None:
        node["multiline"] = multiline
    if level > 0:
        node["level"] = level
    return node


async def _enrich_cdp_snapshot_with_dom(cdp_session: Any,
                                        snapshot: dict[str, Any]) -> None:
    """CDP DOM.describeNode로 snapshot 트리의 모든 노드에 태그/속성 정보를 추가한다."""
    async def _enrich(node: dict[str, Any]) -> None:
        backend_id = node.get("_backendNodeId")
        if backend_id:
            try:
                desc = await cdp_session.send("DOM.describeNode", {
                    "backendNodeId": backend_id,
                })
                dom_node = desc.get("node", {})
                node["tag"] = dom_node.get("localName", "").lower()
                # CDP returns attributes as flat list [name1, value1, name2, value2, ...]
                attrs = dom_node.get("attributes", [])
                for i in range(0, len(attrs) - 1, 2):
                    attr_name = attrs[i]
                    attr_val = attrs[i + 1]
                    if attr_name == "id":
                        node["_id"] = attr_val
                    elif attr_name == "class":
                        node["_class"] = attr_val
                    elif attr_name == "type":
                        node["_type"] = attr_val
                    elif attr_name == "placeholder":
                        node["_placeholder"] = attr_val
                    elif attr_name in ("aria-label", "aria_label"):
                        node["_aria_label"] = attr_val
            except Exception:
                pass
        for child in node.get("children", []):
            await _enrich(child)

    await _enrich(snapshot)


# ─── Legacy CDP fallback (getFullAXTree → snapshot) ───

async def _get_accessibility_via_cdp(page: Page) -> str:
    text, _refs = await _get_accessibility_via_cdp_with_refs(page)
    return text


async def _get_accessibility_via_cdp_with_refs(
    page: Page,
) -> tuple[str, dict[int, dict[str, Any]]]:
    """CDP Accessibility domain 직접 활성화 + getFullAXTree + DOM enrichment."""
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Accessibility.enable")
    result = await cdp.send("Accessibility.getFullAXTree", {})
    raw_nodes = result.get("nodes", [])
    if not raw_nodes:
        return "(No accessibility nodes via CDP)", {}

    snapshot = _cdp_nodes_to_snapshot(raw_nodes)
    try:
        await _enrich_cdp_snapshot_with_dom(cdp, snapshot)
    except Exception as e:
        logger.debug("CDP DOM enrichment failed: %s", e)

    return format_accessibility_tree_with_refs(snapshot)


def _cdp_nodes_to_snapshot(nodes: list[dict]) -> dict[str, Any]:
    """CDP Accessibility.getFullAXTree 노드 리스트 → Playwright 호환 snapshot."""
    node_map: dict[str, dict] = {}
    for n in nodes:
        node_map[n["nodeId"]] = n
    # backendNodeId도 저장
    for n in nodes:
        bid = n.get("backendDOMNodeId") or n.get("backendNodeId")
        if bid:
            node_map[n["nodeId"]]["_backendNodeId"] = bid

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
        required: Optional[bool] = None
        readonly: Optional[bool] = None
        multiline: Optional[bool] = None
        level: int = 0

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
            elif pname == "required":
                required = pvalue in ("true", True)
            elif pname == "readonly":
                readonly = pvalue in ("true", True)
            elif pname == "multiline":
                multiline = pvalue in ("true", True)
            elif pname == "level":
                try:
                    level = int(pvalue)
                except (ValueError, TypeError):
                    pass

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
        if required is not None:
            node["required"] = required
        if readonly is not None:
            node["readonly"] = readonly
        if multiline is not None:
            node["multiline"] = multiline
        if level > 0:
            node["level"] = level
        bid = cdp_node.get("_backendNodeId")
        if bid:
            node["_backendNodeId"] = bid
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

            parts.push("=== Page ===");
            parts.push(document.title || "(no title)");
            parts.push("URL: " + (location.href || ""));

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
                parts.push(`  [${role}] "${label}" (<${tag}${el.id ? ' #'+el.id : ''}>)`);
            });

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
