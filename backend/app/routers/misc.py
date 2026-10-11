"""Settings, rates, opening balances, audit log and backups."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..models import AuditEvent
from ..security import Actor, require_user
from ..services import backup as backup_svc
from ..services import opening
from ..services import settings as svc

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return {
        "settings": svc.all_settings(db),
        "billing_policies": svc.BILLING_POLICIES,
        "rent_due_months": svc.RENT_DUE_MONTHS,
        "rate_keys": svc.RATE_KEYS,
        "rates": [{"id": r.id, "key": r.key, "amount_paise": r.amount_paise, "effective_period": r.effective_period,
                   "note": r.note, "created_by": r.created_by} for r in svc.list_rates(db)],
    }


class SettingIn(BaseModel):
    key: str = Field(max_length=60)
    value: Any


@router.put("/settings")
def put_setting(body: SettingIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    svc.set_setting(db, body.key, body.value, actor.name)
    db.commit()
    return {"ok": True}


class RateIn(BaseModel):
    key: str = Field(max_length=40)
    amount_paise: int = Field(ge=0, le=10**10)
    effective_period: str
    note: str | None = Field(default=None, max_length=500)


@router.post("/settings/rates")
def put_rate(body: RateIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    r = svc.set_rate(db, body.key, body.amount_paise, body.effective_period, actor.name, body.note)
    db.commit()
    return {"id": r.id}


# ---------------------------------------------------------- opening balances


@router.get("/opening-balances")
def list_opening(db: Session = Depends(get_db)):
    return opening.list_all(db)


class OpeningIn(BaseModel):
    kind: str = Field(max_length=24)
    as_of_date: dt.date
    amount_paise: int = Field(gt=0, le=10**10)
    description: str = Field(max_length=2000)
    notes: str | None = Field(default=None, max_length=4000)
    tenancy_id: int | None = None


@router.post("/opening-balances")
def create_opening(body: OpeningIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    ob = opening.create(db, actor=actor.name, **body.model_dump())
    db.commit()
    return {"id": ob.id}


class ReasonIn(BaseModel):
    reason: str = Field(max_length=2000)


@router.post("/opening-balances/{ob_id}/reverse")
def reverse_opening(ob_id: int, body: ReasonIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    opening.reverse(db, ob_id, body.reason, actor.name)
    db.commit()
    return {"ok": True}


# -------------------------------------------------------------------- audit


@router.get("/audit")
def audit_log(entity_type: str | None = None, entity_id: int | None = None, action: str | None = None,
              offset: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    q = select(AuditEvent)
    if entity_type:
        q = q.where(AuditEvent.entity_type == entity_type)
    if entity_id is not None:
        q = q.where(AuditEvent.entity_id == entity_id)
    if action:
        q = q.where(AuditEvent.action.like(f"{action}%"))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(AuditEvent.id.desc()).offset(max(0, offset)).limit(min(limit, 500))).all()
    return {"total": total, "events": [{
        "id": e.id, "ts": e.ts.isoformat(), "actor": e.actor, "action": e.action, "entity_type": e.entity_type,
        "entity_id": e.entity_id, "before": json.loads(e.before_json) if e.before_json else None,
        "after": json.loads(e.after_json) if e.after_json else None, "reason": e.reason} for e in rows]}


# ------------------------------------------------------------------- backup


@router.get("/backups")
def backups():
    return backup_svc.list_backups()


@router.post("/backups")
def make_backup(actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    path = backup_svc.create_backup(label="manual")
    audit.record(db, actor.name, "backup.create", "backup", None, None, {"file": path.name})
    db.commit()
    return {"name": path.name, "size_bytes": path.stat().st_size}


@router.get("/backups/{name}")
def download_backup(name: str):
    path = backup_svc.backup_path(name)
    return FileResponse(path, media_type="application/zip", filename=path.name)
