"""CronTab — CRON + 1회성 작업 관리 탭 (4단계 드릴다운)."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.ui.settings_dialog import (
    _clear_layout,
    _detail_row,
    _make_pagination,
    _make_section,
    _run_async,
    _run_async_task,
    _update_pagination,
)

logger = logging.getLogger("psjba.ui.settings_tab_cron")

MAX_CONTENT_PREVIEW = 500


def _run_async_callback(coro: Any, callback: Optional[Callable[[Any], None]] = None) -> None:
    """편의 래퍼: task_store를 포함한 _run_async_task."""
    _run_async_task(coro, callback=callback)


class CronTab(QWidget):
    """CRON + 1회성 작업 관리 탭 — 4단계 드릴다운 탐색."""

    # job_type constants
    TYPE_CRON = "cron"
    TYPE_SCHEDULED = "scheduled"

    def __init__(self, dialog: Any, config: Any, db: Any, manager: Any = None) -> None:
        super().__init__()
        self._dialog = dialog
        self._config = config
        self._db = db
        self._manager = manager

        # L1 shared pagination
        self._l1_page: int = 1
        self._l1_total_pages: int = 1
        self._l1_filter: str = "all"  # all | cron | scheduled
        self._l1_show_archived: bool = False

        # L2 (session list for a cron job)
        self._l2_page: int = 1
        self._l2_total_pages: int = 1
        self._l2_job_id: Optional[str] = None
        self._l2_job_type: Optional[str] = None  # "cron" | "scheduled"
        self._l3_time_list: list[dict] = []

        # L4 detail
        self._l4_job_type: Optional[str] = None
        self._l4_job_id: Optional[str] = None

        self._async_tasks: list[Any] = []
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._cron_stacked = QStackedWidget()
        layout.addWidget(self._cron_stacked)

        self._cron_stacked.addWidget(self._build_level1())
        self._cron_stacked.addWidget(self._build_level2())
        self._cron_stacked.addWidget(self._build_level3())
        self._cron_stacked.addWidget(self._build_level4())

        self._cron_stacked.setCurrentIndex(0)

    # ================================================================
    # Level 1: 통합 작업 목록 (CRON + Scheduled)
    # ================================================================

    def _build_level1(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        # header + filter tabs
        header = QHBoxLayout()
        header_lbl = QLabel("작업 목록")
        header_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        header.addWidget(header_lbl)
        header.addStretch()

        self._l1_filter_btns: dict[str, QPushButton] = {}
        for key, label in [("all", "전체"), ("cron", "CRON"), ("scheduled", "job")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedWidth(70)
            btn.clicked.connect(lambda checked, k=key: self._on_l1_filter(k))
            header.addWidget(btn)
            self._l1_filter_btns[key] = btn
        self._update_l1_filter_style()

        # archive toggle
        self._l1_archive_btn = QPushButton("아카이브 보기")
        self._l1_archive_btn.setFixedWidth(120)
        self._l1_archive_btn.clicked.connect(self._on_toggle_archived)
        header.addWidget(self._l1_archive_btn)

        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._l1_card_container = QWidget()
        self._l1_card_layout = QVBoxLayout(self._l1_card_container)
        self._l1_card_layout.setContentsMargins(8, 8, 8, 8)
        self._l1_card_layout.setSpacing(4)
        self._l1_card_layout.addStretch()
        scroll.setWidget(self._l1_card_container)
        layout.addWidget(scroll)

        self._l1_pagination = _make_pagination(self._on_l1_prev, self._on_l1_next)
        layout.addLayout(self._l1_pagination)
        return w

    def _update_l1_filter_style(self) -> None:
        for key, btn in self._l1_filter_btns.items():
            active = key == self._l1_filter
            btn.setStyleSheet(
                "QPushButton { font-weight: bold; background-color: #4f46e5; color: #ffffff; }"
                if active else
                "QPushButton { background-color: #e2e8f0; color: #475569; }"
            )
            btn.setChecked(active)

    def _on_l1_filter(self, key: str) -> None:
        self._l1_filter = key
        self._l1_page = 1
        self._update_l1_filter_style()
        self._load_level1()

    def _on_toggle_archived(self) -> None:
        self._l1_show_archived = not self._l1_show_archived
        self._l1_archive_btn.setText(
            "아카이브 숨기기" if self._l1_show_archived else "아카이브 보기"
        )
        self._l1_page = 1
        self._load_level1()

    def _on_l1_prev(self) -> None:
        if self._l1_page > 1:
            self._l1_page -= 1
            self._load_level1()

    def _on_l1_next(self) -> None:
        if self._l1_page < self._l1_total_pages:
            self._l1_page += 1
            self._load_level1()

    def _load_level1(self) -> None:
        _clear_layout(self._l1_card_layout)
        page = self._l1_page
        filt = self._l1_filter

        async def _fetch() -> Any:
            page_size = 10
            show_archived = self._l1_show_archived

            cron_jobs, cron_total = await self._db.list_cron_jobs(
                page=1, page_size=999, include_archived=True,
            ) if filt in ("all", "cron") else ([], 0)

            sched_jobs, sched_total = await self._db.list_scheduled_jobs_paginated(
                page=1, page_size=999,
            ) if filt in ("all", "scheduled") else ([], 0)

            if not show_archived:
                # 활성만: cron=enable, scheduled=pending|running
                cron_jobs = [j for j in (cron_jobs or []) if j.status == "enable"]
                sched_jobs = [j for j in (sched_jobs or []) if j.status in ("pending", "running")]

            combined: list[tuple[str, Any]] = []
            combined.extend((self.TYPE_CRON, j) for j in (cron_jobs or []))
            combined.extend((self.TYPE_SCHEDULED, j) for j in (sched_jobs or []))
            combined.sort(
                key=lambda x: getattr(x[1], "created_at", datetime.min),
                reverse=True,
            )

            total = len(combined)
            total_pages = max(1, (total + page_size - 1) // page_size)
            start = (page - 1) * page_size
            page_items = combined[start:start + page_size]
            return page_items, total, total_pages

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_fetch(), callback=self._on_l1_loaded,
                                task_store=self._async_tasks)
                return
        except RuntimeError:
            pass

        result = _run_async(_fetch())
        self._on_l1_loaded(result)

    def _on_l1_loaded(self, result) -> None:
        _clear_layout(self._l1_card_layout)
        page_items, total, total_pages = result if result else ([], 0, 1)
        self._l1_total_pages = total_pages
        _update_pagination(self._l1_pagination, self._l1_page, self._l1_total_pages)

        if not page_items:
            lbl = QLabel("등록된 작업이 없습니다")
            lbl.setStyleSheet("color: #64748b; border: none;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            count = self._l1_card_layout.count()
            self._l1_card_layout.insertWidget(count - 1, lbl)
            return

        for job_type, job in page_items:
            card = self._make_job_card(job_type, job)
            count = self._l1_card_layout.count()
            self._l1_card_layout.insertWidget(count - 1, card)

    def _make_job_card(self, job_type: str, job: Any) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { border: 1px solid #dce1e8; border-radius: 8px; padding: 10px; "
            "background-color: #ffffff; }"
            "QFrame:hover { background-color: #f8f9fc; border: 1px solid #4f46e5; }"
        )
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(frame)

        # ── type badge ──
        if job_type == self.TYPE_CRON:
            badge = QLabel("CRON")
            badge.setStyleSheet(
                "background-color: #4f46e5; color: #ffffff; border-radius: 8px; "
                "padding: 2px 8px; font-size: 10px; font-weight: bold;"
            )
        else:
            badge = QLabel("JOB")
            badge.setStyleSheet(
                "background-color: #f59e0b; color: #ffffff; border-radius: 8px; "
                "padding: 2px 8px; font-size: 10px; font-weight: bold;"
            )
        badge.setFixedWidth(50)
        layout.addWidget(badge)

        # ── schedule info ──
        if job_type == self.TYPE_CRON:
            schedule_text = getattr(job, "schedule", "") or ""
        else:
            st = getattr(job, "scheduled_at", None)
            schedule_text = st.strftime("%m-%d %H:%M") if isinstance(st, datetime) else str(st or "")
        sched_lbl = QLabel(schedule_text)
        sched_lbl.setFixedWidth(110)
        sched_lbl.setStyleSheet("border: none; font-weight: bold; color: #4f46e5;")
        layout.addWidget(sched_lbl)

        # ── status chip ──
        st = getattr(job, "status", "unknown")
        if job_type == self.TYPE_CRON:
            chip_color = {
                "enable": "#10b981", "disable": "#94a3b8",
            }.get(st, "#94a3b8")
        else:
            chip_color = {
                "pending": "#f59e0b", "running": "#3b82f6",
                "done": "#10b981", "error": "#ef4444",
                "cancelled": "#94a3b8",
            }.get(st, "#94a3b8")
        chip = QLabel(st.upper())
        chip.setFixedSize(70, 22)
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip.setStyleSheet(
            f"background-color: {chip_color}; color: #ffffff; border-radius: 11px; "
            "font-size: 10px; font-weight: bold;"
        )
        layout.addWidget(chip)

        # ── name / prompt ──
        name = getattr(job, "name", "") or ""
        prompt = getattr(job, "prompt", "") or ""
        label_text = name if name else prompt
        if len(label_text) > 40:
            label_text = label_text[:40] + "..."
        prompt_lbl = QLabel(label_text or "(내용 없음)")
        prompt_lbl.setStyleSheet("border: none; color: #64748b;")
        layout.addWidget(prompt_lbl)
        layout.addStretch()

        # ── timestamp ──
        ts_field = "updated_at" if job_type == self.TYPE_CRON else "scheduled_at"
        last = getattr(job, ts_field, "")
        if isinstance(last, datetime):
            last_str = last.strftime("%m-%d %H:%M")
        else:
            last_str = str(last)[:16] if last else "-"
        last_lbl = QLabel(last_str)
        last_lbl.setFixedWidth(90)
        last_lbl.setStyleSheet("border: none; color: #94a3b8; font-size: 11px;")
        layout.addWidget(last_lbl)

        job_id = getattr(job, "id", "")
        frame.mousePressEvent = lambda e, jt=job_type, jid=job_id: self._on_job_card_click(jt, jid)
        return frame

    def _on_job_card_click(self, job_type: str, job_id: str) -> None:
        # 둘 다 L2 상세화면으로
        self._l4_job_type = job_type
        self._l4_job_id = job_id
        self._render_l2_detail(job_type, job_id)

    # ================================================================
    # Level 2: cron/job 상세 페이지
    # ================================================================

    def _build_level3(self) -> QWidget:
        """L3: cron 세션 로그 목록."""
        w = QWidget()
        layout = QVBoxLayout(w)

        header = QHBoxLayout()
        back_btn = QPushButton("◀ 상세")
        back_btn.clicked.connect(lambda: self._cron_stacked.setCurrentIndex(1))
        header.addWidget(back_btn)
        header.addStretch()
        self._l3_title = QLabel("")
        self._l3_title.setStyleSheet("font-weight: bold; font-size: 12px;")
        header.addWidget(self._l3_title)
        header.addStretch()
        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._l3_container = QWidget()
        self._l3_layout = QVBoxLayout(self._l3_container)
        self._l3_layout.setContentsMargins(8, 8, 8, 8)
        self._l3_layout.setSpacing(4)
        self._l3_layout.addStretch()
        scroll.setWidget(self._l3_container)
        layout.addWidget(scroll)

        self._l3_pagination = _make_pagination(self._on_l3_prev, self._on_l3_next)
        layout.addLayout(self._l3_pagination)
        return w

    def _on_l3_prev(self) -> None:
        if self._l2_page > 1:
            self._l2_page -= 1
            self._load_level3()

    def _on_l3_next(self) -> None:
        if self._l2_page < self._l2_total_pages:
            self._l2_page += 1
            self._load_level3()

    def _load_level3(self) -> None:
        _clear_layout(self._l3_layout)
        job_id = self._l2_job_id
        job_type = self._l2_job_type or "cron"
        page = self._l2_page

        async def _fetch() -> Any:
            kwargs = {}
            if job_type == "cron":
                kwargs["cron_job_id"] = job_id
            else:
                kwargs["scheduled_job_id"] = job_id
            sessions, total = await self._db.get_job_session_logs(
                **kwargs, page=page, page_size=10,
            ) if job_id else ([], 0)
            total_pages = max(1, (total + 10 - 1) // 10) if total else 1
            name = ""
            if job_id:
                if job_type == "cron":
                    job = await self._db.get_cron_job(job_id)
                else:
                    job = await self._db.get_scheduled_job(job_id)
                if job:
                    name = getattr(job, "name", "") or "-"
            return sessions, total_pages, name

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_fetch(), callback=self._on_l3_session_loaded,
                                task_store=self._async_tasks)
                return
        except RuntimeError:
            pass
        result = _run_async(_fetch())
        self._on_l3_session_loaded(result)

    def _on_l3_session_loaded(self, result) -> None:
        _clear_layout(self._l3_layout)
        sessions, total_pages, name = result if result else ([], 1, "")
        self._l2_total_pages = total_pages
        _update_pagination(self._l3_pagination, self._l2_page, self._l2_total_pages)
        self._l3_title.setText(name[:40])
        self._cron_stacked.setCurrentIndex(2)

        if not sessions:
            lbl = QLabel("실행 기록이 없습니다")
            lbl.setStyleSheet("color: #64748b; border: none;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            count = self._l3_layout.count()
            self._l3_layout.insertWidget(count - 1, lbl)
            return

        for s in sessions:
            card = self._make_l3_session_card(s)
            count = self._l3_layout.count()
            self._l3_layout.insertWidget(count - 1, card)

    def _make_l3_session_card(self, s: dict) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { border: 1px solid #dce1e8; border-radius: 8px; padding: 8px; "
            "background-color: #ffffff; }"
            "QFrame:hover { background-color: #f8f9fc; border: 1px solid #4f46e5; }"
        )
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(frame)

        title = s.get("title", "")
        title_lbl = QLabel(title[:50] if title else "(제목 없음)")
        title_lbl.setStyleSheet("border: none; font-weight: bold;")
        layout.addWidget(title_lbl)
        layout.addStretch()

        ts = s.get("created_at", "")
        ts_str = str(ts)[:16] if ts else "-"
        ts_lbl = QLabel(ts_str)
        ts_lbl.setStyleSheet("border: none; color: #94a3b8; font-size: 11px;")
        layout.addWidget(ts_lbl)

        resp = s.get("last_response", "")
        if resp:
            resp_text = resp[:30] + "..." if len(resp) > 30 else resp
            resp_lbl = QLabel(resp_text)
            resp_lbl.setStyleSheet("border: none; color: #64748b; font-size: 11px;")
            layout.addWidget(resp_lbl)

        session_id = s.get("id", "")
        frame.mousePressEvent = lambda e, sid=session_id: self._on_l3_session_click(sid)
        return frame

    def _on_l3_session_click(self, session_id: str) -> None:
        """L3 session click → L4 chat detail."""
        async def _fetch() -> Any:
            return await self._db.get_messages(session_id)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_fetch(), callback=self._on_l4_loaded,
                                task_store=self._async_tasks)
                return
        except RuntimeError:
            pass

        messages = _run_async(_fetch()) or []
        self._on_l4_loaded(messages)

    # ================================================================
    # Level 3: cron 세션 로그 목록
    # ================================================================

    def _build_level4(self) -> QWidget:
        """L4: cron/job 로그 상세 채팅 + 타임라인."""
        w = QWidget()
        layout = QVBoxLayout(w)

        header = QHBoxLayout()
        back_btn = QPushButton("◀ 세션 목록")
        back_btn.clicked.connect(lambda: self._cron_stacked.setCurrentIndex(2))
        header.addWidget(back_btn)
        header.addStretch()
        close_btn = QPushButton("✕ 닫기")
        close_btn.clicked.connect(lambda: self._cron_stacked.setCurrentIndex(0))
        header.addWidget(close_btn)
        layout.addLayout(header)

        split = QHBoxLayout()
        # left panel: 타임라인
        self._l4_time_container = QWidget()
        self._l4_time_layout = QVBoxLayout(self._l4_time_container)
        self._l4_time_layout.setContentsMargins(4, 4, 4, 4)
        self._l4_time_layout.setSpacing(2)
        self._l4_time_layout.addStretch()
        time_scroll = QScrollArea()
        time_scroll.setWidgetResizable(True)
        time_scroll.setWidget(self._l4_time_container)
        time_scroll.setMaximumWidth(160)
        split.addWidget(time_scroll)

        # right panel: ChatWidget
        from src.ui.chat_widget import ChatWidget
        self._l4_chat = ChatWidget()
        split.addWidget(self._l4_chat)

        layout.addLayout(split)
        return w

    def _on_l4_loaded(self, messages: Any) -> None:
        if not messages:
            messages = []
        msg_dicts = [{"role": m.role, "content": m.content} for m in messages]
        self._l4_chat.load_messages(msg_dicts)

        _clear_layout(self._l4_time_layout)
        self._l3_time_list = []
        visible_idx = 0
        for m in messages:
            has_content = m.content and m.content.strip()
            if m.role in ("assistant", "tool") and not has_content:
                continue
            ts = getattr(m, "created_at", None)
            time_str = ts.strftime("%m-%d %H:%M") if isinstance(ts, datetime) else ""
            self._l3_time_list.append({"time": time_str, "role": m.role, "content": m.content})
            btn = QPushButton(f"{time_str} [{m.role}]")
            btn.setStyleSheet(
                "QPushButton { text-align: left; border: 1px solid #dce1e8; "
                "border-radius: 4px; padding: 4px; font-size: 11px; "
                "background-color: #ffffff; }"
                "QPushButton:hover { background-color: #f0f2f5; border: 1px solid #4f46e5; }"
            )
            btn.clicked.connect(lambda checked, i=visible_idx: self._on_l4_time_click(i))
            visible_idx += 1
            count = self._l4_time_layout.count()
            self._l4_time_layout.insertWidget(count - 1, btn)
        self._cron_stacked.setCurrentIndex(3)

    def _on_l4_time_click(self, idx: int) -> None:
        self._l4_chat.scroll_to_message(idx)

    # ================================================================
    # Level 4: 채팅 상세 (타임라인)
    # ================================================================

    def _build_level2(self) -> QWidget:
        """L2: cron/job 상세 페이지."""
        w = QWidget()
        layout = QVBoxLayout(w)
        header = QHBoxLayout()
        back_btn = QPushButton("◀ 목록으로")
        back_btn.clicked.connect(lambda: self._cron_stacked.setCurrentIndex(0))
        header.addWidget(back_btn)
        header.addStretch()
        close_btn = QPushButton("✕ 닫기")
        close_btn.clicked.connect(lambda: self._cron_stacked.setCurrentIndex(0))
        header.addWidget(close_btn)
        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._l2_detail_container = QWidget()
        self._l2_detail_layout = QVBoxLayout(self._l2_detail_container)
        scroll.setWidget(self._l2_detail_container)
        layout.addWidget(scroll)
        return w

    def _render_l2_detail(self, job_type: str, job_id: str) -> None:
        _clear_layout(self._l2_detail_layout)

        if job_type == self.TYPE_CRON:
            self._render_l2_cron_detail(job_id)
        else:
            self._render_l2_scheduled_detail(job_id)

    # ── CRON detail ──

    def _render_l2_cron_detail(self, job_id: str) -> None:
        async def _fetch() -> Any:
            job = await self._db.get_cron_job(job_id)
            notes = await self._db.get_cron_notes(job_id)
            return job, notes

        def _callback(result) -> None:
            job, notes = result if result else (None, "")
            if not job:
                return
            self._cron_stacked.setCurrentIndex(1)
            layout = self._l2_detail_layout

            layout.addWidget(_detail_row("ID", getattr(job, "id", "")))
            layout.addWidget(_detail_row("이름", getattr(job, "name", "") or "-"))
            layout.addWidget(_detail_row("Cron", getattr(job, "schedule", "") or "-"))
            layout.addWidget(_detail_row("Prompt", getattr(job, "prompt", "") or "-"))
            layout.addWidget(_detail_row("Status", getattr(job, "status", "")))
            layout.addWidget(_detail_row("Created", str(getattr(job, "created_at", ""))[:19]))
            layout.addWidget(_detail_row("Updated", str(getattr(job, "updated_at", ""))[:19]))

            enable_btn = QPushButton("비활성화" if getattr(job, "status", "") == "enable" else "활성화")
            enable_btn.clicked.connect(lambda: self._on_l2_cron_toggle(job_id))
            layout.addWidget(enable_btn)

            notes_box = QPlainTextEdit()
            notes_box.setPlainText(notes)
            notes_box.setFixedHeight(100)
            notes_lbl = QLabel("메모:")
            notes_lbl.setStyleSheet("border: none; font-weight: bold;")
            layout.addWidget(notes_lbl)
            layout.addWidget(notes_box)

            save_notes_btn = QPushButton("메모 저장")
            save_notes_btn.clicked.connect(
                lambda: self._on_l2_cron_save_notes(job_id, notes_box.toPlainText()))
            layout.addWidget(save_notes_btn)

            sess_btn = QPushButton("📋 세션 로그 보기")
            sess_btn.clicked.connect(lambda: self._on_l2_to_l3(job_id, "cron"))
            layout.addWidget(sess_btn)

            del_btn = QPushButton("삭제")
            del_btn.setStyleSheet("color: #ef4444;")
            del_btn.clicked.connect(lambda: self._on_l2_cron_delete(job_id))
            layout.addWidget(del_btn)

            layout.addStretch()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_fetch(), callback=_callback, task_store=self._async_tasks)
                return
        except RuntimeError:
            pass

        result = _run_async(_fetch())
        _callback(result)

    def _on_l2_cron_toggle(self, job_id: str) -> None:
        async def _toggle() -> Any:
            return await self._db.toggle_cron_job(job_id)

        def _reload(_result) -> None:
            self._render_cron_detail(job_id)
            self._load_level1()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_toggle(), callback=_reload, task_store=self._async_tasks)
                return
        except RuntimeError:
            pass

        _run_async(_toggle())
        self._load_level1()

    def _on_l2_cron_save_notes(self, job_id: str, notes: str) -> None:
        async def _save() -> None:
            return await self._db.update_cron_notes(job_id, notes)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_save(), task_store=self._async_tasks)
                return
        except RuntimeError:
            pass
        _run_async(_save())

    def _on_l2_cron_delete(self, job_id: str) -> None:
        async def _delete() -> None:
            await self._db.delete_cron_job(job_id)

        def _reload(_result) -> None:
            self._cron_stacked.setCurrentIndex(0)
            self._load_level1()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_delete(), callback=_reload, task_store=self._async_tasks)
                return
        except RuntimeError:
            pass
        _run_async(_delete())
        self._cron_stacked.setCurrentIndex(0)
        self._load_level1()

    def _on_l2_to_l3(self, job_id: str, job_type: str = "cron") -> None:
        """L2 detail → L3 session log list."""
        self._l2_job_id = job_id
        self._l2_job_type = job_type
        self._l2_page = 1
        self._load_level3()

    # ── Scheduled detail ──

    def _render_l2_scheduled_detail(self, job_id: str) -> None:
        async def _fetch() -> Any:
            job = await self._db.get_scheduled_job(job_id)
            return job

        def _callback(result) -> None:
            job = result
            if not job:
                return
            self._cron_stacked.setCurrentIndex(1)
            layout = self._l2_detail_layout

            layout.addWidget(_detail_row("ID", getattr(job, "id", "")))
            layout.addWidget(_detail_row("이름", getattr(job, "name", "") or "-"))
            st = getattr(job, "scheduled_at", None)
            st_str = st.strftime("%Y-%m-%d %H:%M:%S") if isinstance(st, datetime) else str(st or "-")
            layout.addWidget(_detail_row("예약 시간", st_str))
            layout.addWidget(_detail_row("Prompt", getattr(job, "prompt", "") or "-"))
            layout.addWidget(_detail_row("Status", getattr(job, "status", "")))
            err = getattr(job, "error_message", None)
            if err:
                layout.addWidget(_detail_row("오류", err))

            if getattr(job, "status", "") == "pending":
                cancel_btn = QPushButton("job 취소")
                cancel_btn.setStyleSheet("color: #ef4444;")
                cancel_btn.clicked.connect(lambda: self._on_l2_scheduled_cancel(job_id))
                layout.addWidget(cancel_btn)

            # JOB도 세션 로그가 있을 수 있음
            sess_btn = QPushButton("📋 세션 로그 보기")
            sess_btn.clicked.connect(lambda: self._on_l2_to_l3(job_id, "scheduled"))
            layout.addWidget(sess_btn)

            layout.addStretch()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_fetch(), callback=_callback, task_store=self._async_tasks)
                return
        except RuntimeError:
            pass

        result = _run_async(_fetch())
        _callback(result)

    def _on_l2_scheduled_cancel(self, job_id: str) -> None:
        async def _cancel() -> None:
            from src.models.schemas import ScheduledJob
            job = await self._db.get_scheduled_job(job_id)
            if job:
                job.status = "cancelled"
                job.updated_at = datetime.now()
                await self._db.update_scheduled_job(job)

        def _reload(_result) -> None:
            self._cron_stacked.setCurrentIndex(0)
            self._load_level1()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_cancel(), callback=_reload, task_store=self._async_tasks)
                return
        except RuntimeError:
            pass
        _run_async(_cancel())
        self._cron_stacked.setCurrentIndex(0)
        self._load_level1()

    # ================================================================
    # 외부에서 호출
    # ================================================================

    def refresh(self) -> None:
        """다이얼로그가 열릴 때마다 DB에서 다시 읽음."""
        self._l1_page = 1
        self._l1_total_pages = 1
        self._load_level1()