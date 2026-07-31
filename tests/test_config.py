"""tests/test_config.py — 설정 모듈 테스트."""

from pathlib import Path

import pytest

from src.config import Config, _cast, decrypt_api_key, encrypt_api_key


class TestConfig:
    def test_defaults(self, temp_dir: Path):
        """기본값으로 Config 생성."""
        cfg = Config(str(temp_dir / "config.ini"))
        cfg.load()
        assert cfg.get("llm", "provider") == "openrouter"
        assert cfg.get("llm", "url") == "https://openrouter.ai/api/v1"
        assert cfg.get("llm", "max_tokens") == 4096
        assert cfg.get("llm", "temperature") == 0.0
        assert cfg.get("cdp", "host") == "127.0.0.1"
        assert cfg.get("cdp", "port") == 9222
        assert cfg.get("cdp", "connection_timeout") == 1.0
        assert cfg.get("chrome", "headless") is False
        assert cfg.get("ui", "language") == "ko"
        assert cfg.get("logging", "level") == "disabled"
        assert cfg.get("agent", "sliding_window_size") == 30
        assert cfg.get("agent", "cron_timeout") == 300

    def test_load_from_file(self, temp_dir: Path, sample_ini_content: str):
        """INI 파일에서 설정 로드."""
        ini_path = temp_dir / "config.ini"
        ini_path.write_text(sample_ini_content, encoding="utf-8")

        cfg = Config(str(ini_path))
        cfg.load()
        assert cfg.get("llm", "provider") == "openrouter"
        assert cfg.get("llm", "model") == "gpt-4o"
        assert cfg.get("cdp", "port") == 9222
        assert cfg.get("chrome", "headless") is False

    def test_save_and_reload(self, temp_dir: Path):
        """저장 후 다시 로드해도 값이 유지되는지."""
        ini_path = temp_dir / "config.ini"
        cfg = Config(str(ini_path))
        cfg.load()
        cfg.set("llm", "model", "claude-3")
        cfg.set("cdp", "port", 9999)
        cfg.save()

        cfg2 = Config(str(ini_path))
        cfg2.load()
        assert cfg2.get("llm", "model") == "claude-3"
        assert cfg2.get("cdp", "port") == 9999

    def test_get_section(self, temp_dir: Path):
        """get_section이 전체 섹션을 반환."""
        cfg = Config(str(temp_dir / "config.ini"))
        cfg.load()
        section = cfg.get_section("cdp")
        assert section.get("host") == "127.0.0.1"
        assert section.get("port") == 9222

    def test_update_section(self, temp_dir: Path):
        """update_section으로 여러 값 한번에 설정."""
        cfg = Config(str(temp_dir / "config.ini"))
        cfg.load()
        cfg.update_section("cdp", {"host": "0.0.0.0", "port": 9333})
        assert cfg.get("cdp", "host") == "0.0.0.0"
        assert cfg.get("cdp", "port") == 9333

    def test_api_key_property(self, temp_dir: Path):
        """api_key property getter/setter."""
        cfg = Config(str(temp_dir / "config.ini"))
        cfg.api_key = "my-secret-key"
        assert cfg.api_key == "my-secret-key"

    def test_save_with_api_key(self, temp_dir: Path):
        """api_key 저장 시 암호화되어 저장."""
        ini_path = temp_dir / "config.ini"
        cfg = Config(str(ini_path))
        cfg.load()
        cfg.api_key = "test-key-12345"
        cfg.save()

        # 저장된 파일에서 api_key가 암호화되었는지 확인
        raw = ini_path.read_text(encoding="utf-8")
        assert "api_key = g" in raw  # Fernet 암호문은 g로 시작
        assert "test-key-12345" not in raw  # 평문 없음

    def test_encrypt_decrypt_roundtrip(self):
        """encrypt/decrypt round-trip."""
        original = "sk-or-v1-abc123def456"
        encrypted = encrypt_api_key(original)
        assert encrypted != original
        decrypted = decrypt_api_key(encrypted)
        assert decrypted == original

    def test_decrypt_failure_returns_empty(self):
        """복호화 실패 시 빈 문자열 반환."""
        result = decrypt_api_key("invalid-encrypted-data")
        assert result == ""

    def test_path_property(self, temp_dir: Path):
        """path property 테스트."""
        ini_path = temp_dir / "config.ini"
        cfg = Config(str(ini_path))
        assert cfg.path == ini_path.resolve()

    def test_get_default(self, temp_dir: Path):
        """존재하지 않는 키에 get 시 default 반환."""
        cfg = Config(str(temp_dir / "config.ini"))
        cfg.load()
        assert cfg.get("nonexistent", "key", "fallback") == "fallback"
        assert cfg.get("llm", "nonexistent", 42) == 42

    def test_set_new_section(self, temp_dir: Path):
        """새 섹션에 값 설정."""
        cfg = Config(str(temp_dir / "config.ini"))
        cfg.load()
        cfg.set("custom", "key1", "val1")
        assert cfg.get("custom", "key1") == "val1"


class TestCast:
    def test_cast_bool(self):
        assert _cast("true", bool) is True
        assert _cast("True", bool) is True
        assert _cast("1", bool) is True
        assert _cast("yes", bool) is True
        assert _cast("false", bool) is False
        assert _cast("False", bool) is False
        assert _cast("0", bool) is False

    def test_cast_int(self):
        assert _cast("42", int) == 42
        assert _cast("0", int) == 0
        assert _cast("-10", int) == -10

    def test_cast_float(self):
        assert _cast("3.14", float) == 3.14
        assert _cast("1.0", float) == 1.0
        assert _cast("0", float) == 0.0

    def test_cast_str(self):
        assert _cast("hello", str) == "hello"
        assert _cast("", str) == ""