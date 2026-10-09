"""Run Alembic migrations programmatically, with a safety backup first."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from .config import settings
from .db import engine

log = logging.getLogger("rentbook.migrate")
BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(url: str | None = None) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", (url or settings.database_url).replace("%", "%%"))
    return cfg


def pending(url: str | None = None) -> bool:
    cfg = alembic_config(url)
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    return current != head


def upgrade_to_head() -> None:
    if not pending():
        return
    with engine.connect() as conn:
        has_data = MigrationContext.configure(conn).get_current_revision() is not None
    if has_data and settings.is_sqlite:
        from .services.backup import create_backup

        path = create_backup(label="pre-migration")
        log.info("Created pre-migration backup %s", path.name)
    command.upgrade(alembic_config(), "head")
    log.info("Database migrated to latest schema")
