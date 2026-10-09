"""Application settings and effective-dated money rates."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import DomainError
from ..models import AppSetting, RateSetting
from ..periods import validate_period

RATE_KEYS = {
    "maintenance_target": "Monthly maintenance cash target",
    "caretaker_salary": "Caretaker monthly salary",
}
BASELINE_PERIOD = "2000-01"
DEFAULT_RATES = {"maintenance_target": 15_000_00, "caretaker_salary": 15_000_00}

BILLING_POLICIES = {
    "due_date_in_tenancy": "Full month's rent if the tenancy covers that month's due date",
    "any_overlap": "Full month's rent if the tenant occupied the flat on any day of the month",
    "full_months_only": "Only fully occupied months are billed automatically; partial months are added manually",
    "prorated_daily": "Partial months are prorated by days occupied",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "billing_policy": "due_date_in_tenancy",
    "overdue_grace_days": 0,
    "payment_method_labels": {"cash": "Cash", "online": "Online Transfer"},
    "report_preferences": {"include_pending_in_reports": True, "default_export_format": "xlsx"},
    "currency_symbol": "₹",
}

# Settings that change financial calculations are audited.
CALCULATION_SETTINGS = {"billing_policy", "overdue_grace_days"}


def get_setting(db: Session, key: str) -> Any:
    row = db.get(AppSetting, key)
    if row is None:
        return DEFAULT_SETTINGS.get(key)
    return json.loads(row.value_json)


def all_settings(db: Session) -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    for row in db.scalars(select(AppSetting)):
        out[row.key] = json.loads(row.value_json)
    return out


def set_setting(db: Session, key: str, value: Any, actor: str) -> None:
    if key not in DEFAULT_SETTINGS:
        raise DomainError("unknown_setting", f"Unknown setting '{key}'")
    if key == "billing_policy" and value not in BILLING_POLICIES:
        raise DomainError("invalid_setting", "Unknown billing policy")
    if key == "overdue_grace_days" and (not isinstance(value, int) or not 0 <= value <= 60):
        raise DomainError("invalid_setting", "Grace days must be between 0 and 60")
    if key == "payment_method_labels":
        if not isinstance(value, dict) or set(value) != {"cash", "online"}:
            raise DomainError("invalid_setting", "Labels must be given for 'cash' and 'online'")
        value = {k: str(v).strip()[:40] or k for k, v in value.items()}
    before = get_setting(db, key)
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value_json=json.dumps(value), updated_by=actor)
        db.add(row)
    else:
        row.value_json = json.dumps(value)
        row.updated_by = actor
    audit.record(
        db, actor, "setting.update", "setting", None, {key: before}, {key: value},
        reason="calculation setting" if key in CALCULATION_SETTINGS else None,
    )


def get_rate(db: Session, key: str, period: str) -> int:
    row = db.scalar(
        select(RateSetting)
        .where(RateSetting.key == key, RateSetting.effective_period <= period)
        .order_by(RateSetting.effective_period.desc())
        .limit(1)
    )
    return row.amount_paise if row else DEFAULT_RATES[key]


def list_rates(db: Session) -> list[RateSetting]:
    return list(db.scalars(select(RateSetting).order_by(RateSetting.key, RateSetting.effective_period)))


def set_rate(db: Session, key: str, amount_paise: int, effective_period: str, actor: str, note: str | None) -> RateSetting:
    if key not in RATE_KEYS:
        raise DomainError("unknown_rate", f"Unknown rate '{key}'")
    validate_period(effective_period)
    if amount_paise < 0:
        raise DomainError("invalid_amount", "Amount cannot be negative")
    row = db.scalar(select(RateSetting).where(RateSetting.key == key, RateSetting.effective_period == effective_period))
    before = audit.snapshot(row) if row else None
    if row:
        row.amount_paise = amount_paise
        row.note = note
    else:
        row = RateSetting(key=key, amount_paise=amount_paise, effective_period=effective_period, note=note, created_by=actor)
        db.add(row)
    db.flush()
    audit.record(db, actor, "rate.set", "rate_setting", row.id, before, audit.snapshot(row))
    return row
