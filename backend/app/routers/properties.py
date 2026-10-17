"""Buildings, flats, tenants, tenancies and sender aliases."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Building, Flat, OwnerOccupancy, RentDue, RentPayment, Tenancy, Tenant
from ..periods import add_months, current_period, first_day, last_day, today, validate_period
from ..security import Actor, require_user
from ..services import property as svc
from ..services import rent as rent_svc

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])


def reference_date(period: str) -> dt.date:
    cur = current_period()
    if period == cur:
        return today()
    return last_day(period) if period < cur else first_day(period)


def describe_tenancy(t: Tenancy, period: str | None = None) -> dict:
    period = period or current_period()
    return {
        "id": t.id, "tenant_id": t.tenant_id, "tenant_name": t.tenant.full_name, "flat_id": t.flat_id,
        "flat_label": t.flat.label, "building_id": t.flat.building_id, "building_name": t.flat.building.name,
        "start_date": t.start_date.isoformat(), "end_date": t.end_date.isoformat() if t.end_date else None,
        "rent_due_day": t.rent_due_day, "billing_start_period": t.billing_start_period,
        "billing_policy": t.billing_policy, "notes": t.notes, "status": svc.tenancy_status(t),
        "current_rent_paise": svc.rent_for_period(t, period),
        "rent_terms": [{"id": r.id, "amount_paise": r.amount_paise, "effective_period": r.effective_period,
                        "note": r.note, "created_by": r.created_by} for r in t.rent_terms],
    }


def _flat_card(db: Session, f: Flat, period: str, dues_by_flat: dict[int, list[dict]]) -> dict:
    ref = reference_date(period)
    state, tenancy = svc.occupancy_on(db, f.id, ref)
    dues = dues_by_flat.get(f.id, [])
    if tenancy is None and dues:
        tenancy = db.get(Tenancy, dues[-1]["tenancy_id"])
    status = None
    if dues:
        order = ["overdue", "partially_paid", "pending_verification", "unpaid", "paid", "waived"]
        status = sorted((d["status"] for d in dues), key=order.index)[0]
    return {
        "id": f.id, "label": f.label, "floor_index": f.floor_index, "floor_label": f.floor_label,
        "position": f.position, "notes": f.notes, "occupancy": state,
        "tenancy": describe_tenancy(tenancy, period) if tenancy else None,
        "period_status": status,
        "period_due_paise": sum(d["amount_paise"] for d in dues if d["kind"] == "rent"),
        "period_confirmed_paise": sum(d["confirmed_paise"] for d in dues if d["kind"] == "rent"),
        "period_outstanding_paise": sum(d["outstanding_paise"] for d in dues if d["kind"] == "rent"),
        "is_overdue": any(d["is_overdue"] for d in dues),
    }


def rent_period_for(db: Session, period: str) -> str:
    """The month whose rent is collected in `period`: the previous one when rent is paid the month after."""
    return add_months(period, -rent_svc.due_months_later(db))


def buildings_overview(db: Session, period: str) -> list[dict]:
    """Who lives in each flat during `period`, with the status of the rent collected in it."""
    rent_svc.ensure_dues(db, period)
    rent_period = rent_period_for(db, period)
    dues = db.scalars(select(RentDue).where(RentDue.period == rent_period, RentDue.kind == "rent")).all()
    by_flat: dict[int, list[dict]] = {}
    for d in rent_svc.describe_dues(db, list(dues)):
        by_flat.setdefault(d["flat_id"], []).append(d)
    out = []
    for b in db.scalars(select(Building).order_by(Building.sort_order)):
        flats = sorted(b.flats, key=lambda f: (f.floor_index, f.position))
        out.append({"id": b.id, "code": b.code, "name": b.name, "color": b.color,
                    "flats": [_flat_card(db, f, period, by_flat) for f in flats]})
    return out


@router.get("/buildings")
def list_buildings(period: str | None = None, db: Session = Depends(get_db)):
    period = validate_period(period or current_period())
    data = buildings_overview(db, period)
    db.commit()
    return {"period": period, "rent_period": rent_period_for(db, period), "buildings": data}


class BuildingIn(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    color: str | None = Field(default=None, max_length=16)


@router.patch("/buildings/{building_id}")
def edit_building(building_id: int, body: BuildingIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    b = svc.update_building(db, building_id, body.name, body.color, actor.name)
    db.commit()
    return {"id": b.id, "name": b.name, "color": b.color}


class FlatIn(BaseModel):
    label: str | None = Field(default=None, max_length=32)
    floor_label: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=2000)


@router.patch("/flats/{flat_id}")
def edit_flat(flat_id: int, body: FlatIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    f = svc.update_flat(db, flat_id, body.label, body.floor_label, body.notes, actor.name)
    db.commit()
    return {"id": f.id, "label": f.label, "floor_label": f.floor_label, "notes": f.notes}


@router.get("/flats/{flat_id}")
def flat_detail(flat_id: int, period: str | None = None, db: Session = Depends(get_db)):
    period = validate_period(period or current_period())
    f = svc.get_or_404(db, Flat, flat_id, "Flat")
    tenancies = db.scalars(select(Tenancy).where(Tenancy.flat_id == f.id).order_by(Tenancy.start_date.desc())).all()
    tids = [t.id for t in tenancies]
    rent_svc.ensure_dues(db, max(period, current_period()), tids)
    ref = reference_date(period)
    state, tenancy = svc.occupancy_on(db, f.id, ref)
    now_state, now_tenancy = svc.occupancy_on(db, f.id, today())
    dues = db.scalars(select(RentDue).where(RentDue.tenancy_id.in_(tids), RentDue.period == period)
                      .order_by(RentDue.kind.desc())).all() if tids else []
    described = rent_svc.describe_dues(db, list(dues))
    arrears_rows = db.scalars(select(RentDue).where(RentDue.tenancy_id.in_(tids), RentDue.period < period,
                                                    RentDue.waived_at.is_(None)).order_by(RentDue.period)).all() if tids else []
    arrears = [d for d in rent_svc.describe_dues(db, list(arrears_rows)) if d["outstanding_paise"] > 0]
    pay_ids = {a.payment_id for d in dues for a in d.allocations}
    period_payments = [rent_svc.describe_payment(p) for p in db.scalars(
        select(RentPayment).where(RentPayment.id.in_(pay_ids)).order_by(RentPayment.received_date))] if pay_ids else []
    history = [rent_svc.describe_payment(p) for p in db.scalars(
        select(RentPayment).where(RentPayment.tenancy_id.in_(tids))
        .order_by(RentPayment.received_date.desc(), RentPayment.id.desc()).limit(100))] if tids else []
    credits = [p for p in history if p["unallocated_paise"] > 0 and p["status"] in rent_svc.ACTIVE]
    owner = db.scalars(select(OwnerOccupancy).where(OwnerOccupancy.flat_id == f.id)
                       .order_by(OwnerOccupancy.start_date.desc())).all()
    all_dues = db.scalars(select(RentDue).where(RentDue.tenancy_id.in_(tids))
                          .order_by(RentDue.period.desc(), RentDue.kind)).all() if tids else []
    db.commit()
    return {
        "period": period,
        "flat": {"id": f.id, "label": f.label, "floor_label": f.floor_label, "notes": f.notes,
                 "building": {"id": f.building.id, "name": f.building.name, "color": f.building.color,
                              "code": f.building.code}},
        "occupancy_in_period": state,
        "tenancy_in_period": describe_tenancy(tenancy, period) if tenancy else None,
        "occupancy_now": now_state,
        "current_tenancy": describe_tenancy(now_tenancy) if now_tenancy else None,
        "dues": described,
        "period_payments": period_payments,
        "arrears": arrears,
        "arrears_total_paise": sum(d["outstanding_paise"] for d in arrears),
        "credits": credits,
        "payment_history": history,
        # Future months only appear once something was paid towards them (or they are the viewed month).
        "due_history": [d for d in rent_svc.describe_dues(db, list(all_dues))
                        if d["period"] <= max(period, current_period()) or d["confirmed_paise"] or d["pending_paise"]][:120],
        "tenancies": [describe_tenancy(t, period) for t in tenancies],
        "owner_occupancies": [{"id": o.id, "start_date": o.start_date.isoformat(),
                               "end_date": o.end_date.isoformat() if o.end_date else None, "notes": o.notes}
                              for o in owner],
    }


class OwnerOccIn(BaseModel):
    start_date: dt.date
    end_date: dt.date | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.post("/flats/{flat_id}/owner-occupancy")
def owner_start(flat_id: int, body: OwnerOccIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    o = svc.add_owner_occupancy(db, flat_id, body.start_date, body.end_date, body.notes, actor.name)
    db.commit()
    return {"id": o.id}


class EndIn(BaseModel):
    end_date: dt.date
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/owner-occupancies/{occ_id}/end")
def owner_end(occ_id: int, body: EndIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    svc.end_owner_occupancy(db, occ_id, body.end_date, actor.name)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ tenants


class TenantIn(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=160)
    preferred_payment_method: str | None = Field(default=None, max_length=16)
    notes: str | None = Field(default=None, max_length=4000)


def describe_tenant(t: Tenant) -> dict:
    return {
        "id": t.id, "full_name": t.full_name, "phone": t.phone, "email": t.email,
        "preferred_payment_method": t.preferred_payment_method, "notes": t.notes,
        "tenancies": [describe_tenancy(x) for x in sorted(t.tenancies, key=lambda x: x.start_date, reverse=True)],
        "aliases": [{"id": a.id, "alias": a.alias, "kind": a.kind, "source": a.source, "is_active": a.is_active}
                    for a in t.aliases if a.is_active],
        "status": "active" if any(svc.tenancy_status(x) in ("active", "upcoming") for x in t.tenancies) else
        ("former" if t.tenancies else "no_tenancy"),
    }


@router.get("/tenants")
def list_tenants(db: Session = Depends(get_db)):
    return [describe_tenant(t) for t in db.scalars(select(Tenant).order_by(Tenant.full_name))]


@router.post("/tenants")
def new_tenant(body: TenantIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    t = svc.create_tenant(db, body.model_dump(exclude_unset=True), actor.name)
    db.commit()
    return describe_tenant(t)


@router.get("/tenants/{tenant_id}")
def get_tenant(tenant_id: int, db: Session = Depends(get_db)):
    return describe_tenant(svc.get_or_404(db, Tenant, tenant_id, "Tenant"))


@router.patch("/tenants/{tenant_id}")
def edit_tenant(tenant_id: int, body: TenantIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    t = svc.update_tenant(db, tenant_id, body.model_dump(exclude_unset=True), actor.name)
    db.commit()
    return describe_tenant(t)


class TenantDeleteIn(BaseModel):
    reason: str = Field(max_length=2000)


@router.post("/tenants/{tenant_id}/delete")
def remove_tenant(tenant_id: int, body: TenantDeleteIn, actor: Actor = Depends(require_user),
                  db: Session = Depends(get_db)):
    svc.delete_tenant(db, tenant_id, body.reason, actor.name)
    db.commit()
    return {"ok": True}


class AliasIn(BaseModel):
    alias: str = Field(max_length=160)
    kind: str = Field(default="sender_name", max_length=16)


@router.post("/tenants/{tenant_id}/aliases")
def new_alias(tenant_id: int, body: AliasIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    a = svc.add_alias(db, tenant_id, body.alias, body.kind, actor.name)
    db.commit()
    return {"id": a.id, "alias": a.alias, "kind": a.kind}


@router.delete("/aliases/{alias_id}")
def remove_alias(alias_id: int, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    svc.deactivate_alias(db, alias_id, actor.name)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------- tenancies


class TenancyIn(BaseModel):
    tenant_id: int
    flat_id: int
    start_date: dt.date
    monthly_rent_paise: int = Field(gt=0, le=10**10)
    rent_due_day: int = Field(default=1, ge=1, le=31)
    billing_start_period: str | None = None
    billing_policy: str | None = None
    notes: str | None = Field(default=None, max_length=4000)


@router.post("/tenancies")
def new_tenancy(body: TenancyIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    t = svc.start_tenancy(db, actor=actor.name, **body.model_dump())
    db.commit()
    return describe_tenancy(t)


@router.get("/tenancies/{tenancy_id}")
def get_tenancy(tenancy_id: int, db: Session = Depends(get_db)):
    t = svc.get_or_404(db, Tenancy, tenancy_id, "Tenancy")
    rent_svc.ensure_dues(db, current_period(), [t.id])
    dues = db.scalars(select(RentDue).where(RentDue.tenancy_id == t.id).order_by(RentDue.period, RentDue.kind)).all()
    db.commit()
    return {**describe_tenancy(t), "dues": rent_svc.describe_dues(db, list(dues))}


class TenancyEditIn(BaseModel):
    rent_due_day: int | None = Field(default=None, ge=1, le=31)
    billing_policy: str | None = None
    notes: str | None = Field(default=None, max_length=4000)


@router.patch("/tenancies/{tenancy_id}")
def edit_tenancy(tenancy_id: int, body: TenancyEditIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    t = svc.update_tenancy(db, tenancy_id, body.model_dump(exclude_unset=True), actor.name)
    db.commit()
    return describe_tenancy(t)


@router.post("/tenancies/{tenancy_id}/end")
def finish_tenancy(tenancy_id: int, body: EndIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    t = svc.end_tenancy(db, tenancy_id, body.end_date, body.reason, actor.name)
    db.commit()
    return describe_tenancy(t)


class RentChangeIn(BaseModel):
    amount_paise: int = Field(gt=0, le=10**10)
    effective_period: str
    note: str | None = Field(default=None, max_length=2000)


@router.post("/tenancies/{tenancy_id}/rent")
def rent_change(tenancy_id: int, body: RentChangeIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    svc.change_rent(db, tenancy_id, body.amount_paise, body.effective_period, body.note, actor.name)
    db.commit()
    return describe_tenancy(db.get(Tenancy, tenancy_id))


class ManualDueIn(BaseModel):
    period: str
    amount_paise: int = Field(gt=0, le=10**10)
    reason: str = Field(max_length=2000)


@router.post("/tenancies/{tenancy_id}/manual-due")
def manual_due(tenancy_id: int, body: ManualDueIn, actor: Actor = Depends(require_user), db: Session = Depends(get_db)):
    due = rent_svc.add_manual_due(db, tenancy_id, body.period, body.amount_paise, body.reason, actor.name)
    db.commit()
    return rent_svc.describe_dues(db, [due])[0]


@router.get("/tenancies/{tenancy_id}/due")
def tenancy_due(tenancy_id: int, period: str, db: Session = Depends(get_db)):
    """The rent due for one month (generated if billable), or null when nothing is due."""
    t = svc.get_or_404(db, Tenancy, tenancy_id, "Tenancy")
    due = rent_svc.get_due_for(db, t, validate_period(period))
    db.commit()
    return {"due": rent_svc.describe_dues(db, [due])[0] if due else None}


@router.get("/tenancies/{tenancy_id}/outstanding")
def tenancy_outstanding(tenancy_id: int, through: str | None = Query(default=None), db: Session = Depends(get_db)):
    t = svc.get_or_404(db, Tenancy, tenancy_id, "Tenancy")
    rent_svc.ensure_dues(db, validate_period(through or current_period()), [t.id])
    db.commit()
    return rent_svc.outstanding_dues(db, t.id)
