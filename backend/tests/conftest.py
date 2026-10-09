"""Test setup: an isolated data directory, migrated once, wiped between tests.

All test data is synthetic. No real tenant or financial data is used.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="rentbook-test-"))
os.environ["RENTBOOK_DATA_DIR"] = str(_TMP)
os.environ["RENTBOOK_DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["RENTBOOK_ENV"] = "test"
os.environ["RENTBOOK_LOGIN_MAX_FAILURES"] = "5"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import periods  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.migrate import upgrade_to_head  # noqa: E402
from app.models import Flat, Tenancy  # noqa: E402
from app.security import create_user  # noqa: E402
from app.services import property as prop  # noqa: E402
from app.services.seed import ensure_initial_data  # noqa: E402

TEST_PIN = "482916"
TODAY = dt.date(2025, 11, 20)


@pytest.fixture(scope="session", autouse=True)
def _migrated():
    upgrade_to_head()
    yield


@pytest.fixture(autouse=True)
def _clean(_migrated):
    periods._today_override = TODAY
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        conn.execute(text("PRAGMA foreign_keys=ON"))
    with SessionLocal() as db:
        ensure_initial_data(db)
    yield
    periods._today_override = None


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def flat(db, label: str) -> Flat:
    from sqlalchemy import select

    return db.scalar(select(Flat).where(Flat.label == label))


def make_tenancy(db, *, name="Test Tenant A", label="P-G1", rent=10_000_00, start=dt.date(2025, 1, 1),
                 due_day=5, billing_start="2025-08", policy=None, end=None) -> Tenancy:
    t = prop.create_tenant(db, {"full_name": name}, "tester")
    ten = prop.start_tenancy(db, tenant_id=t.id, flat_id=flat(db, label).id, start_date=start,
                             monthly_rent_paise=rent, rent_due_day=due_day, billing_start_period=billing_start,
                             billing_policy=policy, notes=None, actor="tester")
    if end:
        prop.end_tenancy(db, ten.id, end, "test", "tester")
    db.commit()
    return ten


@pytest.fixture
def anon_client():
    with TestClient(create_app(run_lifespan=False)) as c:
        yield c


@pytest.fixture
def client(db):
    create_user(db, "Tester", TEST_PIN)
    db.commit()
    with TestClient(create_app(run_lifespan=False)) as c:
        r = c.post("/api/auth/login", json={"name": "Tester", "pin": TEST_PIN})
        assert r.status_code == 200, r.text
        c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        yield c
