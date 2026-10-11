"""Monthly rent dues, payments and allocations.

Core rules
----------
* Each tenancy has at most one ``rent`` due per month, priced from the rent term
  effective in that month. Old dues are never re-priced by later rent changes.
* Outstanding = max(0, due - confirmed allocations). Pending payments never count.
* A payment is split into allocations against specific dues. The sum of active
  (pending + confirmed) allocations on a due may not exceed the due, and a payment
  cannot be allocated to the same due twice.
* Money beyond what the chosen month needs is never silently placed elsewhere: the
  caller must choose how to handle it (arrears, future months, credit, refund).
* Nothing is deleted. Payments are rejected / reversed with a reason.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..errors import Conflict, DomainError, NotFound
from ..models import (
    CashMovement,
    CashMovementSource,
    RentAllocation,
    RentDue,
    RentPayment,
    Tenancy,
    utcnow,
)
from ..periods import (
    add_months,
    current_period,
    days_in,
    due_date_for,
    first_day,
    iter_periods,
    label,
    last_day,
    period_of,
    today,
    validate_period,
)
from .property import get_or_404, rent_for_period, tenancy_covers
from .settings import get_setting

ACTIVE = ("pending", "confirmed")
EXCESS_CHOICES = {"apply_to_arrears", "apply_forward", "hold_as_credit", "refund_due"}
MAX_FUTURE_MONTHS = 24

# ----------------------------------------------------------------- billing


def policy_for(db: Session, t: Tenancy) -> str:
    return t.billing_policy or get_setting(db, "billing_policy")


def due_months_later(db: Session) -> int:
    """1 when a month's rent falls due in the following month (paid in arrears), else 0."""
    return 1 if get_setting(db, "rent_due_month") == "next_month" else 0


def is_billable(db: Session, t: Tenancy, period: str) -> tuple[bool, int | None]:
    """Whether `period` generates a rent due for tenancy `t`, and for how much."""
    if period < t.billing_start_period:
        return False, None
    fd, ld = first_day(period), last_day(period)
    if t.start_date > ld or (t.end_date is not None and t.end_date < fd):
        return False, None
    rent = rent_for_period(t, period)
    if rent is None:
        return False, None
    policy = policy_for(db, t)
    if policy == "due_date_in_tenancy":
        # The due day *within* the month, even when payment falls due the month after:
        # someone who leaves on 30 Sep still owes September's rent, payable in October.
        return tenancy_covers(t, due_date_for(period, t.rent_due_day)), rent
    if policy == "any_overlap":
        return True, rent
    if policy == "full_months_only":
        return (t.start_date <= fd and (t.end_date is None or t.end_date >= ld)), rent
    if policy == "prorated_daily":
        start, end = max(t.start_date, fd), min(t.end_date or ld, ld)
        days, dim = (end - start).days + 1, days_in(period)
        # Integer half-up rounding on paise.
        return True, (rent * days * 2 + dim) // (2 * dim)
    raise DomainError("invalid_policy", f"Unknown billing policy {policy}")


def ensure_dues(db: Session, up_to_period: str, tenancy_ids: list[int] | None = None) -> int:
    """Idempotently create missing rent dues up to `up_to_period` (inclusive)."""
    validate_period(up_to_period)
    up_to = min(up_to_period, add_months(current_period(), MAX_FUTURE_MONTHS))
    q = select(Tenancy).where(Tenancy.billing_start_period <= up_to)
    if tenancy_ids is not None:
        q = q.where(Tenancy.id.in_(tenancy_ids))
    tenancies = db.scalars(q).all()
    if not tenancies:
        return 0
    existing = set(
        db.execute(
            select(RentDue.tenancy_id, RentDue.period).where(
                RentDue.kind == "rent", RentDue.tenancy_id.in_([t.id for t in tenancies])
            )
        ).all()
    )
    created = 0
    later = due_months_later(db)
    for t in tenancies:
        last = up_to if t.end_date is None else min(up_to, period_of(t.end_date))
        for p in iter_periods(t.billing_start_period, last):
            if (t.id, p) in existing:
                continue
            ok, amount = is_billable(db, t, p)
            if not ok:
                continue
            try:
                with db.begin_nested():
                    db.add(RentDue(tenancy_id=t.id, period=p, kind="rent", amount_paise=amount,
                                   due_date=due_date_for(p, t.rent_due_day, later)))
                created += 1
            except IntegrityError:  # created concurrently by another request
                pass
    if created:
        db.flush()
    return created


# ------------------------------------------------------------ due status


def allocation_sums(db: Session, due_ids: list[int]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {i: {"confirmed": 0, "pending": 0} for i in due_ids}
    if not due_ids:
        return out
    rows = db.execute(
        select(RentAllocation.rent_due_id, RentPayment.status, func.sum(RentAllocation.amount_paise))
        .join(RentPayment, RentPayment.id == RentAllocation.payment_id)
        .where(RentAllocation.rent_due_id.in_(due_ids), RentPayment.status.in_(ACTIVE))
        .group_by(RentAllocation.rent_due_id, RentPayment.status)
    ).all()
    for due_id, status, total in rows:
        out[due_id][status] = int(total or 0)
    return out


def due_status(due: RentDue, confirmed: int, pending: int, on: dt.date, grace_days: int) -> tuple[str, bool]:
    """Returns (status, is_overdue). Derived purely from stored facts."""
    if due.waived_at is not None:
        return "waived", False
    if confirmed >= due.amount_paise:
        return "paid", False
    overdue = on > due.due_date + dt.timedelta(days=grace_days)
    if pending > 0:
        return "pending_verification", overdue
    if confirmed > 0:
        return "partially_paid", overdue
    return ("overdue" if overdue else "unpaid"), overdue


def describe_dues(db: Session, dues: list[RentDue], on: dt.date | None = None) -> list[dict]:
    on = on or today()
    grace = int(get_setting(db, "overdue_grace_days") or 0)
    sums = allocation_sums(db, [d.id for d in dues])
    out = []
    for d in dues:
        s = sums[d.id]
        status, overdue = due_status(d, s["confirmed"], s["pending"], on, grace)
        outstanding = 0 if d.waived_at else max(0, d.amount_paise - s["confirmed"])
        t = d.tenancy
        out.append({
            "id": d.id,
            "tenancy_id": d.tenancy_id,
            "tenant_id": t.tenant_id,
            "tenant_name": t.tenant.full_name,
            "flat_id": t.flat_id,
            "flat_label": t.flat.label,
            "building_id": t.flat.building_id,
            "building_name": t.flat.building.name,
            "period": d.period,
            "kind": d.kind,
            "description": d.description,
            "amount_paise": d.amount_paise,
            "due_date": d.due_date.isoformat(),
            "confirmed_paise": s["confirmed"],
            "pending_paise": s["pending"],
            "outstanding_paise": outstanding,
            "available_paise": 0 if d.waived_at else max(0, d.amount_paise - s["confirmed"] - s["pending"]),
            "status": status,
            "is_overdue": overdue,
            "amount_overridden": d.amount_overridden,
            "override_reason": d.override_reason,
            "waived_reason": d.waived_reason,
        })
    return out


def available_on_due(db: Session, due: RentDue) -> int:
    if due.waived_at is not None:
        return 0
    s = allocation_sums(db, [due.id])[due.id]
    return max(0, due.amount_paise - s["confirmed"] - s["pending"])


def get_due_for(db: Session, t: Tenancy, period: str) -> RentDue | None:
    ensure_dues(db, period, [t.id])
    return db.scalar(select(RentDue).where(RentDue.tenancy_id == t.id, RentDue.period == period, RentDue.kind == "rent"))


def outstanding_dues(db: Session, tenancy_id: int) -> list[dict]:
    dues = db.scalars(
        select(RentDue).where(RentDue.tenancy_id == tenancy_id, RentDue.waived_at.is_(None))
        .order_by(RentDue.period, RentDue.kind.desc())
    ).all()
    return [d for d in describe_dues(db, list(dues)) if d["outstanding_paise"] > 0]


# --------------------------------------------------------------- payments


@dataclass
class PaymentInput:
    tenancy_id: int
    received_date: dt.date
    amount_paise: int
    method: str
    status: str = "confirmed"
    rent_period: str | None = None
    allocations: list[tuple[int, int]] | None = None  # (rent_due_id, amount_paise)
    reference: str | None = None
    sender_name: str | None = None
    notes: str | None = None
    excess_handling: str | None = None
    excess_note: str | None = None
    allow_duplicate: bool = False
    bank_transaction_id: int | None = None
    corrects_payment_id: int | None = None
    extra_audit: dict = field(default_factory=dict)


def _clean(s: str | None, n: int) -> str | None:
    s = (s or "").strip()
    return s[:n] or None


def _find_duplicates(db: Session, p: PaymentInput) -> list[RentPayment]:
    dups = []
    ref = _clean(p.reference, 120)
    if ref:
        dups += db.scalars(select(RentPayment).where(
            func.lower(RentPayment.reference) == ref.lower(), RentPayment.status.in_(ACTIVE))).all()
    dups += db.scalars(select(RentPayment).where(
        RentPayment.tenancy_id == p.tenancy_id, RentPayment.amount_paise == p.amount_paise,
        RentPayment.received_date == p.received_date, RentPayment.method == p.method,
        RentPayment.status.in_(ACTIVE))).all()
    return list({d.id: d for d in dups}.values())


def _plan_allocations(db: Session, t: Tenancy, p: PaymentInput) -> tuple[list[tuple[RentDue, int]], int, str | None]:
    """Returns (plan, unallocated_paise, excess_resolution)."""
    plan: list[tuple[RentDue, int]] = []
    remaining = p.amount_paise
    if p.allocations:
        seen = set()
        for due_id, amount in p.allocations:
            if due_id in seen:
                raise Conflict("duplicate_allocation", "The same rent month appears twice in the allocation")
            seen.add(due_id)
            due = db.get(RentDue, due_id)
            if due is None or due.tenancy_id != t.id:
                raise DomainError("invalid_allocation", "Allocation refers to a rent due of a different tenancy")
            if amount <= 0:
                raise DomainError("invalid_allocation", "Allocation amounts must be positive")
            avail = available_on_due(db, due)
            if amount > avail:
                raise Conflict("over_allocation",
                               f"{label(due.period)}: only ₹{avail / 100:,.2f} remains to be paid, cannot allocate ₹{amount / 100:,.2f}",
                               {"rent_due_id": due.id, "available_paise": avail})
            plan.append((due, amount))
            remaining -= amount
        if remaining < 0:
            raise DomainError("invalid_allocation", "Allocations add up to more than the payment amount")
    else:
        if not p.rent_period:
            raise DomainError("missing_period", "Select the rent month this payment is for")
        validate_period(p.rent_period)
        due = get_due_for(db, t, p.rent_period)
        if due is None:
            raise DomainError(
                "no_due",
                f"No rent is due for this tenancy in {p.rent_period} (outside the tenancy, before rent tracking "
                "started, or not billable under the billing policy). Add a manual charge if rent is owed.",
            )
        avail = available_on_due(db, due)
        if avail > 0:
            take = min(avail, remaining)
            plan.append((due, take))
            remaining -= take

    resolution = None
    if remaining > 0:
        h = p.excess_handling
        if h is None:
            used = p.amount_paise - remaining
            raise Conflict(
                "excess_payment",
                f"This payment is {remaining / 100:.2f} more than what remains due for the selected month. "
                "Choose how to handle the extra amount.",
                {"excess_paise": remaining, "allocated_paise": used},
            )
        if h not in EXCESS_CHOICES:
            raise DomainError("invalid_excess_handling", "Unknown option for the extra amount")
        planned_ids = {d.id for d, _ in plan}
        if h == "apply_forward":
            # Walk forward month by month, creating only the months the money actually covers.
            anchor = p.rent_period or max((d.period for d, _ in plan), default=current_period())
            for i in range(1, MAX_FUTURE_MONTHS + 1):
                if remaining <= 0:
                    break
                due = get_due_for(db, t, add_months(anchor, i))
                if due is None or due.id in planned_ids:
                    continue
                avail = available_on_due(db, due)
                if avail <= 0:
                    continue
                take = min(avail, remaining)
                plan.append((due, take))
                remaining -= take
            if remaining > 0:
                resolution = "advance_credit"
        elif h == "apply_to_arrears":
            cond = [RentDue.period <= current_period()]  # older / current outstanding months, oldest first
            candidates = db.scalars(
                select(RentDue).where(RentDue.tenancy_id == t.id, RentDue.waived_at.is_(None), *cond)
                .order_by(RentDue.period, RentDue.kind.desc())
            ).all()
            for due in candidates:
                if remaining <= 0:
                    break
                if due.id in planned_ids:
                    continue
                avail = available_on_due(db, due)
                if avail <= 0:
                    continue
                take = min(avail, remaining)
                plan.append((due, take))
                remaining -= take
            if remaining > 0:
                resolution = "advance_credit"
        elif h == "hold_as_credit":
            resolution = "advance_credit"
        else:
            resolution = "refund_due"
    return plan, remaining, resolution


def record_payment(db: Session, p: PaymentInput, actor: str) -> RentPayment:
    t = get_or_404(db, Tenancy, p.tenancy_id, "Tenancy")
    if p.amount_paise <= 0:
        raise DomainError("invalid_amount", "Amount must be greater than zero")
    if p.method not in ("cash", "online"):
        raise DomainError("invalid_method", "Payment method must be Cash or Online Transfer")
    if p.status not in ACTIVE:
        raise DomainError("invalid_status", "New payments must be pending or confirmed")
    if p.received_date > today() + dt.timedelta(days=1):
        raise DomainError("future_date", "Payment date cannot be in the future")
    if not p.allow_duplicate and p.bank_transaction_id is None:
        dups = _find_duplicates(db, p)
        if dups:
            raise Conflict(
                "possible_duplicate",
                "A payment with the same reference, or the same amount, date and method, is already recorded.",
                {"payment_ids": [d.id for d in dups]},
            )
    plan, unallocated, resolution = _plan_allocations(db, t, p)
    pay = RentPayment(
        tenancy_id=t.id,
        received_date=p.received_date,
        amount_paise=p.amount_paise,
        method=p.method,
        reference=_clean(p.reference, 120),
        sender_name=_clean(p.sender_name, 160),
        status=p.status,
        notes=_clean(p.notes, 4000),
        excess_resolution=resolution,
        excess_note=_clean(p.excess_note, 2000) if unallocated else None,
        bank_transaction_id=p.bank_transaction_id,
        corrects_payment_id=p.corrects_payment_id,
        recorded_by=actor,
    )
    if p.status == "confirmed":
        pay.confirmed_by, pay.confirmed_at = actor, utcnow()
    db.add(pay)
    db.flush()
    for due, amount in plan:
        db.add(RentAllocation(payment_id=pay.id, rent_due_id=due.id, amount_paise=amount, created_by=actor))
    db.flush()
    audit.record(db, actor, "payment.create", "rent_payment", pay.id, None, {
        **audit.snapshot(pay),
        "allocations": [{"period": d.period, "rent_due_id": d.id, "amount_paise": a} for d, a in plan],
        "unallocated_paise": unallocated,
        **p.extra_audit,
    })
    return pay


def _require_reason(reason: str | None) -> str:
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise DomainError("reason_required", "Please give a reason (at least 3 characters)")
    return reason


def confirm_payment(db: Session, payment_id: int, actor: str) -> RentPayment:
    pay = get_or_404(db, RentPayment, payment_id, "Payment")
    if pay.status != "pending":
        raise Conflict("invalid_state", f"Only pending payments can be confirmed (this one is {pay.status})")
    before = audit.snapshot(pay)
    pay.status, pay.confirmed_by, pay.confirmed_at = "confirmed", actor, utcnow()
    audit.record(db, actor, "payment.confirm", "rent_payment", pay.id, before, audit.snapshot(pay))
    return pay


def _active_cash_links(db: Session, payment_id: int) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(CashMovementSource.amount_paise), 0))
        .join(CashMovement, CashMovement.id == CashMovementSource.cash_movement_id)
        .where(CashMovementSource.rent_payment_id == payment_id, CashMovement.status == "active")
    ) or 0


def void_payment(db: Session, payment_id: int, reason: str | None, actor: str) -> RentPayment:
    """Reject a pending payment or reverse a confirmed one. The record is kept."""
    pay = get_or_404(db, RentPayment, payment_id, "Payment")
    reason = _require_reason(reason)
    if pay.status not in ACTIVE:
        raise Conflict("invalid_state", f"This payment is already {pay.status}")
    if pay.method == "cash" and _active_cash_links(db, pay.id) > 0:
        raise Conflict("cash_in_use",
                       "Part of this cash receipt is linked to a cash handover or expense in the Cashbook. "
                       "Reverse that cash movement first.")
    before = audit.snapshot(pay)
    pay.status = "rejected" if pay.status == "pending" else "reversed"
    pay.status_reason, pay.voided_by, pay.voided_at = reason, actor, utcnow()
    audit.record(db, actor, f"payment.{pay.status}", "rent_payment", pay.id, before, audit.snapshot(pay), reason=reason)
    if pay.bank_transaction_id:
        from .bank import refresh_review_status

        refresh_review_status(db, pay.bank_transaction_id)
    return pay


def correct_payment(db: Session, payment_id: int, reason: str | None, new: PaymentInput, actor: str) -> RentPayment:
    """Void the original and record a corrected payment linked to it, atomically."""
    original = void_payment(db, payment_id, reason, actor)
    if original.bank_transaction_id and new.bank_transaction_id is None:
        new.bank_transaction_id = original.bank_transaction_id
    new.corrects_payment_id = original.id
    new.allow_duplicate = True
    db.flush()
    return record_payment(db, new, actor)


def reallocate_payment(db: Session, payment_id: int, allocations: list[tuple[int, int]], reason: str | None, actor: str) -> RentPayment:
    """Move a payment to different rent months (e.g. it was entered against the wrong month)."""
    pay = get_or_404(db, RentPayment, payment_id, "Payment")
    reason = _require_reason(reason)
    if pay.status not in ACTIVE:
        raise Conflict("invalid_state", "Only pending or confirmed payments can be re-allocated")
    before = {"allocations": [{"rent_due_id": a.rent_due_id, "amount_paise": a.amount_paise} for a in pay.allocations]}
    for a in list(pay.allocations):
        db.delete(a)
    db.flush()
    total = 0
    seen = set()
    for due_id, amount in allocations:
        due = db.get(RentDue, due_id)
        if due is None or due.tenancy_id != pay.tenancy_id:
            raise DomainError("invalid_allocation", "Allocation refers to a rent due of a different tenancy")
        if due_id in seen:
            raise Conflict("duplicate_allocation", "The same rent month appears twice")
        seen.add(due_id)
        if amount <= 0 or amount > available_on_due(db, due):
            raise Conflict("over_allocation", f"{due.period}: amount exceeds what remains due")
        db.add(RentAllocation(payment_id=pay.id, rent_due_id=due_id, amount_paise=amount, created_by=actor))
        total += amount
    if total > pay.amount_paise:
        raise DomainError("invalid_allocation", "Allocations add up to more than the payment amount")
    if total == pay.amount_paise:
        pay.excess_resolution, pay.excess_note = None, None
    db.flush()
    db.refresh(pay)
    audit.record(db, actor, "payment.reallocate", "rent_payment", pay.id, before,
                 {"allocations": [{"rent_due_id": d, "amount_paise": a} for d, a in allocations]}, reason=reason)
    return pay


def resolve_excess(db: Session, payment_id: int, resolution: str, note: str | None, actor: str) -> RentPayment:
    pay = get_or_404(db, RentPayment, payment_id, "Payment")
    if resolution not in ("advance_credit", "refund_due", "refunded"):
        raise DomainError("invalid_resolution", "Unknown resolution")
    if unallocated_of(pay) <= 0:
        raise Conflict("no_excess", "This payment has no unallocated amount")
    before = audit.snapshot(pay)
    pay.excess_resolution, pay.excess_note = resolution, _clean(note, 2000)
    audit.record(db, actor, "payment.excess_resolution", "rent_payment", pay.id, before, audit.snapshot(pay))
    return pay


def unallocated_of(pay: RentPayment) -> int:
    return pay.amount_paise - sum(a.amount_paise for a in pay.allocations)


def describe_payment(pay: RentPayment) -> dict:
    t = pay.tenancy
    return {
        "id": pay.id,
        "tenancy_id": pay.tenancy_id,
        "tenant_name": t.tenant.full_name,
        "flat_id": t.flat_id,
        "flat_label": t.flat.label,
        "building_name": t.flat.building.name,
        "received_date": pay.received_date.isoformat(),
        "amount_paise": pay.amount_paise,
        "method": pay.method,
        "reference": pay.reference,
        "sender_name": pay.sender_name,
        "status": pay.status,
        "status_reason": pay.status_reason,
        "notes": pay.notes,
        "excess_resolution": pay.excess_resolution,
        "excess_note": pay.excess_note,
        "unallocated_paise": unallocated_of(pay),
        "needs_excess_decision": unallocated_of(pay) > 0 and pay.excess_resolution is None and pay.status in ACTIVE,
        "bank_transaction_id": pay.bank_transaction_id,
        "corrects_payment_id": pay.corrects_payment_id,
        "recorded_by": pay.recorded_by,
        "created_at": pay.created_at.isoformat(),
        "confirmed_by": pay.confirmed_by,
        "confirmed_at": pay.confirmed_at.isoformat() if pay.confirmed_at else None,
        "voided_by": pay.voided_by,
        "voided_at": pay.voided_at.isoformat() if pay.voided_at else None,
        "allocations": [
            {
                "rent_due_id": a.rent_due_id,
                "period": a.rent_due.period,
                "kind": a.rent_due.kind,
                "amount_paise": a.amount_paise,
                "due_date": a.rent_due.due_date.isoformat(),
                "is_late": pay.received_date > a.rent_due.due_date,
            }
            for a in sorted(pay.allocations, key=lambda a: a.rent_due.period)
        ],
    }


# ------------------------------------------------------------ due edits


def waive_due(db: Session, due_id: int, reason: str | None, actor: str) -> RentDue:
    due = get_or_404(db, RentDue, due_id, "Rent due")
    reason = _require_reason(reason)
    if due.waived_at:
        raise Conflict("invalid_state", "Already waived")
    before = audit.snapshot(due)
    due.waived_at, due.waived_by, due.waived_reason = utcnow(), actor, reason
    audit.record(db, actor, "due.waive", "rent_due", due.id, before, audit.snapshot(due), reason=reason)
    return due


def unwaive_due(db: Session, due_id: int, actor: str) -> RentDue:
    due = get_or_404(db, RentDue, due_id, "Rent due")
    if not due.waived_at:
        raise Conflict("invalid_state", "This due is not waived")
    before = audit.snapshot(due)
    due.waived_at = due.waived_by = due.waived_reason = None
    audit.record(db, actor, "due.unwaive", "rent_due", due.id, before, audit.snapshot(due))
    return due


def override_due_amount(db: Session, due_id: int, amount_paise: int, reason: str | None, actor: str) -> RentDue:
    due = get_or_404(db, RentDue, due_id, "Rent due")
    reason = _require_reason(reason)
    if amount_paise < 0:
        raise DomainError("invalid_amount", "Amount cannot be negative")
    s = allocation_sums(db, [due.id])[due.id]
    if amount_paise < s["confirmed"] + s["pending"]:
        raise Conflict("below_payments", "Payments already recorded for this month exceed that amount")
    before = audit.snapshot(due)
    due.amount_paise, due.amount_overridden, due.override_reason = amount_paise, True, reason
    audit.record(db, actor, "due.override_amount", "rent_due", due.id, before, audit.snapshot(due), reason=reason)
    return due


def add_manual_due(db: Session, tenancy_id: int, period: str, amount_paise: int, reason: str | None, actor: str) -> RentDue:
    """Charge rent for a month the billing policy did not bill automatically (e.g. a part month)."""
    t = get_or_404(db, Tenancy, tenancy_id, "Tenancy")
    validate_period(period)
    reason = _require_reason(reason)
    if amount_paise <= 0:
        raise DomainError("invalid_amount", "Amount must be greater than zero")
    if t.start_date > last_day(period) or (t.end_date and t.end_date < first_day(period)):
        raise DomainError("outside_tenancy", "That month is outside this tenancy")
    if db.scalar(select(RentDue).where(RentDue.tenancy_id == t.id, RentDue.period == period, RentDue.kind == "rent")):
        raise Conflict("due_exists", "Rent for that month already exists. Edit its amount instead.")
    due_date = max(due_date_for(period, t.rent_due_day, due_months_later(db)), t.start_date)
    due = RentDue(tenancy_id=t.id, period=period, kind="rent", amount_paise=amount_paise, due_date=due_date,
                  amount_overridden=True, override_reason=reason)
    db.add(due)
    db.flush()
    audit.record(db, actor, "due.manual_create", "rent_due", due.id, None, audit.snapshot(due), reason=reason)
    return due


def get_payment(db: Session, payment_id: int) -> RentPayment:
    pay = db.get(RentPayment, payment_id)
    if pay is None:
        raise NotFound("Payment")
    return pay
