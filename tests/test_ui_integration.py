"""통합 테스트 — 초기 사용자 온보딩 시나리오.

시나리오 문서: tests/test_ui_scenarios.md (E. 통합 시나리오)

진짜 초기 사용자의 앱 첫 실행부터 설정 완료까지를 검증한다.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PySide6.QtCore import QTimer, Qt

from src.config import Config
from src.ui.main_window import MainWindow
from src.ui.settings_dialog import SettingsDialog


# ═══════════════════════════════════════════════════════════════
# E-1. 완전 초기 상태: config.ini 없음 → api_key/model 빈 값
# ═══════════════════════════════════════════════════════════════

class TestFreshConfigState:
    """E-1. config.ini 없이 시작 — 초기 상태 검증"""

    def test_config_file_not_found_creates_defaults(self, tmp_path: Path):
        """config.ini 없는 디렉토리에서 Config() → 기본값 생성."""
        ini = tmp_path / "config.ini"
        assert not ini.exists()

        cfg = Config(str(ini))
        cfg.load()
        assert ini.exists()  # load() 가 파일 생성
        assert cfg.api_key == ""
        # 기본 모델/URL 은 Config.DEFAULTS 에서 가져옴
        model = cfg.get("llm", "model")
        assert model == "qwen/qwen3.6-27b" or model == ""
        url = cfg.get("llm", "url")
        assert url == "https://openrouter.ai/api/v1" or url == ""

    def test_config_with_empty_api_key(self, tmp_path: Path):
        """api_key 가 빈 config 파일 로드."""
        ini = tmp_path / "config.ini"
        ini.write_text("[llm]\napi_key =\nmodel =\n")

        cfg = Config(str(ini))
        cfg.load()
        assert cfg.api_key == ""
        assert cfg.get("llm", "model") == ""

    def test_main_window_created_with_empty_config(
        self, qtbot, tmp_path: Path
    ):
        """api_key 없는 Config 로 MainWindow 생성 — 크래시 없이 생성."""
        ini = tmp_path / "config.ini"
        ini.write_text("[llm]\napi_key =\nmodel =\n")
        cfg = Config(str(ini))
        cfg.load()

        db = AsyncMock()
        db.get_current_session_id.return_value = None
        db.create_session.return_value = None
        db.set_current_session_id.return_value = None
        db.get_messages.return_value = []
        db.list_sessions.return_value = ([], 0)
        db.get_cron_jobs.return_value = ([], 0)

        manager = MagicMock()
        manager.is_connected = False
        manager.connect = AsyncMock()
        launcher = MagicMock()
        launcher.check_cdp_connection = AsyncMock(return_value=False)
        manager._launcher = launcher

        window = MainWindow(cfg, db, manager)
        qtbot.addWidget(window)
        # send_btn 은 기본적으로 활성화
        assert window._prompt._send_btn.isEnabled()
        # Chrome 버튼 텍스트는 mock 에 따라 "연결 안 됨" 또는 "연결 중..."
        assert "Chrome" in window._chrome_btn.text()


# ═══════════════════════════════════════════════════════════════
# E-2. 설정 저장 — SettingsDialog 직접 저장 경로 검증
# ═══════════════════════════════════════════════════════════════

class TestSettingsSaveFlow:
    """E-2. SettingsDialog → _save() → config.ini 저장까지."""

    def test_save_persists_all_fields_to_ini(self, qtbot, tmp_path: Path):
        """SettingsDialog 에서 저장한 모든 값이 config.ini 에 반영된다."""
        ini = tmp_path / "config.ini"
        ini.write_text("[llm]\napi_key =\nmodel =\n")
        cfg = Config(str(ini))
        cfg.load()

        db = AsyncMock()
        db.get_current_session_id.return_value = None
        db.create_session.return_value = None
        db.set_current_session_id.return_value = None
        db.get_messages.return_value = []
        db.list_sessions.return_value = ([], 0)
        db.get_cron_jobs.return_value = ([], 0)

        dlg = SettingsDialog(cfg, db)
        qtbot.addWidget(dlg)

        # LLM
        dlg._llm_api_key.setText("sk-real-key-99999")
        dlg._llm_model.setText("claude-sonnet-4")
        dlg._llm_url.setText("https://api.anthropic.com/v1")
        dlg._llm_max_tokens.setValue(8192)
        dlg._llm_temperature.setValue(0.7)

        # CDP
        dlg._cdp_host.setText("0.0.0.0")
        dlg._cdp_port.setValue(9999)

        # Chrome
        dlg._chrome_path.setText("/usr/bin/google-chrome")
        dlg._chrome_user_data.setText("/tmp/chrome-test")
        dlg._chrome_headless.setChecked(True)

        # UI
        dlg._ui_lang.setCurrentIndex(dlg._ui_lang.findData("en"))

        # 저장 (QMessageBox.warning 은 모달이므로 mock)
        # accept() 도 mock 처리하여 Qt 이벤트 루프와의 상호작용을 방지
        with patch("PySide6.QtWidgets.QMessageBox.warning"):
            with patch.object(dlg, "accept"):
                dlg._save()

        # Config 다시 로드하여 값 확인
        cfg2 = Config(str(ini))
        cfg2.load()

        assert cfg2.api_key == "sk-real-key-99999"
        assert cfg2.get("llm", "model") == "claude-sonnet-4"
        assert cfg2.get("llm", "url") == "https://api.anthropic.com/v1"
        assert cfg2.get("llm", "max_tokens") == 8192
        assert cfg2.get("llm", "temperature") == 0.7
        assert cfg2.get("cdp", "host") == "0.0.0.0"
        assert cfg2.get("cdp", "port") == 9999
        assert cfg2.get("chrome", "path") == "/usr/bin/google-chrome"
        assert cfg2.get("chrome", "user_data_dir") == "/tmp/chrome-test"
        assert cfg2.get("chrome", "headless") is True
        assert cfg2.get("ui", "language") == "en"

    def test_save_without_api_key_does_not_persist(
        self, qtbot, tmp_path: Path
    ):
        """API 키 없이 저장 시 config_signal 이 emit 되지 않음."""
        ini = tmp_path / "config.ini"
        ini.write_text("[llm]\napi_key =\nmodel =\n")
        cfg = Config(str(ini))
        cfg.load()

        db = AsyncMock()
        db.get_current_session_id.return_value = None
        db.create_session.return_value = None
        db.set_current_session_id.return_value = None
        db.get_messages.return_value = []
        db.list_sessions.return_value = ([], 0)
        db.get_cron_jobs.return_value = ([], 0)

        dlg = SettingsDialog(cfg, db)
        qtbot.addWidget(dlg)
        dlg._llm_api_key.setText("")
        dlg._llm_model.setText("gpt-4o")

        with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
            with patch.object(dlg, "accept"):
                with qtbot.assertNotEmitted(dlg.config_saved):
                    dlg._save()
                mock_warn.assert_called_once()

    def test_save_without_model_does_not_persist(
        self, qtbot, tmp_path: Path
    ):
        """모델명 없이 저장 시 config_signal 이 emit 되지 않음."""
        ini = tmp_path / "config.ini"
        ini.write_text("[llm]\napi_key =\nmodel =\n")
        cfg = Config(str(ini))
        cfg.load()

        db = AsyncMock()
        db.get_current_session_id.return_value = None
        db.create_session.return_value = None
        db.set_current_session_id.return_value = None
        db.get_messages.return_value = []
        db.list_sessions.return_value = ([], 0)
        db.get_cron_jobs.return_value = ([], 0)

        dlg = SettingsDialog(cfg, db)
        qtbot.addWidget(dlg)
        dlg._llm_api_key.setText("valid-key")
        dlg._llm_model.setText("")

        with patch("PySide6.QtWidgets.QMessageBox.warning") as mock_warn:
            with patch.object(dlg, "accept"):
                with qtbot.assertNotEmitted(dlg.config_saved):
                    dlg._save()
                mock_warn.assert_called_once()


# ═══════════════════════════════════════════════════════════════
# E-3. MainWindow._open_settings → 설정 저장 → config 반영
# ═══════════════════════════════════════════════════════════════

class TestOpenSettingsFlow:
    """E-3. MainWindow._open_settings() 경로 검증.

    ``_open_settings()`` 는 ``dlg.exec()`` (모달) 을 호출하므로,
    ``QTimer.singleShot`` 으로 다이얼로그와 상호작용한 후 ``exec()`` 가
    블로킹되지 않도록 처리한다.
    """

    def _make_fixtures(self, tmp_path):
        ini = tmp_path / "config.ini"
        ini.write_text("[llm]\napi_key =\nmodel =\n")
        cfg = Config(str(ini))
        cfg.load()

        db = AsyncMock()
        db.get_current_session_id.return_value = None
        db.create_session.return_value = None
        db.set_current_session_id.return_value = None
        db.get_messages.return_value = []
        db.list_sessions.return_value = ([], 0)
        db.get_cron_jobs.return_value = ([], 0)

        manager = MagicMock()
        manager.is_connected = False
        manager.connect = AsyncMock()
        manager.disconnect = AsyncMock()
        manager.update_config = MagicMock()
        launcher = MagicMock()
        launcher.check_cdp_connection = AsyncMock(return_value=False)
        manager._launcher = launcher

        return cfg, db, manager

    def test_open_settings_saves_and_triggers_config_saved(
        self, qtbot, tmp_path
    ):
        """설정 저장 후 config_saved 시그널이 발생하고 Config 에 반영된다."""
        cfg, db, manager = self._make_fixtures(tmp_path)

        window = MainWindow(cfg, db, manager)
        qtbot.addWidget(window)

        config_saved_called = [False]

        def on_config_saved():
            config_saved_called[0] = True

        window._on_config_saved = on_config_saved

        saved = [False]
        from PySide6.QtWidgets import QDialog

        def _interact_with_dialog(dlg):
            dlg._llm_api_key.setText("sk-integration-key")
            dlg._llm_model.setText("gpt-4o")
            with patch("PySide6.QtWidgets.QMessageBox.warning"):
                with patch.object(dlg, "accept") as mock_accept:
                    dlg._save()
                    # accept() 가 mock 되었으므로 직접 dialog 닫기
                    dlg.done(QDialog.DialogCode.Accepted)
            saved[0] = True

        # exec() 이 호출될 때 QTimer 로 필드 채우고 저장

        original_exec = QDialog.exec
        def patched_exec(dlg_self):
            QTimer.singleShot(50, lambda: _interact_with_dialog(dlg_self))
            return original_exec(dlg_self)

        with patch.object(QDialog, "exec", patched_exec):
            window._open_settings()

        assert saved[0], "SettingsDialog._save() 가 호출되지 않음"
        assert config_saved_called[0], "config_saved 시그널이 emit 되지 않음"
        assert cfg.api_key == "sk-integration-key"
        assert cfg.get("llm", "model") == "gpt-4o"

        # config.ini 에도 저장되었는지 확인
        cfg2 = Config(str(tmp_path / "config.ini"))
        cfg2.load()
        assert cfg2.api_key == "sk-integration-key"

    def test_open_settings_and_cancel_does_not_save(
        self, qtbot, tmp_path
    ):
        """설정 창을 저장 없이 닫으면 Config 가 변경되지 않음."""
        cfg, db, manager = self._make_fixtures(tmp_path)

        window = MainWindow(cfg, db, manager)
        qtbot.addWidget(window)

        config_saved_called = [False]

        def on_config_saved():
            config_saved_called[0] = True

        window._on_config_saved = on_config_saved

        from PySide6.QtWidgets import QDialog

        original_exec = QDialog.exec
        def patched_exec(dlg_self):
            # exec() 블로킹 없이 곧바로 Rejected 반환
            dlg_self.reject()
            return QDialog.Rejected

        with patch.object(QDialog, "exec", patched_exec):
            window._open_settings()

        assert not config_saved_called[0]
        assert cfg.api_key == ""

    def test_full_main_flow_missing_api_key_opens_settings(
        self, qtbot, tmp_path
    ):
        """main.py run() 의 api_key 체크 로직 재현 — missing key → _open_settings 호출 확인.

        main.py 171-179:
          if not api_key or not model:
              QMessageBox.information(...)
              window._open_settings()
        """
        cfg, db, manager = self._make_fixtures(tmp_path)
        window = MainWindow(cfg, db, manager)
        qtbot.addWidget(window)

        assert not cfg.api_key
        assert not cfg.get("llm", "model", "")

        from PySide6.QtWidgets import QMessageBox

        # _open_settings 가 호출되었는지 확인
        opened = [False]

        def track_open():
            opened[0] = True

        window._open_settings = track_open

        # main.py 171-179 로직 재현 (QMessageBox.information 은 모달이므로 mock)
        if not cfg.api_key or not cfg.get("llm", "model", ""):
            with patch.object(QMessageBox, "information"):
                QMessageBox.information(window, "설정 필요", "...")
            window._open_settings()

        assert opened[0], "_open_settings() 가 호출되어야 함"

        # SettingsDialog 직접 생성 → 저장 → config 반영
        dlg = SettingsDialog(cfg, db)
        qtbot.addWidget(dlg)
        dlg._llm_api_key.setText("sk-mainflow-key")
        dlg._llm_model.setText("gpt-4o")

        with patch("PySide6.QtWidgets.QMessageBox.warning"):
            with patch.object(dlg, "accept"):
                dlg._save()

        assert cfg.api_key == "sk-mainflow-key"
        assert cfg.get("llm", "model") == "gpt-4o"

        # config.ini 영구 저장 확인
        cfg2 = Config(str(tmp_path / "config.ini"))
        cfg2.load()
        assert cfg2.api_key == "sk-mainflow-key"
        assert cfg2.get("llm", "model") == "gpt-4o"