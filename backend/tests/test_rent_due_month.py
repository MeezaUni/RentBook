"""When a month's rent falls due: in the following month (default) or the same month."""

from __future__ import annotations

import pytest

from app.errors import DomainError
from app.services import rent, settings

from .conftest import make_tenancy
from .test_rent import D, due, pay


def use_same_month(db):
    settings.set_setting(db, "rent_due_month", "same_month", "tester")
    db.commit()


def test_september_rent_is_due_on_the_due_day_in_october(db):
    ten = make_tenancy(db, due_day=10)
    d = due(db, ten, "2025-09")
    assert d["due_date"] == "2025-10-10"


def test_not_overdue_until_the_next_months_due_day(db):
    ten = make_tenancy(db, due_day=10)
    due(db, ten, "2025-09")
    row = rent.get_due_for(db, ten, "2025-09")
    assert rent.describe_dues(db, [row], on=D(2025, 9, 30))[0]["status"] == "unpaid"
    assert rent.describe_dues(db, [row], on=D(2025, 10, 10))[0]["is_overdue"] is False
    assert rent.describe_dues(db, [row], on=D(2025, 10, 11))[0]["status"] == "overdue"


def test_paying_september_rent_in_october_is_on_time(db):
    ten = make_tenancy(db, due_day=10)
    p = pay(db, ten, 10_000_00, "2025-09", on=D(2025, 10, 8))
    assert rent.describe_payment(p)["allocations"][0]["is_late"] is False


def test_december_rent_is_due_in_january_and_day_is_clamped(db):
    ten = make_tenancy(db, due_day=31, billing_start="2025-11")
    assert due(db, ten, "2025-12")["due_date"] == "2026-01-31"
    assert due(db, ten, "2026-01")["due_date"] == "2026-02-28"


def test_tenant_leaving_end_of_september_still_owes_september(db):
    ten = make_tenancy(db, due_day=10, end=D(2025, 9, 30))
    d = due(db, ten, "2025-09")
    assert d["status"] != "waived" and d["due_date"] == "2025-10-10"


def test_manual_due_follows_the_setting(db):
    ten = make_tenancy(db, start=D(2025, 9, 20), due_day=10, billing_start="2025-09")
    m = rent.add_manual_due(db, ten.id, "2025-09", 5_000_00, "part month", "tester")
    assert m.due_date == D(2025, 10, 10)


def test_same_month_setting(db):
    use_same_month(db)
    ten = make_tenancy(db, due_day=10)
    assert due(db, ten, "2025-09")["due_date"] == "2025-09-10"


def test_setting_is_validated(db):
    with pytest.raises(DomainError):
        settings.set_setting(db, "rent_due_month", "whenever", "tester")


def test_dashboard_shows_rent_being_collected_this_month(client, db):
    ten = make_tenancy(db, due_day=10)
    pay(db, ten, 10_000_00, "2025-10", on=D(2025, 11, 5))
    data = client.get("/api/dashboard?period=2025-11").json()
    assert data["rent_period"] == "2025-10"
    assert data["rent"]["confirmed_paise"] == 10_000_00
    # November's rent is not due until December, so it is not listed as outstanding yet.
    assert all("2025-11" not in f["periods"] for f in data["outstanding_flats"])


def test_dashboard_same_month(client, db):
    use_same_month(db)
    make_tenancy(db, due_day=10)
    assert client.get("/api/dashboard?period=2025-11").json()["rent_period"] == "2025-11"
