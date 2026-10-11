"""Administrative commands.

    uv run python -m app.cli migrate
    uv run python -m app.cli create-user --name Asha
    uv run python -m app.cli reset-pin --name Asha
    uv run python -m app.cli backup
    uv run python -m app.cli verify-backup data/backups/<file>.zip
    uv run python -m app.cli restore data/backups/<file>.zip --yes   (stop the app first!)
    uv run python -m app.cli serve [--host 0.0.0.0] [--port 8000]
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from sqlalchemy import func, select


def _read_pin() -> str:
    env = os.environ.get("RENTBOOK_NEW_PIN")
    if env:
        return env
    pin = getpass.getpass("New PIN (6-12 digits): ")
    if getpass.getpass("Repeat PIN: ") != pin:
        sys.exit("PINs do not match")
    return pin


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="rentbook")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="Apply database migrations")
    p = sub.add_parser("create-user", help="Create a login (PIN read interactively)")
    p.add_argument("--name", required=True)
    p = sub.add_parser("reset-pin", help="Set a new PIN for a user")
    p.add_argument("--name", required=True)
    sub.add_parser("backup", help="Create a backup zip in data/backups")
    p = sub.add_parser("verify-backup")
    p.add_argument("path")
    p = sub.add_parser("restore", help="Restore a backup (the app must be stopped)")
    p.add_argument("path")
    p.add_argument("--yes", action="store_true")
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        from .config import settings

        # Behind a proxy (Tailscale Funnel -> Docker) every request arrives from the proxy's address,
        # so per-visitor login limits need the client address the proxy forwards. Uvicorn takes the
        # right-most address not in this list, so a value the visitor adds themselves is ignored.
        trusted = settings.forwarded_allow_ips
        uvicorn.run("app.main:app", host=args.host, port=args.port, proxy_headers=bool(trusted),
                    forwarded_allow_ips=trusted or None)
        return

    from .db import SessionLocal
    from .migrate import upgrade_to_head

    if args.cmd == "migrate":
        upgrade_to_head()
        print("Database is up to date.")
        return
    if args.cmd in ("create-user", "reset-pin"):
        from . import audit
        from .models import User
        from .security import change_pin, create_user
        from .services.seed import ensure_initial_data

        upgrade_to_head()
        with SessionLocal() as db:
            ensure_initial_data(db)
            if args.cmd == "create-user":
                u = create_user(db, args.name, _read_pin())
                audit.record(db, "cli", "user.create", "user", u.id, None, {"name": u.name})
                print(f"User '{u.name}' created.")
            else:
                u = db.scalar(select(User).where(func.lower(User.name) == args.name.strip().lower()))
                if u is None:
                    sys.exit("No such user")
                change_pin(db, u, _read_pin())
                audit.record(db, "cli", "user.reset_pin", "user", u.id)
                print(f"PIN for '{u.name}' changed; existing sessions were logged out.")
            db.commit()
        return

    from .services import backup

    if args.cmd == "backup":
        path = backup.create_backup(label="cli")
        print(f"Backup written to {path}")
    elif args.cmd == "verify-backup":
        m = backup.verify_backup(Path(args.path))
        print(f"OK: backup from {m['created_at']} (schema {m.get('schema_revision')})")
    elif args.cmd == "restore":
        if not args.yes:
            sys.exit("Restoring replaces ALL current data. Stop the app, then re-run with --yes.")
        safety = backup.restore_backup(Path(args.path))
        upgrade_to_head()
        print("Restore complete." + (f" Previous data saved to {safety}" if safety else ""))


if __name__ == "__main__":
    main()
