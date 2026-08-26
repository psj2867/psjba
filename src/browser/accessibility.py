"""접근성 트리 포맷팅 — Playwright accessibility snapshot → 계층 텍스트 (고도화)."""

import asyncio
import json
import logging
import time
from typing import Any, Optional

from playwright.async_api import Page

logger = logging.getLogger("psjba.browser.accessibility")

# 트리 포맷 표시용 텍스트 최대 길이 (JS enrichment truncation)
TEXT_DISPLAY_LEN = 10

# 토큰 예산
MAX_TOKEN_ESTIMATE = 4000
CHARS_PER_TOKEN = 4

# _format_node
NAME_DISPLAY_LEN = 10          # name 표시 최대 길이
VALUE_DISPLAY_LEN = 60         # value 표시 최대 길이
DESC_DISPLAY_LEN = 80          # description 표시 최대 길이
ALT_DISPLAY_LEN = 60           # alt 표시 최대 길이

# _format_tree 텍스트 리프 요약
TEXT_SUMMARY_MAX = 60          # texts="..." 병합 최대 길이

# browser_get_text return 최대 길이
TEXT_RETURN_MAX_LEN = 5000

# browser_get_dom return 최대 길이
DOM_RETURN_MAX_LEN = 10000

# 연속 동일 타입 비-컨테이너 노드 그루핑: 개별 표시 최대 개수
MAX_GROUP_PREVIEW = 10

# 연속 동일 타입 컨테이너 노드 (row 등) 그루핑: 개별 표시 최대 개수
MAX_CONTAINER_GROUP_PREVIEW = 5

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

    # 태그 정보 — 필요없어서 생략
    # (tag/#id/class는 필요시 selector fallback 용도로만 metadata에 유지)

    if name or node.get("_text_content"):
        children = node.get("children", [])
        if not children:
            # 리프 노드: a11y name(필수) + DOM text(보조)
            an = name.strip() if name else ""
            if len(an) > NAME_DISPLAY_LEN:
                overflow = len(an) - NAME_DISPLAY_LEN
                an = an[:NAME_DISPLAY_LEN] + f"... (+{overflow})"

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
                if len(an) > NAME_DISPLAY_LEN:
                    overflow = len(an) - NAME_DISPLAY_LEN
                    an = an[:NAME_DISPLAY_LEN] + f"... (+{overflow})"
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
        if len(v) > VALUE_DISPLAY_LEN:
            overflow = len(v) - VALUE_DISPLAY_LEN
            v = v[:VALUE_DISPLAY_LEN] + f"... (+{overflow})"
        label_parts.append(f"val={v}")
    description = node.get("description", "")
    if description:
        d = description[:DESC_DISPLAY_LEN]
        if len(description) > DESC_DISPLAY_LEN:
            overflow = len(description) - DESC_DISPLAY_LEN
            d += f"... (+{overflow})"
        label_parts.append(f"desc={d}")

    # alt (image 외에도 있으면 표시)
    alt = node.get("_alt", "")
    if alt and alt != name:
        al = alt[:ALT_DISPLAY_LEN] if len(alt) > ALT_DISPLAY_LEN else alt
        label_parts.append(f'alt="{al}"')

    # position (x, y, width, height) — 있으면 모두 표시
    nx = node.get("_x")
    if nx is not None:
        ny = node.get("_y", 0)
        nw = node.get("_w", 0)
        nh = node.get("_h", 0)
        label_parts.append(f"[{nx}, {ny}] ({nw}x{nh})")

    # 접힌(collapsed) 서브트리 표시 — child_summary와 함께
    collapsed = node.get("_collapsed")
    if collapsed:
        count = collapsed.get("count", 0)
        summary = collapsed.get("summary", [])
        depth_info = collapsed.get("depth")
        phase = collapsed.get("phase", 1)
        if depth_info is not None:
            label_parts.append(f"depth {depth_info}")
        if summary:
            label_parts.append(f"→ {', '.join(summary)}")
        else:
            label_parts.append(f"→ {count} children")

    # 모든 노드에 ref 저장 (키 = psjba_id)
    if refs is not None:
        ref = _next_ref()
        psjba_id = node.get("__psjba_id", "")
        # psjba_id가 없으면 ref 번호로 fallback
        key = psjba_id if psjba_id else f"_noid_{ref}"
        # display: psjba_id 앞 6자
        id_label = psjba_id[:6] if psjba_id else str(ref)
        label_parts.append(f"[{id_label}]")
        refs[key] = {
            "role": role,
            "name": name,
            "value": value,
            "description": description,
            "tag": node.get("tag", ""),
            "_id": node.get("_id", ""),
            "_class": node.get("_class", ""),
            "_type": input_type,
            "_placeholder": placeholder,
            "_form_name": form_name,
            "_aria_label": node.get("_aria_label", ""),
            "_backendNodeId": node.get("_backendNodeId"),
            "_text_content": node.get("_text_content", "") or "",
            "_nth_of_type": node.get("_nth_of_type", 0),
            "_alt": node.get("_alt", ""),
            "_x": node.get("_x", 0),
            "_y": node.get("_y", 0),
            "_w": node.get("_w", 0),
            "_h": node.get("_h", 0),
            "_visible": node.get("_visible", True),
            "__psjba_id": psjba_id,
            "_id_label": id_label,
        }
        if psjba_id:
            refs[key]["_selector"] = f'[data-psjba-id="{psjba_id}"]'

        # 필요할 때 position 계산할 수 있도록 rect 계산식 유도는 안 함 (이미 evaluate에서 수집)

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
    hide_collapsed: bool = False,
) -> list[str]:
    """접근성 스냅샷을 계층적 텍스트 라인 리스트로 변환한다.

    동일 타입 비-컨테이너 노드는 그루핑된다:
    - 텍스트 리프 (non-interactive + text 있음): 무조건 한 줄 요약 [role] [#ref_min-ref_max]
    - 기타 리프 (image/button 등): MAX_GROUP_PREVIEW(10)개 초과 시 처음 10개만 개별, 나머지 ×N 요약
    """
    if lines is None:
        lines = []

    if depth > max_depth:
        lines.append(f"{indent * depth}... (depth {depth})")
        return lines

    # ── collapsed 처리 (hide_collapsed면 아예 건너뜀) ──
    if hide_collapsed and snapshot.get("_collapsed"):
        return lines

    # 자기 자신 출력 (루트 포함)
    formatted = _format_node(snapshot, depth, indent, refs=refs, detail=detail)
    if formatted.strip():
        lines.append(formatted)

    # hidden 요소는 자식도 포함해서 통째로 제외 (트리에 없었던 것처럼)
    if snapshot.get("_visible") is False:
        return lines

    # accessibility expanded=false면 자식 생략 (aria-expanded collapsed)
    if snapshot.get("expanded") is False and not snapshot.get("_collapsed"):
        return lines

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

    # ── 연속 동일 타입 비-컨테이너 그루핑 ──────────────
    # 텍스트 리프는 무조건 항상 그루핑; 나머지(image/button 등)는 MAX_GROUP_PREVIEW 기준
    def _is_text_leaf(n):
        return not n.get("children") and not _is_interactive(n.get("role", "")) and (bool(n.get("_text_content")) or bool(n.get("name")))

    def _text_leaf_key(n):
        return ((n.get("tag", "") or n.get("_tag", "")).lower(),
                n.get("role", "").lower())

    def _is_non_container(n):
        return not n.get("children")

    def _non_container_key(n):
        return ((n.get("tag", "") or n.get("_tag", "")).lower(),
                n.get("role", "").lower())

    def _is_non_container(n):
        return not n.get("children")

    def _container_key(n):
        return ((n.get("tag", "") or n.get("_tag", "")).lower(),
                n.get("role", "").lower())

    grouped: list[dict[str, Any] | list[dict[str, Any]]] = []
    text_pending: list[dict[str, Any]] = []
    other_pending: list[dict[str, Any]] = []
    container_pending: list[dict[str, Any]] = []

    def _flush_pending():
        nonlocal text_pending, other_pending, container_pending
        if text_pending:
            grouped.append(text_pending)
            text_pending = []
        if other_pending:
            grouped.append(other_pending)
            other_pending = []
        if container_pending:
            grouped.append(container_pending)
            container_pending = []

    for child in children:
        if _is_text_leaf(child):
            # 텍스트 리프: 다른 타입 나올 때까지 모으기
            key = _text_leaf_key(child)
            if not text_pending or key == _text_leaf_key(text_pending[0]):
                text_pending.append(child)
            else:
                _flush_pending()
                text_pending = [child]
            if other_pending:
                _flush_pending()
            if container_pending:
                _flush_pending()
        elif _is_non_container(child):
            # 기타 리프 (image, button, link, checkbox 등)
            key = _non_container_key(child)
            if not other_pending or key == _non_container_key(other_pending[0]):
                other_pending.append(child)
            else:
                _flush_pending()
                other_pending = [child]
            if text_pending:
                _flush_pending()
            if container_pending:
                _flush_pending()
        else:
            # 컨테이너 (row 등): 연속 동일-타입 컨테이너 그루핑
            key = _container_key(child)
            if not container_pending or key == _container_key(container_pending[0]):
                container_pending.append(child)
            else:
                _flush_pending()
                container_pending = [child]
            if text_pending:
                _flush_pending()
            if other_pending:
                _flush_pending()
    _flush_pending()

    for item in grouped:
        if depth > 0 and depth % 4 == 0 and len(lines) % 5 == 0:
            await asyncio.sleep(0)

        if isinstance(item, list):
            first = item[0]
            n = len(item)

            # 컨테이너 그룹 (row 등): 첫 N개만 개별 표시, 나머지 ×N more
            if first.get("children") and not _is_text_leaf(first) and not _is_non_container(first):
                role_display = first.get("role", "")
                shown = item[:MAX_CONTAINER_GROUP_PREVIEW]
                for container_node in shown:
                    await _format_tree(container_node, depth + 1, lines, indent,
                                       refs=refs, detail=detail, max_depth=max_depth,
                                       hide_collapsed=hide_collapsed)
                if n > MAX_CONTAINER_GROUP_PREVIEW:
                    remain = n - MAX_CONTAINER_GROUP_PREVIEW
                    parts = [f"... [{role_display}]", f"×{remain} more"]
                    lines.append(f"{indent * (depth + 1)}{' '.join(parts)}")
                continue

            role_display = first.get("role", "")

            if _is_text_leaf(first):
                # ── 텍스트 그룹: 항상 한 줄 요약 ──────
                ref_min = ref_max = 0
                texts: list[str] = []
                for leaf_node in item:
                    ref = _next_ref()
                    if not ref_min:
                        ref_min = ref
                    ref_max = ref
                    if refs is not None:
                        psjba_id = leaf_node.get("__psjba_id", "")
                        key = psjba_id if psjba_id else f"_noid_{ref}"
                        refs[key] = {
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
                            "_alt": leaf_node.get("_alt", ""),
                            "_x": leaf_node.get("_x", 0),
                            "_y": leaf_node.get("_y", 0),
                            "_w": leaf_node.get("_w", 0),
                            "_h": leaf_node.get("_h", 0),
                            "_visible": leaf_node.get("_visible", True),
                            "__psjba_id": psjba_id,
                        }
                        if psjba_id:
                            refs[key]["_selector"] = f'[data-psjba-id="{psjba_id}"]'
                    tc = leaf_node.get("_text_content", "").strip()
                    if tc:
                        texts.append(tc)
                parts = [f"[{role_display}]"]
                # text group: psjba-id 범위 (item의 첫 노드와 마지막 노드 기준)
                first_node = item[0] if item else {}
                last_node = item[-1] if item else {}
                first_id = first_node.get("__psjba_id", "")
                last_id = last_node.get("__psjba_id", "")
                if first_id and last_id and first_id == last_id:
                    id_label = first_id[:6]
                elif first_id and last_id:
                    id_label = f"{first_id[:4]}-{last_id[:4]}"
                elif first_id:
                    id_label = first_id[:6]
                elif last_id:
                    id_label = last_id[:6]
                else:
                    id_label = None
                if id_label:
                    parts.append(f"[{id_label}]")
                else:
                    parts.append(f"[#{ref_min}-{ref_max}]")
                if texts:
                    merged = ", ".join(texts)
                    if len(merged) > TEXT_SUMMARY_MAX:
                        overflow = len(merged) - TEXT_SUMMARY_MAX
                        merged = merged[:TEXT_SUMMARY_MAX] + f"... (+{overflow})"
                    parts.append(f'texts="{merged}"')
                lines.append(f"{indent * (depth + 1)}{' '.join(parts)}")
            else:
                # ── 기타 리프 그룹 (image/button 등) ─
                if n > MAX_GROUP_PREVIEW:
                    shown = item[:MAX_GROUP_PREVIEW]
                    remain = n - MAX_GROUP_PREVIEW
                    for leaf_node in shown:
                        _fmt = _format_node(leaf_node, depth + 1, indent, refs=refs, detail=detail)
                        if _fmt.strip():
                            lines.append(_fmt)
                    parts = [f"... [{role_display}]", f"×{remain} more"]
                    lines.append(f"{indent * (depth + 1)}{' '.join(parts)}")
                else:
                    for leaf_node in item:
                        _fmt = _format_node(leaf_node, depth + 1, indent, refs=refs, detail=detail)
                        if _fmt.strip():
                            lines.append(_fmt)
        else:
            await _format_tree(item, depth + 1, lines, indent, refs=refs, detail=detail, max_depth=max_depth,
                               hide_collapsed=hide_collapsed)

    return lines


def _prune_tree(snapshot: dict[str, Any]) -> Optional[dict[str, Any]]:
    """불필요한 노드를 제거하고 단일-자식 체인을 병합한다. (sync)

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
        pruned_all = [r for r in (_prune_tree(c) for c in children) if r is not None]
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
        child_pruned = _prune_tree(child)
        if child_pruned is not None:
            return child_pruned
        return None

    # 일반 노드: 자식들을 병렬로 prune (sync list comprehension)
    pruned_children = [r for r in (_prune_tree(c) for c in children) if r is not None]

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
    Phase 2: 하단부터 깊은 순으로 non-interactive 노드 collapse.
    Phase 3: Phase 1/2로도 부족하면 interactive 노드까지 하단부터 collapse.
    모든 phase에서 collapse된 노드는 'depth N, → [role] ×N' 형태로 표시된다.
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

    # ── Phase 2: 하단부터 깊은 순 collapse (non-interactive) ──
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
        node["_collapsed"] = {"count": len(children), "summary": summary, "depth": _depth, "phase": 2}
        node["children"] = []
        text = "\n".join(await _format_tree(tree, refs=refs))
        if _estimate_tokens(text) <= max_tokens:
            return tree

    # ── Phase 3: interactive 포함 하단부터 collapse ─────
    # Phase 1/2로 안 들어가면 interactive(버튼/입력 등)까지도
    # 하단부터 최소 depth 만큼 접어서 구조 완결성을 유지한다.
    interactive_bottom_up: list[tuple[int, list[int], dict[str, Any]]] = []

    def _collect_all(node, path, depth):
        children = node.get("children", [])
        if not children:
            return
        for i, child in enumerate(children):
            _collect_all(child, path + [i], depth + 1)
        if node.get("_collapsed"):
            return
        if not children:
            return
        interactive_bottom_up.append((depth, path, node))

    _collect_all(tree, [], 0)
    interactive_bottom_up.sort(key=lambda x: x[0], reverse=True)  # deep first

    for _depth, _path, node in interactive_bottom_up:
        children = node.get("children", [])
        if not children:
            continue
        summary = _build_child_summary(children)
        node["_collapsed"] = {"count": len(children), "summary": summary, "depth": _depth, "phase": 3}
        node["children"] = []
        text = "\n".join(await _format_tree(tree, refs=refs))
        if _estimate_tokens(text) <= max_tokens:
            return tree

    return tree


# ─── Public API ──────────────────────────────────


async def fetch_full_tree(page: Page) -> dict[str, Any]:
    """getFullAXTree + enrichment까지 완료한 full enriched tree를 반환한다.

    manager가 session_id별로 보관하고, subtree detail/재사용에 사용한다.
    """
    t0 = time.monotonic()
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Accessibility.enable")
    t1 = time.monotonic()
    result = await cdp.send("Accessibility.getFullAXTree", {})
    t2 = time.monotonic()
    raw_nodes = result.get("nodes", [])
    if not raw_nodes:
        return {"role": "Root", "name": "", "children": []}

    tree = _cdp_nodes_to_snapshot(raw_nodes)
    t3 = time.monotonic()
    await _enrich_via_batch(page, tree)
    t4 = time.monotonic()

    # scroll + viewport info 수집
    try:
        sv = await page.evaluate("""() => ({
            y: window.pageYOffset || window.scrollY || 0,
            h: document.documentElement.scrollHeight || 0,
            vw: window.innerWidth || 0,
            vh: window.innerHeight || 0
        })""")
        tree["_scroll_y"] = sv.get("y", 0)
        tree["_scroll_h"] = sv.get("h", 0)
        tree["_viewport_w"] = sv.get("vw", 0)
        tree["_viewport_h"] = sv.get("vh", 0)
    except Exception:
        pass

    t5 = time.monotonic()
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("fetch_full_tree timing: enable=%dms getFullAXTree=%dms build=%dms enrich=%dms scroll=%dms total=%dms | %d raw nodes",
                     int((t1 - t0) * 1000), int((t2 - t1) * 1000), int((t3 - t2) * 1000),
                     int((t4 - t3) * 1000), int((t5 - t4) * 1000), int((t5 - t0) * 1000), len(raw_nodes))
    return tree


async def format_tree(tree: dict[str, Any], hide_collapsed: bool = False) -> tuple[str, dict[str, dict[str, Any]]]:
    """full enriched tree → prune/collapse/format → (text, refs).

    refs는 psjba_id를 키로 하는 노드 정보 맵
    (browser_click, browser_get_text 등에서 ref 파라미터로 psjba_id 사용).

    Args:
        tree: full enriched accessibility tree
        hide_collapsed: True면 collapsed 컨테이너 플레이스홀더도 출력에서 제거
    """
    t0 = time.monotonic()
    _reset_ref_counter()
    pruned = _prune_tree(tree) or tree
    t1 = time.monotonic()
    collapsed = await _collapse_tree(pruned)
    t2 = time.monotonic()
    refs: dict[int, dict[str, Any]] = {}
    lines = await _format_tree(collapsed, refs=refs, hide_collapsed=hide_collapsed) if collapsed else []
    text = "\n".join(lines)
    t3 = time.monotonic()
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug("format_tree timing: prune=%dms collapse=%dms format=%dms total=%dms | %d lines",
                     int((t1 - t0) * 1000), int((t2 - t1) * 1000), int((t3 - t2) * 1000),
                     int((t3 - t0) * 1000), len(lines))
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


def format_subtree_detail(tree: dict[str, Any], ref_info: dict[str, Any], context: int = 0) -> str:
    """저장된 full tree + ref_info로 서브트리 detail 텍스트를 반환한다.

    Args:
        tree: fetch_full_tree()로 얻은 enriched tree
        ref_info: refs 맵에 저장된 노드 정보 (__psjba_id, role, name)
        context: 0이면 ref 노드 서브트리 detail, N>0이면 ref 기준 위아래 N레벨 컨텍스트

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
            if context > 0:
                # context view: 경로 추적 → 컨텍스트 렌더링
                path = _find_path_to_psjba(tree, psjba_id)
                lines = _render_context_lines(tree, path, ref_info, context)
                text = "\n".join(lines)
                sel = ref_info.get("_selector", "")
                sel_line = f"\nselector = {sel}" if sel else ""
                id_label = ref_info.get("_id_label", "?")
                return (f"--- Context for [{id_label}] "
                        f"({target_role} \"{target_name}\") ---\n{text}{sel_line}")
            lines = [_format_node(subtree, 0, refs=None, detail=True)]
            for child in subtree.get("children", []):
                _collect_lines(child, 1, lines, detail=True)
            text = "\n".join(lines)
            sel = ref_info.get("_selector", "")
            sel_line = f"\nselector = {sel}" if sel else ""
            id_label = ref_info.get("_id_label", "?")
            return (f"--- Detail for [{id_label}] "
                    f"({target_role} \"{target_name}\") ---\n{text}{sel_line}")

    # 2차: (role, name) match (fallback)
    subtree = _find_ref_subtree(tree, target_role, target_name)
    if subtree is None:
        return (f"Subtree detail: could not locate ref "
                f"({target_role} \"{target_name}\") in tree. "
                "Try browser_snapshot() again.")

    if context > 0:
        path = _find_path_to_node(tree, subtree)
        lines = _render_context_lines(tree, path, ref_info, context)
        return "\n".join(lines)
    lines = [_format_node(subtree, 0, refs=None, detail=True)]
    for child in subtree.get("children", []):
        _collect_lines(child, 1, lines, detail=True)
    text = "\n".join(lines)
    sel = ref_info.get("_selector", "")
    if sel:
        text += f"\nselector = {sel}"
    id_label = ref_info.get("_id_label", "?")
    return (f"--- Detail for [{id_label}] "
            f"({target_role} \"{target_name}\") ---\n{text}")


# ─── Enrichment: batch CDP (captureSnapshot) ─────────────────

# MAX_CHARS for text display truncation
TEXT_ENRICH_MAX_LEN: int = 3000


async def _enrich_via_batch(
    page: Page,
    snapshot: dict[str, Any],
) -> None:
    """Batch CDP enrichment: 2~3회 CDP 호출로 모든 a11y 노드 enrichment.

    현재 _enrich_via_resolve_gather가 노드마다 3 CDP 호출(25초) 하는 것을
    3회 CDP 호출로 대체:
      1. Runtime.evaluate: 모든 DOM element에 data-psjba-id 주입 + textContent 수집
      2. DOMSnapshot.captureSnapshot: 모든 DOM element의 attributes + backendNodeId
      3. Python: backendNodeId 기준으로 a11y 트리에 매핑

    Returns:
        None (snapshot 노드를 in-place로 수정)
    """
    # backendNodeId 수집 (a11y tree normalization)
    all_ids: list[int] = []
    for node in _iter_nodes(snapshot):
        bid = node.get("_backendNodeId")
        if bid:
            all_ids.append(bid)

    if not all_ids:
        return

    try:
        # Step 1 + 3 통합: uid 주입 + textContent (getFastNodeText) 한 번에
        uid_text_map: dict[str, str] = await page.evaluate("""() => {
            function getFastNodeText(n) {
                if (!n) return '';
                if (n.nodeType === Node.TEXT_NODE) return n.nodeValue || '';
                if (n.nodeType !== Node.ELEMENT_NODE) return '';
                var childs = n.childNodes, l = childs.length;
                if (l === 0) return n.textContent.trim();
                if (l === 1) return getFastNodeText(childs[0]);
                var r = '';
                for (var i = 0; i < l; i++) {
                    var t = getFastNodeText(childs[i]);
                    if (t) {
                        if (r && !r.endsWith(' ') && !t.startsWith(' ')) r += ' ';
                        r += t;
                    }
                }
                return r.replace(/\\s+/g, ' ').trim();
            }
            const els = document.querySelectorAll('*');
            const map = {};
            for (let i = 0; i < els.length; i++) {
                const existing = els[i].getAttribute('data-psjba-id');
                const uid = existing || (Math.random().toString(36) + Math.random().toString(36)).slice(2, 10);
                if (!existing) els[i].setAttribute('data-psjba-id', uid);
                map[uid] = getFastNodeText(els[i]);
            }
            return map;
        }""")

        # Step 2: captureSnapshot (한 번에 모든 attributes)
        cdp = await page.context.new_cdp_session(page)
        snap_result = await cdp.send("DOMSnapshot.captureSnapshot", {
            "computedStyles": [],
        })
    except Exception as e:
        logger.warning("batch enrichment failed (%s), fallback to resolve gather", e)
        await _enrich_via_resolve_gather(page, snapshot)
        return

    strings: list[str] = snap_result.get("strings", [])
    if not strings:
        logger.warning("captureSnapshot returned no strings, fallback")
        await _enrich_via_resolve_gather(page, snapshot)
        return

    # captureSnapshot 결과에서 bid → info 매핑
    documents = snap_result.get("documents", [])
    if not documents:
        logger.warning("captureSnapshot returned no documents, fallback")
        await _enrich_via_resolve_gather(page, snapshot)
        return

    doc = documents[0]
    nodes = doc.get("nodes", {})
    backend_ids_list: list[int] = nodes.get("backendNodeId", [])
    node_types: list[int] = nodes.get("nodeType", [])
    node_names: list[int] = nodes.get("nodeName", [])
    attrs_list = nodes.get("attributes", [])
    input_values: list[int] = nodes.get("inputValue", [])

    t0 = time.monotonic()
    bid_to_info: dict[int, dict[str, Any]] = {}
    tag_counter: dict[str, int] = {}

    for i, bid in enumerate(backend_ids_list):
        if not bid or bid <= 0:
            continue
        nt = node_types[i] if i < len(node_types) else 1
        if nt != 1:
            # Element node (Node.ELEMENT_NODE == 1)만 처리
            continue

        # nodeName → tag string
        name_idx = node_names[i] if i < len(node_names) else -1
        tag = strings[name_idx].lower() if 0 <= name_idx < len(strings) else ""

        info: dict[str, Any] = {"tag": tag}
        uid: str | None = None

        # attributes 파싱 (flat [key_idx, val_idx, ...] 배열)
        attrs_raw = attrs_list[i] if i < len(attrs_list) else None
        if attrs_raw and isinstance(attrs_raw, list):
            it = iter(attrs_raw)
            for key_idx, val_idx in zip(it, it):
                if not (0 <= key_idx < len(strings) and 0 <= val_idx < len(strings)):
                    continue
                key = strings[key_idx]
                val = strings[val_idx]
                if key == "data-psjba-id":
                    uid = val
                elif key == "id":
                    info["id"] = val
                elif key == "class":
                    info["class"] = val
                elif key == "type":
                    info["type"] = val
                elif key == "placeholder":
                    info["placeholder"] = val
                elif key == "aria-label":
                    info["aria_label"] = val
                elif key == "name":
                    info["name"] = val
                elif key == "alt":
                    info["alt"] = val
                elif key.startswith("aria-"):
                    if key in ("aria-label",):
                        continue
                    if "aria_attrs" not in info:
                        info["aria_attrs"] = {}
                    norm_key = key.replace("-", "_")
                    info["aria_attrs"][norm_key] = val

        # inputValue (input/textarea의 현재값)
        if i < len(input_values):
            iv_idx = input_values[i]
            if iv_idx >= 0 and iv_idx < len(strings):
                iv = strings[iv_idx]
                if iv:
                    info["input_value"] = iv

        if uid:
            info["uid"] = uid
            # textContent는 step 1에서 수집한 맵에서 uid로 조회
            raw_text = uid_text_map.get(uid, "")
            if raw_text:
                if len(raw_text) > TEXT_ENRICH_MAX_LEN:
                    raw_text = raw_text[:TEXT_ENRICH_MAX_LEN] + f"... (+{len(raw_text) - TEXT_ENRICH_MAX_LEN} more)"
                info["textContent"] = raw_text

        # _nth_of_type 계산 (tag별 순차 카운터)
        if tag:
            tag_counter.setdefault(tag, 0)
            tag_counter[tag] += 1
            if tag_counter[tag] > 1:
                info["_nth_of_type"] = tag_counter[tag]

        bid_to_info[bid] = info

    # Step 3: position + visibility 수집 (getBoundingClientRect, 1 evaluate)
    t1 = time.monotonic()
    parse_ms = int((t1 - t0) * 1000)
    try:
        pos_data: dict[str, dict] = await page.evaluate("""() => {
            const map = {};
            const els = document.querySelectorAll('[data-psjba-id]');
            for (let i = 0; i < els.length; i++) {
                const uid = els[i].getAttribute('data-psjba-id');
                if (!uid) continue;
                const r = els[i].getBoundingClientRect();
                const hasSize = r.width > 0 && r.height > 0;
                const vis = hasSize && (
                    typeof els[i].checkVisibility === 'function'
                        ? els[i].checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })
                        : true
                );
                map[uid] = {
                    x: Math.round(r.x),
                    y: Math.round(r.y),
                    w: Math.round(r.width),
                    h: Math.round(r.height),
                    visible: vis
                };
            }
            return map;
        }""")
        for bid, info in bid_to_info.items():
            uid = info.get("uid", "")
            if uid and uid in pos_data:
                p = pos_data[uid]
                info["_x"] = p["x"]
                info["_y"] = p["y"]
                info["_w"] = p["w"]
                info["_h"] = p["h"]
                info["_visible"] = p["visible"]
    except Exception:
        logger.debug("position enrichment failed, skipping")

    # a11y snapshot 노드에 속성 채우기 (sync)
    _fill_node_enrich(snapshot, bid_to_info)
    t2 = time.monotonic()

    enriched_count = sum(1 for n in _iter_nodes(snapshot) if n.get("__psjba_id"))
    total_ms = int((t2 - t0) * 1000)
    logger.debug("_enrich_via_batch: %d/%d nodes enriched, parse=%dms pos_fill=%dms total=%dms",
                 enriched_count, len(all_ids),
                 parse_ms, total_ms - parse_ms, total_ms)


def _iter_nodes(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """BFS/DFS로 모든 노드를 flat 리스트로 반환."""
    result: list[dict[str, Any]] = []
    stack = [tree]
    while stack:
        node = stack.pop()
        result.append(node)
        children = node.get("children", [])
        # 역순으로 append해서 pop 순서 보존은 신경 안 씀 (flat만 중요)
        for child in children:
            stack.append(child)
    return result


def _fill_node_enrich(node: dict[str, Any], bid_to_info: dict[int, dict[str, Any]]) -> None:
    """sync in-place enrichment: bid_to_info 맵에서 정보를 읽어 노드에 채운다."""
    bid = node.get("_backendNodeId")
    info = bid_to_info.get(bid) if bid else None
    if info:
        node["__psjba_id"] = info.get("uid", "")
        node["tag"] = info.get("tag", "")
        node["_id"] = info.get("id", "")
        node["_class"] = info.get("class", "")
        node["_type"] = info.get("type", "")
        node["_placeholder"] = info.get("placeholder", "")
        node["_aria_label"] = info.get("aria_label", "")
        node["_form_name"] = info.get("name", "")
        node["_alt"] = info.get("alt", "")
        tc = info.get("textContent", "")
        if tc:
            node["_text_content"] = tc
        iv = info.get("input_value", "")
        if iv:
            node["_input_value"] = iv
        nth = info.get("_nth_of_type", 0)
        if nth:
            node["_nth_of_type"] = nth
        aria_attrs = info.get("aria_attrs", {})
        if aria_attrs:
            for akey, aval in aria_attrs.items():
                node[akey] = aval

        # position + visibility
        x = info.get("_x")
        if x is not None:
            node["_x"] = x
            node["_y"] = info.get("_y", 0)
            node["_w"] = info.get("_w", 0)
            node["_h"] = info.get("_h", 0)
        vis = info.get("_visible")
        if vis is not None:
            node["_visible"] = vis

    for child in node.get("children", []):
        _fill_node_enrich(child, bid_to_info)


# ─── Ref context helpers ──────────────────────────────

def _find_path_to_psjba(tree: dict[str, Any], psjba_id: str) -> list[dict]:
    """Root부터 psjba_id를 가진 노드까지의 경로를 반환.

    Returns:
        각 element는 {'parent': ..., 'child_idx': int, 'node': ...} 형태.
        마지막 element의 child_idx는 -1 (자기 자신).
    """
    def _search(node, path):
        if node.get("__psjba_id", "") == psjba_id:
            return path + [{"parent": path[-1]["parent"] if path else None,
                            "child_idx": path[-1]["child_idx"] if path else -1,
                            "node": node}]
        for i, child in enumerate(node.get("children", [])):
            result = _search(child, path + [{"parent": node, "child_idx": i, "node": child}])
            if result:
                return result
        return None

    result = _search(tree, [])
    if result:
        return result
    return []


def _find_path_to_node(tree: dict[str, Any], target: dict) -> list[dict]:
    """Root부터 target 노드까지의 경로. _find_path_to_psjba의 fallback용."""
    def _search(node, path):
        if node is target:
            return path
        for i, child in enumerate(node.get("children", [])):
            p_info = {"parent": node, "child_idx": i, "node": child}
            result = _search(child, path + [p_info])
            if result:
                return result
        return None

    result = _search(tree, [])
    if result:
        return result
    return []


def _render_context_lines(tree: dict, path: list[dict], ref_info: dict, context: int) -> list[str]:
    """ref 노드 기준 context 레벨 만큼의 부모/형제를 포함한 라인 렌더링.

    path: _find_path_to_psjba 결과
    context: 보여줄 부모 레벨 수 (형제는 해당 레벨에서 ref 주변만)
    """
    lines: list[str] = []
    if not path:
        return lines

    # ref 노드 정보
    ref_depth = len(path)  # root depth=0, ref의 depth
    # context 레벨: ref_depth - context 이상부터 시작
    start_level = max(0, ref_depth - context - 1)

    # 각 레벨별로 렌더링
    for level in range(start_level, ref_depth + 1):
        if level == 0:
            # root
            lines.append(_format_node(tree, 0, refs=None))
            continue
        p_info = path[level - 1]
        parent = p_info["parent"]
        child_idx = p_info["child_idx"]
        siblings = parent.get("children", [])

        # Is this the ref's direct ancestor level (level == ref_depth - 1)?
        is_ref_level = (level == ref_depth - 1)

        for si, sib in enumerate(siblings):
            if is_ref_level and si == child_idx:
                # ref 노드: >>> 마커 + 서브트리 detail (2레벨)
                prefix = "  " * level + ">>> "
                lines.append(prefix + _format_node(sib, 0, refs=None, detail=True))
                for child in sib.get("children", []):
                    _collect_lines(child, level + 1, lines, detail=True)
            else:
                # 형제 노드: 한 줄만
                prefix = "  " * level
                fmt = _format_node(sib, 0, refs=None)
                lines.append(prefix + fmt)
        # sibling 렌더링 이후 parent 레벨 건너뜀 (이미 siblings 아래에 렌더링됨)
        # ref_depth 직전까지만 sibling 렌더링하고,
        # ref_depth-1에서 ref 노드 서브트리 detail 포함되었으므로 종료
        if is_ref_level:
            break

    # 만약 context가 0이거나 ref_depth가 0이면 path가 비거나 start_level이 ref_depth
    # 그냥 기본 detail view와 동일하게 서브트리 detail만
    if not lines:
        node = path[-1]["node"] if path else tree
        lines.append(_format_node(node, 0, refs=None, detail=True))
        for child in node.get("children", []):
            _collect_lines(child, 1, lines, detail=True)

    return lines


def _collect_lines(node: dict[str, Any], depth: int, lines: list[str], detail: bool = False) -> None:
    """재귀적으로 노드를 포맷하여 라인 리스트에 추가한다."""
    lines.append(_format_node(node, depth, refs=None, detail=detail))
    for child in node.get("children", []):
        _collect_lines(child, depth + 1, lines, detail=detail)

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
        textContent: t.length > {TEXT_ENRICH_MAX_LEN} ? t.substring(0, {TEXT_ENRICH_MAX_LEN}) + '... (+' + (t.length - {TEXT_ENRICH_MAX_LEN}) + ' more)' : t,
        ariaAttrs: JSON.stringify(ariaAttrs)
    }};
}}"""


async def _enrich_via_resolve_gather(
    page: Page,
    snapshot: dict[str, Any],
) -> None:
    """Fallback: resolveNode → callFunctionOn gather로 모든 노드 enrichment.

    각 노드에 대해 DOM.resolveNode + Runtime.callFunctionOn + releaseObject.
    _enrich_via_batch 실패 시 호출.
    """
    all_ids: list[int] = []
    for node in _iter_nodes(snapshot):
        bid = node.get("_backendNodeId")
        if bid:
            all_ids.append(bid)
    if not all_ids:
        return

    cdp = await page.context.new_cdp_session(page)

    t0 = time.monotonic()
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
    t1 = time.monotonic()

    bid_to_info: dict[int, dict] = {}
    for bid, res in zip(all_ids, results):
        if res and isinstance(res, dict) and res.get("uid"):
            bid_to_info[bid] = res

    _fill_node_enrich(snapshot, bid_to_info)
    t2 = time.monotonic()

    enriched_count = sum(1 for n in _iter_nodes(snapshot) if n.get("__psjba_id"))
    logger.debug("_enrich_via_resolve_gather: %d/%d nodes enriched, cdp=%dms fill=%dms total=%dms",
                 enriched_count, len(all_ids),
                 int((t1 - t0) * 1000), int((t2 - t1) * 1000),
                 int((t2 - t0) * 1000))


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
