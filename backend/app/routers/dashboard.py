"""Monthly dashboard. Every figure is computed from stored records."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import BankTransaction, RentAllocation, RentDue, RentPayment
from ..periods import current_period, first_day, last_day, validate_period
from ..security import require_user
from ..services import cash as cash_svc
from ..services import maintenance as mnt
from .properties import buildings_overview, rent_period_for
from .rent import ledger

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


def received_in_month(db: Session, period: str) -> dict:
    start, end = first_day(period), last_day(period)
    rows = db.execute(select(RentPayment.method, RentPayment.status, func.sum(RentPayment.amount_paise))
                      .where(RentPayment.received_date.between(start, end))
                      .group_by(RentPayment.method, RentPayment.status)).all()
    out = {"cash_confirmed_paise": 0, "online_confirmed_paise": 0, "pending_paise": 0}
    for method, status, total in rows:
        if status == "confirmed":
            out[f"{method}_confirmed_paise"] += int(total or 0)
        elif status == "pending":
            out["pending_paise"] += int(total or 0)
    return out


def collected_for_period_by_method(db: Session, period: str) -> dict:
    rows = db.execute(select(RentPayment.method, func.sum(RentAllocation.amount_paise))
                      .join(RentPayment, RentPayment.id == RentAllocation.payment_id)
                      .join(RentDue, RentDue.id == RentAllocation.rent_due_id)
                      .where(RentDue.period == period, RentDue.kind == "rent", RentPayment.status == "confirmed")
                      .group_by(RentPayment.method)).all()
    out = {"cash_paise": 0, "online_paise": 0}
    for method, total in rows:
        out[f"{method}_paise"] = int(total or 0)
    return out


@router.get("/dashboard")
def dashboard(period: str | None = None, db: Session = Depends(get_db)):
    period = validate_period(period or current_period())
    # The rent being collected this month: last month's when rent is paid the month after.
    rent_period = rent_period_for(db, period)
    led = ledger(db, rent_period)
    occ = {"tenant_occupied": 0, "vacant": 0, "owner_occupied": 0}
    per_building = []
    for b in buildings_overview(db, period):  # flats' rent figures are already for rent_period
        for f in b["flats"]:
            occ[f["occupancy"]] += 1
        per_building.append({
            "id": b["id"], "name": b["name"], "color": b["color"], "code": b["code"],
            "flat_count": len(b["flats"]),
            "expected_paise": sum(f["period_due_paise"] for f in b["flats"]),
            "confirmed_paise": sum(f["period_confirmed_paise"] for f in b["flats"]),
            "outstanding_paise": sum(f["period_outstanding_paise"] for f in b["flats"]),
        })
    outstanding_flats: dict[int, dict] = {}
    for d in led["dues"] + led["arrears"]:
        if d["outstanding_paise"] <= 0:
            continue
        e = outstanding_flats.setdefault(d["flat_id"], {
            "flat_id": d["flat_id"], "flat_label": d["flat_label"], "building_name": d["building_name"],
            "tenant_name": d["tenant_name"], "outstanding_paise": 0, "periods": [], "is_overdue": False})
        e["outstanding_paise"] += d["outstanding_paise"]
        e["periods"].append(d["period"])
        e["is_overdue"] = e["is_overdue"] or d["is_overdue"]
    m = mnt.month_summary(db, period)
    sal = mnt.salary_month(db, period)
    review_q = (BankTransaction.review_status.in_(("unreviewed", "partially_allocated")), BankTransaction.credit_paise > 0)
    pending_payments = db.scalar(select(func.count(RentPayment.id)).where(RentPayment.status == "pending")) or 0
    data = {
        "period": period,
        "rent_period": rent_period,
        "rent": led["summary"],
        "collected_for_period_by_method": collected_for_period_by_method(db, rent_period),
        "received_in_month": received_in_month(db, period),
        "occupancy": occ,
        "buildings": per_building,
        "outstanding_flats": sorted(outstanding_flats.values(), key=lambda e: -e["outstanding_paise"]),
        "maintenance": {k: m[k] for k in ("target_paise", "opening_paise", "top_up_required_paise",
                                          "handed_over_paise", "still_to_hand_over_paise", "total_expenses_paise",
                                          "caretaker_spent_paise", "family_paid_paise", "closing_paise")},
        "maintenance_discrepancies": len(m["discrepancies"]),
        "caretaker_cash_now_paise": cash_svc.balance(db, "caretaker_cash"),
        "family_cash_now_paise": cash_svc.balance(db, "family_cash"),
        "salary": {k: sal[k] for k in ("expected_paise", "confirmed_paise", "pending_paise", "outstanding_paise",
                                       "status")},
        "bank_review": {
            "count": db.scalar(select(func.count(BankTransaction.id)).where(*review_q)) or 0,
            "amount_paise": int(db.scalar(select(func.coalesce(func.sum(BankTransaction.credit_paise), 0))
                                          .where(*review_q)) or 0),
        },
        "pending_payments": pending_payments,
    }
    db.commit()
    return data
