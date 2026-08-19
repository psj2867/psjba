"""UI 헬퍼 함수 — 에러 표시, 세션 리셋, 크래시 후크 등."""

import logging
import sys
import traceback
from typing import Optional

logger = logging.getLogger("psjba")

_CANCELLED_MSG = "⚠️ 사용자가 실행을 취소했습니다."

# 전역 참조 (크래시 후크 → UI 알림)
_main_window_ref: Optional["MainWindow"] = None  # noqa: F821


def set_main_window_ref(window: "MainWindow") -> None:  # noqa: F821
    """크래시 후크에서 사용할 MainWindow 참조를 설정한다."""
    global _main_window_ref
    _main_window_ref = window


def finish_prompt_cancelled(window: "MainWindow") -> None:  # noqa: F821
    """stop/cancel 시 UI 정리: 스켈론 교체 + 입력창 복구."""
    window._chat.replace_thinking_skeleton(_CANCELLED_MSG)
    window._prompt.set_running(False)


def show_error_in_ui(
    window: "MainWindow", title: str, exc: Optional[BaseException] = None  # noqa: F821
) -> None:
    """에러 메시지를 UI 채팅에 표시하고 입력창을 복구한다."""
    detail = f"({type(exc).__name__}: {exc})" if exc else ""
    msg = f"{title} {detail}" if detail else title
    window._chat.replace_thinking_skeleton(msg)
    window._prompt.set_running(False)


def notify_user_critical(title: str, message: str) -> None:
    """MainWindow를 통해 사용자에게 치명적 오류를 알린다."""
    win = _main_window_ref
    if win is None:
        return
    try:
        msg = f"⚠️ **{title}**\n\n{message[:300]}"
        win._chat.add_user_message(msg)
    except Exception:
        logger.exception("notify_user_critical failed")


_ORIG_EXCEPTHOOK: object | None = None


def setup_crash_hooks() -> None:
    """전역 예외 후크를 등록하여 잡히지 않은 예외를 로그에 기록한다."""
    global _ORIG_EXCEPTHOOK
    _ORIG_EXCEPTHOOK = sys.excepthook

    def excepthook(exc_type, exc_value, exc_tb) -> None:
        logger.critical(
            "Unhandled exception (sys.excepthook): %s: %s\n%s",
            exc_type.__name__, exc_value,
            "".join(traceback.format_tb(exc_tb)),
        )
        notify_user_critical(exc_type.__name__, f"{exc_value}")
        if _ORIG_EXCEPTHOOK:
            _ORIG_EXCEPTHOOK(exc_type, exc_value, exc_tb)

    sys.excepthook = excepthook


async def handle_session_reset(db: "Database", window: "MainWindow") -> None:  # noqa: F821
    """세션을 리셋하고 새 대화를 시작한다."""
    from src.database import Database  # noqa: F811
    from src.models.schemas import Session

    logger.info("Session reset requested")

    current_id = await db.get_current_session_id()
    if current_id:
        await db.archive_session(current_id)

    session = Session(title="새 대화", type="chat")
    await db.create_session(session)
    await db.set_current_session_id(session.id)

    window._chat.clear()
    logger.info(f"Session reset: new session {session.id}")