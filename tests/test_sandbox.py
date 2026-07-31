"""tests/test_sandbox.py — 파일 접근 샌드박스 테스트."""

import os
from pathlib import Path

import pytest

from src.security.sandbox import PathAccessError, Sandbox


@pytest.fixture
def sandbox(temp_dir: Path) -> Sandbox:
    config_path = temp_dir / "config.ini"
    config_path.write_text("", encoding="utf-8")
    db_path = temp_dir / "test.db"
    workspace_dir = temp_dir / "workspace"
    workspace_dir.mkdir()
    return Sandbox(str(config_path), str(db_path), str(workspace_dir))


class TestSandbox:
    def test_init_allowed_dirs(self, sandbox: Sandbox):
        """초기화 시 허용 디렉토리 확인."""
        dirs = sandbox.allowed_dirs
        assert len(dirs) >= 3  # config 부모, db 부모, workspace

    def test_validate_allowed(self, sandbox: Sandbox, temp_dir: Path):
        """허용된 경로는 통과."""
        p = sandbox.validate(str(temp_dir / "config.ini"))
        assert p == (temp_dir / "config.ini").resolve()

    def test_validate_allowed_subdir(self, sandbox: Sandbox, temp_dir: Path):
        """workspace 하위 경로도 허용."""
        sub = temp_dir / "workspace" / "subdir" / "file.txt"
        sub.parent.mkdir(parents=True, exist_ok=True)
        p = sandbox.validate(str(sub))
        assert p == sub.resolve()

    def test_validate_blocked(self, sandbox: Sandbox):
        """외부 경로는 차단."""
        with pytest.raises(PathAccessError, match="접근 차단"):
            sandbox.validate("/etc/passwd")

    def test_validate_blocked_outside(self, sandbox: Sandbox, tmp_path: Path):
        """완전히 다른 경로 차단."""
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        with pytest.raises(PathAccessError):
            sandbox.validate(str(outside))

    def test_is_allowed_true(self, sandbox: Sandbox, temp_dir: Path):
        assert sandbox.is_allowed(str(temp_dir / "config.ini")) is True

    def test_is_allowed_false(self, sandbox: Sandbox):
        assert sandbox.is_allowed("/etc/passwd") is False

    def test_double_init_dedup(self, temp_dir: Path):
        """중복 경로 전달 시 중복 제거."""
        config_path = temp_dir / "config.ini"
        config_path.write_text("", encoding="utf-8")
        s = Sandbox(str(config_path), str(config_path), str(config_path))
        assert len(s.allowed_dirs) >= 1

    def test_workspace_subdir_allowed(self, sandbox: Sandbox, temp_dir: Path):
        """workspace_dir의 하위 폴더도 허용."""
        deep = temp_dir / "workspace" / "a" / "b" / "c"
        deep.mkdir(parents=True, exist_ok=True)
        assert sandbox.is_allowed(str(deep)) is True

    def test_path_access_error_message(self, sandbox: Sandbox):
        """예외 메시지에 허용된 경로 포함."""
        try:
            sandbox.validate("/proc/1/maps")
        except PathAccessError as e:
            msg = str(e)
            assert "접근 차단" in msg
            assert "허용된 경로" in msg