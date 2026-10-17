"""Maintenance cash, expenses and the caretaker's (separate) salary.

Top-up rule: required = max(0, target - caretaker cash held at the start of the month).
A month's figures count the cash given *for* that month, even when it is handed over later
(September's top-up given on 20 October counts for September). Expenses count in the month they
happen. The salary never touches the caretaker's maintenance cash.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import Conflict, DomainError
from ..models import (
    CashMovement,
    ExpenseCategory,
    Flat,
    MaintenanceExpense,
    MaintenanceMonthNote,
    SalaryPayment,
    utcnow,
)
from ..periods import add_months, current_period, first_day, last_day, period_of, today, validate_period
from . import cash
from .property import get_or_404
from .settings import before_tracking, get_rate, handover_offset

FAMILY_METHODS = {"cash", "bank", "other"}


def top_up_required(target_paise: int, opening_paise: int) -> int:
    return max(0, target_paise - opening_paise)


_CARETAKER = (CashMovement.to_account == "caretaker_cash") | (CashMovement.from_account == "caretaker_cash")


def opening_for(db: Session, period: str) -> int:
    """Caretaker cash carried into `period`: everything given for earlier months, less what he spent."""
    def total(*where) -> int:
        return int(db.scalar(select(func.coalesce(func.sum(CashMovement.amount_paise), 0))
                             .where(CashMovement.status == "active", CashMovement.period < period, *where)) or 0)

    spent = db.scalar(select(func.coalesce(func.sum(MaintenanceExpense.caretaker_paid_paise), 0)).where(
        MaintenanceExpense.status == "active", MaintenanceExpense.period < period)) or 0
    return (total(CashMovement.to_account == "caretaker_cash")
            - total(CashMovement.from_account == "caretaker_cash") - int(spent))


def given_before_tracking(db: Session, period: str) -> bool:
    """`period`'s top-up was due to be handed over before the app's records begin."""
    handover_month = add_months(period, -handover_offset(db, "maintenance_given_for"))
    return before_tracking(db, period) or before_tracking(db, handover_month)


def still_to_give(db: Session, period: str) -> int:
    """How much of `period`'s top-up has not been handed over yet."""
    if given_before_tracking(db, period):
        return 0
    required = top_up_required(get_rate(db, "maintenance_target", period), opening_for(db, period))
    handed = db.scalar(select(func.coalesce(func.sum(CashMovement.amount_paise), 0)).where(
        CashMovement.status == "active", CashMovement.kind == "caretaker_handover", CashMovement.period == period)) or 0
    return max(0, required - int(handed))


def month_summary(db: Session, period: str) -> dict:
    validate_period(period)
    start, end = first_day(period), last_day(period)
    target = get_rate(db, "maintenance_target", period)
    opening = opening_for(db, period)
    required = top_up_required(target, opening)
    moves = db.scalars(select(CashMovement).where(
        CashMovement.status == "active", CashMovement.period == period, _CARETAKER)
        .order_by(CashMovement.movement_date, CashMovement.id)).all()
    handed = sum(m.amount_paise for m in moves if m.kind == "caretaker_handover")
    other_net = sum((m.amount_paise if m.to_account == "caretaker_cash" else -m.amount_paise)
                    for m in moves if m.kind != "caretaker_handover")
    expenses = db.scalars(select(MaintenanceExpense).where(MaintenanceExpense.expense_date.between(start, end))
                          .order_by(MaintenanceExpense.expense_date, MaintenanceExpense.id)).all()
    act = [e for e in expenses if e.status == "active"]
    caretaker_spent = sum(e.caretaker_paid_paise for e in act)
    family_paid = sum(e.family_paid_paise for e in act)
    total = caretaker_spent + family_paid
    closing = opening + handed + other_net - caretaker_spent
    by_category: dict[str, int] = {}
    for e in act:
        by_category[e.category.name] = by_category.get(e.category.name, 0) + e.total_paise
    discrepancies = []
    if closing < 0:
        discrepancies.append({"type": "negative_balance", "amount_paise": closing,
                              "message": "Expenses paid from the caretaker's cash exceed the cash he was given. "
                                         "Record the extra as family-paid, or record a missing handover."})
    discrepancies += cash._count_discrepancies(db, "caretaker_cash", start, end)
    note = db.get(MaintenanceMonthNote, period)
    return {
        "period": period,
        "target_paise": target,
        "opening_paise": opening,
        "top_up_required_paise": required,
        "handed_over_paise": handed,
        "still_to_hand_over_paise": still_to_give(db, period),
        "given_before_tracking": given_before_tracking(db, period),
        "other_adjustments_paise": other_net,
        "available_paise": opening + handed + other_net,
        "caretaker_spent_paise": caretaker_spent,
        "family_paid_paise": family_paid,
        "total_expenses_paise": total,
        "over_target_paise": max(0, total - target),
        "closing_paise": closing,
        "by_category": [{"category": k, "amount_paise": v} for k, v in sorted(by_category.items())],
        "expenses": [describe_expense(e) for e in expenses],
        "movements": [cash.describe_movement(m) for m in moves],
        "discrepancies": discrepancies,
        "notes": note.notes if note else None,
        # The top-up handed over during this month (next month's when the family gives it in advance).
        "handover_period": add_months(period, handover_offset(db, "maintenance_given_for")),
        "handover_still_to_give_paise": still_to_give(
            db, add_months(period, handover_offset(db, "maintenance_given_for"))),
    }


def overview(db: Session) -> dict:
    """Current position: cash held now and the top-up needed for next/this month."""
    cur = current_period()
    now_balance = cash.balance(db, "caretaker_cash")
    this_month = month_summary(db, cur)
    nxt = add_months(cur, 1)
    next_target = get_rate(db, "maintenance_target", nxt)
    return {
        "current_period": cur,
        "caretaker_cash_now_paise": now_balance,
        "this_month": {k: this_month[k] for k in ("target_paise", "opening_paise", "top_up_required_paise",
                                                  "handed_over_paise", "still_to_hand_over_paise",
                                                  "total_expenses_paise", "closing_paise")},
        "next_period": nxt,
        "next_target_paise": next_target,
        "next_top_up_estimate_paise": top_up_required(next_target, now_balance),
    }


def history(db: Session, months: int = 12) -> list[dict]:
    cur = current_period()
    # Start at the first month with any caretaker record, so empty pre-app months are not shown.
    firsts = [
        db.scalar(select(func.min(CashMovement.period)).where(_CARETAKER)),
        db.scalar(select(func.min(MaintenanceExpense.period))),
    ]
    known = [p for p in firsts if p is not None]
    first = min(known) if known else cur
    out = []
    for i in range(months - 1, -1, -1):
        if add_months(cur, -i) < first or before_tracking(db, add_months(cur, -i)):
            continue
        s = month_summary(db, add_months(cur, -i))
        out.append({k: s[k] for k in ("period", "target_paise", "opening_paise", "top_up_required_paise",
                                      "handed_over_paise", "caretaker_spent_paise", "family_paid_paise",
                                      "total_expenses_paise", "closing_paise")})
    return out


def describe_expense(e: MaintenanceExpense) -> dict:
    return {
        "id": e.id,
        "expense_date": e.expense_date.isoformat(),
        "period": e.period,
        "description": e.description,
        "category_id": e.category_id,
        "category": e.category.name,
        "total_paise": e.total_paise,
        "caretaker_paid_paise": e.caretaker_paid_paise,
        "family_paid_paise": e.family_paid_paise,
        "family_payment_method": e.family_payment_method,
        "flat_id": e.flat_id,
        "vendor": e.vendor,
        "status": e.status,
        "reversed_reason": e.reversed_reason,
        "recorded_by": e.recorded_by,
    }


def caretaker_available(db: Session, on: dt.date) -> int:
    """Cash the caretaker can spend on `on` without any later balance going negative."""
    return min(cash.balance(db, "caretaker_cash", on), cash.balance(db, "caretaker_cash"))


def create_expense(
    db: Session,
    *,
    expense_date: dt.date,
    description: str,
    category_id: int,
    caretaker_paid_paise: int,
    family_paid_paise: int,
    family_payment_method: str | None,
    flat_id: int | None,
    vendor: str | None,
    allow_shortfall: bool,
    actor: str,
) -> MaintenanceExpense:
    description = (description or "").strip()
    if not description:
        raise DomainError("description_required", "Describe the expense")
    cat = get_or_404(db, ExpenseCategory, category_id, "Category")
    if flat_id is not None:
        get_or_404(db, Flat, flat_id, "Flat")
    if caretaker_paid_paise < 0 or family_paid_paise < 0 or caretaker_paid_paise + family_paid_paise <= 0:
        raise DomainError("invalid_amount", "Enter the amount paid")
    if expense_date > today() + dt.timedelta(days=1):
        raise DomainError("future_date", "Date cannot be in the future")
    if family_paid_paise > 0 and family_payment_method not in FAMILY_METHODS:
        raise DomainError("invalid_method", "Say how the family paid (cash, bank or other)")
    if caretaker_paid_paise > 0 and not allow_shortfall:
        available = caretaker_available(db, expense_date)
        if caretaker_paid_paise > available:
            raise Conflict(
                "exceeds_caretaker_cash",
                "The caretaker does not have enough maintenance cash for this. Record the difference as paid "
                "by the family, or record a cash handover first.",
                {"available_paise": max(0, available), "shortfall_paise": caretaker_paid_paise - max(0, available)},
            )
    e = MaintenanceExpense(
        expense_date=expense_date, period=period_of(expense_date), description=description[:2000],
        category_id=cat.id, total_paise=caretaker_paid_paise + family_paid_paise,
        caretaker_paid_paise=caretaker_paid_paise, family_paid_paise=family_paid_paise,
        family_payment_method=family_payment_method if family_paid_paise > 0 else None,
        flat_id=flat_id, vendor=(vendor or "").strip() or None, recorded_by=actor,
    )
    db.add(e)
    db.flush()
    audit.record(db, actor, "maintenance.expense", "maintenance_expense", e.id, None, audit.snapshot(e))
    return e


def reverse_expense(db: Session, expense_id: int, reason: str | None, actor: str) -> MaintenanceExpense:
    e = get_or_404(db, MaintenanceExpense, expense_id, "Expense")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise DomainError("reason_required", "Please give a reason")
    if e.status != "active":
        raise Conflict("invalid_state", "Already reversed")
    before = audit.snapshot(e)
    e.status, e.reversed_reason, e.reversed_by, e.reversed_at = "reversed", reason, actor, utcnow()
    audit.record(db, actor, "maintenance.expense_reverse", "maintenance_expense", e.id, before, audit.snapshot(e), reason=reason)
    return e


def set_month_note(db: Session, period: str, notes: str, actor: str) -> None:
    validate_period(period)
    row = db.get(MaintenanceMonthNote, period)
    if row:
        row.notes = notes
    else:
        db.add(MaintenanceMonthNote(period=period, notes=notes))
    audit.record(db, actor, "maintenance.note", "maintenance_month", None, None, {"period": period, "notes": notes})


# ------------------------------------------------------------- categories


def list_categories(db: Session) -> list[ExpenseCategory]:
    return list(db.scalars(select(ExpenseCategory).order_by(ExpenseCategory.name)))


def upsert_category(db: Session, category_id: int | None, name: str, is_active: bool, actor: str) -> ExpenseCategory:
    name = (name or "").strip()
    if not 1 <= len(name) <= 60:
        raise DomainError("invalid_name", "Category name must be 1-60 characters")
    clash = db.scalar(select(ExpenseCategory).where(func.lower(ExpenseCategory.name) == name.lower()))
    if clash and clash.id != category_id:
        raise Conflict("duplicate", "A category with that name exists")
    if category_id:
        c = get_or_404(db, ExpenseCategory, category_id, "Category")
        c.name, c.is_active = name, is_active
    else:
        c = ExpenseCategory(name=name, is_active=True)
        db.add(c)
    db.flush()
    audit.record(db, actor, "category.save", "expense_category", c.id, None, audit.snapshot(c))
    return c


# ------------------------------------------------------------------ salary

SALARY_METHODS = {"bank", "cash", "other"}


def salary_paid_in(db: Session, period: str) -> str:
    """The month in which `period`'s salary is paid (October for November's, when paid in advance)."""
    return add_months(period, -handover_offset(db, "salary_paid_for"))


def salary_paid_during(db: Session, period: str) -> str:
    """The salary month paid during `period` (November's in October, when paid in advance)."""
    return add_months(period, handover_offset(db, "salary_paid_for"))


def salary_month(db: Session, period: str) -> dict:
    validate_period(period)
    expected = get_rate(db, "caretaker_salary", period)
    not_tracked = before_tracking(db, period) or before_tracking(db, salary_paid_in(db, period))
    pays = db.scalars(select(SalaryPayment).where(SalaryPayment.salary_month == period)
                      .order_by(SalaryPayment.payment_date, SalaryPayment.id)).all()
    confirmed = sum(p.amount_paise for p in pays if p.status == "confirmed")
    pending = sum(p.amount_paise for p in pays if p.status == "pending")
    if not_tracked and not pays:
        status = "not_tracked"
    elif confirmed >= expected:
        status = "paid"
    elif pending > 0:
        status = "pending_verification"
    elif confirmed > 0:
        status = "partially_paid"
    else:
        status = "unpaid"
    return {
        "period": period,
        "expected_paise": expected,
        "confirmed_paise": confirmed,
        "pending_paise": pending,
        "outstanding_paise": 0 if status == "not_tracked" else max(0, expected - confirmed),
        "overpaid_paise": max(0, confirmed - expected),
        "status": status,
        "paid_in_period": salary_paid_in(db, period),
        # The salary paid during this month (next month's when it is paid in advance).
        "pay_now_period": salary_paid_during(db, period),
        "payments": [describe_salary(p) for p in pays],
    }


def describe_salary(p: SalaryPayment) -> dict:
    return {
        "id": p.id, "salary_month": p.salary_month, "amount_paise": p.amount_paise,
        "payment_date": p.payment_date.isoformat(), "method": p.method, "bank_reference": p.bank_reference,
        "status": p.status, "status_reason": p.status_reason, "notes": p.notes, "recorded_by": p.recorded_by,
        "confirmed_by": p.confirmed_by,
    }


def record_salary(db: Session, *, salary_month_: str, amount_paise: int, payment_date: dt.date, method: str,
                  bank_reference: str | None, status: str, notes: str | None, actor: str) -> SalaryPayment:
    validate_period(salary_month_)
    if amount_paise <= 0:
        raise DomainError("invalid_amount", "Amount must be greater than zero")
    if method not in SALARY_METHODS:
        raise DomainError("invalid_method", "Method must be bank, cash or other")
    if status not in ("pending", "confirmed"):
        raise DomainError("invalid_status", "Status must be pending or confirmed")
    if payment_date > today() + dt.timedelta(days=1):
        raise DomainError("future_date", "Date cannot be in the future")
    p = SalaryPayment(salary_month=salary_month_, amount_paise=amount_paise, payment_date=payment_date,
                      method=method, bank_reference=(bank_reference or "").strip() or None, status=status,
                      notes=(notes or "").strip() or None, recorded_by=actor)
    if status == "confirmed":
        p.confirmed_by, p.confirmed_at = actor, utcnow()
    db.add(p)
    db.flush()
    audit.record(db, actor, "salary.create", "salary_payment", p.id, None, audit.snapshot(p))
    return p


def confirm_salary(db: Session, payment_id: int, actor: str) -> SalaryPayment:
    p = get_or_404(db, SalaryPayment, payment_id, "Salary payment")
    if p.status != "pending":
        raise Conflict("invalid_state", "Only pending salary payments can be confirmed")
    before = audit.snapshot(p)
    p.status, p.confirmed_by, p.confirmed_at = "confirmed", actor, utcnow()
    audit.record(db, actor, "salary.confirm", "salary_payment", p.id, before, audit.snapshot(p))
    return p


def cancel_salary(db: Session, payment_id: int, reason: str | None, actor: str) -> SalaryPayment:
    p = get_or_404(db, SalaryPayment, payment_id, "Salary payment")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise DomainError("reason_required", "Please give a reason")
    if p.status == "cancelled":
        raise Conflict("invalid_state", "Already cancelled")
    before = audit.snapshot(p)
    p.status, p.status_reason = "cancelled", reason
    audit.record(db, actor, "salary.cancel", "salary_payment", p.id, before, audit.snapshot(p), reason=reason)
    return p
