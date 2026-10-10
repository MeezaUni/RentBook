"""Bank statement import, duplicate prevention, deterministic matching and allocation.

Nothing here marks rent as paid on its own: suggestions are only suggestions, and a
rent payment is created only when the user confirms an allocation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..errors import Conflict, DomainError
from ..models import (
    BankImport,
    BankTransaction,
    BankTransactionSource,
    Document,
    DocumentLink,
    RentDue,
    RentPayment,
    StatementProfile,
    Tenancy,
    TenantAlias,
    utcnow,
)
from ..periods import add_months, current_period, label, period_of
from ..storage import STATEMENT_TYPES, get_storage, sanitize_filename, validate_upload
from . import bank_parse
from .property import add_alias, get_or_404, normalize_alias, rent_for_period
from .rent import ACTIVE, PaymentInput, describe_dues, ensure_dues, record_payment

# ------------------------------------------------------------ fingerprints


def _fp_base(r: bank_parse.ParsedRow) -> str:
    return "|".join([
        r.txn_date.isoformat() if r.txn_date else "",
        normalize_alias(r.narration),
        normalize_alias(r.reference or ""),
        str(r.debit_paise),
        str(r.credit_paise),
        "" if r.balance_paise is None else str(r.balance_paise),
    ])


def fingerprints(rows: list[bank_parse.ParsedRow]) -> list[tuple[str, bool]]:
    """Stable identity per row: hash of date, narration, reference, amounts and balance.

    Identical rows inside one file (possible when the statement has no balance
    column) get an occurrence suffix and are flagged as possible collisions.
    """
    seen: Counter[str] = Counter()
    out = []
    for r in rows:
        base = _fp_base(r)
        seen[base] += 1
        n = seen[base]
        key = base if n == 1 else f"{base}#{n}"
        out.append(("v1:" + hashlib.sha256(key.encode()).hexdigest()[:60], n > 1))
    # Mark the first occurrence of a repeated row as a collision too.
    repeated = {b for b, c in seen.items() if c > 1}
    return [(fp, col or _fp_base(r) in repeated) for (fp, col), r in zip(out, rows)]


# ------------------------------------------------------------ preview/commit


@dataclass
class Prepared:
    table: bank_parse.Table
    mapping: dict
    rows: list[bank_parse.ParsedRow]
    content_type: str
    sha256: str
    info: bank_parse.StatementInfo
    checks: list[dict]
    table_end: int | None


def prepare(data: bytes, filename: str, sheet: str | None, mapping: dict | None) -> Prepared:
    ctype, sha = validate_upload(data, filename, STATEMENT_TYPES)
    table = bank_parse.read_table(data, ctype, sheet)
    if not table.rows:
        raise DomainError("empty_statement", "The statement has no rows")
    if mapping is None:
        mapping = bank_parse.auto_mapping(table.rows)
    ncols = max((len(r) for r in table.rows), default=0)
    parsed: list[bank_parse.ParsedRow] = []
    mapping_ok = True
    try:
        bank_parse.validate_mapping(mapping, ncols)
    except DomainError:
        mapping_ok = False
    info, checks, table_end = bank_parse.StatementInfo(), [], None
    if mapping_ok:
        parsed = bank_parse.parse_rows(table.rows, mapping)
        if parsed:
            table_end = max(r.row_number for r in parsed)  # 1-based row number == 0-based index after it
            info = bank_parse.extract_statement_info(table.rows, mapping.get("header_row"), table_end)
            checks = bank_parse.statement_checks(parsed, info)
    mapping["valid"] = mapping_ok
    return Prepared(table, mapping, parsed, ctype, sha, info, checks, table_end)


def _existing_by_fp(db: Session, fps: list[str]) -> dict[str, BankTransaction]:
    out = {}
    for i in range(0, len(fps), 500):
        for t in db.scalars(select(BankTransaction).where(BankTransaction.fingerprint.in_(fps[i:i + 500]))):
            out[t.fingerprint] = t
    return out


def _soft_duplicate(db: Session, r: bank_parse.ParsedRow) -> BankTransaction | None:
    """Same date, amounts and reference but a different fingerprint (e.g. narration
    formatted differently between a CSV and an XLS export of the same statement)."""
    if not r.reference or r.txn_date is None:
        return None
    return db.scalar(select(BankTransaction).where(
        BankTransaction.txn_date == r.txn_date, BankTransaction.reference == r.reference,
        BankTransaction.debit_paise == r.debit_paise, BankTransaction.credit_paise == r.credit_paise).limit(1))


def preview(db: Session, data: bytes, filename: str, sheet: str | None, mapping: dict | None) -> dict:
    p = prepare(data, filename, sheet, mapping)
    previous = db.scalars(select(BankImport).where(BankImport.file_sha256 == p.sha256)).all()
    header_row = p.mapping.get("header_row")
    header = [str(c) if c is not None else "" for c in p.table.rows[header_row]] if header_row is not None else []
    # The account-holder block above the table (and masked footer details) never leave the server.
    raw_preview = bank_parse.redact_preview_rows(p.table.rows[:40], header_row, p.table_end, _cellstr)
    good = [r for r in p.rows if not r.errors]
    fps = fingerprints(good)
    existing = _existing_by_fp(db, [fp for fp, _ in fps])
    ctx = MatchContext(db)
    out_rows = []
    counts = Counter()
    for r in p.rows:
        if r.errors:
            counts["errors"] += 1
    for r, (fp, collision) in zip(good, fps):
        dup = fp in existing
        soft = None if dup else _soft_duplicate(db, r)
        flags = []
        if dup:
            flags.append("already_imported")
            counts["duplicates"] += 1
        elif soft:
            flags.append("possible_duplicate")
            counts["possible_duplicates"] += 1
        if collision:
            flags.append("identical_rows_in_file")
            counts["collisions"] += 1
        if bank_parse.is_outside_period(r, p.info):
            flags.append("outside_statement_period")
        if r.credit_paise:
            counts["credits"] += 1
            counts["credit_total"] += r.credit_paise
        else:
            counts["debits"] += 1
            counts["debit_total"] += r.debit_paise
        likely_rent = False
        if r.credit_paise and not dup:
            sug = ctx.suggest_for(r.txn_date, r.narration, r.reference, r.credit_paise)
            likely_rent = sug["category"] in ("strong", "possible", "ambiguous", "potential_duplicate")
            if likely_rent:
                counts["potential_rent"] += 1
        out_rows.append({
            "row_number": r.row_number, "txn_date": r.txn_date.isoformat(), "narration": r.narration,
            "reference": r.reference, "debit_paise": r.debit_paise, "credit_paise": r.credit_paise,
            "balance_paise": r.balance_paise, "flags": flags, "likely_rent": likely_rent,
        })
    dates = [r.txn_date for r in good]
    return {
        "sheets": p.table.sheets,
        "sheet": p.table.sheet,
        "mapping": p.mapping,
        "fields": bank_parse.FIELDS,
        "header": header,
        "raw_preview": raw_preview,
        "file_previously_imported": [{"id": b.id, "uploaded_at": b.uploaded_at.isoformat(), "name": b.original_name}
                                     for b in previous],
        "summary": {
            "detected": len(p.rows),
            "valid": len(good),
            "credits": counts["credits"],
            "debits": counts["debits"],
            "credit_total_paise": counts["credit_total"],
            "debit_total_paise": counts["debit_total"],
            "already_imported": counts["duplicates"],
            "possible_duplicates": counts["possible_duplicates"],
            "identical_rows": counts["collisions"],
            "errors": counts["errors"],
            "potential_rent": counts["potential_rent"],
            "period_start": min(dates).isoformat() if dates else None,
            "period_end": max(dates).isoformat() if dates else None,
        },
        "statement": p.info.as_dict(),
        "checks": p.checks,
        "errors": [{"row_number": r.row_number, "messages": r.errors} for r in p.rows if r.errors][:200],
        "rows": out_rows[:1000],
    }


def _cellstr(c) -> str:
    if c is None:
        return ""
    if isinstance(c, (dt.datetime, dt.date)):
        return c.strftime("%d/%m/%Y")
    return str(c)


def commit(db: Session, data: bytes, filename: str, sheet: str | None, mapping: dict, *,
           accept_errors: bool, skip_possible_duplicates: bool, save_profile_as: str | None, actor: str) -> BankImport:
    p = prepare(data, filename, sheet, mapping)
    if not p.mapping.get("valid"):
        raise DomainError("invalid_mapping", "The column mapping is incomplete")
    bad = [r for r in p.rows if r.errors]
    if bad and not accept_errors:
        raise Conflict("rows_with_errors", f"{len(bad)} rows could not be read. Review them, then confirm to skip them.",
                       {"rows": [r.row_number for r in bad][:50]})
    good = [r for r in p.rows if not r.errors]
    if not good:
        raise DomainError("no_rows", "No valid transactions found in the statement")
    storage = get_storage()
    key = storage.save(data, p.content_type)
    doc = Document(storage_key=key, original_name=sanitize_filename(filename), content_type=p.content_type,
                   size_bytes=len(data), sha256=p.sha256, category="bank_statement", uploaded_by=actor)
    db.add(doc)
    db.flush()
    clean_mapping = {k: v for k, v in p.mapping.items() if k != "valid"}
    imp = BankImport(original_name=doc.original_name, document_id=doc.id, file_sha256=p.sha256,
                     sheet_name=p.table.sheet, mapping_json=json.dumps(clean_mapping), uploaded_by=actor,
                     rows_total=len(p.rows), rows_error=len(bad),
                     statement_checks_json=json.dumps({"statement": p.info.as_dict(), "checks": p.checks}),
                     period_start=min(r.txn_date for r in good), period_end=max(r.txn_date for r in good))
    db.add(imp)
    db.flush()
    db.add(DocumentLink(document_id=doc.id, entity_type="bank_import", entity_id=imp.id))
    fps = fingerprints(good)
    existing = _existing_by_fp(db, [fp for fp, _ in fps])
    new = dup = skipped = 0
    for r, (fp, collision) in zip(good, fps):
        txn = existing.get(fp)
        if txn is None and skip_possible_duplicates and _soft_duplicate(db, r):
            skipped += 1
            continue
        if txn is None:
            txn = BankTransaction(
                fingerprint=fp, txn_date=r.txn_date, value_date=r.value_date, narration=r.narration or "",
                reference=r.reference, debit_paise=r.debit_paise, credit_paise=r.credit_paise,
                balance_paise=r.balance_paise, first_import_id=imp.id, collision_flag=collision,
                review_status="unreviewed" if r.credit_paise else "debit",
            )
            db.add(txn)
            db.flush()
            existing[fp] = txn
            new += 1
        else:
            dup += 1
        if not db.scalar(select(BankTransactionSource.id).where(BankTransactionSource.bank_transaction_id == txn.id,
                                                               BankTransactionSource.import_id == imp.id)):
            db.add(BankTransactionSource(bank_transaction_id=txn.id, import_id=imp.id, row_number=r.row_number))
    imp.rows_new, imp.rows_duplicate = new, dup + skipped
    if save_profile_as:
        name = save_profile_as.strip()[:80]
        prof = db.scalar(select(StatementProfile).where(StatementProfile.name == name))
        if prof:
            prof.mapping_json = json.dumps(clean_mapping)
        else:
            db.add(StatementProfile(name=name, mapping_json=json.dumps(clean_mapping)))
    db.flush()
    audit.record(db, actor, "bank.import", "bank_import", imp.id, None,
                 {"file": imp.original_name, "rows": len(p.rows), "new": new, "duplicates": dup,
                  "skipped_possible_duplicates": skipped, "errors": len(bad)})
    return imp


# ---------------------------------------------------------------- matching


_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def extract_sender(narration: str) -> str | None:
    """Best-effort counterparty name from common HDFC narration formats."""
    n = (narration or "").upper()
    parts = [p.strip() for p in n.split("-")]
    if n.startswith("UPI") and len(parts) >= 2:
        return normalize_alias(parts[1]) or None
    if ("NEFT" in parts[0] or "RTGS" in parts[0]) and len(parts) >= 3:
        for i, p in enumerate(parts):
            if _IFSC.match(p) and i + 1 < len(parts):
                return normalize_alias(parts[i + 1]) or None
        return normalize_alias(parts[2]) or None
    if n.startswith("IMPS") and len(parts) >= 3:
        return normalize_alias(parts[2]) or None
    return None


def _word_in(needle: str, hay: str) -> bool:
    return bool(needle) and f" {needle} " in f" {hay} "


@dataclass
class Candidate:
    tenancy: Tenancy
    identity: int = 0
    amount: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def score(self) -> int:
        return self.identity * 10 + self.amount


class MatchContext:
    """Loads tenancies, aliases, outstanding dues and previous senders once."""

    def __init__(self, db: Session):
        self.db = db
        cur = current_period()
        ensure_dues(db, cur)
        self.tenancies = db.scalars(select(Tenancy)).all()
        self.aliases: dict[int, list[TenantAlias]] = defaultdict(list)
        for a in db.scalars(select(TenantAlias).where(TenantAlias.is_active)):
            self.aliases[a.tenant_id].append(a)
        dues = db.scalars(select(RentDue).where(RentDue.waived_at.is_(None), RentDue.period <= add_months(cur, 1))
                          .order_by(RentDue.period, RentDue.kind.desc())).all()
        self.outstanding: dict[int, list[dict]] = defaultdict(list)
        for d in describe_dues(db, list(dues)):
            if d["available_paise"] > 0:
                self.outstanding[d["tenancy_id"]].append(d)
        self.prev_senders: dict[int, set[str]] = defaultdict(set)
        rows = db.execute(select(RentPayment.tenancy_id, BankTransaction.narration)
                          .join(BankTransaction, BankTransaction.id == RentPayment.bank_transaction_id)
                          .where(RentPayment.status == "confirmed")).all()
        for tid, narr in rows:
            s = extract_sender(narr)
            if s:
                self.prev_senders[tid].add(s)

    def suggest_for(self, txn_date: dt.date, narration: str, reference: str | None, amount: int,
                    exclude_txn_id: int | None = None) -> dict:
        narr = normalize_alias(narration)
        ref = normalize_alias(reference or "")
        sender = extract_sender(narration)
        cands: list[Candidate] = []
        for t in self.tenancies:
            c = Candidate(t)
            recent = t.end_date is None or t.end_date >= txn_date - dt.timedelta(days=120)
            started = t.start_date <= txn_date + dt.timedelta(days=45)
            outs = self.outstanding.get(t.id, [])
            if not (recent and started) and not outs:
                continue
            for a in self.aliases.get(t.tenant_id, []):
                hit = _word_in(a.alias_normalized, narr) if a.kind == "sender_name" else (
                    a.alias_normalized in narr or (ref and a.alias_normalized == ref))
                if hit:
                    c.identity = max(c.identity, 3)
                    c.reasons.append(f"Saved sender alias “{a.alias}” appears in the narration")
            name = normalize_alias(t.tenant.full_name)
            if _word_in(name, narr):
                c.identity = max(c.identity, 2)
                c.reasons.append("Tenant's full name appears in the narration")
            else:
                tokens = [w for w in name.split() if len(w) >= 3]
                hits = [w for w in tokens if _word_in(w, narr)]
                if len(tokens) >= 2 and len(hits) >= 2:
                    c.identity = max(c.identity, 2)
                    c.reasons.append("Tenant's name appears in the narration")
                elif any(len(w) >= 4 for w in hits):
                    c.identity = max(c.identity, 1)
                    c.reasons.append(f"Part of the tenant's name (“{hits[0].title()}”) appears in the narration")
            if sender and sender in self.prev_senders.get(t.id, set()):
                c.identity = max(c.identity, 3)
                c.reasons.append("Same sender as a previously confirmed payment for this tenancy")
            exact = [d for d in outs if d["available_paise"] == amount]
            total = sum(d["available_paise"] for d in outs)
            rent_now = rent_for_period(t, period_of(txn_date))
            if exact:
                c.amount = 2
                c.reasons.append(f"Amount equals the rent still due for {label(exact[0]['period'])}")
            elif outs and total == amount:
                c.amount = 2
                c.reasons.append("Amount equals this tenancy's total outstanding rent")
            elif rent_now and rent_now == amount and (recent and started):
                c.amount = 1
                c.reasons.append("Amount equals the monthly rent")
            if c.score > 0:
                cands.append(c)
        cands.sort(key=lambda c: (-c.score, c.tenancy.id))
        dups = self._duplicate_payments(txn_date, reference, amount, exclude_txn_id,
                                        [c.tenancy.id for c in cands])
        if dups:
            category = "potential_duplicate"
        elif not cands:
            category = "no_match"
        else:
            top = cands[0]
            ties = [c for c in cands if c.identity == top.identity and c.amount == top.amount]
            if len(ties) > 1:
                category = "ambiguous"
            elif top.identity >= 2 and top.amount >= 1:
                category = "strong"
            else:
                category = "possible"
        return {
            "category": category,
            "sender": sender,
            "candidates": [self._describe(c, amount) for c in cands[:5]],
            "duplicate_payments": dups,
        }

    def _describe(self, c: Candidate, amount: int) -> dict:
        t = c.tenancy
        outs = self.outstanding.get(t.id, [])
        exact = [d for d in outs if d["available_paise"] == amount]
        proposal, left = [], amount
        for d in (exact[:1] or outs):
            if left <= 0:
                break
            take = min(left, d["available_paise"])
            proposal.append({"rent_due_id": d["id"], "period": d["period"], "kind": d["kind"],
                             "amount_paise": take, "available_paise": d["available_paise"]})
            left -= take
        return {
            "tenancy_id": t.id, "tenant_id": t.tenant_id, "tenant_name": t.tenant.full_name,
            "flat_id": t.flat_id, "flat_label": t.flat.label, "building_name": t.flat.building.name,
            "identity_signal": c.identity, "amount_signal": c.amount, "reasons": c.reasons,
            "outstanding_paise": sum(d["outstanding_paise"] for d in outs),
            "proposed_allocations": proposal, "unallocated_paise": left,
        }

    def _duplicate_payments(self, txn_date, reference, amount, exclude_txn_id, tenancy_ids) -> list[dict]:
        """Existing rent payments this credit may duplicate.

        * unlinked online payments with the same reference, or same tenancy + amount within 5 days
          (typically a payment entered manually before the statement arrived: link it instead);
        * payments already linked to a *different* bank transaction with the same reference.
        """
        db = self.db
        found: dict[int, RentPayment] = {}
        base = (RentPayment.status.in_(ACTIVE), RentPayment.method == "online")
        if reference:
            same_ref = func.lower(RentPayment.reference) == reference.lower()
            for p in db.scalars(select(RentPayment).where(same_ref, *base)):
                if p.bank_transaction_id != exclude_txn_id or p.bank_transaction_id is None:
                    found[p.id] = p
        if tenancy_ids:
            for p in db.scalars(select(RentPayment).where(
                    *base, RentPayment.bank_transaction_id.is_(None), RentPayment.tenancy_id.in_(tenancy_ids),
                    RentPayment.amount_paise == amount,
                    RentPayment.received_date.between(txn_date - dt.timedelta(days=5),
                                                      txn_date + dt.timedelta(days=5)))):
                found[p.id] = p
        return [{"payment_id": p.id, "tenancy_id": p.tenancy_id, "tenant_name": p.tenancy.tenant.full_name,
                 "flat_label": p.tenancy.flat.label, "received_date": p.received_date.isoformat(),
                 "amount_paise": p.amount_paise, "status": p.status, "reference": p.reference,
                 "linked_to_bank_transaction": p.bank_transaction_id,
                 "can_link": p.bank_transaction_id is None}
                for p in found.values()]


# -------------------------------------------------------------- resolution


def linked_total(db: Session, txn_id: int, statuses=ACTIVE) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(RentPayment.amount_paise), 0)).where(
        RentPayment.bank_transaction_id == txn_id, RentPayment.status.in_(statuses))) or 0)


def refresh_review_status(db: Session, txn_id: int) -> None:
    txn = db.get(BankTransaction, txn_id)
    if txn is None or txn.credit_paise == 0 or txn.review_status == "not_rent":
        return
    linked = linked_total(db, txn.id)
    if linked == 0:
        txn.review_status, txn.remainder_not_rent = "unreviewed", False
    elif linked >= txn.credit_paise or txn.remainder_not_rent:
        txn.review_status = "allocated"
    else:
        txn.review_status = "partially_allocated"
    db.flush()


def _get_credit(db: Session, txn_id: int) -> BankTransaction:
    txn = get_or_404(db, BankTransaction, txn_id, "Bank transaction")
    if txn.credit_paise <= 0:
        raise DomainError("not_a_credit", "Only credits (money received) can be matched to rent")
    return txn


def allocate(db: Session, txn_id: int, allocations: list[dict], *, save_alias: dict | None,
             remainder_not_rent_reason: str | None, actor: str) -> list[RentPayment]:
    """Create confirmed rent payments from a bank credit, split across rent dues/tenancies.

    allocations: [{tenancy_id, rent_due_id | period | hold_as_credit, amount_paise}]
    """
    txn = _get_credit(db, txn_id)
    if txn.review_status == "not_rent":
        raise Conflict("marked_not_rent", "This transaction is marked as not rent. Reopen it first.")
    if not allocations:
        raise DomainError("no_allocations", "Choose at least one rent month")
    already = linked_total(db, txn.id)
    total = 0
    by_tenancy: dict[int, list[tuple[int | None, int]]] = defaultdict(list)
    for a in allocations:
        tid = int(a["tenancy_id"])
        t = get_or_404(db, Tenancy, tid, "Tenancy")
        amt = int(a["amount_paise"])
        if amt <= 0:
            raise DomainError("invalid_amount", "Allocated amounts must be positive")
        total += amt
        if a.get("hold_as_credit"):
            by_tenancy[tid].append((None, amt))
            continue
        due_id = a.get("rent_due_id")
        if due_id is None:
            period = a.get("period")
            if not period:
                raise DomainError("missing_period", "Each allocation needs a rent month")
            ensure_dues(db, period, [tid])
            due = db.scalar(select(RentDue).where(RentDue.tenancy_id == tid, RentDue.period == period,
                                                  RentDue.kind == "rent"))
            if due is None:
                raise DomainError("no_due", f"No rent is due for {t.flat.label} in {period}")
            due_id = due.id
        by_tenancy[tid].append((int(due_id), amt))
    if already + total > txn.credit_paise:
        raise Conflict("over_allocated_credit",
                       f"Allocations exceed the bank credit. Credit {txn.credit_paise / 100:.2f}, already allocated "
                       f"{already / 100:.2f}, this allocation {total / 100:.2f}.",
                       {"credit_paise": txn.credit_paise, "already_paise": already, "requested_paise": total})
    sender = extract_sender(txn.narration)
    payments = []
    for tid, items in by_tenancy.items():
        explicit = [(d, a) for d, a in items if d is not None]
        credit_part = sum(a for d, a in items if d is None)
        p = PaymentInput(
            tenancy_id=tid, received_date=txn.txn_date, amount_paise=sum(a for _, a in items), method="online",
            status="confirmed", allocations=explicit or None, reference=txn.reference,
            sender_name=sender.title() if sender else None,
            notes=f"From bank statement: {txn.narration[:300]}",
            excess_handling="hold_as_credit" if credit_part else None,
            excess_note="Held as advance credit at bank reconciliation" if credit_part else None,
            bank_transaction_id=txn.id, extra_audit={"source": "bank_reconciliation"},
        )
        if not explicit:
            # Entire amount held as credit: no rent month.
            p.allocations = None
            p.rent_period = None
            payments.append(_credit_only_payment(db, p, actor))
        else:
            payments.append(record_payment(db, p, actor))
    if save_alias and save_alias.get("alias"):
        add_alias(db, int(save_alias["tenant_id"]), save_alias["alias"], save_alias.get("kind", "sender_name"),
                  actor, source="bank_match")
    if remainder_not_rent_reason:
        txn.remainder_not_rent = True
        txn.review_note = remainder_not_rent_reason.strip()[:500]
    txn.reviewed_by, txn.reviewed_at = actor, utcnow()
    refresh_review_status(db, txn.id)
    audit.record(db, actor, "bank.allocate", "bank_transaction", txn.id, None,
                 {"payments": [p.id for p in payments], "allocations": allocations})
    return payments


def _credit_only_payment(db: Session, p: PaymentInput, actor: str) -> RentPayment:
    pay = RentPayment(tenancy_id=p.tenancy_id, received_date=p.received_date, amount_paise=p.amount_paise,
                      method="online", reference=p.reference, sender_name=p.sender_name, status="confirmed",
                      notes=p.notes, excess_resolution="advance_credit", excess_note=p.excess_note,
                      bank_transaction_id=p.bank_transaction_id, recorded_by=actor, confirmed_by=actor,
                      confirmed_at=utcnow())
    db.add(pay)
    db.flush()
    audit.record(db, actor, "payment.create", "rent_payment", pay.id, None,
                 {**audit.snapshot(pay), "source": "bank_reconciliation", "held_as_credit": True})
    return pay


def link_existing_payment(db: Session, txn_id: int, payment_id: int, actor: str) -> RentPayment:
    """Attach a manually entered online payment to its bank credit (and confirm it)."""
    txn = _get_credit(db, txn_id)
    pay = get_or_404(db, RentPayment, payment_id, "Payment")
    if pay.status not in ACTIVE:
        raise Conflict("invalid_state", "That payment is rejected or reversed")
    if pay.bank_transaction_id is not None:
        raise Conflict("already_linked", "That payment is already linked to a bank transaction")
    if linked_total(db, txn.id) + pay.amount_paise > txn.credit_paise:
        raise Conflict("over_allocated_credit", "The payment amount exceeds what remains of this credit")
    if pay.method != "online":
        raise Conflict("not_online", "Only online payments can be linked to a bank credit")
    before = audit.snapshot(pay)
    pay.bank_transaction_id = txn.id
    if pay.status == "pending":
        pay.status, pay.confirmed_by, pay.confirmed_at = "confirmed", actor, utcnow()
    txn.reviewed_by, txn.reviewed_at = actor, utcnow()
    refresh_review_status(db, txn.id)
    audit.record(db, actor, "bank.link_payment", "rent_payment", pay.id, before, audit.snapshot(pay))
    return pay


def mark_not_rent(db: Session, txn_id: int, reason: str | None, actor: str) -> BankTransaction:
    txn = _get_credit(db, txn_id)
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise DomainError("reason_required", "Say what this credit is (e.g. refund, transfer from savings)")
    if linked_total(db, txn.id) > 0:
        raise Conflict("has_allocations", "Part of this credit is already allocated to rent. Use 'remaining amount "
                                          "is not rent' instead, or reverse those allocations first.")
    before = audit.snapshot(txn)
    txn.review_status, txn.review_note, txn.reviewed_by, txn.reviewed_at = "not_rent", reason[:500], actor, utcnow()
    audit.record(db, actor, "bank.not_rent", "bank_transaction", txn.id, before, audit.snapshot(txn), reason=reason)
    return txn


def mark_remainder_not_rent(db: Session, txn_id: int, reason: str | None, actor: str) -> BankTransaction:
    txn = _get_credit(db, txn_id)
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise DomainError("reason_required", "Say what the remaining amount is")
    if linked_total(db, txn.id) == 0:
        return mark_not_rent(db, txn_id, reason, actor)
    before = audit.snapshot(txn)
    txn.remainder_not_rent, txn.review_note = True, reason[:500]
    refresh_review_status(db, txn.id)
    audit.record(db, actor, "bank.remainder_not_rent", "bank_transaction", txn.id, before, audit.snapshot(txn))
    return txn


def reopen(db: Session, txn_id: int, actor: str) -> BankTransaction:
    """Undo a 'not rent' decision. Allocated payments are reversed separately (with a reason)."""
    txn = _get_credit(db, txn_id)
    before = audit.snapshot(txn)
    if txn.review_status == "not_rent":
        txn.review_status = "unreviewed"
    txn.remainder_not_rent = False
    txn.review_note = None
    refresh_review_status(db, txn.id)
    audit.record(db, actor, "bank.reopen", "bank_transaction", txn.id, before, audit.snapshot(txn))
    return txn


# -------------------------------------------------------------- read models


def describe_txn(db: Session, txn: BankTransaction, ctx: MatchContext | None = None) -> dict:
    pays = db.scalars(select(RentPayment).where(RentPayment.bank_transaction_id == txn.id)
                      .order_by(RentPayment.id)).all()
    linked = sum(p.amount_paise for p in pays if p.status in ACTIVE)
    d = {
        "id": txn.id, "txn_date": txn.txn_date.isoformat(),
        "value_date": txn.value_date.isoformat() if txn.value_date else None,
        "narration": txn.narration, "reference": txn.reference, "debit_paise": txn.debit_paise,
        "credit_paise": txn.credit_paise, "balance_paise": txn.balance_paise, "review_status": txn.review_status,
        "review_note": txn.review_note, "remainder_not_rent": txn.remainder_not_rent,
        "collision_flag": txn.collision_flag, "allocated_paise": linked,
        "remaining_paise": 0 if (txn.review_status == "not_rent" or txn.remainder_not_rent)
        else max(0, txn.credit_paise - linked),
        "sender": extract_sender(txn.narration),
        "payments": [{"id": p.id, "tenancy_id": p.tenancy_id, "tenant_name": p.tenancy.tenant.full_name,
                      "flat_label": p.tenancy.flat.label, "amount_paise": p.amount_paise, "status": p.status,
                      "periods": sorted({a.rent_due.period for a in p.allocations}),
                      "excess_resolution": p.excess_resolution} for p in pays],
        "import_ids": list(db.scalars(select(BankTransactionSource.import_id)
                                      .where(BankTransactionSource.bank_transaction_id == txn.id))),
    }
    if ctx is not None and txn.credit_paise and d["remaining_paise"] > 0:
        d["suggestion"] = ctx.suggest_for(txn.txn_date, txn.narration, txn.reference, d["remaining_paise"],
                                          exclude_txn_id=txn.id)
    return d


def list_transactions(db: Session, *, status: str | None, import_id: int | None, start: dt.date | None,
                      end: dt.date | None, kind: str | None, with_suggestions: bool) -> list[dict]:
    q = select(BankTransaction)
    if import_id:
        q = q.join(BankTransactionSource, BankTransactionSource.bank_transaction_id == BankTransaction.id).where(
            BankTransactionSource.import_id == import_id)
    if status == "needs_review":
        q = q.where(BankTransaction.review_status.in_(("unreviewed", "partially_allocated")))
    elif status:
        q = q.where(BankTransaction.review_status == status)
    if kind == "credit":
        q = q.where(BankTransaction.credit_paise > 0)
    elif kind == "debit":
        q = q.where(BankTransaction.debit_paise > 0)
    if start:
        q = q.where(BankTransaction.txn_date >= start)
    if end:
        q = q.where(BankTransaction.txn_date <= end)
    txns = db.scalars(q.order_by(BankTransaction.txn_date, BankTransaction.id).limit(2000)).all()
    ctx = MatchContext(db) if with_suggestions else None
    return [describe_txn(db, t, ctx) for t in txns]


def summary(db: Session, *, import_id: int | None = None, start: dt.date | None = None,
            end: dt.date | None = None) -> dict:
    txns = list_transactions(db, status=None, import_id=import_id, start=start, end=end, kind=None,
                             with_suggestions=False)
    credits = [t for t in txns if t["credit_paise"] > 0]
    confirmed_alloc = 0
    pending_alloc = 0
    for t in credits:
        for p in t["payments"]:
            if p["status"] == "confirmed":
                confirmed_alloc += p["amount_paise"]
            elif p["status"] == "pending":
                pending_alloc += p["amount_paise"]
    not_rent = sum(t["credit_paise"] for t in credits if t["review_status"] == "not_rent")
    not_rent += sum(t["credit_paise"] - t["allocated_paise"] for t in credits
                    if t["remainder_not_rent"] and t["review_status"] != "not_rent")
    awaiting = [t for t in credits if t["review_status"] in ("unreviewed", "partially_allocated")]
    return {
        "transactions": len(txns),
        "credit_count": len(credits),
        "debit_count": len(txns) - len(credits),
        "total_credits_paise": sum(t["credit_paise"] for t in credits),
        "total_debits_paise": sum(t["debit_paise"] for t in txns),
        "allocated_to_rent_confirmed_paise": confirmed_alloc,
        "allocated_to_rent_pending_paise": pending_alloc,
        "awaiting_review_count": len(awaiting),
        "awaiting_review_paise": sum(t["remaining_paise"] for t in awaiting),
        "unmatched_count": sum(1 for t in credits if t["review_status"] == "unreviewed"),
        "not_rent_paise": not_rent,
        "not_rent_count": sum(1 for t in credits if t["review_status"] == "not_rent"),
        "collision_count": sum(1 for t in txns if t["collision_flag"]),
    }


def list_imports(db: Session) -> list[dict]:
    return [{
        "id": i.id, "original_name": i.original_name, "uploaded_at": i.uploaded_at.isoformat(),
        "uploaded_by": i.uploaded_by, "rows_total": i.rows_total, "rows_new": i.rows_new,
        "rows_duplicate": i.rows_duplicate, "rows_error": i.rows_error, "sheet_name": i.sheet_name,
        "period_start": i.period_start.isoformat() if i.period_start else None,
        "period_end": i.period_end.isoformat() if i.period_end else None, "document_id": i.document_id,
        **(json.loads(i.statement_checks_json) if i.statement_checks_json else {"statement": None, "checks": []}),
    } for i in db.scalars(select(BankImport).order_by(BankImport.uploaded_at.desc()))]
