"""Cashbook: family cash, receipts, movements and physical counts."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..periods import current_period, validate_period
from ..security import Actor, require_user
from ..services import cash as svc

router = APIRouter(prefix="/api/cash", dependencies=[Depends(require_user)])


@router.get("/cashbook")
def cashbook(period: str | None = None, db: Session = Depends(get_db)):
    return svc.cashbook(db, validate_period(period or current_period()))


@router.get("/balances")
def balances(db: Session = Depends(get_db)):
    return {"family_cash_paise": svc.balance(db, "family_cash"),
            "caretaker_cash_paise": svc.balance(db, "caretaker_cash")}


@router.get("/receipts")
def receipts(only_with_cash_left: bool = False, db: Session = Depends(get_db)):
    rows = svc.cash_receipts(db)
    if only_with_cash_left:
        rows = [r for r in rows if r["status"] == "confirmed" and r["with_family_paise"] > 0]
    return rows


class SourceIn(BaseModel):
    rent_payment_id: int
    amount_paise: int = Field(gt=0, le=10**10)


class MovementIn(BaseModel):
    kind: str = Field(max_length=24)
    movement_date: dt.date
    amount_paise: int = Field(gt=0, le=10**10)
    from_account: str = Field(max_length=16)
    to_account: str = Field(max_length=16)
    description: str | None = Field(default=None, max_length=2000)
    sources: list[SourceIn] = []


@router.post("/movements")
def new_movement(body: MovementIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    m = svc.create_movement(db, kind=body.kind, movement_date=body.movement_date, amount_paise=body.amount_paise,
                            from_account=body.from_account, to_account=body.to_account, description=body.description,
                            sources=[(s.rent_payment_id, s.amount_paise) for s in body.sources], actor=actor.name)
    db.commit()
    db.refresh(m)
    return svc.describe_movement(m)


class ReasonIn(BaseModel):
    reason: str = Field(max_length=2000)


@router.post("/movements/{movement_id}/reverse")
def reverse(movement_id: int, body: ReasonIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    m = svc.reverse_movement(db, movement_id, body.reason, actor.name)
    db.commit()
    return svc.describe_movement(m)


class CountIn(BaseModel):
    account: str = Field(pattern="^(family_cash|caretaker_cash)$")
    count_date: dt.date
    counted_paise: int = Field(ge=0, le=10**10)
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/counts")
def new_count(body: CountIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    c = svc.record_count(db, body.account, body.count_date, body.counted_paise, body.notes, actor.name)
    db.commit()
    return {"id": c.id, "computed_paise": svc.balance(db, body.account, body.count_date)}


@router.get("/counts")
def list_counts(account: str = "caretaker_cash", db: Session = Depends(get_db)):
    return svc.counts(db, account)
