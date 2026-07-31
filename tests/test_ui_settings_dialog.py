"""SettingsDialog 헤드리스 UI 테스트.

시나리오 문서: tests/test_ui_scenarios.md (D. SettingsDialog)

Note: QMessageBox.warning() 은 모달 다이얼로그로 헤드리스 환경에서 블로킹되므로
mock 처리한다.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PySide6.QtCore import Qt

from src.config import Config
from src.ui.settings_dialog import SettingsDialog


@pytest.fixture
def running_asyncio_loop():
    """asyncio 이벤트 루프가 실행 중인 상태를 만든다.

    ``_run_async()`` 가 ``loop.is_running() == True`` 분기를 타도록 한다.
    qasync 가 asyncio 이벤트 루프를 Qt 이벤트 루프와 통합하므로,
    이미 실행 중인 QApplication 위에서 새 asyncio 이벤트 루프를 생성하여
    ``is_running() == True`` 로 만든다.
    """
    import qasync

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # 아직 시작되지 않았으므로 runner 로 실행
    return loop


class TestSettingsDialogCreation:
    """D-1. SettingsDialog 생성 — 버그 재현 테스트 포함"""

    def test_create_settings_dialog(self, qtbot, mock_config, mock_db):
        """SettingsDialog 생성 시 AttributeError(crashing bug)가 발생하지 않아야 한다."""
        dlg = SettingsDialog(mock_config, mock_db)
        qtbot.addWidget(dlg)
        # 생성만으로도 _init_ui → _build_settings_tab() 이 호출되며
        # chrome_box.addWidget() 버그가 있으면 AttributeError 발생
        assert dlg.windowTitle() == "설정"

    def test_three_tabs_exist(self, qtbot, mock_config, mock_db):
        dlg = SettingsDialog(mock_config, mock_db)
        qtbot.addWidget(dlg)
        assert dlg._tabs.count() == 3
        assert dlg._tabs.tabText(0) == "설정"
        assert dlg._tabs.tabText(1) == "CRON"
        assert dlg._tabs.tabText(2) == "로그"

    def test_settings_loaded_into_fields(self, qtbot, mock_config, mock_db):
        """설정값이 UI 필드에 로드되는지 확인."""
        dlg = SettingsDialog(mock_config, mock_db)
        qtbot.addWidget(dlg)
        # mock_config는 gpt-4o, ko 등을 반환
        assert dlg._llm_model.text() == "gpt-4o"
        assert dlg._ui_lang.currentData() == "ko"

    def test_chrome_headless_checkbox_exists(self, qtbot, mock_config, mock_db):
        """Chrome 섹션에 헤드리스 체크박스가 존재해야 한다 (버그 검증)."""
        dlg = SettingsDialog(mock_config, mock_db)
        qtbot.addWidget(dlg)
        assert dlg._chrome_headless is not None
        assert dlg._chrome_headless.text() == "헤드리스 모드"


class TestSettingsDialogValidation:
    """D-2. 저장 유효성 검사"""

    def test_save_without_api_key_shows_warning(self, qtbot, mock_config, mock_db):
        dlg = SettingsDialog(mock_config, mock_db)
        qtbot.addWidget(dlg)
        dlg._llm_api_key.setText("")
        dlg._llm_model.setText("gpt-4o")

        # QMessageBox.warning 은 모달이므로 mock 처리 (내부 import)
        with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
            with qtbot.assertNotEmitted(dlg.config_saved):
                dlg._save()
            mock_warn.assert_called_once()

    def test_save_without_model_shows_warning(self, qtbot, mock_config, mock_db):
        dlg = SettingsDialog(mock_config, mock_db)
        qtbot.addWidget(dlg)
        dlg._llm_api_key.setText("valid-key")
        dlg._llm_model.setText("")

        with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
            with qtbot.assertNotEmitted(dlg.config_saved):
                dlg._save()
            mock_warn.assert_called_once()

    def test_save_success(self, qtbot, mock_config, mock_db):
        """유효한 값으로 저장 성공."""
        dlg = SettingsDialog(mock_config, mock_db)
        qtbot.addWidget(dlg)
        dlg._llm_api_key.setText("test-key-12345")
        dlg._llm_model.setText("gpt-4o")

        with qtbot.waitSignal(dlg.config_saved, timeout=500):
            dlg._save()


class TestSettingsDialogRealConfig:
    """실제 Config 파일로 SettingsDialog 테스트."""

    @pytest.fixture
    def ini_file(self, temp_dir: Path, sample_ini_content: str) -> Path:
        p = temp_dir / "config.ini"
        p.write_text(sample_ini_content, encoding="utf-8")
        return p

    def test_real_config_load(self, qtbot, ini_file, mock_db):
        """실제 Config 파일 로드 후 다이얼로그 생성."""
        cfg = Config(str(ini_file))
        cfg.load()
        dlg = SettingsDialog(cfg, mock_db)
        qtbot.addWidget(dlg)
        assert dlg._llm_model.text() == "gpt-4o"
        assert not dlg._chrome_headless.isChecked()  # headless = false

    def test_real_config_save_persists(self, qtbot, ini_file, mock_db, temp_dir):
        """저장 후 Config 파일에 값이 반영되는지 확인."""
        cfg = Config(str(ini_file))
        cfg.load()
        dlg = SettingsDialog(cfg, mock_db)
        qtbot.addWidget(dlg)

        dlg._llm_api_key.setText("new-encrypted-key")
        dlg._llm_model.setText("claude-sonnet-4")

        with qtbot.waitSignal(dlg.config_saved, timeout=500):
            dlg._save()

        # Config 다시 로드해서 값 확인
        cfg2 = Config(str(ini_file))
        cfg2.load()
        assert cfg2.get("llm", "model") == "claude-sonnet-4"
        # api_key는 암호화되어 저장되므로 cfg.api_key 로 확인
        assert cfg2.api_key == "new-encrypted-key"


class TestSettingsDialogAsyncLoop:
    """D-3. asyncio 이벤트 루프 실행 중 SettingsDialog 생성.

    main.py 의 ``run()`` 코루틴 내에서 ``_open_settings()`` 가 호출되는
    상황에서 ``_run_async()`` 가 중첩 태스크를 생성하지 않고
    정상 동작해야 함을 검증한다.

    Python 3.14+ 의 ``RuntimeError: Cannot enter into task while another task
    is being executed`` 를 유발하지 않아야 하며, pending 태스크를 남기지 않아야 한다.
    """

    def test_create_no_asyncio_loop_does_not_crash(
        self, qtbot, mock_config, mock_db
    ):
        """asyncio 루프 없는 상태에서 SettingsDialog 생성 → 정상."""
        dlg = SettingsDialog(mock_config, mock_db)
        qtbot.addWidget(dlg)
        assert dlg.windowTitle() == "설정"

    def test_run_async_not_running_loop(self):
        """``_run_async()`` 가 실행 중이지 않은 루프에서 정상 동작."""
        from src.ui.settings_dialog import _run_async

        async def dummy():
            return "ok"

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = _run_async(dummy())
            assert result == "ok"
        finally:
            loop.close()
            # set_event_loop(None) 은 이후 테스트의 get_event_loop() 를 깨트리므로
            # 새 루프로 대체
            asyncio.set_event_loop(asyncio.new_event_loop())

    def test_run_async_recovers_gracefully_with_running_loop(self):
        """``_run_async`` 가 실행 중인 루프에서 호출되어도 크래시 없음.

        실제 시나리오: ``main.py`` 의 ``run()`` 코루틴이 실행 중일 때
        ``SettingsDialog.__init__`` → ``_load_cron_level1()`` → ``_run_async()`` 호출.
        ``_run_async`` 는 ``None`` 을 반환하고, 태스크/예약을 생성하지 않음 (Python 3.14+).
        """
        from src.ui.settings_dialog import _run_async

        async def dummy():
            return "ok"

        async def simulate_running_loop():
            """loop.is_running() == True 상태에서 _run_async 호출."""
            result = _run_async(dummy())
            # 실행 중인 루프에서는 None 반환 (태스크 생성 안 함)
            return result

        result = asyncio.run(simulate_running_loop())
        assert result is None

    def test_no_pending_tasks_after_running_loop(self):
        """``_run_async`` 가 실행 중인 루프에서 호출된 후 pending 태스크가 없어야 함.

        Python 3.14+ 크래시의 근본 원인은 ``ensure_future()`` 로 생성된 태스크가
        ``Task was destroyed but it is pending`` 경고를 유발한 것.
        새로운 구현은 태스크를 전혀 생성하지 않아야 한다.
        """
        from src.ui.settings_dialog import _run_async

        async def dummy():
            return "ok"

        async def simulate():
            before = asyncio.all_tasks()
            result = _run_async(dummy())
            after = asyncio.all_tasks()
            # 실행 중인 루프에서 호출해도 새 태스크가 생성되지 않아야 함
            assert result is None
            assert len(after - before) == 0, (
                f"_run_async 가 {len(after - before)}개의 pending 태스크를 남김"
            )
            return result

        asyncio.run(simulate())

    def test_qasync_loop_settings_dialog_no_crash(
        self, qtbot, mock_config, mock_db
    ):
        """qasync QEventLoop 실행 중 SettingsDialog 생성 → pending 태스크 0.

        main.py 와 동일한 시나리오:
          qasync.QEventLoop → loop.run_until_complete(coro)
            → SettingsDialog.__init__
              → _load_cron_level1 / _load_log_level1 → _run_async()

        검증:
        1. SettingsDialog 생성 중 예외가 발생하지 않음
        2. ``asyncio.all_tasks()`` 에 pending 태스크가 남지 않음
        """
        import qasync

        from src.ui.settings_dialog import _run_async

        errors = []
        tasks_after = []

        async def simulate_main():
            """main.py 의 run() 과 유사한 흐름."""
            nonlocal errors, tasks_after

            # --- Given: loop.is_running() == True ---
            assert asyncio.get_event_loop().is_running()

            # --- When: SettingsDialog 생성 (내부에서 _run_async 호출) ---
            try:
                dlg = SettingsDialog(mock_config, mock_db)
                qtbot.addWidget(dlg)
            except Exception as e:
                errors.append(e)
                return

            # --- Then: pending 태스크가 없어야 함 ---
            ts = asyncio.all_tasks()
            # 현재 실행 중인 태스크 본인은 제외
            current = asyncio.current_task()
            tasks_after = [t for t in ts if t is not current]

            # dialog 가 정상 생성되었는지 확인
            assert dlg.windowTitle() == "설정"

        # 현재 루프 저장 (없으면 None)
        try:
            loop_before = asyncio.get_event_loop()
        except RuntimeError:
            loop_before = None

        # qasync.QEventLoop 로 simulate_main() 실행
        loop = qasync.QEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(simulate_main())
        finally:
            loop.close()
            # 원래 루프로 복원 (다른 테스트에 영향 없도록)
            if loop_before is not None:
                asyncio.set_event_loop(loop_before)
            else:
                # 루프가 없었으면 새 루프 생성 (Python 3.14+ get_event_loop() 호환)
                asyncio.set_event_loop(asyncio.new_event_loop())

        # --- Then ---
        assert not errors, f"SettingsDialog 생성 중 예외 발생: {errors}"
        assert len(tasks_after) == 0, (
            f"qasync 루프 실행 중 SettingsDialog 생성 후 "
            f"{len(tasks_after)}개의 pending 태스크가 남음: {tasks_after}"
        )

    def test_qasync_loop_all_tasks_count_unchanged(
        self, qtbot, mock_config, mock_db
    ):
        """qasync 루프 실행 중 _run_async 호출 전후 all_tasks() 개수가 동일.

        이 테스트는 Python 3.14+ 환경에서 ``ensure_future`` 가 생성한
        태스크가 ``Task was destroyed but it is pending`` 경고를 유발하는
        현상을 직접 검증한다.
        """
        import qasync

        from src.ui.settings_dialog import _run_async

        task_diff = []

        async def dummy():
            return "ok"

        async def simulate():
            nonlocal task_diff
            before = asyncio.all_tasks()
            _run_async(dummy())
            after = asyncio.all_tasks()
            task_diff = after - before
            return task_diff

        # 현재 루프 저장 (없으면 None)
        try:
            loop_before = asyncio.get_event_loop()
        except RuntimeError:
            loop_before = None

        loop = qasync.QEventLoop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(simulate())
        finally:
            loop.close()
            if loop_before is not None:
                asyncio.set_event_loop(loop_before)
            else:
                asyncio.set_event_loop(asyncio.new_event_loop())

        assert len(task_diff) == 0, (
            f"_run_async 가 {len(task_diff)}개의 추가 태스크를 생성함: {task_diff}"
        )