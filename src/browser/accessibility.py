"""접근성 트리 포맷팅 — Playwright accessibility snapshot → 계층 텍스트 (고도화)."""

import asyncio
import json
import logging
from typing import Any, Optional

from playwright.async_api import Page

logger = logging.getLogger("psjba.browser.accessibility")

# 트리 포맷 표시용 텍스트 최대 길이
TEXT_DISPLAY_LEN = 10

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
            # 리프 노드: a11y name(필수) + DOM text(보조)
            an = name.strip() if name else ""
            if len(an) > 10:
                an = an[:10] + "..."

            dt = node.get("_text_content", "").strip()
            parts = []
            if an:
                parts.append(f'"{an}"')
            if dt:
                parts.append(f'text="{dt}"')
            _leaf_text = " ".join(parts)
        else:
            # non-leaf: a11y name 표시 (필수)
            if name:
                an = name.strip()
                if len(an) > 10:
                    an = an[:10] + "..."
                label_parts.append(f'"{an}"')
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
            psjba_id = node.get("__psjba_id", "")
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
            "_text_content": node.get("_text_content", "") or "",
            "_nth_of_type": node.get("_nth_of_type", 0),
            "__psjba_id": psjba_id,
        }
            if psjba_id:
                refs[ref]["_selector"] = f'[data-psjba-id="{psjba_id}"]'

    # detail 모드에서 추가 정보
    auto = node.get("_autocomplete", "")
    if detail and auto:
        label_parts.append(f"autocomplete={auto}")
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

    # 나머지 _aria_*, aria-* 속성 자동 수집 (위에서 처리 안된 것들)
    for k, v in node.items():
        if not v and v is not False and v != 0:
            continue
        if k in ("_aria_label", "_aria_current", "aria-pressed",
                 "checked", "disabled", "focused", "expanded",
                 "selected", "required", "readonly", "multiline"):
            continue
        if k.startswith("_aria_") or k.startswith("aria-"):
            display = k.removeprefix("_aria_").removeprefix("aria-")
            flags.append(f"{display}={v}")

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

    # 같은 부모 아래 (tag, role) 쌍별 sibling count — nth-of-type 용
    type_counts: dict[tuple[str, str], int] = {}
    for child in children:
        cr = child.get("role", "")
        ct = (child.get("tag", "") or child.get("_tag", "")).lower()
        if cr or ct:
            key = (ct, cr)
            type_counts[key] = type_counts.get(key, 0) + 1

    # nth-of-type 인덱스를 각 child dict에 기록
    type_used: dict[tuple[str, str], int] = {}
    for child in children:
        cr = child.get("role", "")
        ct = (child.get("tag", "") or child.get("_tag", "")).lower()
        key = (ct, cr)
        if type_counts.get(key, 0) > 1:
            type_used[key] = type_used.get(key, 0) + 1
            child["_nth_of_type"] = type_used[key]
        else:
            child["_nth_of_type"] = 0

    _IMAGE_ROLES = {"image", "img", "graphic", "icon"}

    # 연속 그루핑 (image + text leaf)
    def _is_text_leaf(n):
        if n.get("children"):
            return False
        if _is_interactive(n.get("role", "")):
            return False
        return bool(n.get("_text_content")) or bool(n.get("name"))

    def _text_leaf_key(n):
        return ((n.get("tag", "") or n.get("_tag", "")).lower(),
                n.get("role", "").lower())

    grouped: list[dict[str, Any] | list[dict[str, Any]]] = []
    img_pending: list[dict[str, Any]] = []
    text_pending: list[dict[str, Any]] = []
    for child in children:
        role = child.get("role", "").lower()
        name = child.get("name", "")
        if role in _IMAGE_ROLES and not name:
            if text_pending:
                grouped.append(text_pending)
                text_pending = []
            img_pending.append(child)
        else:
            if img_pending:
                grouped.append(img_pending)
                img_pending = []
            if _is_text_leaf(child):
                key = _text_leaf_key(child)
                if not text_pending or key != _text_leaf_key(text_pending[0]):
                    if text_pending:
                        grouped.append(text_pending)
                    text_pending = [child]
                else:
                    text_pending.append(child)
            else:
                if text_pending:
                    grouped.append(text_pending)
                    text_pending = []
                grouped.append(child)
    if img_pending:
        grouped.append(img_pending)
    if text_pending:
        grouped.append(text_pending)

    for item in grouped:
        if depth > 0 and depth % 4 == 0 and len(lines) % 5 == 0:
            await asyncio.sleep(0)

        if isinstance(item, list):
            first = item[0]
            role = first.get("role", "").lower()
            if role in _IMAGE_ROLES:
                # 연속 image 그룹 → "[image] ×N"
                img_role = first.get("role", "image")
                n = len(item)
                if n == 1:
                    lines.append(f"{indent * (depth + 1)}[{img_role}]")
                else:
                    lines.append(f"{indent * (depth + 1)}[{img_role}] ×{n}")
            else:
                # 연속 text leaf 그룹 (code, paragraph 등)
                tag = (first.get("tag", "") or first.get("_tag", "") or "").lower()
                role_display = first.get("role", "")
                ref_min = ref_max = 0
                texts: list[str] = []
                for leaf_node in item:
                    ref = _next_ref()
                    if not ref_min:
                        ref_min = ref
                    ref_max = ref
                    if refs is not None:
                        psjba_id = leaf_node.get("__psjba_id", "")
                        refs[ref] = {
                            "role": leaf_node.get("role", ""),
                            "name": leaf_node.get("name", ""),
                            "value": leaf_node.get("value", ""),
                            "tag": leaf_node.get("tag", ""),
                            "_id": leaf_node.get("_id", ""),
                            "_class": leaf_node.get("_class", ""),
                            "_type": leaf_node.get("_type", ""),
                            "_placeholder": leaf_node.get("_placeholder", ""),
                            "_form_name": leaf_node.get("_form_name", ""),
                            "_aria_label": leaf_node.get("_aria_label", ""),
                            "_backendNodeId": leaf_node.get("_backendNodeId"),
                            "_text_content": leaf_node.get("_text_content", "") or "",
                            "_nth_of_type": leaf_node.get("_nth_of_type", 0),
                            "__psjba_id": psjba_id,
                        }
                        if psjba_id:
                            refs[ref]["_selector"] = f'[data-psjba-id="{psjba_id}"]'
                    tc = leaf_node.get("_text_content", "").strip()
                    if tc:
                        texts.append(tc)
                parts = [f"[{role_display}]"]
                if tag:
                    parts.append(f"<{tag}>")
                parts.append(f"[#{ref_min}-{ref_max}]")
                if texts:
                    merged = ", ".join(texts)
                    if len(merged) > 60:
                        merged = merged[:60] + "..."
                    parts.append(f'texts="{merged}"')
                lines.append(f"{indent * (depth + 1)}{' '.join(parts)}")
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
        and not value and not description
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
            # pure container는 name 버리고 children만 유지 (role도 유지)
            snapshot["children"] = pruned_all
            snapshot.pop("name", None)
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

    Phase 1: 최소 20회, 최대 30회 반복하며 non-interactive 서브트리를
    '→ [role] "name", [role] ×N' 형태로 치환한다.
    Phase 2: 30회로도 부족하면 하단부터 깊은 순으로 collapse하여
    리프 텍스트가 보이도록 하고 'more below' 표시를 추가한다.
    """
    # ── Phase 1: 최소 20 / 최대 30 반복 ──────────────────
    for iteration in range(30):
        await asyncio.sleep(0)
        text = "\n".join(await _format_tree(tree, refs=refs))
        if _estimate_tokens(text) <= max_tokens and iteration >= 20:
            return tree

        candidates: list[tuple[int, list[int], dict[str, Any]]] = []

        def _find_candidates(
            node: dict[str, Any],
            path: list[int],
        ) -> None:
            children = node.get("children", [])
            if not children:
                return
            for i, child in enumerate(children):
                _find_candidates(child, path + [i])
            if node.get("_collapsed"):
                return
            if _has_interactive_descendant(node):
                return
            candidates.append((_count_descendants(node), path, node))

        _find_candidates(tree, [])

        if not candidates:
            break  # 더 이상 접을 게 없음

        candidates.sort(key=lambda x: x[0], reverse=True)
        _, path, node = candidates[0]

        children = node.get("children", [])
        summary = _build_child_summary(children)
        node["_collapsed"] = {"count": len(children), "summary": summary}
        node["children"] = []

    # ── Phase 2: 하단부터 깊은 순 fallback collapse ────
    text = "\n".join(await _format_tree(tree, refs=refs))
    if _estimate_tokens(text) <= max_tokens:
        return tree

    # 하단(깊은 depth) non-interactive 노드부터 collapse
    bottom_up: list[tuple[int, list[int], dict[str, Any]]] = []

    def _collect_bottom_up(node, path, depth):
        children = node.get("children", [])
        if not children:
            return
        for i, child in enumerate(children):
            _collect_bottom_up(child, path + [i], depth + 1)
        if node.get("_collapsed"):
            return
        if _has_interactive_descendant(node):
            return
        bottom_up.append((depth, path, node))

    _collect_bottom_up(tree, [], 0)
    bottom_up.sort(key=lambda x: x[0], reverse=True)  # deep first

    for _depth, _path, node in bottom_up:
        children = node.get("children", [])
        if not children:
            continue
        summary = _build_child_summary(children)
        node["_collapsed"] = {"count": len(children), "summary": summary}
        node["children"] = []
        text = "\n".join(await _format_tree(tree, refs=refs))
        if _estimate_tokens(text) <= max_tokens:
            return tree

    return tree


def _truncate_by_token(text: str, max_tokens: int = MAX_TOKEN_ESTIMATE) -> str:
    """토큰 추정치에 기반하여 텍스트를 자른다."""
    if _estimate_tokens(text) <= max_tokens:
        return text
    lines = text.split('\n')
    while lines and _estimate_tokens('\n'.join(lines)) > max_tokens:
        lines.pop()
    text = '\n'.join(lines)
    text += "\n... (more content below — call browser_snapshot(ref=N) to drill into child refs)"
    return text


# ─── Public API ──────────────────────────────────


async def fetch_full_tree(page: Page) -> dict[str, Any]:
    """getFullAXTree + enrichment까지 완료한 full enriched tree를 반환한다.

    manager가 session_id별로 보관하고, subtree detail/재사용에 사용한다.
    """
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Accessibility.enable")
    result = await cdp.send("Accessibility.getFullAXTree", {})
    raw_nodes = result.get("nodes", [])
    if not raw_nodes:
        return {"role": "Root", "name": "", "children": []}

    tree = _cdp_nodes_to_snapshot(raw_nodes)
    await _enrich_via_resolve_gather(page, tree)
    return tree


async def format_tree(tree: dict[str, Any]) -> tuple[str, dict[int, dict[str, Any]]]:
    """full enriched tree → prune/collapse/format → (text, refs).

    refs는 interactive/리프 노드의 맵 (browser_click, browser_get_text 등에서 사용).
    """
    _reset_ref_counter()
    pruned = (await _prune_tree(tree)) or tree
    collapsed = await _collapse_tree(pruned)
    refs: dict[int, dict[str, Any]] = {}
    lines = await _format_tree(collapsed, refs=refs) if collapsed else []
    text = "\n".join(lines)
    text = _truncate_by_token(text)
    return text, refs


async def get_accessibility_tree(page: Page) -> str:
    """페이지 접근성 트리를 가져온다. (DOM 정보 포함)"""
    text, refs = await get_accessibility_tree_with_refs(page)
    return text


async def get_accessibility_tree_with_refs(
    page: Page,
) -> tuple[str, dict[int, dict[str, Any]]]:
    """접근성 트리 텍스트와 ref 매핑을 함께 가져온다.

    CDP Accessibility.getFullAXTree → resolveNode enrichment → format.
    CDP가 실패하면 빈 결과를 반환한다.

    Returns:
        (포맷팅된 텍스트, {ref: {role, name, tag, _id, _class, ...}})
    """
    try:
        tree = await fetch_full_tree(page)
        return await format_tree(tree)
    except Exception as e:
        logger.warning("CDP accessibility failed: %s", e)
        return f"(Accessibility error: {e})", {}


# ─── Subtree detail ──────────────────────────────


def _find_ref_subtree(
    tree: dict[str, Any],
    target_role: str,
    target_name: str,
) -> Optional[dict[str, Any]]:
    """tree에서 role+name이 일치하는 첫 번째 서브트리를 찾는다."""
    if (tree.get("role") == target_role and tree.get("name") == target_name):
        return tree
    for child in tree.get("children", []):
        found = _find_ref_subtree(child, target_role, target_name)
        if found:
            return found
    return None


def _find_node_by_psjba_id(tree: dict[str, Any], psjba_id: str) -> Optional[dict[str, Any]]:
    """full enriched tree에서 __psjba_id가 일치하는 노드를 찾아 반환한다."""
    if tree.get("__psjba_id") == psjba_id:
        return tree
    for child in tree.get("children", []):
        found = _find_node_by_psjba_id(child, psjba_id)
        if found:
            return found
    return None


def format_subtree_detail(tree: dict[str, Any], ref_info: dict[str, Any]) -> str:
    """저장된 full tree + ref_info로 서브트리 detail 텍스트를 반환한다.

    Args:
        tree: fetch_full_tree()로 얻은 enriched tree
        ref_info: refs 맵에 저장된 노드 정보 (__psjba_id, role, name)

    Returns:
        포맷된 서브트리 텍스트
    """
    target_role = ref_info.get("role", "")
    target_name = ref_info.get("name", "")

    # 1차: __psjba_id로 정확히 찾기
    psjba_id = ref_info.get("__psjba_id", "")
    if psjba_id:
        subtree = _find_node_by_psjba_id(tree, psjba_id)
        if subtree is not None:
            lines = [_format_node(subtree, 0, refs=None, detail=True)]
            for child in subtree.get("children", []):
                _collect_lines(child, 1, lines, detail=True)
            text = "\n".join(lines)
            sel = ref_info.get("_selector", "")
            sel_line = f"\nselector = {sel}" if sel else ""
            return (f"--- Detail for ref #{ref_info.get('_ref', '?')} "
                    f"({target_role} \"{target_name}\") ---\n{text}{sel_line}")

    # 2차: (role, name) match (fallback)
    subtree = _find_ref_subtree(tree, target_role, target_name)
    if subtree is None:
        return (f"Subtree detail: could not locate ref "
                f"({target_role} \"{target_name}\") in tree. "
                "Try browser_snapshot() again.")

    lines = [_format_node(subtree, 0, refs=None, detail=True)]
    for child in subtree.get("children", []):
        _collect_lines(child, 1, lines, detail=True)
    text = "\n".join(lines)
    sel = ref_info.get("_selector", "")
    if sel:
        text += f"\nselector = {sel}"
    return (f"--- Detail for ref #{ref_info.get('_ref', '?')} "
            f"({target_role} \"{target_name}\") ---\n{text}")


def _collect_lines(node: dict[str, Any], depth: int, lines: list[str], detail: bool = False) -> None:
    """재귀적으로 노드를 포맷하여 라인 리스트에 추가한다."""
    lines.append(_format_node(node, depth, refs=None, detail=detail))
    for child in node.get("children", []):
        _collect_lines(child, depth + 1, lines, detail=detail)


# ─── Enrichment: resolveNode + callFunctionOn gather ───

_ENRICH_FN = rf"""function() {{
    function getFastNodeText(n) {{
        if (!n) return '';
        if (n.nodeType === Node.TEXT_NODE) return n.nodeValue || '';
        if (n.nodeType !== Node.ELEMENT_NODE) return '';
        var childs = n.childNodes, l = childs.length;
        if (l === 0) return n.textContent.trim();
        if (l === 1) return getFastNodeText(childs[0]);
        var r = '';
        for (var i = 0; i < l; i++) {{
            var t = getFastNodeText(childs[i]);
            if (t) {{
                if (r && !r.endsWith(' ') && !t.startsWith(' ')) r += ' ';
                r += t;
            }}
        }}
        return r.replace(/\s+/g, ' ').trim();
    }}
    var uid = (Math.random().toString(36) + Math.random().toString(36)).slice(2, 10);
    this.setAttribute('data-psjba-id', uid);
    var ariaAttrs = {{}};
    for (var i = 0; i < this.attributes.length; i++) {{
        var a = this.attributes[i];
        if (a.name.indexOf('aria-') === 0) ariaAttrs[a.name] = a.value;
    }}
    var t = getFastNodeText(this);
    return {{
        uid: uid,
        tag: this.tagName.toLowerCase(),
        id: this.id || '',
        'class': this.className || '',
        type: this.getAttribute('type') || '',
        placeholder: this.getAttribute('placeholder') || '',
        aria_label: this.getAttribute('aria-label') || '',
        name: this.getAttribute('name') || '',
        textContent: t.length > {TEXT_DISPLAY_LEN} ? t.substring(0, {TEXT_DISPLAY_LEN}) + '...' : t,
        ariaAttrs: JSON.stringify(ariaAttrs)
    }};
}}"""


async def _enrich_via_resolve_gather(
    page: Page,
    snapshot: dict[str, Any],
) -> None:
    """snapshot 트리의 모든 a11y 노드를 resolveNode → callFunctionOn gather로
    enrichment한다.

    각 노드에 대해:
    1. DOM.resolveNode(backendNodeId) → objectId 확보
    2. Runtime.callFunctionOn으로 data-psjba-id 주입 + 정보 수집 (한 번에)
    3. Runtime.releaseObject(objectId) 해제

    Returns:
        None (snapshot 노드를 in-place로 수정)
    """
    # backendNodeId 수집
    all_ids: list[int] = []

    async def _normalize(node):
        bid = node.get("backendDOMNodeId")
        if bid:
            node["_backendNodeId"] = bid
        for child in node.get("children", []):
            await _normalize(child)

    async def _collect_ids(node):
        bid = node.get("_backendNodeId")
        if bid:
            all_ids.append(bid)
        for child in node.get("children", []):
            await _collect_ids(child)

    await _normalize(snapshot)
    await _collect_ids(snapshot)

    if not all_ids:
        return

    cdp = await page.context.new_cdp_session(page)

    async def _resolve_and_fetch(bid: int) -> Optional[dict[str, Any]]:
        try:
            resolved = await cdp.send("DOM.resolveNode", {"backendNodeId": bid})
            obj_id = resolved["object"]["objectId"]
            eval_res = await cdp.send("Runtime.callFunctionOn", {
                "objectId": obj_id,
                "functionDeclaration": _ENRICH_FN,
                "returnByValue": True,
            })
            await cdp.send("Runtime.releaseObject", {"objectId": obj_id})
            return eval_res["result"]["value"]
        except Exception:
            return None

    results = await asyncio.gather(*[_resolve_and_fetch(bid) for bid in all_ids],
                                   return_exceptions=False)

    # backendNodeId → enrichment info 맵
    bid_to_info: dict[int, dict] = {}
    for bid, res in zip(all_ids, results):
        if res and isinstance(res, dict) and res.get("uid"):
            bid_to_info[bid] = res

    # snapshot 노드에 속성 채우기
    async def _fill_node(node):
        bid = node.get("_backendNodeId")
        info = bid_to_info.get(bid) if bid else None
        if info:
            node["__psjba_id"] = info["uid"]
            node["tag"] = info.get("tag", "")
            node["_id"] = info.get("id", "")
            node["_class"] = info.get("class", "")
            node["_type"] = info.get("type", "")
            node["_placeholder"] = info.get("placeholder", "")
            node["_aria_label"] = info.get("aria_label", "")
            node["_form_name"] = info.get("name", "")
            tc = info.get("textContent", "")
            if tc:
                node["_text_content"] = tc

            # ariaAttrs JSON 파싱
            aria_raw = info.get("ariaAttrs", "")
            if aria_raw:
                try:
                    aria_map = json.loads(aria_raw)
                    for akey, aval in aria_map.items():
                        if akey == "aria-label":
                            continue
                        if akey == "aria-pressed":
                            node["aria-pressed"] = aval
                            continue
                        norm_key = "_" + akey.replace("-", "_")
                        node[norm_key] = aval
                except json.JSONDecodeError:
                    pass
        for child in node.get("children", []):
            await _fill_node(child)

    await _fill_node(snapshot)
    logger.debug("_enrich_via_resolve_gather: %d/%d nodes enriched",
                 len(bid_to_info), len(all_ids))


# ─── CDP raw nodes → Playwright 호환 snapshot 변환 ───


def _cdp_nodes_to_snapshot(nodes: list[dict]) -> dict[str, Any]:
    """CDP Accessibility.getFullAXTree 노드 리스트 → Playwright 호환 snapshot.

    a11y 속성(role, name, value, description, flags)만 추출하고,
    DOM 속성(tag, class, aria-* 등)은 _enrich_via_resolve_gather에서 채운다.
    """
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
        aria_current = ""
        aria_pressed = ""

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
                aria_current = str(pvalue)
            elif pname == "pressed":
                pv = str(pvalue)
                if pv in ("true", True):
                    aria_pressed = "true"
                elif pv in ("false", False):
                    aria_pressed = "false"
                elif pv == "mixed":
                    aria_pressed = "mixed"

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
        if aria_current:
            node["_aria_current"] = aria_current
        if aria_pressed:
            node["aria-pressed"] = aria_pressed
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
