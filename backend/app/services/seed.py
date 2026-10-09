"""Idempotent initial data: the property layout and default categories/rates.

Only structural data is created here. No tenants, rents or payments are invented.
Flat labels are temporary placeholders and can be renamed in Settings.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Building, ExpenseCategory, Flat, RateSetting
from .settings import BASELINE_PERIOD, DEFAULT_RATES

FLOOR_NAMES = ["Ground floor", "First floor", "Second floor", "Third floor"]

LAYOUT = [
    # code, name, colour, floors, flats per floor, label prefix
    ("pink", "Pink Building", "#db2777", 3, 2, "P"),
    ("blue", "Blue Building", "#2563eb", 4, 1, "B"),
]

DEFAULT_CATEGORIES = ["Plumbing", "Electrical", "Cleaning", "Repairs", "Painting", "Supplies", "Other"]


def _label(prefix: str, floor: int, pos: int, per_floor: int) -> str:
    floor_code = "G" if floor == 0 else str(floor)
    if per_floor == 1:
        return f"{prefix}-{floor_code}"
    return f"{prefix}-{floor_code}{pos + 1}" if floor == 0 else f"{prefix}-{floor}0{pos + 1}"


def ensure_initial_data(db: Session) -> None:
    if db.scalar(select(Building.id).limit(1)) is None:
        for order, (code, name, colour, floors, per_floor, prefix) in enumerate(LAYOUT):
            b = Building(code=code, name=name, color=colour, sort_order=order)
            db.add(b)
            db.flush()
            for f in range(floors):
                for pos in range(per_floor):
                    db.add(
                        Flat(
                            building_id=b.id,
                            label=_label(prefix, f, pos, per_floor),
                            floor_index=f,
                            floor_label=FLOOR_NAMES[f],
                            position=pos,
                        )
                    )
    if db.scalar(select(ExpenseCategory.id).limit(1)) is None:
        for name in DEFAULT_CATEGORIES:
            db.add(ExpenseCategory(name=name))
    for key, amount in DEFAULT_RATES.items():
        exists = db.scalar(select(RateSetting.id).where(RateSetting.key == key).limit(1))
        if exists is None:
            db.add(RateSetting(key=key, amount_paise=amount, effective_period=BASELINE_PERIOD,
                               note="Default", created_by="system"))
    db.commit()
