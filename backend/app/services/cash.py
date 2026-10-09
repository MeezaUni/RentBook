"""Cashbook: family cash and caretaker maintenance cash.

Balances are always derived from persisted rows:

family_cash   = confirmed cash rent receipts
              + cash movements into family_cash  - movements out of family_cash
              - maintenance expenses paid by the family in cash
caretaker_cash = movements into caretaker_cash - movements out of caretaker_cash
              - maintenance expenses paid from the caretaker's cash

Cash rent receipts are never copied into the movements table, so handing cash to
the caretaker cannot create a second rent receipt.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import Conflict, DomainError
from ..models import (
    CashCount,
    CashMovement,
    CashMovementSource,
    MaintenanceExpense,
    RentPayment,
    utcnow,
)
from ..periods import first_day, last_day, today
from .property import get_or_404

ACCOUNTS = {"family_cash", "caretaker_cash", "external"}
HOLDING_ACCOUNTS = ("family_cash", "caretaker_cash")

# kind -> allowed (from, to) pairs
KIND_RULES: dict[str, set[tuple[str, str]]] = {
    "caretaker_handover": {("family_cash", "caretaker_cash"), ("external", "caretaker_cash")},
    "caretaker_return": {("caretaker_cash", "family_cash")},
    "family_expense": {("family_cash", "external")},
    "bank_deposit": {("family_cash", "external")},
    "cash_in_other": {("external", "family_cash")},
    "adjustment": {("external", "family_cash"), ("family_cash", "external"),
                   ("external", "caretaker_cash"), ("caretaker_cash", "external")},
}
KIND_LABELS = {
    "caretaker_handover": "Maintenance cash handed to caretaker",
    "caretaker_return": "Cash returned by caretaker",
    "family_expense": "Other property expense (cash)",
    "bank_deposit": "Cash deposited to bank",
    "cash_in_other": "Other cash received",
    "adjustment": "Reconciliation adjustment",
    "opening_balance": "Opening balance (manually entered)",
}
FAR_FUTURE = dt.date(9999, 12, 31)


def balance(db: Session, account: str, as_of: dt.date = FAR_FUTURE) -> int:
    if account not in HOLDING_ACCOUNTS:
        raise DomainError("invalid_account", "Unknown cash account")
    active = (CashMovement.status == "active", CashMovement.movement_date <= as_of)
    inflow = db.scalar(select(func.coalesce(func.sum(CashMovement.amount_paise), 0))
                       .where(CashMovement.to_account == account, *active)) or 0
    outflow = db.scalar(select(func.coalesce(func.sum(CashMovement.amount_paise), 0))
                        .where(CashMovement.from_account == account, *active)) or 0
    total = inflow - outflow
    exp_active = (MaintenanceExpense.status == "active", MaintenanceExpense.expense_date <= as_of)
    if account == "family_cash":
        total += db.scalar(select(func.coalesce(func.sum(RentPayment.amount_paise), 0)).where(
            RentPayment.method == "cash", RentPayment.status == "confirmed", RentPayment.received_date <= as_of)) or 0
        total -= db.scalar(select(func.coalesce(func.sum(MaintenanceExpense.family_paid_paise), 0)).where(
            MaintenanceExpense.family_payment_method == "cash", *exp_active)) or 0
    else:
        total -= db.scalar(select(func.coalesce(func.sum(MaintenanceExpense.caretaker_paid_paise), 0))
                           .where(*exp_active)) or 0
    return int(total)


def linked_amount(db: Session, payment_id: int) -> int:
    return int(db.scalar(
        select(func.coalesce(func.sum(CashMovementSource.amount_paise), 0))
        .join(CashMovement, CashMovement.id == CashMovementSource.cash_movement_id)
        .where(CashMovementSource.rent_payment_id == payment_id, CashMovement.status == "active")
    ) or 0)


def create_movement(
    db: Session,
    *,
    kind: str,
    movement_date: dt.date,
    amount_paise: int,
    from_account: str,
    to_account: str,
    description: str | None,
    sources: list[tuple[int, int]] | None,
    actor: str,
) -> CashMovement:
    if kind not in KIND_RULES:
        raise DomainError("invalid_kind", "Unknown cash movement type")
    if (from_account, to_account) not in KIND_RULES[kind]:
        raise DomainError("invalid_accounts", f"'{KIND_LABELS[kind]}' cannot move cash from {from_account} to {to_account}")
    if amount_paise <= 0:
        raise DomainError("invalid_amount", "Amount must be greater than zero")
    if movement_date > today() + dt.timedelta(days=1):
        raise DomainError("future_date", "Date cannot be in the future")
    if kind == "adjustment" and len((description or "").strip()) < 3:
        raise DomainError("reason_required", "Adjustments need a description of why they are needed")
    sources = sources or []
    if sources and from_account != "family_cash":
        raise DomainError("invalid_sources", "Only cash leaving the family can be linked to tenant receipts")
    if sum(a for _, a in sources) > amount_paise:
        raise DomainError("invalid_sources", "Linked receipts add up to more than this movement")
    seen = set()
    for pay_id, amt in sources:
        if pay_id in seen:
            raise Conflict("duplicate_source", "The same receipt is linked twice")
        seen.add(pay_id)
        pay = db.get(RentPayment, pay_id)
        if pay is None or pay.method != "cash" or pay.status != "confirmed":
            raise DomainError("invalid_sources", "Only confirmed cash rent receipts can be linked")
        if amt <= 0 or linked_amount(db, pay_id) + amt > pay.amount_paise:
            raise Conflict("source_exhausted", f"Receipt #{pay_id} does not have that much unallocated cash left")
    m = CashMovement(kind=kind, movement_date=movement_date, amount_paise=amount_paise, from_account=from_account,
                     to_account=to_account, period=f"{movement_date:%Y-%m}",
                     description=(description or "").strip() or None, recorded_by=actor)
    db.add(m)
    db.flush()
    for pay_id, amt in sources:
        db.add(CashMovementSource(cash_movement_id=m.id, rent_payment_id=pay_id, amount_paise=amt))
    db.flush()
    audit.record(db, actor, f"cash.{kind}", "cash_movement", m.id, None,
                 {**audit.snapshot(m), "sources": [{"rent_payment_id": p, "amount_paise": a} for p, a in sources]})
    return m


def reverse_movement(db: Session, movement_id: int, reason: str | None, actor: str) -> CashMovement:
    m = get_or_404(db, CashMovement, movement_id, "Cash movement")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise DomainError("reason_required", "Please give a reason")
    if m.status != "active":
        raise Conflict("invalid_state", "Already reversed")
    if m.kind == "opening_balance":
        raise Conflict("use_opening_balance", "Reverse this from Settings → Opening balances")
    before = audit.snapshot(m)
    m.status, m.reversed_reason, m.reversed_by, m.reversed_at = "reversed", reason, actor, utcnow()
    audit.record(db, actor, "cash.reverse", "cash_movement", m.id, before, audit.snapshot(m), reason=reason)
    return m


def describe_movement(m: CashMovement) -> dict:
    return {
        "id": m.id,
        "movement_date": m.movement_date.isoformat(),
        "kind": m.kind,
        "kind_label": KIND_LABELS.get(m.kind, m.kind),
        "amount_paise": m.amount_paise,
        "from_account": m.from_account,
        "to_account": m.to_account,
        "description": m.description,
        "status": m.status,
        "reversed_reason": m.reversed_reason,
        "recorded_by": m.recorded_by,
        "sources": [{"rent_payment_id": s.rent_payment_id, "amount_paise": s.amount_paise} for s in m.sources],
    }


def cash_receipts(db: Session, start: dt.date | None = None, end: dt.date | None = None) -> list[dict]:
    q = select(RentPayment).where(RentPayment.method == "cash")
    if start:
        q = q.where(RentPayment.received_date >= start)
    if end:
        q = q.where(RentPayment.received_date <= end)
    out = []
    for p in db.scalars(q.order_by(RentPayment.received_date.desc(), RentPayment.id.desc())):
        linked = linked_amount(db, p.id) if p.status == "confirmed" else 0
        links = db.execute(
            select(CashMovement.id, CashMovement.kind, CashMovement.movement_date, CashMovementSource.amount_paise)
            .join(CashMovementSource, CashMovementSource.cash_movement_id == CashMovement.id)
            .where(CashMovementSource.rent_payment_id == p.id, CashMovement.status == "active")
        ).all()
        out.append({
            "payment_id": p.id,
            "received_date": p.received_date.isoformat(),
            "tenant_name": p.tenancy.tenant.full_name,
            "flat_label": p.tenancy.flat.label,
            "building_name": p.tenancy.flat.building.name,
            "periods": sorted({a.rent_due.period for a in p.allocations}),
            "amount_paise": p.amount_paise,
            "status": p.status,
            "recorded_by": p.recorded_by,
            "notes": p.notes,
            "allocated_paise": linked,
            "with_family_paise": p.amount_paise - linked if p.status == "confirmed" else 0,
            "links": [{"cash_movement_id": i, "kind": k, "kind_label": KIND_LABELS.get(k, k),
                       "date": d.isoformat(), "amount_paise": a} for i, k, d, a in links],
        })
    return out


def cashbook(db: Session, period: str) -> dict:
    start, end = first_day(period), last_day(period)
    opening = balance(db, "family_cash", start - dt.timedelta(days=1))
    receipts = cash_receipts(db, start, end)
    confirmed_receipts = sum(r["amount_paise"] for r in receipts if r["status"] == "confirmed")
    movements = db.scalars(
        select(CashMovement).where(CashMovement.movement_date.between(start, end))
        .order_by(CashMovement.movement_date, CashMovement.id)
    ).all()
    fam = [m for m in movements if "family_cash" in (m.from_account, m.to_account)]
    active = [m for m in fam if m.status == "active"]
    to_caretaker = sum(m.amount_paise for m in active if m.from_account == "family_cash" and m.to_account == "caretaker_cash")
    other_out = sum(m.amount_paise for m in active if m.from_account == "family_cash" and m.to_account != "caretaker_cash")
    other_in = sum(m.amount_paise for m in active if m.to_account == "family_cash")
    fam_cash_expenses = int(db.scalar(select(func.coalesce(func.sum(MaintenanceExpense.family_paid_paise), 0)).where(
        MaintenanceExpense.status == "active", MaintenanceExpense.family_payment_method == "cash",
        MaintenanceExpense.expense_date.between(start, end))) or 0)
    closing = balance(db, "family_cash", end)
    by_tenant: dict[str, int] = {}
    for r in receipts:
        if r["status"] == "confirmed":
            key = f"{r['tenant_name']} ({r['flat_label']})"
            by_tenant[key] = by_tenant.get(key, 0) + r["amount_paise"]
    discrepancies = []
    if closing < 0:
        discrepancies.append({"type": "negative_balance",
                              "message": "More cash has left the family than was received. Check for a missing receipt "
                                         "or record an adjustment.", "amount_paise": closing})
    discrepancies += _count_discrepancies(db, "family_cash", start, end)
    return {
        "period": period,
        "opening_paise": opening,
        "cash_rent_received_paise": confirmed_receipts,
        "other_cash_in_paise": other_in,
        "handed_to_caretaker_paise": to_caretaker,
        "other_cash_out_paise": other_out,
        "family_paid_maintenance_cash_paise": fam_cash_expenses,
        "closing_paise": closing,
        "current_balance_paise": balance(db, "family_cash"),
        "received_by_tenant": [{"tenant": k, "amount_paise": v} for k, v in sorted(by_tenant.items())],
        "receipts": receipts,
        "movements": [describe_movement(m) for m in fam],
        "discrepancies": discrepancies,
    }


def record_count(db: Session, account: str, count_date: dt.date, counted_paise: int, notes: str | None, actor: str) -> CashCount:
    if account not in HOLDING_ACCOUNTS:
        raise DomainError("invalid_account", "Unknown cash account")
    if counted_paise < 0:
        raise DomainError("invalid_amount", "Counted amount cannot be negative")
    c = CashCount(account=account, count_date=count_date, counted_paise=counted_paise,
                  notes=(notes or "").strip() or None, recorded_by=actor)
    db.add(c)
    db.flush()
    audit.record(db, actor, "cash.count", "cash_count", c.id, None,
                 {**audit.snapshot(c), "computed_paise": balance(db, account, count_date)})
    return c


def _count_discrepancies(db: Session, account: str, start: dt.date, end: dt.date) -> list[dict]:
    out = []
    for c in db.scalars(select(CashCount).where(CashCount.account == account, CashCount.count_date.between(start, end))
                        .order_by(CashCount.count_date)):
        computed = balance(db, account, c.count_date)
        if computed != c.counted_paise:
            out.append({"type": "count_mismatch", "count_id": c.id, "date": c.count_date.isoformat(),
                        "counted_paise": c.counted_paise, "computed_paise": computed,
                        "amount_paise": c.counted_paise - computed,
                        "message": f"Physical count on {c.count_date} differs from the records. "
                                   "Record the missing transaction or an adjustment."})
    return out


def counts(db: Session, account: str) -> list[dict]:
    return [{"id": c.id, "count_date": c.count_date.isoformat(), "counted_paise": c.counted_paise,
             "computed_paise": balance(db, account, c.count_date), "notes": c.notes, "recorded_by": c.recorded_by}
            for c in db.scalars(select(CashCount).where(CashCount.account == account).order_by(CashCount.count_date.desc()))]
