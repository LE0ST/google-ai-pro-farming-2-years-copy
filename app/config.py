"""Settings. Everything path- or secret-bearing lives outside the repo."""

from __future__ import annotations

import secrets
import stat
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "GaiConsole"


def _default_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_NAME


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GAI_", extra="ignore")

    data_dir: Path = _default_data_dir()
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765

    # Google login flows: parallel logins from one egress burn reputation faster
    # than a sequential queue. Keep this at 1 unless measured otherwise.
    max_concurrent: int = 1

    # Seconds a single account run may take before the supervisor cancels it.
    run_timeout: int = 15 * 60

    console_token: str = ""

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
        for d in (self.data_dir, self.profiles_dir, self.artifacts_dir):
            d.mkdir(parents=True, exist_ok=True)
        try:
            self.data_dir.chmod(stat.S_IRWXU)  # 0700: profile dirs hold live cookies
        except OSError:
            pass

    def load_or_create_token(self) -> str:
        """Console token. Without it, any local process can read the account list."""
        path = self.data_dir / "console.token"
        if path.exists():
            token = path.read_text(encoding="ascii").strip()
            if token:
                self.console_token = token
                return token
        token = secrets.token_urlsafe(32)
        path.write_text(token, encoding="ascii")
        path.chmod(0o600)
        self.console_token = token
        return token


settings = Settings()
