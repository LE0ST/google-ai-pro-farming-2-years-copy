"""Configuration for the local read-only diagnostics application."""

from __future__ import annotations

import os
import secrets
import stat
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "GaiConsole-ReadOnly"


def _default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GAI_", extra="ignore")

    data_dir: Path = _default_data_dir()
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    console_token: str = ""
    browser_channel: str = "auto"
    browser_executable: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db.sqlite3"

    @property
    def profiles_dir(self) -> Path:
        return self.data_dir / "profiles"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    def ensure_dirs(self) -> None:
        for directory in (self.data_dir, self.profiles_dir, self.artifacts_dir):
            directory.mkdir(parents=True, exist_ok=True)
        try:
            self.data_dir.chmod(stat.S_IRWXU)
        except OSError:
            pass

    def load_or_create_token(self) -> str:
        token_path = self.data_dir / "console.token"
        if token_path.exists():
            token = token_path.read_text(encoding="ascii").strip()
            if token:
                self.console_token = token
                return token
        token = secrets.token_urlsafe(32)
        token_path.write_text(token, encoding="ascii")
        try:
            token_path.chmod(0o600)
        except OSError:
            pass
        self.console_token = token
        return token


settings = Settings()
