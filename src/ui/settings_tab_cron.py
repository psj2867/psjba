"""CronTab — CRON 작업 관리 탭 (4단계 드릴다운)."""

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
    """CRON 작업 관리 탭 — 4단계 드릴다운 탐색."""

    def __init__(self, dialog: Any, config: Any, db: Any, manager: Any = None) -> None:
        super().__init__()
        self._dialog = dialog
        self._config = config
        self._db = db
        self._manager = manager
        self._cron_l1_page: int = 1
        self._cron_l1_total_pages: int = 1
        self._cron_l2_page: int = 1
        self._cron_l2_total_pages: int = 1
        self._cron_l2_cron_job_id: Optional[str] = None
        self._cron_l3_time_list: list[dict] = []
        self._cron_show_archived: bool = False
        self._async_tasks: list[Any] = []
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._cron_stacked = QStackedWidget()
        layout.addWidget(self._cron_stacked)

        self._cron_stacked.addWidget(self._build_cron_level1())
        self._cron_stacked.addWidget(self._build_cron_level2())
        self._cron_stacked.addWidget(self._build_cron_level3())
        self._cron_stacked.addWidget(self._build_cron_level4())

        self._cron_stacked.setCurrentIndex(0)

    # ─── Level 1: 크론 job 목록 ──────────────────────────────────

    def _build_cron_level1(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        header = QHBoxLayout()
        header_lbl = QLabel("CRON 작업")
        header_lbl.setStyleSheet("font-weight: bold; font-size: 13px;")
        header.addWidget(header_lbl)
        header.addStretch()
        self._cron_archive_btn = QPushButton("아카이브 숨기기" if self._cron_show_archived else "아카이브 보기")
        self._cron_archive_btn.setFixedWidth(120)
        self._cron_archive_btn.clicked.connect(self._on_toggle_archived)
        header.addWidget(self._cron_archive_btn)
        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._cron_card_container = QWidget()
        self._cron_card_layout = QVBoxLayout(self._cron_card_container)
        self._cron_card_layout.setContentsMargins(8, 8, 8, 8)
        self._cron_card_layout.setSpacing(4)
        self._cron_card_layout.addStretch()
        scroll.setWidget(self._cron_card_container)
        layout.addWidget(scroll)

        self._cron_l1_pagination = _make_pagination(self._on_cron_l1_prev, self._on_cron_l1_next)
        layout.addLayout(self._cron_l1_pagination)
        return w

    def _on_toggle_archived(self) -> None:
        self._cron_show_archived = not self._cron_show_archived
        self._cron_archive_btn.setText(
            "아카이브 숨기기" if self._cron_show_archived else "아카이브 보기"
        )
        self._load_cron_level1()

    def _on_cron_l1_prev(self) -> None:
        if self._cron_l1_page > 1:
            self._cron_l1_page -= 1
            self._load_cron_level1()

    def _on_cron_l1_next(self) -> None:
        if self._cron_l1_page < self._cron_l1_total_pages:
            self._cron_l1_page += 1
            self._load_cron_level1()

    def _load_cron_level1(self) -> None:
        _clear_layout(self._cron_card_layout)
        page = self._cron_l1_page
        show_archived = self._cron_show_archived

        async def _fetch() -> Any:
            return await self._db.list_cron_jobs(
                page=page, page_size=10, include_archived=show_archived
            )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_fetch(), callback=self._on_cron_l1_loaded,
                                task_store=self._async_tasks)
                return
        except RuntimeError:
            pass

        result = _run_async(_fetch())
        self._on_cron_l1_loaded(result)

    def _on_cron_l1_loaded(self, result) -> None:
        _clear_layout(self._cron_card_layout)
        jobs, total = result if result else ([], 0)
        page_size = 10
        self._cron_l1_total_pages = max(1, (total + page_size - 1) // page_size)
        _update_pagination(self._cron_l1_pagination, self._cron_l1_page, self._cron_l1_total_pages)

        if not jobs:
            lbl = QLabel("등록된 CRON 작업이 없습니다")
            lbl.setStyleSheet("color: #64748b; border: none;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            count = self._cron_card_layout.count()
            self._cron_card_layout.insertWidget(count - 1, lbl)
            return

        for job in jobs:
            card = self._make_cron_card(job)
            count = self._cron_card_layout.count()
            self._cron_card_layout.insertWidget(count - 1, card)

    def _make_cron_card(self, job: Any) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { border: 1px solid #dce1e8; border-radius: 8px; padding: 10px; "
            "background-color: #ffffff; }"
            "QFrame:hover { background-color: #f8f9fc; border: 1px solid #4f46e5; }"
        )
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(frame)

        # CRON expression
        cron_expr = getattr(job, "cron_expression", "") or ""
        cron_lbl = QLabel(cron_expr or f"[{getattr(job, 'interval', '?')}]")
        cron_lbl.setFixedWidth(100)
        cron_lbl.setStyleSheet("border: none; font-weight: bold; color: #4f46e5;")
        layout.addWidget(cron_lbl)

        # status chip
        st = getattr(job, "status", "unknown")
        chip_color = {
            "enable": "#10b981", "disable": "#94a3b8",
            "archived": "#f59e0b",
        }.get(st, "#94a3b8")
        chip = QLabel(st.upper())
        chip.setFixedSize(70, 22)
        chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip.setStyleSheet(
            f"background-color: {chip_color}; color: #ffffff; border-radius: 11px; "
            "font-size: 10px; font-weight: bold;"
        )
        layout.addWidget(chip)

        # prompt
        prompt = getattr(job, "prompt", "") or ""
        if len(prompt) > 40:
            prompt = prompt[:40] + "..."
        prompt_lbl = QLabel(prompt or "(프롬프트 없음)")
        prompt_lbl.setStyleSheet("border: none; color: #64748b;")
        layout.addWidget(prompt_lbl)

        # last run
        last = getattr(job, "updated_at", "")
        if isinstance(last, datetime):
            last_str = last.strftime("%m-%d %H:%M")
        else:
            last_str = str(last)[:16] if last else "-"
        last_lbl = QLabel(last_str)
        last_lbl.setFixedWidth(90)
        last_lbl.setStyleSheet("border: none; color: #94a3b8; font-size: 11px;")
        layout.addWidget(last_lbl)

        job_id = getattr(job, "id", "")
        frame.mousePressEvent = lambda e, jid=job_id: self._on_cron_card_click(jid)
        return frame

    # ─── Level 2: 크론 job 선택 → 세션 목록 ──────────────────────

    def _build_cron_level2(self) -> QWidget:
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
        self._cron_session_container = QWidget()
        self._cron_session_layout = QVBoxLayout(self._cron_session_container)
        self._cron_session_layout.setContentsMargins(8, 8, 8, 8)
        self._cron_session_layout.setSpacing(4)
        self._cron_session_layout.addStretch()
        scroll.setWidget(self._cron_session_container)
        layout.addWidget(scroll)

        self._cron_l2_pagination = _make_pagination(self._on_cron_l2_prev, self._on_cron_l2_next)
        layout.addLayout(self._cron_l2_pagination)
        return w

    def _on_cron_l2_prev(self) -> None:
        if self._cron_l2_page > 1:
            self._cron_l2_page -= 1
            self._load_cron_level2()

    def _on_cron_l2_next(self) -> None:
        if self._cron_l2_page < self._cron_l2_total_pages:
            self._cron_l2_page += 1
            self._load_cron_level2()

    def _on_cron_card_click(self, job_id: str) -> None:
        self._cron_l2_cron_job_id = job_id
        self._cron_l2_page = 1
        self._load_cron_level2()
        self._cron_stacked.setCurrentIndex(1)

    def _load_cron_level2(self) -> None:
        _clear_layout(self._cron_session_layout)

        async def _fetch() -> Any:
            return await self._db.list_sessions(
                cron_job_id=self._cron_l2_cron_job_id,
                page=self._cron_l2_page,
                page_size=20,
            )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_fetch(), callback=self._on_cron_l2_loaded,
                                task_store=self._async_tasks)
                return
        except RuntimeError:
            pass

        result = _run_async(_fetch())
        self._on_cron_l2_loaded(result)

    def _on_cron_l2_loaded(self, result) -> None:
        _clear_layout(self._cron_session_layout)
        sessions, total = result if result else ([], 0)
        page_size = 20
        self._cron_l2_total_pages = max(1, (total + page_size - 1) // page_size)
        _update_pagination(self._cron_l2_pagination, self._cron_l2_page, self._cron_l2_total_pages)

        if not sessions:
            lbl = QLabel("실행 이력이 없습니다")
            lbl.setStyleSheet("color: #64748b; border: none;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            count = self._cron_session_layout.count()
            self._cron_session_layout.insertWidget(count - 1, lbl)
            return

        for s in sessions:
            row = self._make_cron_session_row(s)
            count = self._cron_session_layout.count()
            self._cron_session_layout.insertWidget(count - 1, row)

    def _make_cron_session_row(self, session: dict) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { border: 1px solid #dce1e8; border-radius: 6px; padding: 8px; "
            "background-color: #ffffff; }"
            "QFrame:hover { background-color: #e8ecf0; border: 1px solid #4f46e5; }"
        )
        frame.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(frame)

        created = session.get("created_at", "")
        if isinstance(created, str) and len(created) >= 16:
            time_txt = created[5:16].replace("T", " ")
        else:
            time_txt = str(created)[:16]
        time_lbl = QLabel(time_txt)
        time_lbl.setFixedWidth(100)
        time_lbl.setStyleSheet("border: none; color: #64748b;")
        layout.addWidget(time_lbl)

        sid = session.get("id", "")[:8] + "..."
        sid_lbl = QLabel(sid)
        sid_lbl.setFixedWidth(70)
        sid_lbl.setStyleSheet("border: none; color: #94a3b8; font-size: 11px;")
        layout.addWidget(sid_lbl)

        prompt = session.get("first_prompt", "") or ""
        prompt_short = prompt[:50] + ("..." if len(prompt) > 50 else "")
        prompt_lbl = QLabel(prompt_short or "(내용 없음)")
        prompt_lbl.setStyleSheet("border: none; color: #cbd5e1;")
        layout.addWidget(prompt_lbl)

        frame.mousePressEvent = (
            lambda e, sid=session.get("id"): self._on_cron_l3_click(sid))
        return frame

    # ─── Level 3: 세션 선택 → 타임라인 ──────────────────────────────

    def _build_cron_level3(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)

        header = QHBoxLayout()
        back_btn = QPushButton("◀ 세션 목록으로")
        back_btn.clicked.connect(lambda: self._cron_stacked.setCurrentIndex(1))
        header.addWidget(back_btn)
        header.addStretch()
        close_btn = QPushButton("✕ 닫기")
        close_btn.clicked.connect(lambda: self._cron_stacked.setCurrentIndex(1))
        header.addWidget(close_btn)
        layout.addLayout(header)

        split = QHBoxLayout()

        # left panel: time list
        self._cron_l3_time_container = QWidget()
        self._cron_l3_time_container.setMaximumWidth(150)
        self._cron_l3_time_layout = QVBoxLayout(self._cron_l3_time_container)
        self._cron_l3_time_layout.setContentsMargins(4, 4, 4, 4)
        self._cron_l3_time_layout.setSpacing(2)
        self._cron_l3_time_layout.addStretch()
        time_scroll = QScrollArea()
        time_scroll.setWidgetResizable(True)
        time_scroll.setWidget(self._cron_l3_time_container)
        time_scroll.setMaximumWidth(160)
        split.addWidget(time_scroll)

        # right panel: ChatWidget
        from src.ui.chat_widget import ChatWidget
        self._cron_l3_chat = ChatWidget()
        split.addWidget(self._cron_l3_chat)

        layout.addLayout(split)
        return w

    def _on_cron_l3_click(self, session_id: str) -> None:
        async def _fetch() -> Any:
            return await self._db.get_messages(session_id)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_fetch(), callback=self._on_cron_l3_loaded,
                                task_store=self._async_tasks)
                return
        except RuntimeError:
            pass

        messages = _run_async(_fetch()) or []
        self._on_cron_l3_loaded(messages)

    def _on_cron_l3_loaded(self, messages: Any) -> None:
        if not messages:
            messages = []
        msg_dicts = [{"role": m.role, "content": m.content} for m in messages]
        self._cron_l3_chat.load_messages(msg_dicts)

        _clear_layout(self._cron_l3_time_layout)
        self._cron_l3_time_list = []
        visible_idx = 0
        for m in messages:
            has_content = m.content and m.content.strip()
            if m.role in ("assistant", "tool") and not has_content:
                continue
            ts = getattr(m, "created_at", None)
            time_str = ts.strftime("%m-%d %H:%M") if isinstance(ts, datetime) else ""
            self._cron_l3_time_list.append({"time": time_str, "role": m.role, "content": m.content})
            btn = QPushButton(f"{time_str} [{m.role}]")
            btn.setStyleSheet(
                "QPushButton { text-align: left; border: 1px solid #dce1e8; "
                "border-radius: 4px; padding: 4px; font-size: 11px; "
                "background-color: #ffffff; }"
                "QPushButton:hover { background-color: #f0f2f5; border: 1px solid #4f46e5; }"
            )
            btn.clicked.connect(lambda checked, i=visible_idx: self._on_cron_l3_time_click(i))
            visible_idx += 1
            count = self._cron_l3_time_layout.count()
            self._cron_l3_time_layout.insertWidget(count - 1, btn)
        self._cron_stacked.setCurrentIndex(2)

    def _on_cron_l3_time_click(self, idx: int) -> None:
        self._cron_l3_chat.scroll_to_message(idx)

    # ─── Level 4: 크론 상세 정보 (읽기 전용) ────────────────────────

    def _build_cron_level4(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        header = QHBoxLayout()
        back_btn = QPushButton("◀ CRON 목록으로")
        back_btn.clicked.connect(lambda: self._cron_stacked.setCurrentIndex(0))
        header.addWidget(back_btn)
        header.addStretch()
        close_btn = QPushButton("✕ 닫기")
        close_btn.clicked.connect(lambda: self._cron_stacked.setCurrentIndex(0))
        header.addWidget(close_btn)
        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._cron_detail_container = QWidget()
        self._cron_detail_layout = QVBoxLayout(self._cron_detail_container)
        scroll.setWidget(self._cron_detail_container)
        layout.addWidget(scroll)
        return w

    def _render_cron_detail(self, job_id: str) -> None:
        _clear_layout(self._cron_detail_layout)

        async def _fetch() -> Any:
            job = await self._db.get_cron_job(job_id)
            notes = await self._db.get_cron_notes(job_id)
            return job, notes

        def _callback(result) -> None:
            job, notes = result if result else (None, "")
            if not job:
                return
            self._cron_stacked.setCurrentIndex(3)
            layout = self._cron_detail_layout

            layout.addLayout(_detail_row("ID", getattr(job, "id", "")))
            layout.addLayout(_detail_row("Cron", getattr(job, "cron_expression", "") or "-"))
            layout.addLayout(_detail_row("Prompt", getattr(job, "prompt", "") or "-"))
            layout.addLayout(_detail_row("Status", getattr(job, "status", "")))
            layout.addLayout(_detail_row("Created", str(getattr(job, "created_at", ""))[:19]))
            layout.addLayout(_detail_row("Updated", str(getattr(job, "updated_at", ""))[:19]))

            enable_btn = QPushButton("비활성화" if getattr(job, "status", "") == "enable" else "활성화")
            enable_btn.clicked.connect(lambda: self._on_cron_l4_toggle(job_id))
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
                lambda: self._on_cron_l4_save_notes(job_id, notes_box.toPlainText()))
            layout.addWidget(save_notes_btn)

            del_btn = QPushButton("삭제")
            del_btn.setStyleSheet("color: #ef4444;")
            del_btn.clicked.connect(lambda: self._on_cron_l4_delete(job_id))
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

    def _on_cron_l4_toggle(self, job_id: str) -> None:
        async def _toggle() -> Any:
            return await self._db.toggle_cron_job(job_id)

        def _reload(_result) -> None:
            self._render_cron_detail(job_id)
            self._load_cron_level1()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_toggle(), callback=_reload, task_store=self._async_tasks)
                return
        except RuntimeError:
            pass

        _run_async(_toggle())
        self._load_cron_level1()

    def _on_cron_l4_save_notes(self, job_id: str, notes: str) -> None:
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

    def _on_cron_l4_delete(self, job_id: str) -> None:
        async def _delete() -> None:
            await self._db.delete_cron_job(job_id)

        def _reload(_result) -> None:
            self._cron_stacked.setCurrentIndex(0)
            self._load_cron_level1()

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                _run_async_task(_delete(), callback=_reload, task_store=self._async_tasks)
                return
        except RuntimeError:
            pass
        _run_async(_delete())
        self._cron_stacked.setCurrentIndex(0)
        self._load_cron_level1()

    # ─── 외부에서 호출 ────────────────────────────────────────────

    def refresh(self) -> None:
        """다이얼로그가 열릴 때마다 DB에서 다시 읽음."""
        self._cron_l1_page = 1
        self._cron_l1_total_pages = 1
        self._load_cron_level1()