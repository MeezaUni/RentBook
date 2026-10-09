"""Bank statement import and reconciliation."""

from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import settings
from ..db import get_db
from ..errors import DomainError, NotFound
from ..models import BankImport, BankTransaction, StatementProfile
from ..security import Actor, require_user
from ..services import bank as svc

router = APIRouter(prefix="/api/bank", dependencies=[Depends(require_user)])


async def _read(file: UploadFile) -> bytes:
    limit = settings.max_upload_mb * 1024 * 1024
    return await file.read(limit + 1)


def _mapping(db: Session, mapping: str | None, profile_id: int | None) -> dict | None:
    if mapping:
        try:
            m = json.loads(mapping)
        except json.JSONDecodeError as exc:
            raise DomainError("invalid_mapping", "Column mapping is not valid JSON") from exc
        if not isinstance(m, dict) or not isinstance(m.get("columns", {}), dict):
            raise DomainError("invalid_mapping", "Column mapping has the wrong shape")
        m["columns"] = {k: int(v) for k, v in m.get("columns", {}).items() if v is not None and v != ""}
        return m
    if profile_id:
        prof = db.get(StatementProfile, profile_id)
        if prof is None:
            raise NotFound("Statement profile")
        return json.loads(prof.mapping_json)
    return None


@router.post("/preview")
async def preview(file: UploadFile = File(...), sheet: str | None = Form(None), mapping: str | None = Form(None),
                  profile_id: int | None = Form(None), db: Session = Depends(get_db)):
    data = await _read(file)
    return svc.preview(db, data, file.filename or "statement", sheet or None, _mapping(db, mapping, profile_id))


@router.post("/imports")
async def commit(file: UploadFile = File(...), sheet: str | None = Form(None), mapping: str = Form(...),
                 accept_errors: bool = Form(False), skip_possible_duplicates: bool = Form(True),
                 save_profile_as: str | None = Form(None), actor: Actor = Depends(require_user),
                 db: Session = Depends(get_db)):
    data = await _read(file)
    imp = svc.commit(db, data, file.filename or "statement", sheet or None, _mapping(db, mapping, None),
                     accept_errors=accept_errors, skip_possible_duplicates=skip_possible_duplicates,
                     save_profile_as=save_profile_as or None, actor=actor.name)
    db.commit()
    return {"id": imp.id, "rows_total": imp.rows_total, "rows_new": imp.rows_new,
            "rows_duplicate": imp.rows_duplicate, "rows_error": imp.rows_error}


@router.get("/imports")
def imports(db: Session = Depends(get_db)):
    return svc.list_imports(db)


@router.get("/imports/{import_id}")
def import_detail(import_id: int, db: Session = Depends(get_db)):
    imp = db.get(BankImport, import_id)
    if imp is None:
        raise NotFound("Import")
    return {
        "import": next(i for i in svc.list_imports(db) if i["id"] == import_id),
        "summary": svc.summary(db, import_id=import_id),
        "transactions": svc.list_transactions(db, status=None, import_id=import_id, start=None, end=None,
                                              kind=None, with_suggestions=False),
    }


@router.get("/profiles")
def profiles(db: Session = Depends(get_db)):
    return [{"id": p.id, "name": p.name, "mapping": json.loads(p.mapping_json)}
            for p in db.scalars(select(StatementProfile).order_by(StatementProfile.name))]


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    p = db.get(StatementProfile, profile_id)
    if p is None:
        raise NotFound("Statement profile")
    audit.record(db, actor.name, "bank.profile_delete", "statement_profile", p.id, {"name": p.name})
    db.delete(p)  # a column-mapping template, not a financial record
    db.commit()
    return {"ok": True}


@router.get("/transactions")
def transactions(status: str | None = None, import_id: int | None = None, start: dt.date | None = None,
                 end: dt.date | None = None, kind: str | None = None, suggestions: bool = False,
                 db: Session = Depends(get_db)):
    data = svc.list_transactions(db, status=status, import_id=import_id, start=start, end=end, kind=kind,
                                 with_suggestions=suggestions)
    db.commit()  # persists any rent dues generated for matching
    return data


@router.get("/transactions/{txn_id}")
def transaction(txn_id: int, db: Session = Depends(get_db)):
    txn = db.get(BankTransaction, txn_id)
    if txn is None:
        raise NotFound("Bank transaction")
    data = svc.describe_txn(db, txn, svc.MatchContext(db))
    db.commit()
    return data


class AllocationIn(BaseModel):
    tenancy_id: int
    rent_due_id: int | None = None
    period: str | None = None
    hold_as_credit: bool = False
    amount_paise: int = Field(gt=0, le=10**10)


class AliasIn(BaseModel):
    tenant_id: int
    alias: str = Field(max_length=160)
    kind: str = "sender_name"


class AllocateIn(BaseModel):
    allocations: list[AllocationIn]
    confirm: bool = False
    save_alias: AliasIn | None = None
    remainder_not_rent_reason: str | None = Field(default=None, max_length=500)


@router.post("/transactions/{txn_id}/allocate")
def allocate(txn_id: int, body: AllocateIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    if not body.confirm:
        raise DomainError("confirmation_required", "Review and confirm the allocation before it is applied")
    pays = svc.allocate(db, txn_id, [a.model_dump() for a in body.allocations],
                        save_alias=body.save_alias.model_dump() if body.save_alias else None,
                        remainder_not_rent_reason=body.remainder_not_rent_reason, actor=actor.name)
    db.commit()
    return {"payment_ids": [p.id for p in pays],
            "transaction": svc.describe_txn(db, db.get(BankTransaction, txn_id))}


class LinkIn(BaseModel):
    payment_id: int


@router.post("/transactions/{txn_id}/link-payment")
def link(txn_id: int, body: LinkIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    svc.link_existing_payment(db, txn_id, body.payment_id, actor.name)
    db.commit()
    return svc.describe_txn(db, db.get(BankTransaction, txn_id))


class ReasonIn(BaseModel):
    reason: str = Field(max_length=500)


@router.post("/transactions/{txn_id}/not-rent")
def not_rent(txn_id: int, body: ReasonIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    svc.mark_not_rent(db, txn_id, body.reason, actor.name)
    db.commit()
    return svc.describe_txn(db, db.get(BankTransaction, txn_id))


@router.post("/transactions/{txn_id}/remainder-not-rent")
def remainder_not_rent(txn_id: int, body: ReasonIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    svc.mark_remainder_not_rent(db, txn_id, body.reason, actor.name)
    db.commit()
    return svc.describe_txn(db, db.get(BankTransaction, txn_id))


@router.post("/transactions/{txn_id}/reopen")
def reopen(txn_id: int, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    svc.reopen(db, txn_id, actor.name)
    db.commit()
    return svc.describe_txn(db, db.get(BankTransaction, txn_id))


@router.get("/summary")
def summary(start: dt.date | None = None, end: dt.date | None = None, import_id: int | None = None,
            db: Session = Depends(get_db)):
    return svc.summary(db, import_id=import_id, start=start, end=end)
