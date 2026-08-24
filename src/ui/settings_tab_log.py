"""LogTab — 세션 로그 보기 탭 (2단계 드릴다운)."""

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.ui.settings_dialog import (
    _clear_layout,
    _make_pagination,
    _run_async,
    _run_async_task,
    _update_pagination,
)

logger = logging.getLogger("psjba.ui.settings_tab_log")


class LogTab(QWidget):
    """세션 로그 보기 탭 — 2단계 드릴다운 (세션 목록 → 타임라인)."""

    def __init__(self, dialog: Any, config: Any, db: Any) -> None:
        super().__init__()
        self._dialog = dialog
        self._config = config
        self._db = db
        self._log_l1_page: int = 1
        self._log_l1_total_pages: int = 1
        self._log_l2_time_list: list[dict] = []
        self._async_tasks: list[Any] = []
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._log_stacked = QStackedWidget()
        layout.addWidget(self._log_stacked)

        self._log_stacked.addWidget(self._build_log_level1())
        self._log_stacked.addWidget(self._build_log_level2())

        self._log_stacked.setCurrentIndex(0)

    # ─── Level 1: 세션 목록 ──────────────────────────────────────

    def _build_log_level1(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        header = QHBoxLayout()
        header_lbl = QLabel("모든 세션")
        header_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        header.addWidget(header_lbl)
        header.addStretch()
        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._log_session_container = QWidget()
        self._log_session_layout = QVBoxLayout(self._log_session_container)
        self._log_session_layout.setContentsMargins(8, 8, 8, 8)
        self._log_session_layout.setSpacing(4)
        self._log_session_layout.addStretch()
        scroll.setWidget(self._log_session_container)
        layout.addWidget(scroll)

        self._log_l1_pagination = _make_pagination(self._on_log_l1_prev, self._on_log_l1_next)
        layout.addLayout(self._log_l1_pagination)
        return w

    def _on_log_l1_prev(self) -> None:
        if self._log_l1_page > 1:
            self._log_l1_page -= 1
            self._load_log_level1()

    def _on_log_l1_next(self) -> None:
        if self._log_l1_page < self._log_l1_total_pages:
            self._log_l1_page += 1
            self._load_log_level1()

    def _load_log_level1(self) -> None:
        _clear_layout(self._log_session_layout)

        async def _fetch() -> Any:
            return await self._db.list_sessions(page=self._log_l1_page)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_fetch(), callback=self._on_log_l1_loaded,
                                task_store=self._async_tasks)
                return
        except RuntimeError:
            pass

        result = _run_async(_fetch())
        self._on_log_l1_loaded(result)

    def _on_log_l1_loaded(self, result) -> None:
        _clear_layout(self._log_session_layout)
        sessions, total = result if result else ([], 0)
        page_size = 20
        self._log_l1_total_pages = max(1, (total + page_size - 1) // page_size)
        _update_pagination(self._log_l1_pagination, self._log_l1_page, self._log_l1_total_pages)

        if not sessions:
            lbl = QLabel("세션이 없습니다")
            lbl.setStyleSheet("color: #64748b; border: none;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            count = self._log_session_layout.count()
            self._log_session_layout.insertWidget(count - 1, lbl)
            return

        for s in sessions:
            row = self._make_log_session_row(s)
            count = self._log_session_layout.count()
            self._log_session_layout.insertWidget(count - 1, row)

    def _make_log_session_row(self, session: dict) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { border: 1px solid #dce1e8; border-radius: 6px; padding: 8px; "
            "background-color: #ffffff; }"
            "QFrame:hover { background-color: #e8ecf0; border: 1px solid #4f46e5; }"
        )
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(frame)

        stype = session.get("type", "chat")
        type_icon = "💬 채팅" if stype == "chat" else "⏰ 크론"
        type_lbl = QLabel(type_icon)
        type_lbl.setFixedWidth(70)
        type_lbl.setStyleSheet("border: none; color: #64748b;")
        layout.addWidget(type_lbl)

        created = session.get("created_at", "")
        if isinstance(created, str) and len(created) >= 16:
            time_txt = created[5:16].replace("T", " ")
        else:
            time_txt = str(created)[:16]
        time_lbl = QLabel(time_txt)
        time_lbl.setFixedWidth(100)
        time_lbl.setStyleSheet("border: none; color: #64748b;")
        layout.addWidget(time_lbl)

        sid = session.get("id", "")[:8]
        sid_lbl = QLabel(f"{sid}...")
        sid_lbl.setFixedWidth(80)
        sid_lbl.setStyleSheet("border: none; color: #64748b; font-size: 11px;")
        layout.addWidget(sid_lbl)

        prompt = session.get("first_prompt", "") or ""
        prompt_short = prompt[:50] + ("..." if len(prompt) > 50 else "")
        if not prompt_short:
            prompt_short = "(내용 없음)"
        prompt_lbl = QLabel(prompt_short)
        prompt_lbl.setStyleSheet("border: none; color: #cbd5e1;")
        layout.addWidget(prompt_lbl)

        frame.mousePressEvent = lambda e, sid=session.get("id"): self._on_log_l2_click(sid)
        return frame

    # ─── Level 2: 세션 상세 (타임라인 + ChatWidget) ──────────────

    def _build_log_level2(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        header = QHBoxLayout()
        back_btn = QPushButton("◀ 세션 목록으로")
        back_btn.clicked.connect(lambda: self._log_stacked.setCurrentIndex(0))
        header.addWidget(back_btn)
        header.addStretch()
        close_btn = QPushButton("✕ 닫기")
        close_btn.clicked.connect(lambda: self._log_stacked.setCurrentIndex(0))
        header.addWidget(close_btn)
        layout.addLayout(header)

        split = QHBoxLayout()

        self._log_l2_time_container = QWidget()
        self._log_l2_time_container.setMaximumWidth(150)
        self._log_l2_time_layout = QVBoxLayout(self._log_l2_time_container)
        self._log_l2_time_layout.setContentsMargins(4, 4, 4, 4)
        self._log_l2_time_layout.setSpacing(2)
        self._log_l2_time_layout.addStretch()
        time_scroll = QScrollArea()
        time_scroll.setWidgetResizable(True)
        time_scroll.setWidget(self._log_l2_time_container)
        time_scroll.setMaximumWidth(160)
        split.addWidget(time_scroll)

        from src.ui.chat_widget import ChatWidget
        self._log_l2_chat = ChatWidget()
        split.addWidget(self._log_l2_chat)

        layout.addLayout(split)
        return w

    def _on_log_l2_click(self, session_id: str) -> None:
        async def _fetch() -> Any:
            return await self._db.get_messages(session_id)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_fetch(), callback=self._on_log_l2_loaded,
                                task_store=self._async_tasks)
                return
        except RuntimeError:
            pass

        messages = _run_async(_fetch()) or []
        self._on_log_l2_loaded(messages)

    def _on_log_l2_loaded(self, messages: Any) -> None:
        if not messages:
            messages = []
        msg_dicts = [
            {"role": m.role, "content": m.content, "tool_calls": m.tool_calls,
             "tool_call_id": m.tool_call_id, "created_at": m.created_at}
            for m in messages
        ]
        self._log_l2_chat.load_messages(msg_dicts)

        _clear_layout(self._log_l2_time_layout)
        self._log_l2_time_list = []
        visible_idx = 0
        for m in messages:
            has_content = m.content and m.content.strip()
            has_tool_calls = bool(m.tool_calls)
            if m.role in ("assistant", "tool") and not has_content and not has_tool_calls:
                continue
            ts = getattr(m, "created_at", None)
            time_str = ts.strftime("%m-%d %H:%M") if isinstance(ts, datetime) else ""
            role_label = f"[{m.role}]"
            tool_badge = ""
            if m.role == "assistant" and m.tool_calls:
                tool_badge = " 🛠"
            self._log_l2_time_list.append({"time": time_str, "role": m.role, "content": m.content})
            btn = QPushButton(f"{time_str} {role_label}{tool_badge}")
            btn.setStyleSheet(
                "QPushButton { text-align: left; border: 1px solid #dce1e8; "
                "border-radius: 4px; padding: 4px; font-size: 11px; "
                "background-color: #ffffff; }"
                "QPushButton:hover { background-color: #f0f2f5; border: 1px solid #4f46e5; }"
            )
            btn.clicked.connect(lambda checked, i=visible_idx: self._scroll_log_l2_to(i))
            visible_idx += 1
            count = self._log_l2_time_layout.count()
            self._log_l2_time_layout.insertWidget(count - 1, btn)
        self._log_stacked.setCurrentIndex(1)

    def _scroll_log_l2_to(self, idx: int) -> None:
        self._log_l2_chat.scroll_to_message(idx)

    # ─── 외부에서 호출 ────────────────────────────────────────────

    def refresh(self) -> None:
        """다이얼로그가 열릴 때마다 DB에서 다시 읽음."""
        self._log_l1_page = 1
        self._log_l1_total_pages = 1
        self._load_log_level1()