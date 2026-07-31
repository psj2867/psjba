"""Sandbox — 파일 경로 유효성 검사 (config.ini, DB, workspace만 허용)."""

import logging
from pathlib import Path
from typing import Union

logger = logging.getLogger("psjba.security.sandbox")


class PathAccessError(Exception):
    """샌드박스 정책 위반 — 허용되지 않은 경로 접근 시도."""


class Sandbox:
    """파일 접근 제한 샌드박스.

    프로그램이 읽고 쓸 수 있는 경로를 세 곳으로 제한한다:
    1. config.ini
    2. SQLite DB 파일
    3. workspace_dir (및 하위 디렉토리)
    """

    def __init__(
        self,
        config_path: Union[str, Path],
        db_path: Union[str, Path],
        workspace_dir: Union[str, Path],
    ) -> None:
        self._allowed: list[Path] = []
        for p in (config_path, db_path, workspace_dir):
            resolved = Path(p).resolve()
            if resolved.is_file():
                self._allowed.append(resolved.parent)
            else:
                self._allowed.append(resolved)

        unique: list[Path] = []
        for p in self._allowed:
            if not any(p == u or p in u.parents for u in unique):
                unique = [u for u in unique if not u in p.parents]
                unique.append(p)
        self._allowed = unique

        logger.debug(
            f"Sandbox initialized: {[str(p) for p in self._allowed]}"
        )

    @property
    def allowed_dirs(self) -> list[Path]:
        return list(self._allowed)

    def validate(self, path: Union[str, Path]) -> Path:
        """경로가 허용된 디렉토리 내에 있는지 검증.

        Args:
            path: 검증할 파일/디렉토리 경로

        Returns:
            검증된 절대 경로 (Path)

        Raises:
            PathAccessError: 허용되지 않은 경로인 경우
        """
        target = Path(path).resolve()

        for allowed in self._allowed:
            try:
                target.relative_to(allowed)
                return target
            except ValueError:
                continue

        raise PathAccessError(
            f"접근 차단: '{target}'는 허용된 경로가 없습니다. "
            f"허용된 경로: {[str(p) for p in self._allowed]}"
        )

    def is_allowed(self, path: Union[str, Path]) -> bool:
        """경로가 허용된 디렉토리 내에 있는지 확인 (예외 없이)."""
        try:
            self.validate(path)
            return True
        except PathAccessError:
            return False
