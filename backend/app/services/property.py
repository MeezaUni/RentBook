"""Buildings, flats, tenants, tenancies, rent terms, owner occupancy and aliases."""

from __future__ import annotations

import datetime as dt
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import Conflict, DomainError, NotFound
from ..models import (
    Building,
    Document,
    DocumentLink,
    Flat,
    OpeningBalance,
    OwnerOccupancy,
    RentAllocation,
    RentDue,
    RentPayment,
    RentTerm,
    Tenancy,
    Tenant,
    TenantAlias,
    utcnow,
)
from ..periods import add_months, period_of, today, validate_period
from .settings import BILLING_POLICIES

PAYMENT_METHODS = {"cash", "online"}

# ------------------------------------------------------------------ helpers


def get_or_404(db: Session, model, obj_id: int, what: str):
    obj = db.get(model, obj_id)
    if obj is None:
        raise NotFound(what)
    return obj


def _overlaps(a_start: dt.date, a_end: dt.date | None, b_start: dt.date, b_end: dt.date | None) -> bool:
    far = dt.date.max
    return a_start <= (b_end or far) and b_start <= (a_end or far)


def normalize_alias(text: str) -> str:
    """Uppercase, alphanumerics + single spaces. Used for matching narrations."""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9@.]+", " ", (text or "").upper())).strip()


# --------------------------------------------------------------- buildings


def update_building(db: Session, building_id: int, name: str | None, color: str | None, actor: str) -> Building:
    b = get_or_404(db, Building, building_id, "Building")
    before = audit.snapshot(b)
    if name is not None:
        name = name.strip()
        if not 1 <= len(name) <= 80:
            raise DomainError("invalid_name", "Building name must be 1-80 characters")
        b.name = name
    if color is not None:
        if not re.match(r"^#[0-9a-fA-F]{6}$", color):
            raise DomainError("invalid_color", "Colour must be a hex value like #db2777")
        b.color = color
    audit.record(db, actor, "building.update", "building", b.id, before, audit.snapshot(b))
    return b


def update_flat(db: Session, flat_id: int, label: str | None, floor_label: str | None, notes: str | None, actor: str) -> Flat:
    f = get_or_404(db, Flat, flat_id, "Flat")
    before = audit.snapshot(f)
    if label is not None:
        label = label.strip()
        if not 1 <= len(label) <= 32:
            raise DomainError("invalid_label", "Flat identifier must be 1-32 characters")
        clash = db.scalar(select(Flat).where(Flat.building_id == f.building_id, Flat.label == label, Flat.id != f.id))
        if clash:
            raise Conflict("duplicate_label", f"Another flat in this building is already called '{label}'")
        f.label = label
    if floor_label is not None:
        floor_label = floor_label.strip()
        if not 1 <= len(floor_label) <= 40:
            raise DomainError("invalid_label", "Floor label must be 1-40 characters")
        # Floor labels are shared by all flats on the same floor.
        for other in db.scalars(select(Flat).where(Flat.building_id == f.building_id, Flat.floor_index == f.floor_index)):
            other.floor_label = floor_label
    if notes is not None:
        f.notes = notes.strip() or None
    audit.record(db, actor, "flat.update", "flat", f.id, before, audit.snapshot(f))
    return f


# --------------------------------------------------------------- occupancy


def tenancy_covers(t: Tenancy, d: dt.date) -> bool:
    return t.start_date <= d and (t.end_date is None or t.end_date >= d)


def occupancy_on(db: Session, flat_id: int, d: dt.date) -> tuple[str, Tenancy | None]:
    tenancy = db.scalar(
        select(Tenancy).where(
            Tenancy.flat_id == flat_id,
            Tenancy.start_date <= d,
            (Tenancy.end_date.is_(None)) | (Tenancy.end_date >= d),
        )
    )
    if tenancy:
        return "tenant_occupied", tenancy
    owner = db.scalar(
        select(OwnerOccupancy.id).where(
            OwnerOccupancy.flat_id == flat_id,
            OwnerOccupancy.start_date <= d,
            (OwnerOccupancy.end_date.is_(None)) | (OwnerOccupancy.end_date >= d),
        )
    )
    if owner:
        return "owner_occupied", None
    return "vacant", None


def _check_flat_free(db: Session, flat_id: int, start: dt.date, end: dt.date | None, ignore_tenancy: int | None = None,
                     ignore_owner: int | None = None) -> None:
    for t in db.scalars(select(Tenancy).where(Tenancy.flat_id == flat_id)):
        if t.id != ignore_tenancy and _overlaps(start, end, t.start_date, t.end_date):
            raise Conflict(
                "tenancy_overlap",
                f"This flat already has a tenancy ({t.tenant.full_name}, from {t.start_date}"
                + (f" to {t.end_date}" if t.end_date else ", still active")
                + "). End it first.",
            )
    for o in db.scalars(select(OwnerOccupancy).where(OwnerOccupancy.flat_id == flat_id)):
        if o.id != ignore_owner and _overlaps(start, end, o.start_date, o.end_date):
            raise Conflict("owner_overlap", f"The flat is marked owner-occupied from {o.start_date}. End that first.")


def add_owner_occupancy(db: Session, flat_id: int, start: dt.date, end: dt.date | None, notes: str | None, actor: str) -> OwnerOccupancy:
    get_or_404(db, Flat, flat_id, "Flat")
    if end and end < start:
        raise DomainError("invalid_dates", "End date is before start date")
    _check_flat_free(db, flat_id, start, end)
    o = OwnerOccupancy(flat_id=flat_id, start_date=start, end_date=end, notes=notes)
    db.add(o)
    db.flush()
    audit.record(db, actor, "occupancy.owner_start", "owner_occupancy", o.id, None, audit.snapshot(o))
    return o


def end_owner_occupancy(db: Session, occ_id: int, end: dt.date, actor: str) -> OwnerOccupancy:
    o = get_or_404(db, OwnerOccupancy, occ_id, "Owner occupancy")
    if end < o.start_date:
        raise DomainError("invalid_dates", "End date is before start date")
    before = audit.snapshot(o)
    o.end_date = end
    audit.record(db, actor, "occupancy.owner_end", "owner_occupancy", o.id, before, audit.snapshot(o))
    return o


# ------------------------------------------------------------------ tenants

TENANT_FIELDS = ["full_name", "phone", "email", "preferred_payment_method", "notes"]


def _clean_tenant(data: dict) -> dict:
    out = {}
    for k in TENANT_FIELDS:
        if k in data:
            v = data[k]
            out[k] = v.strip() if isinstance(v, str) else v
            if out[k] == "":
                out[k] = None
    if "full_name" in out and not out["full_name"]:
        raise DomainError("invalid_name", "Tenant name is required")
    if out.get("preferred_payment_method") and out["preferred_payment_method"] not in PAYMENT_METHODS:
        raise DomainError("invalid_method", "Payment method must be cash or online")
    if out.get("email") and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", out["email"]):
        raise DomainError("invalid_email", "Email address looks invalid")
    return out


def create_tenant(db: Session, data: dict, actor: str) -> Tenant:
    clean = _clean_tenant(data)
    if not clean.get("full_name"):
        raise DomainError("invalid_name", "Tenant name is required")
    t = Tenant(**clean)
    db.add(t)
    db.flush()
    audit.record(db, actor, "tenant.create", "tenant", t.id, None, audit.snapshot(t))
    return t


def update_tenant(db: Session, tenant_id: int, data: dict, actor: str) -> Tenant:
    t = get_or_404(db, Tenant, tenant_id, "Tenant")
    before = audit.snapshot(t, TENANT_FIELDS)
    for k, v in _clean_tenant(data).items():
        setattr(t, k, v)
    audit.record(db, actor, "tenant.update", "tenant", t.id, before, audit.snapshot(t, TENANT_FIELDS))
    return t


def delete_tenant(db: Session, tenant_id: int, reason: str, actor: str) -> None:
    """Remove a tenant entered by mistake, with their tenancies, dues and sender names.

    Refused once any money has been recorded against them: deleting would rewrite rent
    history. Such tenants are moved out (end the tenancy) instead and stay as "former".
    """
    t = get_or_404(db, Tenant, tenant_id, "Tenant")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise DomainError("reason_required", "Please give a reason for deleting this tenant")
    tenancy_ids = [x.id for x in t.tenancies]
    if tenancy_ids:
        payments = db.scalar(select(func.count()).select_from(RentPayment)
                             .where(RentPayment.tenancy_id.in_(tenancy_ids)))
        openings = db.scalar(select(func.count()).select_from(OpeningBalance)
                             .where(OpeningBalance.tenancy_id.in_(tenancy_ids)))
        if payments or openings:
            raise Conflict(
                "tenant_has_money_records",
                f"{t.full_name} has payments or opening balances recorded, so deleting them would change "
                "your rent history. End their tenancy from the flat page instead; they will stay listed "
                "as a former tenant.",
                {"payments": payments, "opening_balances": openings},
            )
    docs = db.scalar(select(func.count()).select_from(DocumentLink)
                     .join(Document, Document.id == DocumentLink.document_id)
                     .where(DocumentLink.entity_type == "tenant", DocumentLink.entity_id == t.id,
                            Document.deleted_at.is_(None)))
    if docs:
        raise Conflict("tenant_has_documents",
                       f"{t.full_name} has {docs} document(s) attached. Delete those first.", {"documents": docs})

    before = {
        **audit.snapshot(t, TENANT_FIELDS),
        "tenancies": [{**audit.snapshot(x), "rent_terms": [audit.snapshot(r) for r in x.rent_terms]}
                      for x in t.tenancies],
        "aliases": [audit.snapshot(a) for a in t.aliases],
    }
    if tenancy_ids:
        for model in (RentDue, RentTerm):
            for row in db.scalars(select(model).where(model.tenancy_id.in_(tenancy_ids))):
                db.delete(row)
        db.flush()
    for x in list(t.tenancies):
        db.delete(x)
    for a in list(t.aliases):
        db.delete(a)
    for link in db.scalars(select(DocumentLink).where(DocumentLink.entity_type == "tenant",
                                                      DocumentLink.entity_id == t.id)):
        db.delete(link)  # only links to already-deleted documents remain here
    db.flush()
    db.delete(t)
    db.flush()
    audit.record(db, actor, "tenant.delete", "tenant", tenant_id, before, None, reason)


# ---------------------------------------------------------------- tenancies


def rent_for_period(t: Tenancy, period: str) -> int | None:
    applicable = [rt for rt in t.rent_terms if rt.effective_period <= period]
    return applicable[-1].amount_paise if applicable else None


def start_tenancy(
    db: Session,
    *,
    tenant_id: int,
    flat_id: int,
    start_date: dt.date,
    monthly_rent_paise: int,
    rent_due_day: int,
    billing_start_period: str | None,
    billing_policy: str | None,
    notes: str | None,
    actor: str,
) -> Tenancy:
    get_or_404(db, Tenant, tenant_id, "Tenant")
    get_or_404(db, Flat, flat_id, "Flat")
    if not 1 <= rent_due_day <= 31:
        raise DomainError("invalid_due_day", "Rent due day must be between 1 and 31")
    if monthly_rent_paise <= 0:
        raise DomainError("invalid_amount", "Monthly rent must be greater than zero")
    if billing_policy is not None and billing_policy not in BILLING_POLICIES:
        raise DomainError("invalid_policy", "Unknown billing policy")
    start_period = period_of(start_date)
    billing_start_period = billing_start_period or start_period
    validate_period(billing_start_period)
    if billing_start_period < start_period:
        raise DomainError("invalid_billing_start", "Rent tracking cannot start before the tenancy starts")
    _check_flat_free(db, flat_id, start_date, None)
    t = Tenancy(
        tenant_id=tenant_id,
        flat_id=flat_id,
        start_date=start_date,
        rent_due_day=rent_due_day,
        billing_start_period=billing_start_period,
        billing_policy=billing_policy,
        notes=notes,
    )
    db.add(t)
    db.flush()
    # The opening rent term starts at the tenancy's first month so early dues are covered.
    db.add(RentTerm(tenancy_id=t.id, amount_paise=monthly_rent_paise, effective_period=start_period,
                    note="Initial rent", created_by=actor))
    db.flush()
    db.refresh(t)
    audit.record(db, actor, "tenancy.start", "tenancy", t.id, None,
                 {**audit.snapshot(t), "monthly_rent_paise": monthly_rent_paise})
    return t


def _due_allocated(db: Session, due_id: int) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(RentAllocation.amount_paise), 0))
        .join(RentPayment, RentPayment.id == RentAllocation.payment_id)
        .where(RentAllocation.rent_due_id == due_id, RentPayment.status.in_(("pending", "confirmed")))
    ) or 0


def end_tenancy(db: Session, tenancy_id: int, end_date: dt.date, reason: str | None, actor: str) -> Tenancy:
    """Record a move-out. Dues that are no longer billable are waived, never deleted."""
    from .rent import is_billable  # local import avoids a cycle

    t = get_or_404(db, Tenancy, tenancy_id, "Tenancy")
    if end_date < t.start_date:
        raise DomainError("invalid_dates", "Move-out date is before the move-in date")
    before = audit.snapshot(t)
    t.end_date = end_date
    waived = []
    for due in db.scalars(select(RentDue).where(RentDue.tenancy_id == t.id, RentDue.kind == "rent",
                                                RentDue.waived_at.is_(None))):
        if is_billable(db, t, due.period)[0]:
            continue
        if _due_allocated(db, due.id) > 0:
            raise Conflict(
                "due_has_payments",
                f"Rent for {due.period} already has payments recorded against it. Move those payments to "
                "another month before setting this move-out date.",
            )
        due.waived_at = utcnow()
        due.waived_by = actor
        due.waived_reason = f"Tenancy ended on {end_date}"
        waived.append(due.period)
    audit.record(db, actor, "tenancy.end", "tenancy", t.id, before,
                 {**audit.snapshot(t), "waived_periods": waived}, reason=reason)
    return t


def update_tenancy(db: Session, tenancy_id: int, data: dict, actor: str) -> Tenancy:
    """Edit due day / notes / billing policy.

    A new due day also moves the due dates of months not yet fully paid; the billing policy
    applies to months generated from now on.
    """
    from .rent import refresh_due_dates  # local import avoids a cycle

    t = get_or_404(db, Tenancy, tenancy_id, "Tenancy")
    before = audit.snapshot(t)
    redated: list[str] = []
    if "rent_due_day" in data and data["rent_due_day"] is not None:
        if not 1 <= int(data["rent_due_day"]) <= 31:
            raise DomainError("invalid_due_day", "Rent due day must be between 1 and 31")
        if int(data["rent_due_day"]) != t.rent_due_day:
            t.rent_due_day = int(data["rent_due_day"])
            redated = refresh_due_dates(db, t)
    if "billing_policy" in data:
        if data["billing_policy"] is not None and data["billing_policy"] not in BILLING_POLICIES:
            raise DomainError("invalid_policy", "Unknown billing policy")
        t.billing_policy = data["billing_policy"]
    if "notes" in data:
        t.notes = (data["notes"] or "").strip() or None
    after = audit.snapshot(t)
    if redated:
        after["redated_periods"] = redated
    audit.record(db, actor, "tenancy.update", "tenancy", t.id, before, after)
    return t


def change_rent(db: Session, tenancy_id: int, amount_paise: int, effective_period: str, note: str | None, actor: str) -> RentTerm:
    """Set the rent from `effective_period` onwards.

    Dues for months before `effective_period` are never touched. Dues already
    generated for later months (e.g. viewed in advance) are re-priced, unless their
    amount was manually overridden, until the next rent term starts.
    """
    t = get_or_404(db, Tenancy, tenancy_id, "Tenancy")
    validate_period(effective_period)
    if amount_paise <= 0:
        raise DomainError("invalid_amount", "Monthly rent must be greater than zero")
    if effective_period < period_of(t.start_date):
        raise DomainError("invalid_period", "Rent change cannot take effect before the tenancy started")
    term = db.scalar(select(RentTerm).where(RentTerm.tenancy_id == t.id, RentTerm.effective_period == effective_period))
    before = audit.snapshot(term) if term else {"amount_paise": rent_for_period(t, effective_period)}
    next_term = db.scalar(
        select(RentTerm.effective_period)
        .where(RentTerm.tenancy_id == t.id, RentTerm.effective_period > effective_period)
        .order_by(RentTerm.effective_period)
        .limit(1)
    )
    dues = db.scalars(
        select(RentDue).where(
            RentDue.tenancy_id == t.id,
            RentDue.kind == "rent",
            RentDue.period >= effective_period,
            RentDue.amount_overridden.is_(False),
            *( [RentDue.period < next_term] if next_term else [] ),
        )
    ).all()
    for due in dues:
        if amount_paise < _due_allocated(db, due.id):
            raise Conflict(
                "rent_below_payments",
                f"Payments already recorded for {due.period} exceed the new rent. Adjust those payments first.",
            )
    if term:
        term.amount_paise = amount_paise
        term.note = note
    else:
        term = RentTerm(tenancy_id=t.id, amount_paise=amount_paise, effective_period=effective_period, note=note, created_by=actor)
        db.add(term)
    repriced = []
    for due in dues:
        if due.amount_paise != amount_paise:
            repriced.append({"period": due.period, "from": due.amount_paise, "to": amount_paise})
            due.amount_paise = amount_paise
    db.flush()
    db.refresh(t)
    audit.record(db, actor, "rent.change", "tenancy", t.id, before,
                 {"amount_paise": amount_paise, "effective_period": effective_period, "repriced_dues": repriced},
                 reason=note)
    return term


def tenancy_status(t: Tenancy, on: dt.date | None = None) -> str:
    on = on or today()
    if t.start_date > on:
        return "upcoming"
    if t.end_date is not None and t.end_date < on:
        return "ended"
    return "active"


def next_period_after(period: str) -> str:
    return add_months(period, 1)


# ------------------------------------------------------------------ aliases


def add_alias(db: Session, tenant_id: int, alias: str, kind: str, actor: str, source: str = "manual") -> TenantAlias:
    get_or_404(db, Tenant, tenant_id, "Tenant")
    if kind not in {"sender_name", "reference", "upi_id", "account"}:
        raise DomainError("invalid_kind", "Unknown alias type")
    norm = normalize_alias(alias)
    if len(norm) < 3:
        raise DomainError("alias_too_short", "Alias must contain at least 3 letters or digits")
    existing = db.scalar(select(TenantAlias).where(TenantAlias.tenant_id == tenant_id,
                                                   TenantAlias.alias_normalized == norm, TenantAlias.kind == kind))
    if existing:
        if not existing.is_active:
            existing.is_active = True
            audit.record(db, actor, "alias.reactivate", "tenant_alias", existing.id, None, audit.snapshot(existing))
        return existing
    a = TenantAlias(tenant_id=tenant_id, alias=alias.strip()[:160], alias_normalized=norm, kind=kind,
                    source=source, created_by=actor)
    db.add(a)
    db.flush()
    audit.record(db, actor, "alias.create", "tenant_alias", a.id, None, audit.snapshot(a))
    return a


def deactivate_alias(db: Session, alias_id: int, actor: str) -> TenantAlias:
    a = get_or_404(db, TenantAlias, alias_id, "Alias")
    before = audit.snapshot(a)
    a.is_active = False
    audit.record(db, actor, "alias.deactivate", "tenant_alias", a.id, before, audit.snapshot(a))
    return a
