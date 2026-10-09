"""Append-only audit trail for financially relevant operations."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy.orm import Session

from .models import AuditEvent

# Never copy these into audit rows.
_SENSITIVE = {"pin_hash", "token_hash", "csrf_token", "pin"}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def snapshot(obj: Any, fields: list[str] | None = None) -> dict:
    if obj is None:
        return {}
    if fields is None:
        fields = [c.key for c in obj.__table__.columns]
    return {f: _jsonable(getattr(obj, f)) for f in fields if f not in _SENSITIVE}


def record(
    db: Session,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: int | None,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
) -> AuditEvent:
    ev = AuditEvent(
        actor=actor or "system",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=json.dumps(before, default=str, ensure_ascii=False) if before else None,
        after_json=json.dumps(after, default=str, ensure_ascii=False) if after else None,
        reason=reason,
    )
    db.add(ev)
    return ev
