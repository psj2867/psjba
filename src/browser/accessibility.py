"""접근성 트리 포맷팅 — Playwright accessibility snapshot → 계층 텍스트 (고도화)."""

import asyncio
import logging
import time
from typing import Any, Optional

from playwright.async_api import Page

logger = logging.getLogger("psjba.browser.accessibility")

MAX_TOKEN_ESTIMATE = 4000
CHARS_PER_TOKEN = 4

# ref 번호 부여용 카운터 (module-level, session 동안 단조 증가)
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

# 제거/병합 대상 wrapper role들 (lowercase + 기존 mixed-case 병합)
_WRAPPER_ROLES = {
    "none", "None",
    "generic", "Generic",
    "presentation",
    "group",
    "region", "Region",
    "section", "Section",
    "div", "Div",
    "span", "Span",
    "StaticText",
}

# 시맨틱 섹션 라벨 (LLM이 페이지 구조를 빠르게 파악)
_LANDMARK_LABELS = {
    "navigation": "Nav",
    "form": "Form",
    "dialog": "Dialog",
    "main": "Main",
    "banner": "Header",
    "contentinfo": "Footer",
    "complementary": "Sidebar",
    "search": "Search",
    "tabpanel": "TabPanel",
    "region": "Section",
    "article": "Article",
    "alertdialog": "AlertDialog",
    "toolbar": "Toolbar",
    "menubar": "MenuBar",
}


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


def _build_child_summary(children: list[dict[str, Any]]) -> list[str]:
    """자식 노드 리스트의 간결한 요약 문자열 리스트를 생성한다.

    interactive 자식은 [role] "name", 비-interactive는 role로 표시,
    중복은 ×N으로 집계한다.
    """
    from collections import Counter
    items: list[str] = []
    for c in children:
        role = c.get("role", "")
        name = c.get("name", "")
        if _is_interactive(role) and name:
            n = name[:30]
            items.append(f"[{role}] \"{n}\"")
        elif name:
            items.append(role or "?")
        else:
            items.append(role or "?")
    counted: list[tuple[str, int]] = Counter(items).most_common()
    result: list[str] = []
    for item, cnt in counted:
        if cnt > 1:
            result.append(f"{item} ×{cnt}")
        else:
            result.append(item)
    return result


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
        semantic = _LANDMARK_LABELS.get(role.lower())
        label_parts.append(f"[{semantic or role}]")

    # 태그 정보
    tag = node.get("tag", "")
    nid = node.get("_id", "")
    cls = node.get("_class", "")
    if tag:
        attr_parts = [tag]
        if nid:
            attr_parts.append(f"#{nid}")
        if attr_parts:
            label_parts.append(f"<{' '.join(attr_parts)}>")

    if name or node.get("_text_content"):
        children = node.get("children", [])
        if not children:
            # 리프 노드: DOM textContent + accessibility name 둘 다 표시
            dom_text = node.get("_text_content", "")
            if dom_text:
                dt = dom_text.strip()
                if len(dt) > 10:
                    dt = dt[:10] + "..."
                _leaf_text = f'"{dt}"'
            else:
                _leaf_text = ""
            # accessibility name이 DOM text와 다르면 따로 표시
            if name and name.strip() != dom_text.strip():
                an = name.strip()
                if len(an) > 10:
                    an = an[:10] + "..."
                _leaf_text += f' a11y="{an}"' if _leaf_text else f'"{an}"'
            if not _leaf_text:
                _leaf_text = ""
        else:
            # non-leaf: name이 있으면 표시, 없으면 _text_content로 fallback
            if name:
                label_parts.append(f'"{name}"')
            else:
                tc = node.get("_text_content", "").strip()
                if tc:
                    if len(tc) > 15:
                        tc = tc[:15] + "..."
                    label_parts.append(f'"{tc}"')
            _leaf_text = ""
    else:
        _leaf_text = ""

    # image: name/alt 없으면 role만 표시하고 디테일 생략
    is_image = role.lower() in ("image", "img", "graphic", "icon")
    if is_image and not _leaf_text and not name:
        return f"{indent * depth}[{role}] {_leaf_text}".rstrip()

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

    # 접힌(collapsed) 서브트리 표시 — child_summary와 함께
    collapsed = node.get("_collapsed")
    if collapsed:
        count = collapsed.get("count", 0)
        summary = collapsed.get("summary", [])
        if summary:
            label_parts.append(f"→ {', '.join(summary)}")
        else:
            label_parts.append(f"→ {count} children")

    # 모든 노드 중 리프 노드(텍스트 get용)와 interactive(클릭용)에 ref 부여
    if refs is not None:
        is_leaf = not node.get("children")
        if _is_interactive(role) or is_leaf:
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
            "_backendNodeId": node.get("_backendNodeId"),
            "_text_content": (node.get("_text_content", "") or "")[:500],
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

    aria_current = node.get("_aria_current", "")
    if aria_current:
        flags.append(f"current={aria_current}")
    aria_pressed = node.get("aria-pressed")
    if aria_pressed is not None and aria_pressed != "":
        flags.append(f"pressed={aria_pressed}")

    level = node.get("level", 0)
    if detail and level > 0:
        label_parts.append(f"H{level}")

    prefix = indent * depth
    label = " ".join(label_parts)
    if flags:
        label += f" [{', '.join(flags)}]"
    if _leaf_text:
        label += f" {_leaf_text}"

    return f"{prefix}{label}"


async def _format_tree(
    snapshot: dict[str, Any],
    depth: int = 0,
    lines: Optional[list[str]] = None,
    indent: str = "  ",
    refs: Optional[dict[int, dict[str, Any]]] = None,
    detail: bool = False,
    max_depth: int = 999,
) -> list[str]:
    """접근성 스냅샷을 계층적 텍스트 라인 리스트로 변환한다.

    연속된 name/alt 없는 image 노드는 [image]×N 으로 접는다.
    """
    if lines is None:
        lines = []

    if depth > max_depth:
        lines.append(f"{indent * depth}... (depth {depth})")
        return lines

    # 자기 자신 출력 (루트 포함)
    formatted = _format_node(snapshot, depth, indent, refs=refs, detail=detail)
    if formatted.strip():
        lines.append(formatted)

    children = snapshot.get("children", [])
    _IMAGE_ROLES = {"image", "img", "graphic", "icon"}

    # 연속 image 그루핑
    grouped: list[dict[str, Any] | list[dict[str, Any]]] = []
    pending: list[dict[str, Any]] = []
    for child in children:
        role = child.get("role", "").lower()
        name = child.get("name", "")
        if role in _IMAGE_ROLES and not name:
            pending.append(child)
        else:
            if pending:
                grouped.append(pending)
                pending = []
            grouped.append(child)
    if pending:
        grouped.append(pending)

    for item in grouped:
        if depth > 0 and depth % 4 == 0 and len(lines) % 5 == 0:
            await asyncio.sleep(0)

        if isinstance(item, list):
            # 연속 image 그룹 → "[image] ×N" (depth는 부모+1)
            img_role = item[0].get("role", "image")
            n = len(item)
            if n == 1:
                lines.append(f"{indent * (depth + 1)}[{img_role}]")
            else:
                lines.append(f"{indent * (depth + 1)}[{img_role}] ×{n}")
        else:
            await _format_tree(item, depth + 1, lines, indent, refs=refs, detail=detail, max_depth=max_depth)

    return lines


async def _prune_tree(snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    """불필요한 노드를 제거하고 단일-자식 체인을 병합한다.

    1. 내용 없는 StaticText 제거
    2. 이름/값/설명이 없고 자식도 없는 wrapper 노드 제거
    3. 단일 자식만 가진 wrapper는 자식으로 대체 (체인 병합)
    4. prune 후 빈 wrapper는 재검사하여 제거
    """
    role = snapshot.get("role", "")
    name = snapshot.get("name", "")
    value = snapshot.get("value", "")
    description = snapshot.get("description", "")
    children = snapshot.get("children", [])

    # 내용 없는 StaticText 제거
    if role == "StaticText" and not name.strip():
        return None

    # 자식도 없고 이름도 없는 쓸모없는 컨테이너 제거
    if role in _WRAPPER_ROLES and not name and not value and not description:
        if not children:
            return None

    # 순수 레이아웃 컨테이너 — class 유무와 관계없이 자식을 한 단계 끌어올림
    is_pure_container = (
        role in ("none", "None", "generic", "Generic")
        and not name and not value and not description
        and not snapshot.get("_id")
        and bool(children)
    )
    if is_pure_container:
        results = await asyncio.gather(
            *(_prune_tree(c) for c in children),
        )
        pruned_all = [r for r in results if r is not None]
        if len(pruned_all) == 1:
            return pruned_all[0]
        if pruned_all:
            snapshot["role"] = role
            snapshot["children"] = pruned_all
            snapshot.pop("_class", None)
            snapshot.pop("tag", None)
            return snapshot
        return None

    # 단일 자식만 가진 wrapper 노드는 펼치기 (interactive 자식 없을 때)
    has_interactive_child = any(
        _is_interactive(c.get("role", ""))
        for c in children
    )

    if (
        role in _WRAPPER_ROLES
        and not name and not value and not description
        and len(children) == 1
        and not has_interactive_child
    ):
        child = children[0]
        child_pruned = await _prune_tree(child)
        if child_pruned is not None:
            return child_pruned
        return None

    # 일반 노드: 자식들을 병렬로 prune
    results = await asyncio.gather(
        *(_prune_tree(c) for c in children),
    )
    pruned_children = [r for r in results if r is not None]

    # --- post-prune 재검사: 자식이 모두 제거되어 빈 wrapper가 된 경우 제거 ---
    if (
        role in _WRAPPER_ROLES
        and not name and not value and not description
        and not pruned_children
    ):
        return None

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


async def _collapse_tree(
    tree: dict[str, Any],
    max_tokens: int = MAX_TOKEN_ESTIMATE,
    refs: Optional[dict[int, dict[str, Any]]] = None,
) -> dict[str, Any]:
    """비-인터랙티브 서브트리를 요약 라인으로 접어 토큰을 줄인다.

    _prune_tree 로 더 이상 줄일 수 없을 때 호출된다.
    interactive 요소가 전혀 없는 서브트리를 찾아
    '→ [role] "name", [role] ×N' 형태로 치환한다.
    """
    for iteration in range(20):
        await asyncio.sleep(0)
        text = "\n".join(await _format_tree(tree, refs=refs))
        if _estimate_tokens(text) <= max_tokens:
            return tree

        candidates: list[tuple[int, list[int], dict[str, Any]]] = []

        def _find_candidates(
            node: dict[str, Any],
            path: list[int],
        ) -> None:
            children = node.get("children", [])
            if not children:
                return
            # 자식을 먼저 방문 (bottom-up)
            for i, child in enumerate(children):
                _find_candidates(child, path + [i])

            # 자기 자신 평가
            if node.get("_collapsed"):
                return
            if _has_interactive_descendant(node):
                return
            savings = _count_descendants(node)
            candidates.append((savings, path, node))

        _find_candidates(tree, [])

        if not candidates:
            break  # 더 이상 접을 게 없음

        # savings 큰 순으로 정렬
        candidates.sort(key=lambda x: x[0], reverse=True)
        _, path, node = candidates[0]

        children = node.get("children", [])
        summary = _build_child_summary(children)
        node["_collapsed"] = {"count": len(children), "summary": summary}
        node["children"] = []

    return tree


async def format_accessibility_tree(
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

    t0 = time.monotonic()
    tree = await _prune_tree(snapshot) or {"role": "Root", "children": []}
    t1 = time.monotonic()
    logger.debug("prune_tree: %dms (tree: %d nodes→%d nodes)",
                 int((t1 - t0) * 1000),
                 _count_descendants(snapshot),
                 _count_descendants(tree))

    if max_tokens > 0:
        tree = await _collapse_tree(tree, max_tokens, refs=refs)
        t2 = time.monotonic()
        logger.debug("collapse: %dms (tree: %d nodes)",
                     int((t2 - t1) * 1000),
                     _count_descendants(tree))
    else:
        t2 = t1

    lines = await _format_tree(tree, refs=refs, max_depth=10)
    t3 = time.monotonic()
    logger.debug("format_tree: %dms (%d lines)",
                 int((t3 - t2) * 1000), len(lines))
    text = "\n".join(lines)

    token_count = _estimate_tokens(text)
    if token_count > max_tokens:
        truncated_lines = lines
        while truncated_lines and _estimate_tokens("\n".join(truncated_lines)) > max_tokens:
            truncated_lines.pop()
        text = "\n".join(truncated_lines)
        text += f"\n... (truncated, ~{_estimate_tokens(text)} tokens)"

    logger.debug("format_accessibility_tree total: %dms, %d tokens",
                 int((time.monotonic() - t0) * 1000), _estimate_tokens(text))
    return text


async def format_accessibility_tree_with_refs(
    snapshot: Optional[dict[str, Any]],
    max_tokens: int = MAX_TOKEN_ESTIMATE,
) -> tuple[str, dict[int, dict[str, Any]]]:
    """접근성 트리를 포맷하고 ref 번호 → 노드 정보 매핑도 함께 반환한다.

    Returns:
        (포맷된 텍스트, {ref: {role, name, value, description, tag, ...}})
    """
    refs: dict[int, dict[str, Any]] = {}
    text = await format_accessibility_tree(snapshot, max_tokens, refs=refs)
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

    CDP DOM.describeNode로 전체 트리에 속성(tag, class, id, name 등)을
    심어서 _format_node에서 <tag .class #id name=...> 형태로 출력한다.

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

    # CDP 세션으로 snapshot 트리에 DOM 속성(name, class, id 등) 심기
    try:
        cdp = await page.context.new_cdp_session(page)
        await _enrich_cdp_snapshot_with_dom(cdp, snapshot)
    except Exception as e:
        logger.debug("CDP tree enrichment failed (non-fatal): %s", e)

    text, refs = await format_accessibility_tree_with_refs(snapshot)
    return text, refs


# ─────────────────── Detail subtree ───────────────────

async def get_subtree_detail(page: Page, ref: int, refs: dict[int, dict[str, Any]]) -> str:
    """특정 ref의 서브트리를 전체 a11y snapshot 포맷 기준으로 반환한다.

    browser_snapshot()과 동일한 _prune_tree → _collapse_tree 파이프라인을 태워
    토큰 제한 내에서 최대한 많은 정보를 보여준다.

    Args:
        page: Playwright Page
        ref: 대상 ref 번호
        refs: 현재 a11y refs 맵 (browser_snapshot()에서 얻은 것)

    Returns:
        포맷된 서브트리 텍스트
    """
    from playwright.async_api import Error as PlaywrightError

    ref_info = refs.get(ref)
    if not ref_info:
        return f"Subtree detail: ref #{ref} not found. Call browser_snapshot() first."

    target_role = ref_info.get("role", "")
    target_name = ref_info.get("name", "")

    # Fresh snapshot + enrich
    try:
        snapshot = await page.accessibility.snapshot()
    except (AttributeError, PlaywrightError):
        snapshot = None
    except Exception as e:
        logger.warning("get_subtree_detail accessibility.snapshot() failed: %s", e)
        snapshot = None

    if snapshot is None:
        try:
            snapshot = await _get_cdp_snapshot(page)
        except Exception as e:
            return f"Subtree detail error: {e}"

    if snapshot is None:
        return "(No accessibility tree available)"

    # ref에 해당하는 서브트리 찾기
    subtree = _find_ref_subtree(snapshot, target_role, target_name)
    if subtree is None:
        return (f"Subtree detail: could not locate ref #{ref} "
                f"({target_role} \"{target_name}\") in current snapshot. "
                "Try calling browser_snapshot() again.")

    # 동일한 포맷 파이프라인 적용 (refs 미할당 — detail view는 참고용)
    t0 = time.monotonic()
    pruned = (await _prune_tree(subtree)) or subtree
    t1 = time.monotonic()
    logger.debug("get_subtree_detail prune_tree: %dms (ref=%d)",
                 int((t1 - t0) * 1000), ref)
    collapsed = await _collapse_tree(pruned, refs=refs)
    t2 = time.monotonic()
    logger.debug("get_subtree_detail collapse: %dms (ref=%d)",
                 int((t2 - t1) * 1000), ref)
    lines = await _format_tree(collapsed, refs=refs) if collapsed else []
    t3 = time.monotonic()
    logger.debug("get_subtree_detail format_tree: %dms (ref=%d)",
                 int((t3 - t2) * 1000), ref)
    text = "\n".join(lines)
    text = (f"--- Detail for ref #{ref} ({target_role} \"{target_name}\") ---\n{text}")

    logger.debug("get_subtree_detail total: %dms (ref=%d)",
                 int((time.monotonic() - t0) * 1000), ref)
    return text


def _find_ref_subtree(
    snapshot: dict[str, Any],
    target_role: str,
    target_name: str,
) -> Optional[dict[str, Any]]:
    """snapshot에서 role+name이 일치하는 서브트리를 찾는다."""
    if snapshot.get("role") == target_role and snapshot.get("name", "") == target_name:
        return snapshot
    for child in snapshot.get("children", []):
        found = _find_ref_subtree(child, target_role, target_name)
        if found:
            return found
    return None


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
    """CDP DOM.describeNode로 snapshot 트리의 모든 노드에 태그/속성 정보를 추가한다.

    Playwright snapshot(native)과 CDP snapshot 모두에서 동작:
    - Playwright: backendDOMNodeId → _backendNodeId로 정규화
    - CDP: _backendNodeId 직접 보유
    """
    # backendDOMNodeId → _backendNodeId 정규화
    async def _normalize(node):
        bid = node.get("backendDOMNodeId")
        if bid:
            node["_backendNodeId"] = bid
        for child in node.get("children", []):
            await _normalize(child)

    async def _enrich_all(node):
        await _enrich_node(cdp_session, node)
        for child in node.get("children", []):
            await _enrich_all(child)

    await _normalize(snapshot)
    await _enrich_all(snapshot)


async def _enrich_node(cdp_session: Any, node: dict[str, Any]) -> None:
    """단일 노드의 DOM 속성과 텍스트를 CDP describeNode로 채운다."""
    backend_id = node.get("_backendNodeId")
    if not backend_id:
        return
    try:
        desc = await cdp_session.send("DOM.describeNode", {
            "backendNodeId": backend_id,
        })
        dom_node = desc.get("node", {})
        node["tag"] = dom_node.get("localName", "").lower()
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
            elif attr_name == "name":
                node["_form_name"] = attr_val
            elif attr_name == "aria-current":
                node["_aria_current"] = attr_val
            elif attr_name == "aria-pressed":
                node["aria-pressed"] = attr_val
            elif attr_name == "aria-haspopup":
                node["_aria_haspopup"] = attr_val

        # DOM getOuterHTML로 순수 텍스트 추출 (모든 노드 시도)
        try:
            html_resp = await cdp_session.send("DOM.getOuterHTML", {
                "backendNodeId": backend_id,
            })
            outer = html_resp.get("outerHTML", "")
            if outer:
                import re
                text = re.sub(r'<[^>]+>', '', outer).strip()
                text = ' '.join(text.split())  # 연속 공백 제거
                if text:
                    node["_text_content"] = text[:200]  # 최대 200자
        except Exception:
            pass
    except Exception:
        pass


# ─── Legacy CDP fallback (getFullAXTree → snapshot) ───

async def _get_accessibility_via_cdp(page: Page) -> str:
    text, _refs = await _get_accessibility_via_cdp_with_refs(page)
    return text


async def _get_cdp_snapshot(page: Page) -> Optional[dict[str, Any]]:
    """CDP Accessibility domain으로 raw snapshot dict를 가져온다.

    page.accessibility.snapshot()이 실패할 때 fallback으로 사용.
    """
    try:
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Accessibility.enable")
        result = await cdp.send("Accessibility.getFullAXTree", {})
        raw_nodes = result.get("nodes", [])
        if not raw_nodes:
            return None
        snapshot = _cdp_nodes_to_snapshot(raw_nodes)
        try:
            await _enrich_cdp_snapshot_with_dom(cdp, snapshot)
        except Exception as e:
            logger.debug("CDP DOM enrichment failed: %s", e)
        return snapshot
    except Exception as e:
        logger.warning("CDP snapshot failed: %s", e)
        return None


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

    return await format_accessibility_tree_with_refs(snapshot)


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
            elif pname == "current":
                node["_aria_current"] = str(pvalue)
            elif pname == "pressed":
                pv = str(pvalue)
                if pv in ("true", True):
                    node["aria-pressed"] = "true"
                elif pv in ("false", False):
                    node["aria-pressed"] = "false"
                elif pv == "mixed":
                    node["aria-pressed"] = "mixed"

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
        if cdp_node.get("_aria_current"):
            node["_aria_current"] = cdp_node["_aria_current"]
        if cdp_node.get("aria-pressed"):
            node["aria-pressed"] = cdp_node["aria-pressed"]
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


async def _fallback_via_evaluate(page: Page, max_tokens: int = 2000) -> str:
    """page.evaluate() 로 DOM 기반 접근성 정보를 추출한다.

    _get_accessibility_via_cdp_with_refs와 동일한 format_accessibility_tree 파이프라인을 태운다.
    """
    try:
        js = r"""
        () => {
            const root = {
                role: "Root",
                name: document.title || "",
                children: []
            };
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
            document.querySelectorAll(selectors.join(",")).forEach(el => {
                const tag = el.tagName.toLowerCase();
                const text = (el.textContent || "").trim().slice(0, 100);
                const aria = el.getAttribute("aria-label") || "";
                const name = aria || text;
                if (!name || seen.has(name)) return;
                seen.add(name);
                const role = el.getAttribute("role") || tag;
                const node = {
                    role: role,
                    name: name,
                    tag: tag,
                    _id: el.id || "",
                    _class: el.className || "",
                };
                root.children.push(node);
            });
            return JSON.stringify(root);
        }
        """
        json_str = await page.evaluate(js)
        snapshot = json.loads(json_str)

        text, _refs = await format_accessibility_tree_with_refs(snapshot, max_tokens=max_tokens)
        return text or "(Empty page)"
    except Exception as e:
        logger.error("Fallback evaluate failed: %s", e)
        return f"(Error getting page structure: {e})"
