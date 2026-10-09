"""Rent ledger rules (spec section 19: rent tests 1-12, plus edge cases)."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.errors import Conflict, DomainError
from app.models import RentAllocation, RentDue
from app.services import property as prop
from app.services import rent

from .conftest import flat, make_tenancy

D = dt.date


def due(db, ten, period, kind="rent") -> dict:
    rent.ensure_dues(db, period, [ten.id])
    row = db.scalar(select(RentDue).where(RentDue.tenancy_id == ten.id, RentDue.period == period, RentDue.kind == kind))
    assert row is not None, f"no due for {period}"
    return rent.describe_dues(db, [row])[0]


def pay(db, ten, amount, period, *, on=D(2025, 9, 5), method="cash", status="confirmed", **kw):
    p = rent.record_payment(db, rent.PaymentInput(tenancy_id=ten.id, received_date=on, amount_paise=amount,
                                                  method=method, status=status, rent_period=period, **kw), "tester")
    db.commit()
    return p


# 1
def test_full_payment_settles_due(db):
    ten = make_tenancy(db)
    pay(db, ten, 10_000_00, "2025-09")
    d = due(db, ten, "2025-09")
    assert (d["status"], d["outstanding_paise"], d["confirmed_paise"]) == ("paid", 0, 10_000_00)


# 2
def test_partial_payment_leaves_balance(db):
    ten = make_tenancy(db)
    pay(db, ten, 4_000_00, "2025-09")
    d = due(db, ten, "2025-09")
    assert d["status"] == "partially_paid"
    assert d["outstanding_paise"] == 6_000_00
    assert d["is_overdue"] is True  # today (20 Nov) is after 5 Sep


# 3
def test_multiple_payments_add_up(db):
    ten = make_tenancy(db)
    pay(db, ten, 4_000_00, "2025-09", on=D(2025, 9, 3))
    pay(db, ten, 3_500_00, "2025-09", on=D(2025, 9, 10))
    assert due(db, ten, "2025-09")["outstanding_paise"] == 2_500_00
    pay(db, ten, 2_500_00, "2025-09", on=D(2025, 9, 20))
    d = due(db, ten, "2025-09")
    assert d["status"] == "paid" and d["confirmed_paise"] == 10_000_00


# 4
def test_late_payment_credits_original_month(db):
    ten = make_tenancy(db)
    p = pay(db, ten, 10_000_00, "2025-10", on=D(2025, 11, 8))
    d = due(db, ten, "2025-10")
    assert d["status"] == "paid"
    assert d["due_date"] == "2025-10-05"  # original due date preserved
    desc = rent.describe_payment(p)
    assert desc["received_date"] == "2025-11-08"
    assert desc["allocations"][0]["period"] == "2025-10" and desc["allocations"][0]["is_late"] is True
    # November remains a separate, unpaid due
    assert due(db, ten, "2025-11")["confirmed_paise"] == 0


# 5
def test_previous_unpaid_month_is_arrears_not_new_rent(db, client):
    ten = make_tenancy(db)
    pay(db, ten, 10_000_00, "2025-08", on=D(2025, 8, 5))
    pay(db, ten, 10_000_00, "2025-09", on=D(2025, 9, 5))
    led = client.get("/api/rent/ledger?period=2025-11").json()
    nov = [d for d in led["dues"] if d["tenancy_id"] == ten.id]
    assert len(nov) == 1 and nov[0]["amount_paise"] == 10_000_00  # not inflated by October
    assert [a["period"] for a in led["arrears"]] == ["2025-10"]
    assert led["summary"]["arrears_paise"] == 10_000_00


# 6
def test_rent_change_does_not_alter_previous_months(db):
    ten = make_tenancy(db)
    for p in ("2025-08", "2025-09", "2025-10", "2025-11"):
        due(db, ten, p)  # generate in advance, including November
    prop.change_rent(db, ten.id, 12_000_00, "2025-11", "annual increase", "tester")
    db.commit()
    assert due(db, ten, "2025-09")["amount_paise"] == 10_000_00
    assert due(db, ten, "2025-10")["amount_paise"] == 10_000_00
    assert due(db, ten, "2025-11")["amount_paise"] == 12_000_00
    assert due(db, ten, "2025-12")["amount_paise"] == 12_000_00


def test_rent_change_cannot_drop_below_recorded_payments(db):
    ten = make_tenancy(db)
    pay(db, ten, 10_000_00, "2025-11", on=D(2025, 11, 5))
    with pytest.raises(Conflict):
        prop.change_rent(db, ten.id, 9_000_00, "2025-11", None, "tester")


# 7
def test_vacant_flat_generates_no_dues(db):
    ten = make_tenancy(db, end=D(2025, 9, 30))
    rent.ensure_dues(db, "2025-12")
    db.commit()
    periods = sorted(db.scalars(select(RentDue.period).where(RentDue.tenancy_id == ten.id)))
    assert periods == ["2025-08", "2025-09"]
    state, _ = prop.occupancy_on(db, ten.flat_id, D(2025, 11, 1))
    assert state == "vacant"
    # A flat that never had a tenant has no dues at all
    other = flat(db, "B-3")
    assert not db.scalars(select(RentDue).join(RentDue.tenancy).where(RentDue.tenancy.has(flat_id=other.id))).all()


def test_move_out_waives_unpaid_later_dues_and_blocks_paid_ones(db):
    ten = make_tenancy(db)
    due(db, ten, "2025-11")
    pay(db, ten, 10_000_00, "2025-11", on=D(2025, 11, 5))
    with pytest.raises(Conflict):
        prop.end_tenancy(db, ten.id, D(2025, 10, 31), "moving", "tester")
    db.rollback()
    ten2 = make_tenancy(db, name="Test Tenant B", label="P-G2")
    due(db, ten2, "2025-11")
    prop.end_tenancy(db, ten2.id, D(2025, 10, 31), "moving", "tester")
    db.commit()
    assert due(db, ten2, "2025-11")["status"] == "waived"


# 8
def test_owner_occupied_flat_generates_no_dues(db):
    f = flat(db, "P-G2")
    prop.add_owner_occupancy(db, f.id, D(2024, 1, 1), None, "family lives here", "tester")
    db.commit()
    rent.ensure_dues(db, "2025-12")
    assert prop.occupancy_on(db, f.id, D(2025, 11, 1))[0] == "owner_occupied"
    assert db.scalars(select(RentDue)).all() == []
    with pytest.raises(Conflict):
        make_tenancy(db, label="P-G2")


# 9
def test_pending_payment_not_counted_as_confirmed(db):
    ten = make_tenancy(db)
    pay(db, ten, 10_000_00, "2025-09", method="online", status="pending")
    d = due(db, ten, "2025-09")
    assert d["status"] == "pending_verification"
    assert d["confirmed_paise"] == 0 and d["pending_paise"] == 10_000_00
    assert d["outstanding_paise"] == 10_000_00


# 10
def test_reversed_payment_no_longer_counts(db):
    ten = make_tenancy(db)
    p = pay(db, ten, 10_000_00, "2025-09")
    rent.void_payment(db, p.id, "entered twice by mistake", "tester")
    db.commit()
    d = due(db, ten, "2025-09")
    assert d["confirmed_paise"] == 0 and d["outstanding_paise"] == 10_000_00
    assert rent.get_payment(db, p.id).status == "reversed"
    with pytest.raises(DomainError):
        rent.void_payment(db, p.id, "x", "tester")  # reason too short / already reversed


# 11
def test_duplicate_allocation_prevented(db):
    ten = make_tenancy(db)
    pay(db, ten, 10_000_00, "2025-09")
    sep = due(db, ten, "2025-09")
    # Due is fully covered: an explicit allocation to it is refused.
    with pytest.raises(Conflict) as e:
        rent.record_payment(db, rent.PaymentInput(tenancy_id=ten.id, received_date=D(2025, 9, 6),
                                                  amount_paise=1_000_00, method="online",
                                                  allocations=[(sep["id"], 1_000_00)]), "tester")
    assert e.value.code == "over_allocation"
    oct_ = due(db, ten, "2025-10")
    with pytest.raises(Conflict) as e:
        rent.record_payment(db, rent.PaymentInput(tenancy_id=ten.id, received_date=D(2025, 10, 6),
                                                  amount_paise=2_000_00, method="online",
                                                  allocations=[(oct_["id"], 1_000_00), (oct_["id"], 1_000_00)]),
                            "tester")
    assert e.value.code == "duplicate_allocation"
    # Same reference twice is flagged as a duplicate payment.
    pay(db, ten, 5_000_00, "2025-10", method="online", reference="UTR123", on=D(2025, 10, 6))
    with pytest.raises(Conflict) as e:
        pay(db, ten, 5_000_00, "2025-10", method="online", reference="utr123", on=D(2025, 10, 7))
    assert e.value.code == "possible_duplicate"


def test_database_rejects_same_payment_twice_on_one_due(db):
    from sqlalchemy.exc import IntegrityError

    ten = make_tenancy(db)
    p = pay(db, ten, 5_000_00, "2025-09")
    sep_id = due(db, ten, "2025-09")["id"]
    db.add(RentAllocation(payment_id=p.id, rent_due_id=sep_id, amount_paise=1))
    with pytest.raises(IntegrityError):
        db.flush()


# 12
def test_excess_payment_requires_explicit_handling(db):
    ten = make_tenancy(db)
    with pytest.raises(Conflict) as e:
        pay(db, ten, 15_000_00, "2025-09")
    assert e.value.code == "excess_payment" and e.value.details["excess_paise"] == 5_000_00
    db.rollback()
    p = pay(db, ten, 15_000_00, "2025-09", excess_handling="apply_forward")
    assert due(db, ten, "2025-09")["status"] == "paid"
    assert due(db, ten, "2025-10")["confirmed_paise"] == 5_000_00
    assert rent.describe_payment(p)["unallocated_paise"] == 0


def test_excess_held_as_credit_is_visible(db):
    ten = make_tenancy(db)
    p = pay(db, ten, 12_000_00, "2025-09", excess_handling="hold_as_credit", excess_note="advance")
    d = rent.describe_payment(p)
    assert d["unallocated_paise"] == 2_000_00
    assert d["excess_resolution"] == "advance_credit" and not d["needs_excess_decision"]
    # Later apply the credit to October by re-allocating.
    sep, oct_ = due(db, ten, "2025-09"), due(db, ten, "2025-10")
    rent.reallocate_payment(db, p.id, [(sep["id"], 10_000_00), (oct_["id"], 2_000_00)], "apply credit", "tester")
    db.commit()
    assert due(db, ten, "2025-10")["confirmed_paise"] == 2_000_00
    assert rent.describe_payment(rent.get_payment(db, p.id))["unallocated_paise"] == 0


def test_apply_excess_to_arrears_oldest_first(db):
    ten = make_tenancy(db)
    p = pay(db, ten, 25_000_00, "2025-10", on=D(2025, 10, 10), excess_handling="apply_to_arrears")
    periods = [a["period"] for a in rent.describe_payment(p)["allocations"]]
    assert periods == ["2025-08", "2025-09", "2025-10"]
    assert due(db, ten, "2025-10")["status"] == "paid"
    assert due(db, ten, "2025-08")["status"] == "paid"
    assert due(db, ten, "2025-09")["outstanding_paise"] == 5_000_00


def test_correction_links_to_original_and_recalculates(db):
    ten = make_tenancy(db)
    p = pay(db, ten, 10_000_00, "2025-09")
    new = rent.correct_payment(db, p.id, "wrong month", rent.PaymentInput(
        tenancy_id=ten.id, received_date=D(2025, 9, 5), amount_paise=10_000_00, method="cash", rent_period="2025-10"),
        "tester")
    db.commit()
    assert new.corrects_payment_id == p.id
    assert due(db, ten, "2025-09")["outstanding_paise"] == 10_000_00
    assert due(db, ten, "2025-10")["status"] == "paid"


def test_billing_start_prevents_fictional_old_arrears(db):
    ten = make_tenancy(db, start=D(2015, 3, 1), billing_start="2025-10")
    rent.ensure_dues(db, "2025-11")
    assert sorted(db.scalars(select(RentDue.period).where(RentDue.tenancy_id == ten.id))) == ["2025-10", "2025-11"]


def test_due_day_31_clamps_to_month_end(db):
    ten = make_tenancy(db, due_day=31, billing_start="2025-09")
    assert due(db, ten, "2025-09")["due_date"] == "2025-09-30"


def test_policy_due_date_in_tenancy_for_mid_month_move_in(db):
    ten = make_tenancy(db, start=D(2025, 9, 15), due_day=1, billing_start="2025-09")
    rent.ensure_dues(db, "2025-10", [ten.id])
    assert sorted(db.scalars(select(RentDue.period).where(RentDue.tenancy_id == ten.id))) == ["2025-10"]


def test_prorated_policy_only_when_configured(db):
    ten = make_tenancy(db, start=D(2025, 9, 16), due_day=1, billing_start="2025-09", policy="prorated_daily",
                       rent=30_000_00)
    assert due(db, ten, "2025-09")["amount_paise"] == 15_000_00  # 15 of 30 days


def test_conflicting_active_tenancies_are_refused(db):
    make_tenancy(db, label="B-1")
    with pytest.raises(Conflict) as e:
        make_tenancy(db, name="Test Tenant B", label="B-1", start=D(2025, 6, 1))
    assert e.value.code == "tenancy_overlap"


def test_new_tenant_after_previous_tenancy_keeps_history(db):
    old = make_tenancy(db, label="B-2", end=D(2025, 9, 30))
    pay(db, old, 10_000_00, "2025-08")
    new = make_tenancy(db, name="Test Tenant B", label="B-2", start=D(2025, 10, 1), billing_start="2025-10",
                       rent=11_000_00)
    assert due(db, new, "2025-10")["amount_paise"] == 11_000_00
    assert due(db, old, "2025-08")["status"] == "paid"  # history intact


def test_waive_and_manual_due(db):
    ten = make_tenancy(db, policy="full_months_only", start=D(2025, 9, 10), billing_start="2025-09")
    rent.ensure_dues(db, "2025-09", [ten.id])
    assert db.scalar(select(RentDue).where(RentDue.tenancy_id == ten.id, RentDue.period == "2025-09")) is None
    md = rent.add_manual_due(db, ten.id, "2025-09", 7_000_00, "part month agreed", "tester")
    db.commit()
    assert due(db, ten, "2025-09")["amount_paise"] == 7_000_00
    rent.waive_due(db, md.id, "goodwill", "tester")
    db.commit()
    assert due(db, ten, "2025-09")["status"] == "waived"


def test_apply_forward_creates_only_months_it_covers(db):
    ten = make_tenancy(db)
    pay(db, ten, 25_000_00, "2025-11", on=D(2025, 11, 5), excess_handling="apply_forward")
    periods = sorted(db.scalars(select(RentDue.period).where(RentDue.tenancy_id == ten.id, RentDue.period > "2025-11")))
    assert periods == ["2025-12", "2026-01"]
    assert due(db, ten, "2026-01")["confirmed_paise"] == 5_000_00
