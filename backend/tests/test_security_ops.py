"""Authentication, authorization, CSRF, rate limiting, file access, error hygiene,
backups and migrations."""

from __future__ import annotations

import re
import zipfile

import pytest
from sqlalchemy import select

from app.main import create_app
from app.models import User
from app.security import create_user, hash_pin, validate_pin
from app.services import backup

from .conftest import TEST_PIN, make_tenancy

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_pin_is_hashed_with_argon2id_and_never_stored_plain(db):
    u = create_user(db, "Someone", "739104")
    db.commit()
    stored = db.scalar(select(User.pin_hash).where(User.id == u.id))
    assert stored.startswith("$argon2id$") and "739104" not in stored


@pytest.mark.parametrize("pin", ["1234", "111111", "123456", "abcdef", "12345678901234"])
def test_weak_or_invalid_pins_rejected(pin):
    from app.errors import DomainError

    with pytest.raises(DomainError):
        validate_pin(pin)


def test_every_api_route_requires_login_except_health_and_auth(anon_client):
    public = {"/api/health", "/api/auth/login", "/api/auth/logout"}
    paths = create_app(run_lifespan=False).openapi()["paths"]
    checked = 0
    for raw_path, ops in paths.items():
        if raw_path in public:
            continue
        path = re.sub(r"\{[^}]+\}", "1", raw_path)
        for method in ops:
            r = anon_client.request(method.upper(), path)
            assert r.status_code == 401, f"{method} {path} returned {r.status_code}"
            checked += 1
    assert checked > 50


def test_login_wrong_pin_and_rate_limit(anon_client, db):
    create_user(db, "Tester", TEST_PIN)
    db.commit()
    for _ in range(5):
        r = anon_client.post("/api/auth/login", json={"name": "Tester", "pin": "000001"})
        assert r.status_code == 401
        assert r.json()["error"]["message"] == "Name or PIN is incorrect."
    r = anon_client.post("/api/auth/login", json={"name": "Tester", "pin": TEST_PIN})
    assert r.status_code == 429  # locked out even with the correct PIN


def test_unknown_user_gets_same_error(anon_client):
    r = anon_client.post("/api/auth/login", json={"name": "Nobody", "pin": "739104"})
    assert r.status_code == 401 and r.json()["error"]["message"] == "Name or PIN is incorrect."


def test_session_cookie_flags_and_logout_revokes(anon_client, db):
    create_user(db, "Tester", TEST_PIN)
    db.commit()
    r = anon_client.post("/api/auth/login", json={"name": "Tester", "pin": TEST_PIN})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    token = anon_client.cookies.get("rb_session")
    assert anon_client.get("/api/auth/me").status_code == 200
    anon_client.post("/api/auth/logout")
    anon_client.cookies.set("rb_session", token)  # replaying the old cookie must not work
    assert anon_client.get("/api/auth/me").status_code == 401


def test_csrf_token_required_for_changes(client):
    good = client.headers["X-CSRF-Token"]
    del client.headers["X-CSRF-Token"]
    r = client.post("/api/tenants", json={"full_name": "Test Tenant C"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    client.headers["X-CSRF-Token"] = "forged"
    assert client.post("/api/tenants", json={"full_name": "Test Tenant C"}).status_code == 403
    client.headers["X-CSRF-Token"] = good
    assert client.post("/api/tenants", json={"full_name": "Test Tenant C"}).status_code == 200


def test_validation_errors_do_not_echo_input(client):
    r = client.post("/api/payments", json={"tenancy_id": 1, "received_date": "not-a-date-SECRET123",
                                           "amount_paise": -5, "method": "cheque"})
    assert r.status_code == 422
    assert "SECRET123" not in r.text and r.json()["error"]["code"] == "validation_error"


def test_unexpected_errors_are_generic(client, monkeypatch):
    from app.routers import dashboard

    def boom(*_a, **_k):
        raise RuntimeError("database password is hunter2")

    monkeypatch.setattr(dashboard, "ledger", boom)
    from fastapi.testclient import TestClient

    with TestClient(create_app(run_lifespan=False), raise_server_exceptions=False) as c:
        c.cookies = client.cookies
        r = c.get("/api/dashboard")
    assert r.status_code == 500
    assert "hunter2" not in r.text and r.json()["error"]["code"] == "server_error"


def test_document_upload_validation_and_private_access(client, db, anon_client):
    ten = make_tenancy(db)
    p = client.post("/api/payments", json={"tenancy_id": ten.id, "received_date": "2025-11-02",
                                           "amount_paise": 1_000_00, "method": "cash", "rent_period": "2025-11"}).json()
    # Wrong type (an HTML file renamed to .png) is refused.
    r = client.post("/api/documents", files={"file": ("x.png", b"<html><script>alert(1)</script>")},
                    data={"entity_type": "rent_payment", "entity_id": str(p["id"])})
    assert r.status_code == 415
    r = client.post("/api/documents", files={"file": ("../../etc/passwd proof.png", PNG)},
                    data={"entity_type": "rent_payment", "entity_id": str(p["id"]), "category": "payment_proof"})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert "/" not in doc["original_name"] and doc["is_image"]
    f = client.get(f"/api/documents/{doc['id']}/file")
    assert f.status_code == 200 and f.content == PNG and "sandbox" in f.headers["content-security-policy"]
    assert anon_client.get(f"/api/documents/{doc['id']}/file").status_code == 401
    # Deleting evidence of a financial record needs explicit confirmation.
    r = client.post(f"/api/documents/{doc['id']}/delete", json={"reason": "wrong file"})
    assert r.status_code == 409
    r = client.post(f"/api/documents/{doc['id']}/delete", json={"reason": "wrong file",
                                                               "confirm_linked_financial_record": True})
    assert r.status_code == 200
    assert client.get(f"/api/documents/{doc['id']}/file").status_code == 404


def test_uploads_are_not_served_by_the_spa(client):
    r = client.get("/data/uploads/anything.png")
    assert r.status_code in (404, 200)
    assert r.content != PNG


def test_storage_key_cannot_escape_directory():
    from app.errors import DomainError
    from app.storage import LocalStorage

    with pytest.raises(DomainError):
        LocalStorage(backup.settings.uploads_dir).path_for("../../secret.txt")


def test_audit_log_records_financial_changes(client, db):
    ten = make_tenancy(db)
    p = client.post("/api/payments", json={"tenancy_id": ten.id, "received_date": "2025-11-02",
                                           "amount_paise": 1_000_00, "method": "cash", "rent_period": "2025-11"}).json()
    client.post(f"/api/payments/{p['id']}/void", json={"reason": "test reversal"})
    events = client.get("/api/audit?entity_type=rent_payment").json()["events"]
    actions = [e["action"] for e in events]
    assert "payment.create" in actions and "payment.reversed" in actions
    rev = next(e for e in events if e["action"] == "payment.reversed")
    assert rev["actor"] == "Tester" and rev["reason"] == "test reversal" and rev["before"]["status"] == "confirmed"


def test_backup_contains_database_uploads_and_csv(client, db):
    make_tenancy(db)
    client.post("/api/documents", files={"file": ("p.png", PNG)}, data={"entity_type": "tenant", "entity_id": "1"})
    r = client.post("/api/backups")
    assert r.status_code == 200
    path = backup.backup_path(r.json()["name"])
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    assert "rentbook.db" in names and "manifest.json" in names and "csv/rent_payments.csv" in names
    assert any(n.startswith("uploads/") for n in names)
    assert backup.verify_backup(path)["integrity_check"] == "ok"
    d = client.get(f"/api/backups/{path.name}")
    assert d.status_code == 200 and d.content[:2] == b"PK"
    assert client.get("/api/backups/..%2F..%2Fsecret.zip").status_code in (400, 404)


def test_full_export_and_xlsx_report(client, db):
    make_tenancy(db)
    r = client.get("/api/reports/export?period=2025-11&format=xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    r = client.get("/api/reports/export?period=2025-11&format=csv&report=rent_dues")
    assert r.status_code == 200 and b"Rent due" in r.content
    r = client.get("/api/reports/full-export")
    assert r.status_code == 200 and r.content[:2] == b"PK"


def test_migrations_match_models():
    """Autogenerate must find no differences between migrations and models."""
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from app.db import Base, engine

    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_dashboard_reflects_database(client, db):
    ten = make_tenancy(db)
    client.post("/api/payments", json={"tenancy_id": ten.id, "received_date": "2025-11-02",
                                       "amount_paise": 4_000_00, "method": "cash", "rent_period": "2025-10"})
    client.post("/api/payments", json={"tenancy_id": ten.id, "received_date": "2025-11-03",
                                       "amount_paise": 6_000_00, "method": "online", "status": "pending",
                                       "rent_period": "2025-10"})
    d = client.get("/api/dashboard?period=2025-11").json()  # shows October's rent, collected in November
    assert d["rent"]["expected_paise"] == 10_000_00
    assert d["rent"]["confirmed_paise"] == 4_000_00 and d["rent"]["pending_paise"] == 6_000_00
    assert d["rent"]["outstanding_paise"] == 6_000_00
    assert d["collected_for_period_by_method"] == {"cash_paise": 4_000_00, "online_paise": 0}
    assert d["occupancy"] == {"tenant_occupied": 1, "vacant": 9, "owner_occupied": 0}
    assert d["maintenance"]["top_up_required_paise"] == 15_000_00
    assert d["pending_payments"] == 1


def test_buildings_layout_has_ten_flats(client):
    b = client.get("/api/buildings").json()["buildings"]
    assert [x["name"] for x in b] == ["Pink Building", "Blue Building"]
    assert [len(x["flats"]) for x in b] == [6, 4]
    pink_floors = [f["floor_index"] for f in b[0]["flats"]]
    assert pink_floors == [0, 0, 1, 1, 2, 2]
    assert [f["floor_index"] for f in b[1]["flats"]] == [0, 1, 2, 3]


def test_flat_label_editable_and_unique(client):
    flats = client.get("/api/buildings").json()["buildings"][0]["flats"]
    r = client.patch(f"/api/flats/{flats[0]['id']}", json={"label": "Flat 1"})
    assert r.status_code == 200
    r = client.patch(f"/api/flats/{flats[1]['id']}", json={"label": "Flat 1"})
    assert r.status_code == 409


def test_health_is_public(anon_client):
    assert anon_client.get("/api/health").json() == {"status": "ok"}


def test_hash_pin_helper_rejects_weak():
    from app.errors import DomainError

    with pytest.raises(DomainError):
        hash_pin("000000")
