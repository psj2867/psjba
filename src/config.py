import base64
import hashlib
import logging
import uuid
from configparser import ConfigParser, NoSectionError
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("psjba.config")

CONFIG_PATH = "config.ini"
SALT = b"psjba_2024"


def _derive_fernet_key() -> bytes:
    machine_id = uuid.getnode()
    key = hashlib.pbkdf2_hmac(
        "sha256", str(machine_id).encode(), SALT, 100000
    )
    return base64.urlsafe_b64encode(key[:32])


def encrypt_api_key(plaintext: str) -> str:
    cipher = Fernet(_derive_fernet_key())
    return cipher.encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    try:
        cipher = Fernet(_derive_fernet_key())
        return cipher.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        logger.warning("MAC address changed, cannot decrypt api_key")
        return ""
    except Exception as e:
        logger.warning(f"Failed to decrypt api_key: {e}")
        return ""


DEFAULTS: dict[str, dict[str, Any]] = {
    "llm": {
        "api_key": "",
        "model": "deepseek/deepseek-v4-flash",
        "url": "https://openrouter.ai/api/v1",
        "max_tokens": 4096,
        "temperature": 0.0,
        "timeout": 60,
    },
    "cdp": {
        "host": "127.0.0.1",
        "port": 9222,
        "connection_timeout": 1.0,
    },
    "chrome": {
        "path": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        "user_data_dir": "",
        "headless": False,
    },
    "sqlite": {
        "path": "./browser_agent.data",
    },
    "agent": {
        "sliding_window_size": 10,
        "cron_timeout": 300,
        "max_tool_calls": 30,
    },
    "workspace": {
        "workspace_dir": "./psjba_temp",
    },
    "ui": {
        "font_size": 14,
        "max_chat_lines": 1000,
        "language": "ko",
    },
    "logging": {
        "level": "disabled",
        "path": "./psjba.log",
    },
}

TYPE_MAP: dict[str, type] = {
    "api_key": str,
    "model": str,
    "url": str,
    "host": str,
    "path": str,
    "user_data_dir": str,
    "workspace_dir": str,
    "language": str,
    "level": str,
    "port": int,
    "max_tokens": int,
    "timeout": int,
    "sliding_window_size": int,
    "cron_timeout": int,
    "font_size": int,
    "max_chat_lines": int,
    "connection_timeout": float,
    "temperature": float,
    "headless": bool,
}


def _cast(value: str, target_type: type) -> Any:
    if target_type == bool:
        return value.lower() in ("true", "1", "yes")
    return target_type(value)


class Config:
    def __init__(self, config_path: str = CONFIG_PATH):
        self._path = Path(config_path).resolve()
        self._parser = ConfigParser()
        self._data: dict[str, dict[str, Any]] = {}
        self._api_key_plain: str = ""

    @property
    def api_key(self) -> str:
        return self._api_key_plain

    @api_key.setter
    def api_key(self, value: str) -> None:
        self._api_key_plain = value

    @property
    def path(self) -> Path:
        return self._path

    def get(self, section: str, key: str, default: Any = None) -> Any:
        try:
            return self._data[section][key]
        except KeyError:
            return default

    def set(self, section: str, key: str, value: Any) -> None:
        if section not in self._data:
            self._data[section] = {}
        self._data[section][key] = value

    def load(self) -> None:
        self._data = {}
        self._api_key_plain = ""
        for section, keys in DEFAULTS.items():
            self._data[section] = dict(keys)

        if not self._path.exists():
            logger.info(f"Config file not found, creating defaults at {self._path}")
            self._save_raw()
            return

        self._parser.read(str(self._path), encoding="utf-8")

        for section in DEFAULTS:
            if not self._parser.has_section(section):
                continue
            if section not in self._data:
                self._data[section] = {}
            for key in DEFAULTS[section]:
                if self._parser.has_option(section, key):
                    raw = self._parser.get(section, key)
                    target_type = TYPE_MAP.get(key, str)
                    self._data[section][key] = _cast(raw, target_type)

        encrypted = self._data.get("llm", {}).get("api_key", "")
        if encrypted:
            self._api_key_plain = decrypt_api_key(str(encrypted))
            self._data["llm"]["api_key"] = self._api_key_plain
        else:
            self._api_key_plain = ""

    def save(self) -> None:
        encrypted = encrypt_api_key(self._api_key_plain) if self._api_key_plain else ""

        for section in DEFAULTS:
            if not self._parser.has_section(section):
                self._parser.add_section(section)
            for key in DEFAULTS[section]:
                if key == "api_key":
                    self._parser.set(section, key, encrypted)
                elif section in self._data and key in self._data[section]:
                    val = self._data[section][key]
                    self._parser.set(section, key, str(val).lower() if isinstance(val, bool) else str(val))
                else:
                    val = DEFAULTS[section][key]
                    self._parser.set(section, key, str(val).lower() if isinstance(val, bool) else str(val))

        self._save_raw()

    def _save_raw(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            self._parser.write(f)

    def get_section(self, section: str) -> dict[str, Any]:
        return dict(self._data.get(section, {}))

    def update_section(self, section: str, values: dict[str, Any]) -> None:
        if section not in self._data:
            self._data[section] = {}
        for key, val in values.items():
            if key == "api_key":
                self._api_key_plain = str(val)
            else:
                self._data[section][key] = val