"""Maintenance cash, expenses, categories and caretaker salary."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..periods import add_months, current_period, today, validate_period
from ..security import Actor, require_user
from ..services import maintenance as svc
from ..services.settings import tracking_start

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


@router.get("/maintenance/summary")
def summary(period: str | None = None, db: Session = Depends(get_db)):
    return svc.month_summary(db, validate_period(period or current_period()))


@router.get("/maintenance/overview")
def overview(db: Session = Depends(get_db)):
    return svc.overview(db)


@router.get("/maintenance/history")
def history(months: int = 12, db: Session = Depends(get_db)):
    return svc.history(db, max(1, min(months, 36)))


class ExpenseIn(BaseModel):
    expense_date: dt.date
    description: str = Field(max_length=2000)
    category_id: int
    caretaker_paid_paise: int = Field(ge=0, le=10**10)
    family_paid_paise: int = Field(ge=0, le=10**10)
    family_payment_method: str | None = None
    flat_id: int | None = None
    vendor: str | None = Field(default=None, max_length=120)
    allow_shortfall: bool = False


@router.post("/maintenance/expenses")
def new_expense(body: ExpenseIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    e = svc.create_expense(db, actor=actor.name, **body.model_dump())
    db.commit()
    return svc.describe_expense(e)


class ReasonIn(BaseModel):
    reason: str = Field(max_length=2000)


@router.post("/maintenance/expenses/{expense_id}/reverse")
def reverse_expense(expense_id: int, body: ReasonIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    e = svc.reverse_expense(db, expense_id, body.reason, actor.name)
    db.commit()
    return svc.describe_expense(e)


@router.get("/maintenance/available")
def available(on: dt.date | None = None, db: Session = Depends(get_db)):
    return {"available_paise": svc.caretaker_available(db, on or today())}


class NoteIn(BaseModel):
    period: str
    notes: str = Field(max_length=4000)


@router.put("/maintenance/notes")
def note(body: NoteIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    svc.set_month_note(db, body.period, body.notes, actor.name)
    db.commit()
    return {"ok": True}


@router.get("/maintenance/categories")
def categories(db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name, "is_active": c.is_active} for c in svc.list_categories(db)]


class CategoryIn(BaseModel):
    name: str = Field(max_length=60)
    is_active: bool = True


@router.post("/maintenance/categories")
def add_category(body: CategoryIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    c = svc.upsert_category(db, None, body.name, True, actor.name)
    db.commit()
    return {"id": c.id, "name": c.name, "is_active": c.is_active}


@router.patch("/maintenance/categories/{category_id}")
def edit_category(category_id: int, body: CategoryIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    c = svc.upsert_category(db, category_id, body.name, body.is_active, actor.name)
    db.commit()
    return {"id": c.id, "name": c.name, "is_active": c.is_active}


# ------------------------------------------------------------------- salary


@router.get("/salary")
def salary(period: str | None = None, db: Session = Depends(get_db)):
    return svc.salary_month(db, validate_period(period or current_period()))


@router.get("/salary/history")
def salary_history(months: int = 12, db: Session = Depends(get_db)):
    cur = current_period()
    start = tracking_start(db)
    months_back = [add_months(cur, -i) for i in range(max(1, min(months, 36)))]
    return [svc.salary_month(db, p) for p in months_back if start is None or p >= start]


class SalaryIn(BaseModel):
    salary_month: str
    amount_paise: int = Field(gt=0, le=10**10)
    payment_date: dt.date
    method: str = Field(max_length=16)
    bank_reference: str | None = Field(default=None, max_length=120)
    status: str = Field(default="confirmed", pattern="^(pending|confirmed)$")
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/salary")
def new_salary(body: SalaryIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    p = svc.record_salary(db, salary_month_=body.salary_month, amount_paise=body.amount_paise,
                          payment_date=body.payment_date, method=body.method, bank_reference=body.bank_reference,
                          status=body.status, notes=body.notes, actor=actor.name)
    db.commit()
    return svc.describe_salary(p)


@router.post("/salary/{payment_id}/confirm")
def confirm_salary(payment_id: int, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    p = svc.confirm_salary(db, payment_id, actor.name)
    db.commit()
    return svc.describe_salary(p)


@router.post("/salary/{payment_id}/cancel")
def cancel_salary(payment_id: int, body: ReasonIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    p = svc.cancel_salary(db, payment_id, body.reason, actor.name)
    db.commit()
    return svc.describe_salary(p)
