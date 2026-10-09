"""Backups: database + uploaded documents + plain CSV exports, in one zip.

SQLite is copied with the online backup API (consistent even while the app runs),
never by copying the live file. Restore is a CLI-only operation (see cli.py).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from ..config import settings
from ..errors import DomainError

EXPORT_TABLES = [
    "buildings", "flats", "tenants", "tenancies", "rent_terms", "rent_dues", "rent_payments", "rent_allocations",
    "bank_imports", "bank_transactions", "cash_movements", "cash_movement_sources", "maintenance_expenses",
    "expense_categories", "salary_payments", "opening_balances", "rate_settings", "cash_counts",
    "tenant_aliases", "owner_occupancies", "documents", "document_links", "audit_events",
]


def _timestamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _sqlite_snapshot(dest: Path) -> None:
    src_path = settings.sqlite_path
    if src_path is None:
        raise DomainError("unsupported", "Built-in backup supports SQLite only. For PostgreSQL use pg_dump (see README).")
    if not src_path.exists():
        raise DomainError("no_database", "Database file not found")
    src = sqlite3.connect(f"file:{src_path.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _csv_exports(db_file: Path) -> dict[str, bytes]:
    out = {}
    con = sqlite3.connect(db_file)
    try:
        existing = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in EXPORT_TABLES:
            if table not in existing:
                continue
            cur = con.execute(f'SELECT * FROM "{table}"')  # table names come from the fixed list above
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow([d[0] for d in cur.description])
            w.writerows(cur.fetchall())
            out[f"csv/{table}.csv"] = buf.getvalue().encode("utf-8-sig")
    finally:
        con.close()
    return out


def create_backup(label: str = "manual") -> Path:
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    name = f"rentbook-backup-{_timestamp()}-{label}.zip"
    target = settings.backups_dir / name
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / "rentbook.db"
        _sqlite_snapshot(snap)
        con = sqlite3.connect(snap)
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            try:
                revision = con.execute("SELECT version_num FROM alembic_version").fetchone()
            except sqlite3.Error:
                revision = None
        finally:
            con.close()
        manifest = {
            "app": "rentbook",
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "label": label,
            "schema_revision": revision[0] if revision else None,
            "integrity_check": integrity,
            "contents": ["rentbook.db", "uploads/", "csv/"],
        }
        partial = target.with_suffix(".zip.partial")
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
            z.write(snap, "rentbook.db")
            for path, data in _csv_exports(snap).items():
                z.writestr(path, data)
            uploads = settings.uploads_dir
            if uploads.exists():
                for f in sorted(uploads.rglob("*")):
                    if f.is_file() and not f.name.endswith(".tmp"):
                        z.write(f, "uploads/" + f.relative_to(uploads).as_posix())
        partial.replace(target)
    return target


def list_backups() -> list[dict]:
    d = settings.backups_dir
    if not d.exists():
        return []
    return [{"name": p.name, "size_bytes": p.stat().st_size,
             "created_at": dt.datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")}
            for p in sorted(d.glob("rentbook-backup-*.zip"), reverse=True)]


def backup_path(name: str) -> Path:
    if "/" in name or "\\" in name or not name.startswith("rentbook-backup-") or not name.endswith(".zip"):
        raise DomainError("invalid_name", "Invalid backup name")
    p = settings.backups_dir / name
    if not p.is_file():
        raise DomainError("not_found", "Backup not found", status=404)
    return p


def verify_backup(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        if "manifest.json" not in names or "rentbook.db" not in names:
            raise DomainError("invalid_backup", "Not a Rentbook backup (manifest or database missing)")
        for n in names:
            if n.startswith("/") or ".." in Path(n).parts:
                raise DomainError("invalid_backup", "Backup contains unsafe paths")
        manifest = json.loads(z.read("manifest.json"))
        with tempfile.TemporaryDirectory() as tmp:
            z.extract("rentbook.db", tmp)
            con = sqlite3.connect(Path(tmp) / "rentbook.db")
            try:
                ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                con.close()
        if ok != "ok":
            raise DomainError("invalid_backup", f"Database in backup failed integrity check: {ok}")
    return manifest


def restore_backup(zip_path: Path) -> Path:
    """Replace the current database and uploads with a backup. The app MUST be stopped.

    A safety backup of the current state is taken first and its path returned.
    """
    verify_backup(zip_path)
    safety = create_backup(label="before-restore") if settings.sqlite_path and settings.sqlite_path.exists() else None
    db_path = settings.sqlite_path
    assert db_path is not None
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        for suffix in ("-wal", "-shm"):
            side = db_path.with_name(db_path.name + suffix)
            if side.exists():
                side.unlink()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(tmp) / "rentbook.db", db_path)
        uploads = settings.uploads_dir
        if uploads.exists():
            uploads.rename(uploads.with_name(f"uploads.before-restore-{_timestamp()}"))
        src_uploads = Path(tmp) / "uploads"
        if src_uploads.exists():
            shutil.copytree(src_uploads, uploads)
        else:
            uploads.mkdir(parents=True, exist_ok=True)
    return safety
