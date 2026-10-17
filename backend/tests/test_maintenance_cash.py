"""Maintenance, caretaker cash, salary and cashbook rules (spec section 19: maintenance tests 1-10)."""

from __future__ import annotations

import datetime as dt

import pytest

from app.errors import Conflict
from app.services import cash, maintenance as mnt, opening, rent
from app.services.settings import set_rate, set_setting

from .conftest import make_tenancy

D = dt.date
K = 1_000_00  # one thousand rupees in paise


def category_id(db, name="Repairs"):
    return next(c.id for c in mnt.list_categories(db) if c.name == name)


def handover(db, amount, on, frm="external"):
    m = cash.create_movement(db, kind="caretaker_handover", movement_date=on, amount_paise=amount,
                             from_account=frm, to_account="caretaker_cash", description="top-up", sources=None,
                             actor="tester")
    db.commit()
    return m


def expense(db, on, caretaker=0, family=0, method=None, allow=False):
    e = mnt.create_expense(db, expense_date=on, description="test work", category_id=category_id(db),
                           caretaker_paid_paise=caretaker, family_paid_paise=family, family_payment_method=method,
                           flat_id=None, vendor=None, allow_shortfall=allow, actor="tester")
    db.commit()
    return e


def caretaker_opening(db, amount, on=D(2025, 10, 31)):
    opening.create(db, kind="caretaker_cash", as_of_date=on, amount_paise=amount, description="cash held at start",
                   notes=None, tenancy_id=None, actor="tester")
    db.commit()


# 1
def test_zero_opening_requires_full_top_up(db):
    s = mnt.month_summary(db, "2025-11")
    assert s["opening_paise"] == 0 and s["target_paise"] == 15 * K
    assert s["top_up_required_paise"] == 15 * K


# 2 & 6: the specification's worked example
def test_unused_cash_carries_forward_and_reduces_top_up(db):
    handover(db, 15 * K, D(2025, 10, 1))
    expense(db, D(2025, 10, 15), caretaker=12 * K)
    oct_ = mnt.month_summary(db, "2025-10")
    assert oct_["closing_paise"] == 3 * K
    nov = mnt.month_summary(db, "2025-11")
    assert nov["opening_paise"] == 3 * K
    assert nov["top_up_required_paise"] == 12 * K
    handover(db, 12 * K, D(2025, 11, 2))
    nov = mnt.month_summary(db, "2025-11")
    assert nov["available_paise"] == 15 * K and nov["still_to_hand_over_paise"] == 0


# 3
def test_opening_equal_to_target_requires_no_top_up(db):
    caretaker_opening(db, 15 * K)
    assert mnt.month_summary(db, "2025-11")["top_up_required_paise"] == 0


# 4
def test_opening_above_target_requires_no_top_up_and_stays_visible(db):
    caretaker_opening(db, 18 * K)
    s = mnt.month_summary(db, "2025-11")
    assert s["top_up_required_paise"] == 0
    assert s["opening_paise"] == 18 * K and s["available_paise"] == 18 * K


# 5
def test_expenses_reduce_caretaker_cash(db):
    handover(db, 15 * K, D(2025, 11, 1))
    expense(db, D(2025, 11, 5), caretaker=4 * K)
    assert cash.balance(db, "caretaker_cash") == 11 * K
    assert mnt.month_summary(db, "2025-11")["closing_paise"] == 11 * K


# 7
def test_expense_beyond_available_cash_needs_explicit_workflow(db):
    handover(db, 15 * K, D(2025, 11, 1))
    with pytest.raises(Conflict) as e:
        expense(db, D(2025, 11, 10), caretaker=20 * K)
    assert e.value.code == "exceeds_caretaker_cash"
    assert e.value.details == {"available_paise": 15 * K, "shortfall_paise": 5 * K}
    db.rollback()
    # Explicit split: 15k from caretaker cash, 5k paid directly by family.
    expense(db, D(2025, 11, 10), caretaker=15 * K, family=5 * K, method="bank")
    s = mnt.month_summary(db, "2025-11")
    assert s["closing_paise"] == 0 and s["family_paid_paise"] == 5 * K and s["total_expenses_paise"] == 20 * K
    assert s["over_target_paise"] == 5 * K and s["discrepancies"] == []


def test_forced_shortfall_shows_negative_discrepancy_not_zero(db):
    handover(db, 1 * K, D(2025, 11, 1))
    expense(db, D(2025, 11, 3), caretaker=3 * K, allow=True)
    s = mnt.month_summary(db, "2025-11")
    assert s["closing_paise"] == -2 * K
    assert s["discrepancies"][0]["type"] == "negative_balance"


# 8
def test_family_funded_part_not_double_counted(db):
    handover(db, 15 * K, D(2025, 11, 1))
    expense(db, D(2025, 11, 5), caretaker=2 * K, family=3 * K, method="cash")
    assert cash.balance(db, "caretaker_cash") == 13 * K  # only the caretaker's part
    s = mnt.month_summary(db, "2025-11")
    assert (s["caretaker_spent_paise"], s["family_paid_paise"], s["total_expenses_paise"]) == (2 * K, 3 * K, 5 * K)
    assert cash.balance(db, "family_cash") == -3 * K  # family paid cash it had not recorded receiving


# 9
def test_salary_does_not_touch_maintenance(db):
    handover(db, 15 * K, D(2025, 11, 1))
    mnt.record_salary(db, salary_month_="2025-11", amount_paise=15 * K, payment_date=D(2025, 11, 1), method="bank",
                      bank_reference="NEFT1", status="confirmed", notes=None, actor="tester")
    db.commit()
    assert cash.balance(db, "caretaker_cash") == 15 * K
    assert mnt.month_summary(db, "2025-11")["total_expenses_paise"] == 0
    sal = mnt.salary_month(db, "2025-11")
    assert sal["status"] == "paid" and sal["outstanding_paise"] == 0


def test_salary_pending_and_cancel(db):
    p = mnt.record_salary(db, salary_month_="2025-10", amount_paise=15 * K, payment_date=D(2025, 10, 31),
                          method="bank", bank_reference=None, status="pending", notes=None, actor="tester")
    db.commit()
    assert mnt.salary_month(db, "2025-10")["status"] == "pending_verification"
    mnt.cancel_salary(db, p.id, "bank transfer failed", "tester")
    db.commit()
    assert mnt.salary_month(db, "2025-10")["status"] == "unpaid"


# 10
def test_cash_from_rent_receipt_moved_to_caretaker_is_not_a_second_receipt(db, client):
    ten = make_tenancy(db)
    p = rent.record_payment(db, rent.PaymentInput(tenancy_id=ten.id, received_date=D(2025, 11, 3),
                                                  amount_paise=10 * K, method="cash", rent_period="2025-11"),
                            "tester")
    db.commit()
    assert cash.balance(db, "family_cash") == 10 * K
    cash.create_movement(db, kind="caretaker_handover", movement_date=D(2025, 11, 4), amount_paise=10 * K,
                         from_account="family_cash", to_account="caretaker_cash", description="from rent",
                         sources=[(p.id, 10 * K)], actor="tester")
    db.commit()
    led = client.get("/api/rent/ledger?period=2025-11").json()
    assert led["summary"]["confirmed_paise"] == 10 * K  # still exactly one receipt
    book = cash.cashbook(db, "2025-11")
    assert book["cash_rent_received_paise"] == 10 * K
    assert book["handed_to_caretaker_paise"] == 10 * K and book["closing_paise"] == 0
    assert cash.balance(db, "caretaker_cash") == 10 * K
    receipt = next(r for r in book["receipts"] if r["payment_id"] == p.id)
    assert receipt["allocated_paise"] == 10 * K and receipt["with_family_paise"] == 0
    # The receipt cannot be linked again beyond its amount ...
    with pytest.raises(Conflict):
        cash.create_movement(db, kind="bank_deposit", movement_date=D(2025, 11, 5), amount_paise=1 * K,
                             from_account="family_cash", to_account="external", description=None,
                             sources=[(p.id, 1 * K)], actor="tester")
    db.rollback()
    # ... and cannot be reversed while its cash is linked to a movement.
    with pytest.raises(Conflict) as e:
        rent.void_payment(db, p.id, "mistake", "tester")
    assert e.value.code == "cash_in_use"


def test_cash_count_mismatch_is_reported(db):
    handover(db, 15 * K, D(2025, 11, 1))
    cash.record_count(db, "caretaker_cash", D(2025, 11, 10), 14 * K, "counted with caretaker", "tester")
    db.commit()
    s = mnt.month_summary(db, "2025-11")
    d = [x for x in s["discrepancies"] if x["type"] == "count_mismatch"][0]
    assert d["amount_paise"] == -1 * K


def test_target_change_applies_from_its_month_only(db):
    set_rate(db, "maintenance_target", 20 * K, "2025-11", "tester", "higher costs")
    db.commit()
    assert mnt.month_summary(db, "2025-10")["target_paise"] == 15 * K
    assert mnt.month_summary(db, "2025-11")["target_paise"] == 20 * K


def test_reversed_expense_restores_balance(db):
    handover(db, 15 * K, D(2025, 11, 1))
    e = expense(db, D(2025, 11, 5), caretaker=5 * K)
    mnt.reverse_expense(db, e.id, "duplicate entry", "tester")
    db.commit()
    assert cash.balance(db, "caretaker_cash") == 15 * K


def test_tenant_arrears_opening_balance_is_payable_and_labelled(db):
    ten = make_tenancy(db)
    ob = opening.create(db, kind="tenant_arrears", as_of_date=D(2025, 7, 31), amount_paise=5 * K,
                        description="unpaid before using the app", notes=None, tenancy_id=ten.id, actor="tester")
    db.commit()
    p = rent.record_payment(db, rent.PaymentInput(tenancy_id=ten.id, received_date=D(2025, 9, 1),
                                                  amount_paise=5 * K, method="cash",
                                                  allocations=[(ob.rent_due_id, 5 * K)]), "tester")
    db.commit()
    alloc = rent.describe_payment(p)["allocations"][0]
    assert alloc["kind"] == "opening_balance"
    assert opening.list_all(db)[0]["kind_label"] == "Tenant arrears"



def give(db, on, amount, for_period):
    m = cash.create_movement(db, kind="caretaker_handover", movement_date=on, amount_paise=amount,
                             from_account="external", to_account="caretaker_cash", description=None, sources=None,
                             actor="tester", period=for_period)
    db.commit()
    return m


def setting(db, key, value):
    set_setting(db, key, value, "tester")
    db.commit()


def test_handover_counts_for_the_month_it_is_for(db):
    m = give(db, D(2025, 10, 20), 15 * K, "2025-11")  # November's top-up, handed over in October
    assert cash.describe_movement(m)["period"] == "2025-11"
    nov = mnt.month_summary(db, "2025-11")
    assert (nov["handed_over_paise"], nov["still_to_hand_over_paise"], nov["closing_paise"]) == (15 * K, 0, 15 * K)
    assert mnt.month_summary(db, "2025-10")["handed_over_paise"] == 0


def test_in_advance_this_months_handover_is_next_months_top_up(db):
    oct_ = mnt.month_summary(db, "2025-10")
    assert oct_["handover_period"] == "2025-11" and oct_["handover_still_to_give_paise"] == 15 * K
    give(db, D(2025, 10, 20), 15 * K, "2025-11")
    assert mnt.month_summary(db, "2025-10")["handover_still_to_give_paise"] == 0


def test_previous_month_setting_flags_last_months_top_up(db):
    setting(db, "maintenance_given_for", "previous_month")
    nov = mnt.month_summary(db, "2025-11")
    assert nov["handover_period"] == "2025-10" and nov["handover_still_to_give_paise"] == 15 * K


def test_nothing_owed_before_records_start(db):
    setting(db, "tracking_start_period", "2025-10")
    # October's top-up was handed over in September, before records start.
    oct_ = mnt.month_summary(db, "2025-10")
    assert oct_["given_before_tracking"] and oct_["still_to_hand_over_paise"] == 0
    assert mnt.month_summary(db, "2025-11")["still_to_hand_over_paise"] == 15 * K
    assert mnt.salary_month(db, "2025-10")["status"] == "not_tracked"
    assert mnt.salary_month(db, "2025-10")["outstanding_paise"] == 0
    assert mnt.salary_month(db, "2025-11")["status"] == "unpaid"  # paid in October


def test_rent_before_records_start_is_not_charged(db):
    setting(db, "tracking_start_period", "2025-10")
    ten = make_tenancy(db, billing_start="2025-08")
    rent.ensure_dues(db, "2025-11", [ten.id])
    from sqlalchemy import select

    from app.models import RentDue

    assert sorted(db.scalars(select(RentDue.period).where(RentDue.tenancy_id == ten.id))) == ["2025-10", "2025-11"]


def test_salary_is_paid_a_month_ahead(db):
    s = mnt.salary_month(db, "2025-12")
    assert s["paid_in_period"] == "2025-11" and mnt.salary_month(db, "2025-11")["pay_now_period"] == "2025-12"


def test_handover_for_month_must_be_near_the_date(db):
    from app.errors import DomainError

    with pytest.raises(DomainError) as e:
        give(db, D(2025, 10, 20), K, "2024-01")
    assert e.value.code == "invalid_period"


def test_dashboard_shows_what_is_handed_over_this_month(client, db):
    d = client.get("/api/dashboard?period=2025-11").json()
    assert d["maintenance_top_up"]["period"] == "2025-12"
    assert d["salary"]["period"] == "2025-12"
