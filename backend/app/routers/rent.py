"""Rent ledger, dues and payments."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Flat, RentDue, RentPayment, Tenancy
from ..periods import current_period, validate_period
from ..security import Actor, require_user
from ..services import rent as svc

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


def ledger(db: Session, period: str, building_id: int | None = None) -> dict:
    svc.ensure_dues(db, max(period, current_period()))
    q = select(RentDue).join(Tenancy, Tenancy.id == RentDue.tenancy_id).join(Flat, Flat.id == Tenancy.flat_id)
    if building_id:
        q = q.where(Flat.building_id == building_id)
    dues = svc.describe_dues(db, list(db.scalars(q.where(RentDue.period == period)
                                                  .order_by(Flat.building_id, Flat.floor_index, Flat.position))))
    older = svc.describe_dues(db, list(db.scalars(q.where(RentDue.period < period, RentDue.waived_at.is_(None))
                                                   .order_by(RentDue.period))))
    arrears = [d for d in older if d["outstanding_paise"] > 0]
    rent_dues = [d for d in dues if d["kind"] == "rent" and d["status"] != "waived"]
    counts = {s: sum(1 for d in rent_dues if d["status"] == s)
              for s in ("paid", "partially_paid", "pending_verification", "unpaid", "overdue")}
    return {
        "period": period,
        "dues": dues,
        "arrears": arrears,
        "summary": {
            "expected_paise": sum(d["amount_paise"] for d in rent_dues),
            "confirmed_paise": sum(d["confirmed_paise"] for d in rent_dues),
            "pending_paise": sum(d["pending_paise"] for d in rent_dues),
            "outstanding_paise": sum(d["outstanding_paise"] for d in rent_dues),
            "overdue_paise": sum(d["outstanding_paise"] for d in rent_dues if d["is_overdue"]),
            "arrears_paise": sum(d["outstanding_paise"] for d in arrears),
            "counts": counts,
        },
    }


@router.get("/rent/ledger")
def rent_ledger(period: str | None = None, building_id: int | None = None, db: Session = Depends(get_db)):
    data = ledger(db, validate_period(period or current_period()), building_id)
    db.commit()
    return data


class ReasonIn(BaseModel):
    reason: str = Field(max_length=2000)


@router.post("/rent/dues/{due_id}/waive")
def waive(due_id: int, body: ReasonIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    due = svc.waive_due(db, due_id, body.reason, actor.name)
    db.commit()
    return svc.describe_dues(db, [due])[0]


@router.post("/rent/dues/{due_id}/unwaive")
def unwaive(due_id: int, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    due = svc.unwaive_due(db, due_id, actor.name)
    db.commit()
    return svc.describe_dues(db, [due])[0]


class DueAmountIn(BaseModel):
    amount_paise: int = Field(ge=0, le=10**10)
    reason: str = Field(max_length=2000)


@router.post("/rent/dues/{due_id}/amount")
def due_amount(due_id: int, body: DueAmountIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    due = svc.override_due_amount(db, due_id, body.amount_paise, body.reason, actor.name)
    db.commit()
    return svc.describe_dues(db, [due])[0]


# ----------------------------------------------------------------- payments


class AllocationIn(BaseModel):
    rent_due_id: int
    amount_paise: int = Field(gt=0, le=10**10)


class PaymentIn(BaseModel):
    tenancy_id: int
    received_date: dt.date
    amount_paise: int = Field(gt=0, le=10**10)
    method: str = Field(pattern="^(cash|online)$")
    status: str = Field(default="confirmed", pattern="^(pending|confirmed)$")
    rent_period: str | None = None
    allocations: list[AllocationIn] | None = None
    reference: str | None = Field(default=None, max_length=120)
    sender_name: str | None = Field(default=None, max_length=160)
    notes: str | None = Field(default=None, max_length=4000)
    excess_handling: str | None = None
    excess_note: str | None = Field(default=None, max_length=2000)
    allow_duplicate: bool = False

    def to_input(self) -> svc.PaymentInput:
        return svc.PaymentInput(
            tenancy_id=self.tenancy_id, received_date=self.received_date, amount_paise=self.amount_paise,
            method=self.method, status=self.status, rent_period=self.rent_period,
            allocations=[(a.rent_due_id, a.amount_paise) for a in self.allocations] if self.allocations else None,
            reference=self.reference, sender_name=self.sender_name, notes=self.notes,
            excess_handling=self.excess_handling, excess_note=self.excess_note, allow_duplicate=self.allow_duplicate,
        )


@router.post("/payments")
def create_payment(body: PaymentIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    pay = svc.record_payment(db, body.to_input(), actor.name)
    db.commit()
    return svc.describe_payment(pay)


@router.get("/payments")
def list_payments(tenancy_id: int | None = None, flat_id: int | None = None, status: str | None = None,
                  method: str | None = None, start: dt.date | None = None, end: dt.date | None = None,
                  needs_decision: bool = False, limit: int = 300, db: Session = Depends(get_db)):
    q = select(RentPayment).join(Tenancy, Tenancy.id == RentPayment.tenancy_id)
    if tenancy_id:
        q = q.where(RentPayment.tenancy_id == tenancy_id)
    if flat_id:
        q = q.where(Tenancy.flat_id == flat_id)
    if status:
        q = q.where(RentPayment.status == status)
    if method:
        q = q.where(RentPayment.method == method)
    if start:
        q = q.where(RentPayment.received_date >= start)
    if end:
        q = q.where(RentPayment.received_date <= end)
    rows = [svc.describe_payment(p) for p in db.scalars(
        q.order_by(RentPayment.received_date.desc(), RentPayment.id.desc()).limit(min(limit, 2000)))]
    if needs_decision:
        rows = [r for r in rows if r["needs_excess_decision"]]
    return rows


@router.get("/payments/{payment_id}")
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    return svc.describe_payment(svc.get_payment(db, payment_id))


@router.post("/payments/{payment_id}/confirm")
def confirm(payment_id: int, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    pay = svc.confirm_payment(db, payment_id, actor.name)
    db.commit()
    return svc.describe_payment(pay)


@router.post("/payments/{payment_id}/void")
def void(payment_id: int, body: ReasonIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    pay = svc.void_payment(db, payment_id, body.reason, actor.name)
    db.commit()
    return svc.describe_payment(pay)


class CorrectIn(PaymentIn):
    reason: str = Field(max_length=2000)


@router.post("/payments/{payment_id}/correct")
def correct(payment_id: int, body: CorrectIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    pay = svc.correct_payment(db, payment_id, body.reason, body.to_input(), actor.name)
    db.commit()
    return svc.describe_payment(pay)


class ReallocateIn(BaseModel):
    allocations: list[AllocationIn]
    reason: str = Field(max_length=2000)


@router.post("/payments/{payment_id}/reallocate")
def reallocate(payment_id: int, body: ReallocateIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    pay = svc.reallocate_payment(db, payment_id, [(a.rent_due_id, a.amount_paise) for a in body.allocations],
                                 body.reason, actor.name)
    db.commit()
    return svc.describe_payment(pay)


class ExcessIn(BaseModel):
    resolution: str = Field(pattern="^(advance_credit|refund_due|refunded)$")
    note: str | None = Field(default=None, max_length=2000)


@router.post("/payments/{payment_id}/excess")
def excess(payment_id: int, body: ExcessIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    pay = svc.resolve_excess(db, payment_id, body.resolution, body.note, actor.name)
    db.commit()
    return svc.describe_payment(pay)
