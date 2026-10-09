"""Manually entered historical opening balances.

These are labelled as manual balances everywhere. They never create fictitious
payments: tenant arrears become an 'opening_balance' due that real payments can
later settle, and cash balances become an 'opening_balance' cash movement.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import Conflict, DomainError
from ..models import CashMovement, OpeningBalance, RentDue, Tenancy, utcnow
from ..periods import period_of, today
from .property import get_or_404
from .rent import allocation_sums

KINDS = {"tenant_arrears": "Tenant arrears", "caretaker_cash": "Caretaker maintenance cash",
         "family_cash": "Cash held by family"}


def create(db: Session, *, kind: str, as_of_date: dt.date, amount_paise: int, description: str,
           notes: str | None, tenancy_id: int | None, actor: str) -> OpeningBalance:
    if kind not in KINDS:
        raise DomainError("invalid_kind", "Unknown opening balance type")
    if amount_paise <= 0:
        raise DomainError("invalid_amount", "Amount must be greater than zero")
    description = (description or "").strip()
    if len(description) < 3:
        raise DomainError("description_required", "Describe what this balance represents")
    if as_of_date > today():
        raise DomainError("future_date", "Opening balance date cannot be in the future")
    ob = OpeningBalance(kind=kind, as_of_date=as_of_date, amount_paise=amount_paise, description=description,
                        notes=(notes or "").strip() or None, created_by=actor)
    label = f"Opening balance (manually entered): {description}"
    if kind == "tenant_arrears":
        if tenancy_id is None:
            raise DomainError("tenancy_required", "Choose the tenancy that owes this amount")
        t = get_or_404(db, Tenancy, tenancy_id, "Tenancy")
        period = period_of(as_of_date)
        if db.scalar(select(RentDue).where(RentDue.tenancy_id == t.id, RentDue.period == period,
                                           RentDue.kind == "opening_balance")):
            raise Conflict("exists", "An opening balance for this tenancy and month already exists")
        due = RentDue(tenancy_id=t.id, period=period, kind="opening_balance", amount_paise=amount_paise,
                      due_date=as_of_date, description=label)
        db.add(due)
        db.flush()
        ob.tenancy_id, ob.rent_due_id = t.id, due.id
    else:
        if tenancy_id is not None:
            raise DomainError("invalid_tenancy", "Cash balances are not linked to a tenancy")
        m = CashMovement(kind="opening_balance", movement_date=as_of_date, amount_paise=amount_paise,
                         from_account="external", to_account=kind, period=period_of(as_of_date),
                         description=label, recorded_by=actor)
        db.add(m)
        db.flush()
        ob.cash_movement_id = m.id
    db.add(ob)
    db.flush()
    audit.record(db, actor, "opening_balance.create", "opening_balance", ob.id, None, audit.snapshot(ob))
    return ob


def reverse(db: Session, ob_id: int, reason: str | None, actor: str) -> OpeningBalance:
    ob = get_or_404(db, OpeningBalance, ob_id, "Opening balance")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise DomainError("reason_required", "Please give a reason")
    if ob.status != "active":
        raise Conflict("invalid_state", "Already reversed")
    before = audit.snapshot(ob)
    if ob.rent_due_id:
        due = db.get(RentDue, ob.rent_due_id)
        s = allocation_sums(db, [due.id])[due.id]
        if s["confirmed"] + s["pending"] > 0:
            raise Conflict("has_payments", "Payments are allocated to this opening balance. Move them first.")
        due.waived_at, due.waived_by, due.waived_reason = utcnow(), actor, f"Opening balance reversed: {reason}"
    if ob.cash_movement_id:
        m = db.get(CashMovement, ob.cash_movement_id)
        m.status, m.reversed_reason, m.reversed_by, m.reversed_at = "reversed", reason, actor, utcnow()
    ob.status = "reversed"
    audit.record(db, actor, "opening_balance.reverse", "opening_balance", ob.id, before, audit.snapshot(ob), reason=reason)
    return ob


def list_all(db: Session) -> list[dict]:
    out = []
    for ob in db.scalars(select(OpeningBalance).order_by(OpeningBalance.as_of_date.desc(), OpeningBalance.id.desc())):
        t = db.get(Tenancy, ob.tenancy_id) if ob.tenancy_id else None
        out.append({
            "id": ob.id, "kind": ob.kind, "kind_label": KINDS[ob.kind], "as_of_date": ob.as_of_date.isoformat(),
            "amount_paise": ob.amount_paise, "description": ob.description, "notes": ob.notes,
            "tenancy_id": ob.tenancy_id, "tenant_name": t.tenant.full_name if t else None,
            "flat_label": t.flat.label if t else None, "status": ob.status, "created_by": ob.created_by,
        })
    return out
