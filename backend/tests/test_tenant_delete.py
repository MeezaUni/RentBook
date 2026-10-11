"""Deleting a tenant: allowed for mistakes, refused once money history exists."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.errors import Conflict, DomainError
from app.models import AuditEvent, RentDue, RentTerm, Tenancy, Tenant, TenantAlias
from app.services import property as prop
from app.services import rent

from .conftest import flat, make_tenancy
from .test_rent import D, pay


def count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_delete_tenant_without_tenancy(db):
    t = prop.create_tenant(db, {"full_name": "Typo Tenant"}, "tester")
    prop.add_alias(db, t.id, "TYPO TENANT", "sender_name", "tester")
    db.commit()
    prop.delete_tenant(db, t.id, "entered twice", "tester")
    db.commit()
    assert db.get(Tenant, t.id) is None
    assert count(db, TenantAlias) == 0
    ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "tenant.delete"))
    assert ev.entity_id == t.id and ev.reason == "entered twice"
    assert json.loads(ev.before_json)["full_name"] == "Typo Tenant"


def test_delete_tenant_with_unpaid_tenancy_frees_the_flat(db):
    ten = make_tenancy(db, label="P-G1")
    rent.ensure_dues(db, "2025-10", [ten.id])
    db.commit()
    assert count(db, RentDue) > 0
    prop.delete_tenant(db, ten.tenant_id, "wrong flat", "tester")
    db.commit()
    assert (count(db, Tenancy), count(db, RentDue), count(db, RentTerm)) == (0, 0, 0)
    assert prop.occupancy_on(db, flat(db, "P-G1").id, D(2025, 11, 1))[0] == "vacant"
    make_tenancy(db, name="Right Tenant", label="P-G1")  # flat is usable again


@pytest.mark.parametrize("status", ["confirmed", "pending"])
def test_delete_refused_once_payments_exist(db, status):
    ten = make_tenancy(db)
    pay(db, ten, 10_000_00, "2025-09", status=status)
    with pytest.raises(Conflict) as e:
        prop.delete_tenant(db, ten.tenant_id, "mistake", "tester")
    assert e.value.code == "tenant_has_money_records"
    db.rollback()
    assert db.get(Tenant, ten.tenant_id) is not None


def test_delete_needs_a_reason(db):
    t = prop.create_tenant(db, {"full_name": "Typo Tenant"}, "tester")
    with pytest.raises(DomainError) as e:
        prop.delete_tenant(db, t.id, "  ", "tester")
    assert e.value.code == "reason_required"


def test_delete_endpoint(client, db):
    t = prop.create_tenant(db, {"full_name": "Typo Tenant"}, "tester")
    db.commit()
    assert client.post(f"/api/tenants/{t.id}/delete", json={"reason": "duplicate"}).json() == {"ok": True}
    assert client.get(f"/api/tenants/{t.id}").status_code == 404
    db.expire_all()  # the API deleted it in another session

    ten = make_tenancy(db, name="Paying Tenant")
    pay(db, ten, 10_000_00, "2025-09")
    r = client.post(f"/api/tenants/{ten.tenant_id}/delete", json={"reason": "duplicate"})
    assert r.status_code == 409
    assert "End their tenancy" in r.text
