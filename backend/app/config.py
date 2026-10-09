"""Application configuration, read from environment variables.

Nothing secret is hardcoded here. See `.env.example` at the repository root.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Minimal `.env` loader (KEY=VALUE lines) so no extra dependency is needed.

    Values already present in the real environment always win.
    """
    for candidate in (REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"):
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    env: str = "development"
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "data")
    database_url: str = ""
    cookie_secure: bool = False
    session_hours: int = 12
    max_upload_mb: int = 15
    frontend_dist: Path = field(default_factory=lambda: REPO_ROOT / "frontend" / "dist")
    login_max_failures: int = 5
    login_window_minutes: int = 15
    log_level: str = "INFO"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlite_path(self) -> Path | None:
        if not self.is_sqlite:
            return None
        return Path(self.database_url.split("///", 1)[1])


def load_settings() -> Settings:
    _load_dotenv()
    s = Settings()
    s.env = os.environ.get("RENTBOOK_ENV", "development")
    if os.environ.get("RENTBOOK_DATA_DIR"):
        raw = Path(os.environ["RENTBOOK_DATA_DIR"])
        s.data_dir = (raw if raw.is_absolute() else REPO_ROOT / raw).resolve()
    s.database_url = os.environ.get(
        "RENTBOOK_DATABASE_URL", f"sqlite:///{(s.data_dir / 'rentbook.db').as_posix()}"
    )
    # Secure cookies by default in production (HTTPS expected); off for plain-http local dev.
    s.cookie_secure = _bool("RENTBOOK_COOKIE_SECURE", s.env == "production")
    s.session_hours = int(os.environ.get("RENTBOOK_SESSION_HOURS", s.session_hours))
    s.max_upload_mb = int(os.environ.get("RENTBOOK_MAX_UPLOAD_MB", s.max_upload_mb))
    if os.environ.get("RENTBOOK_FRONTEND_DIST"):
        s.frontend_dist = Path(os.environ["RENTBOOK_FRONTEND_DIST"]).resolve()
    s.login_max_failures = int(os.environ.get("RENTBOOK_LOGIN_MAX_FAILURES", s.login_max_failures))
    s.login_window_minutes = int(
        os.environ.get("RENTBOOK_LOGIN_WINDOW_MINUTES", s.login_window_minutes)
    )
    s.log_level = os.environ.get("RENTBOOK_LOG_LEVEL", s.log_level)
    return s


settings = load_settings()
