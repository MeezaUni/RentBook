"""Relational schema.

Conventions
-----------
* All money is stored as integer paise (``*_paise`` columns). Never floats.
* Billing / accounting months are stored as ``period`` strings ``YYYY-MM``.
* Financial rows are never physically deleted; they carry a status
  (``reversed``, ``rejected``, ``cancelled`` ...) plus who/when/why.
* Timestamps are UTC.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


Money = BigInteger
Period = String(7)


# --------------------------------------------------------------------------- auth


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    pin_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime)


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    user: Mapped[User] = relationship()


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    ip: Mapped[str] = mapped_column(String(64))
    succeeded: Mapped[bool] = mapped_column(Boolean)
    attempted_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (Index("ix_login_attempts_ip_time", "ip", "attempted_at"),)


# ---------------------------------------------------------------- property layout


class Building(Base):
    __tablename__ = "buildings"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    color: Mapped[str] = mapped_column(String(16))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    flats: Mapped[list[Flat]] = relationship(back_populates="building", order_by="Flat.floor_index")


class Flat(Base):
    __tablename__ = "flats"
    id: Mapped[int] = mapped_column(primary_key=True)
    building_id: Mapped[int] = mapped_column(ForeignKey("buildings.id"))
    label: Mapped[str] = mapped_column(String(32))
    floor_index: Mapped[int] = mapped_column(Integer)
    floor_label: Mapped[str] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    building: Mapped[Building] = relationship(back_populates="flats")
    __table_args__ = (
        UniqueConstraint("building_id", "label", name="uq_flat_label"),
        UniqueConstraint("building_id", "floor_index", "position", name="uq_flat_slot"),
    )


class OwnerOccupancy(Base):
    """Periods in which the family itself lives in a flat (never generates rent)."""

    __tablename__ = "owner_occupancies"
    id: Mapped[int] = mapped_column(primary_key=True)
    flat_id: Mapped[int] = mapped_column(ForeignKey("flats.id"), index=True)
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


# ------------------------------------------------------------------------ tenants


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(160))
    preferred_payment_method: Mapped[str | None] = mapped_column(String(16))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    tenancies: Mapped[list[Tenancy]] = relationship(back_populates="tenant")
    aliases: Mapped[list[TenantAlias]] = relationship(back_populates="tenant")


class Tenancy(Base):
    """A tenant occupying a flat between two dates. History is never erased."""

    __tablename__ = "tenancies"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    flat_id: Mapped[int] = mapped_column(ForeignKey("flats.id"), index=True)
    start_date: Mapped[dt.date] = mapped_column(Date)
    end_date: Mapped[dt.date | None] = mapped_column(Date)
    rent_due_day: Mapped[int] = mapped_column(Integer, default=1)
    # First month whose rent this app tracks. Earlier arrears are entered as opening balances.
    billing_start_period: Mapped[str] = mapped_column(Period)
    billing_policy: Mapped[str | None] = mapped_column(String(32))  # None => global setting
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    tenant: Mapped[Tenant] = relationship(back_populates="tenancies")
    flat: Mapped[Flat] = relationship()
    rent_terms: Mapped[list[RentTerm]] = relationship(
        back_populates="tenancy", order_by="RentTerm.effective_period"
    )
    __table_args__ = (
        CheckConstraint("rent_due_day BETWEEN 1 AND 31", name="ck_due_day"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="ck_tenancy_dates"),
    )


class RentTerm(Base):
    """Effective-dated monthly rent for a tenancy."""

    __tablename__ = "rent_terms"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenancy_id: Mapped[int] = mapped_column(ForeignKey("tenancies.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(Money)
    effective_period: Mapped[str] = mapped_column(Period)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(80))
    tenancy: Mapped[Tenancy] = relationship(back_populates="rent_terms")
    __table_args__ = (
        UniqueConstraint("tenancy_id", "effective_period", name="uq_rent_term_period"),
        CheckConstraint("amount_paise >= 0", name="ck_rent_term_amount"),
    )


class TenantAlias(Base):
    """Bank sender names / references that identify a tenant's payments.

    Deliberately NOT unique across tenants: one sender may pay for several flats.
    """

    __tablename__ = "tenant_aliases"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    alias: Mapped[str] = mapped_column(String(160))
    alias_normalized: Mapped[str] = mapped_column(String(160), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="sender_name")
    source: Mapped[str] = mapped_column(String(32), default="manual")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(80))
    tenant: Mapped[Tenant] = relationship(back_populates="aliases")
    __table_args__ = (
        UniqueConstraint("tenant_id", "alias_normalized", "kind", name="uq_tenant_alias"),
    )


# --------------------------------------------------------------------------- rent


class RentDue(Base):
    """One month's rent for one tenancy (or a manually entered opening balance)."""

    __tablename__ = "rent_dues"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenancy_id: Mapped[int] = mapped_column(ForeignKey("tenancies.id"), index=True)
    period: Mapped[str] = mapped_column(Period, index=True)
    kind: Mapped[str] = mapped_column(String(20), default="rent")  # rent | opening_balance
    amount_paise: Mapped[int] = mapped_column(Money)
    due_date: Mapped[dt.date] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
    amount_overridden: Mapped[bool] = mapped_column(Boolean, default=False)
    override_reason: Mapped[str | None] = mapped_column(Text)
    waived_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    waived_by: Mapped[str | None] = mapped_column(String(80))
    waived_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    tenancy: Mapped[Tenancy] = relationship()
    allocations: Mapped[list[RentAllocation]] = relationship(back_populates="rent_due")
    __table_args__ = (
        UniqueConstraint("tenancy_id", "period", "kind", name="uq_rent_due_period"),
        CheckConstraint("amount_paise >= 0", name="ck_rent_due_amount"),
    )


class RentPayment(Base):
    """Money received (or claimed received) from a tenant.

    status: pending | confirmed | rejected | reversed. Only ``confirmed`` counts.
    """

    __tablename__ = "rent_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    tenancy_id: Mapped[int] = mapped_column(ForeignKey("tenancies.id"), index=True)
    received_date: Mapped[dt.date] = mapped_column(Date, index=True)
    amount_paise: Mapped[int] = mapped_column(Money)
    method: Mapped[str] = mapped_column(String(16))  # cash | online
    reference: Mapped[str | None] = mapped_column(String(120), index=True)
    sender_name: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    excess_resolution: Mapped[str | None] = mapped_column(String(24))
    excess_note: Mapped[str | None] = mapped_column(Text)
    bank_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("bank_transactions.id"), index=True
    )
    corrects_payment_id: Mapped[int | None] = mapped_column(ForeignKey("rent_payments.id"))
    recorded_by: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    confirmed_by: Mapped[str | None] = mapped_column(String(80))
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    voided_by: Mapped[str | None] = mapped_column(String(80))
    voided_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    tenancy: Mapped[Tenancy] = relationship()
    allocations: Mapped[list[RentAllocation]] = relationship(back_populates="payment")
    __table_args__ = (CheckConstraint("amount_paise > 0", name="ck_payment_amount"),)


class RentAllocation(Base):
    """Part (or all) of a payment applied to a specific rent due."""

    __tablename__ = "rent_allocations"
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("rent_payments.id"), index=True)
    rent_due_id: Mapped[int] = mapped_column(ForeignKey("rent_dues.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(Money)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(80))
    payment: Mapped[RentPayment] = relationship(back_populates="allocations")
    rent_due: Mapped[RentDue] = relationship(back_populates="allocations")
    __table_args__ = (
        UniqueConstraint("payment_id", "rent_due_id", name="uq_allocation_pair"),
        CheckConstraint("amount_paise > 0", name="ck_allocation_amount"),
    )


# ---------------------------------------------------------------------- documents


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    storage_key: Mapped[str] = mapped_column(String(80), unique=True)
    original_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    category: Mapped[str] = mapped_column(String(40), default="other")
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    uploaded_by: Mapped[str | None] = mapped_column(String(80))
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    deleted_by: Mapped[str | None] = mapped_column(String(80))
    delete_reason: Mapped[str | None] = mapped_column(Text)
    links: Mapped[list[DocumentLink]] = relationship(back_populates="document")


class DocumentLink(Base):
    __tablename__ = "document_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    document: Mapped[Document] = relationship(back_populates="links")
    __table_args__ = (
        UniqueConstraint("document_id", "entity_type", "entity_id", name="uq_document_link"),
        Index("ix_document_links_entity", "entity_type", "entity_id"),
    )


# --------------------------------------------------------------------------- bank


class StatementProfile(Base):
    """A saved column mapping for a bank statement layout."""

    __tablename__ = "statement_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    mapping_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class BankImport(Base):
    __tablename__ = "bank_imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    original_name: Mapped[str] = mapped_column(String(255))
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    sheet_name: Mapped[str | None] = mapped_column(String(120))
    mapping_json: Mapped[str] = mapped_column(Text)
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_new: Mapped[int] = mapped_column(Integer, default=0)
    rows_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    rows_error: Mapped[int] = mapped_column(Integer, default=0)
    period_start: Mapped[dt.date | None] = mapped_column(Date)
    period_end: Mapped[dt.date | None] = mapped_column(Date)
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    uploaded_by: Mapped[str | None] = mapped_column(String(80))


class BankTransaction(Base):
    """A normalized statement row. Kept intact; allocations live elsewhere.

    review_status: unreviewed | partially_allocated | allocated | not_rent | debit
    """

    __tablename__ = "bank_transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(80), unique=True)
    txn_date: Mapped[dt.date] = mapped_column(Date, index=True)
    value_date: Mapped[dt.date | None] = mapped_column(Date)
    narration: Mapped[str] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(120), index=True)
    debit_paise: Mapped[int] = mapped_column(Money, default=0)
    credit_paise: Mapped[int] = mapped_column(Money, default=0)
    balance_paise: Mapped[int | None] = mapped_column(Money)
    first_import_id: Mapped[int] = mapped_column(ForeignKey("bank_imports.id"))
    collision_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    # True when the part of the credit not allocated to rent was explicitly marked "not rent".
    remainder_not_rent: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[str] = mapped_column(String(24), default="unreviewed", index=True)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(80))
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (
        CheckConstraint("debit_paise >= 0 AND credit_paise >= 0", name="ck_bank_amounts"),
    )


class BankTransactionSource(Base):
    """Every (import, row) in which a bank transaction appeared."""

    __tablename__ = "bank_transaction_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    bank_transaction_id: Mapped[int] = mapped_column(ForeignKey("bank_transactions.id"))
    import_id: Mapped[int] = mapped_column(ForeignKey("bank_imports.id"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        UniqueConstraint("bank_transaction_id", "import_id", name="uq_bank_txn_source"),
    )


# ----------------------------------------------------------------- cash & upkeep


class CashMovement(Base):
    """Movement of physical cash between accounts.

    Accounts: ``family_cash``, ``caretaker_cash``, ``external`` (bank / outside world).
    Cash rent receipts are NOT stored here: they are derived from confirmed cash
    RentPayments so a receipt can never be counted twice.
    kind: caretaker_handover | caretaker_return | family_expense | bank_deposit |
          cash_in_other | adjustment | opening_balance
    """

    __tablename__ = "cash_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    movement_date: Mapped[dt.date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    amount_paise: Mapped[int] = mapped_column(Money)
    from_account: Mapped[str] = mapped_column(String(16))
    to_account: Mapped[str] = mapped_column(String(16))
    period: Mapped[str | None] = mapped_column(Period, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active")
    reversed_reason: Mapped[str | None] = mapped_column(Text)
    reversed_by: Mapped[str | None] = mapped_column(String(80))
    reversed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    recorded_by: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    sources: Mapped[list[CashMovementSource]] = relationship(back_populates="movement")
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="ck_cash_amount"),
        CheckConstraint("from_account <> to_account", name="ck_cash_accounts"),
    )


class CashMovementSource(Base):
    """Links cash leaving the family to the tenant receipts it came from."""

    __tablename__ = "cash_movement_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    cash_movement_id: Mapped[int] = mapped_column(ForeignKey("cash_movements.id"), index=True)
    rent_payment_id: Mapped[int] = mapped_column(ForeignKey("rent_payments.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(Money)
    movement: Mapped[CashMovement] = relationship(back_populates="sources")
    __table_args__ = (
        UniqueConstraint("cash_movement_id", "rent_payment_id", name="uq_cash_source_pair"),
        CheckConstraint("amount_paise > 0", name="ck_cash_source_amount"),
    )


class RateSetting(Base):
    """Effective-dated money settings (maintenance target, caretaker salary).

    Changing a rate from a later month never rewrites earlier months.
    """

    __tablename__ = "rate_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40))
    amount_paise: Mapped[int] = mapped_column(Money)
    effective_period: Mapped[str] = mapped_column(Period)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(80))
    __table_args__ = (UniqueConstraint("key", "effective_period", name="uq_rate_period"),)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str | None] = mapped_column(String(80))


class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class MaintenanceExpense(Base):
    """A maintenance expense, split between caretaker cash and family funds."""

    __tablename__ = "maintenance_expenses"
    id: Mapped[int] = mapped_column(primary_key=True)
    expense_date: Mapped[dt.date] = mapped_column(Date, index=True)
    period: Mapped[str] = mapped_column(Period, index=True)
    description: Mapped[str] = mapped_column(Text)
    category_id: Mapped[int] = mapped_column(ForeignKey("expense_categories.id"))
    total_paise: Mapped[int] = mapped_column(Money)
    caretaker_paid_paise: Mapped[int] = mapped_column(Money, default=0)
    family_paid_paise: Mapped[int] = mapped_column(Money, default=0)
    family_payment_method: Mapped[str | None] = mapped_column(String(16))  # cash|bank|other
    flat_id: Mapped[int | None] = mapped_column(ForeignKey("flats.id"))
    vendor: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="active")
    reversed_reason: Mapped[str | None] = mapped_column(Text)
    reversed_by: Mapped[str | None] = mapped_column(String(80))
    reversed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    recorded_by: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    category: Mapped[ExpenseCategory] = relationship()
    __table_args__ = (
        CheckConstraint(
            "total_paise = caretaker_paid_paise + family_paid_paise", name="ck_expense_split"
        ),
        CheckConstraint(
            "caretaker_paid_paise >= 0 AND family_paid_paise >= 0 AND total_paise > 0",
            name="ck_expense_amounts",
        ),
    )


class CashCount(Base):
    """A physical count of cash held (family or caretaker), for reconciliation."""

    __tablename__ = "cash_counts"
    id: Mapped[int] = mapped_column(primary_key=True)
    account: Mapped[str] = mapped_column(String(16))  # family_cash | caretaker_cash
    count_date: Mapped[dt.date] = mapped_column(Date, index=True)
    counted_paise: Mapped[int] = mapped_column(Money)
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class MaintenanceMonthNote(Base):
    __tablename__ = "maintenance_month_notes"
    period: Mapped[str] = mapped_column(Period, primary_key=True)
    notes: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SalaryPayment(Base):
    """Caretaker salary. Completely separate from the maintenance ledger."""

    __tablename__ = "salary_payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    salary_month: Mapped[str] = mapped_column(Period, index=True)
    amount_paise: Mapped[int] = mapped_column(Money)
    payment_date: Mapped[dt.date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(16))  # bank | cash | other
    bank_reference: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|confirmed|cancelled
    status_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    confirmed_by: Mapped[str | None] = mapped_column(String(80))
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime)
    __table_args__ = (CheckConstraint("amount_paise > 0", name="ck_salary_amount"),)


class OpeningBalance(Base):
    """Manually entered historical balance. Never an imported/actual transaction.

    kind: tenant_arrears | caretaker_cash | family_cash
    """

    __tablename__ = "opening_balances"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))
    as_of_date: Mapped[dt.date] = mapped_column(Date)
    amount_paise: Mapped[int] = mapped_column(Money)
    description: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    tenancy_id: Mapped[int | None] = mapped_column(ForeignKey("tenancies.id"))
    rent_due_id: Mapped[int | None] = mapped_column(ForeignKey("rent_dues.id"))
    cash_movement_id: Mapped[int | None] = mapped_column(ForeignKey("cash_movements.id"))
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (CheckConstraint("amount_paise > 0", name="ck_opening_amount"),)


# -------------------------------------------------------------------------- audit


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
