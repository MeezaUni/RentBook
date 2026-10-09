"""Bank statement import & reconciliation (spec section 19: bank tests 1-11).

Statements are synthetic, HDFC-style files with a preamble, header, separator row,
transactions and a summary footer.
"""

from __future__ import annotations

import datetime as dt
import io
import json

import pytest
from sqlalchemy import func, select

from app.models import BankTransaction, RentPayment
from app.services import property as prop
from app.services import rent

from .conftest import make_tenancy

K = 1_000_00
HEADER = "Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance"
PREAMBLE = ["HDFC BANK Ltd.,,,,,,", "Statement of account,,,,,,", "Account No :,XXXXXXXX1234,,,,,", ",,,,,,"]
FOOTER = [",,,,,,", "STATEMENT SUMMARY :-,,,,,,", "Opening Balance,Dr Count,Cr Count,Debits,Credits,Closing Bal,",
          '"1,00,000.00",1,3,"2,500.00","40,000.00","1,37,500.00",']

ROWS = {
    "a": '01/11/25,UPI-RAVI SAMPLE-ravi@okaxis-HDFC0000001-530500000001-RENT NOV,0000530500000001,01/11/25,,"10,000.00","1,10,000.00"',
    "b": '03/11/25,NEFT CR-SBIN0000001-MEERA FAMILYMEMBER-RENT-SBINN000000002,SBINN000000002,03/11/25,,"20,000.00","1,30,000.00"',
    "c": '05/11/25,POS 4000XXXX GROCERY STORE,0000000000000000,05/11/25,"2,500.00",,"1,27,500.00"',
    "d": '07/11/25,IMPS-530500000009-REFUND FROM SHOP-XXXX-REFUND,530500000009,07/11/25,,"10,000.00","1,37,500.00"',
    "e": '10/11/25,UPI-RAVI SAMPLE-ravi@okaxis-HDFC0000001-530500000010-RENT,0000530500000010,10/11/25,,"10,000.00","1,47,500.00"',
}


def statement(*keys: str) -> bytes:
    lines = PREAMBLE + [HEADER, "********,********,********,********,********,********,********"]
    lines += [ROWS[k] for k in keys] + FOOTER
    return ("\n".join(lines) + "\n").encode()


def preview(client, data: bytes, name="stmt.csv", **form):
    return client.post("/api/bank/preview", files={"file": (name, data)}, data=form)


def commit(client, data: bytes, mapping: dict, name="stmt.csv", **form):
    return client.post("/api/bank/imports", files={"file": (name, data)},
                       data={"mapping": json.dumps(mapping), **form})


def import_statement(client, data: bytes, name="stmt.csv"):
    p = preview(client, data, name)
    assert p.status_code == 200, p.text
    r = commit(client, data, p.json()["mapping"], name)
    assert r.status_code == 200, r.text
    return r.json()


def txn_by_ref(db, ref: str) -> BankTransaction:
    return db.scalar(select(BankTransaction).where(BankTransaction.reference == ref))


# 1
def test_valid_statement_is_parsed(client):
    r = preview(client, statement("a", "b", "c"))
    assert r.status_code == 200, r.text
    body = r.json()
    s = body["summary"]
    assert (s["detected"], s["credits"], s["debits"], s["errors"]) == (3, 2, 1, 0)
    assert s["credit_total_paise"] == 30 * K and s["debit_total_paise"] == 250_000
    assert s["period_start"] == "2025-11-01" and s["period_end"] == "2025-11-05"
    assert body["mapping"]["columns"] == {"date": 0, "narration": 1, "reference": 2, "value_date": 3, "debit": 4,
                                          "credit": 5, "balance": 6}
    row = body["rows"][0]
    assert row["reference"] == "0000530500000001" and row["balance_paise"] == 1_10_000_00


def test_xlsx_statement_is_parsed(client):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Statement"
    ws.append(["HDFC BANK Ltd."])
    ws.append([])
    ws.append(["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"])
    ws.append([dt.datetime(2025, 11, 1), "UPI-RAVI SAMPLE-ravi@okaxis-X-1-RENT", "530500000001",
               dt.datetime(2025, 11, 1), None, 10000, 110000])
    ws.append([dt.datetime(2025, 11, 2), "ATM WDL", "0", dt.datetime(2025, 11, 2), 1500.5, None, 108499.5])
    wb.create_sheet("Other")
    buf = io.BytesIO()
    wb.save(buf)
    r = preview(client, buf.getvalue(), "stmt.xlsx")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["sheets"] == ["Statement", "Other"]
    assert b["summary"]["credits"] == 1 and b["summary"]["debit_total_paise"] == 1500_50


# 2
def test_malformed_statement_gives_useful_errors(client):
    r = preview(client, b"\x89PNG\r\n\x1a\n" + b"0" * 100, "stmt.csv")
    assert r.status_code == 415 and "Unsupported file type" in r.json()["error"]["message"]
    r = preview(client, b"just,some,text\n1,2,3\n")
    assert r.status_code == 200 and r.json()["mapping"]["valid"] is False
    r = commit(client, b"just,some,text\n1,2,3\n", {"header_row": 0, "columns": {}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_mapping"
    bad = statement("a").replace(b'"10,000.00"', b'"10,0x0.00"')
    r = preview(client, bad)
    assert r.json()["summary"]["errors"] == 1 and "Not a valid amount" in r.json()["errors"][0]["messages"][0]
    r = commit(client, bad, r.json()["mapping"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "rows_with_errors"


# 3 & 4
def test_same_and_overlapping_statements_do_not_duplicate(client, db):
    first = import_statement(client, statement("a", "b", "c"))
    assert first["rows_new"] == 3
    again = import_statement(client, statement("a", "b", "c"))
    assert again["rows_new"] == 0 and again["rows_duplicate"] == 3
    overlap = import_statement(client, statement("b", "c", "d", "e"))
    assert overlap["rows_new"] == 2 and overlap["rows_duplicate"] == 2
    assert db.scalar(select(func.count(BankTransaction.id))) == 5
    p = preview(client, statement("a", "b")).json()
    assert p["summary"]["already_imported"] == 2 and len(p["file_previously_imported"]) == 0


# 5
def test_sender_alias_produces_strong_candidate(client, db):
    ten = make_tenancy(db, name="Ravi Sample")
    meera_ten = make_tenancy(db, name="Test Tenant B", label="P-G2", rent=20 * K)
    prop.add_alias(db, meera_ten.tenant_id, "Meera Familymember", "sender_name", "tester")
    db.commit()
    import_statement(client, statement("a", "b"))
    txns = client.get("/api/bank/transactions?status=needs_review&suggestions=true").json()
    a = next(t for t in txns if t["reference"] == "0000530500000001")
    assert a["suggestion"]["category"] == "strong"
    assert a["suggestion"]["candidates"][0]["tenancy_id"] == ten.id
    b = next(t for t in txns if t["reference"] == "SBINN000000002")
    top = b["suggestion"]["candidates"][0]
    assert b["suggestion"]["category"] == "strong" and top["tenancy_id"] == meera_ten.id
    assert any("alias" in r for r in top["reasons"])


# 6
def test_ambiguous_transaction_is_not_confirmed_automatically(client, db):
    t1 = make_tenancy(db, name="Test Tenant A", label="P-G1")
    t2 = make_tenancy(db, name="Test Tenant B", label="P-G2")
    for t in (t1, t2):  # the same relative pays for both flats
        prop.add_alias(db, t.tenant_id, "Ravi Sample", "sender_name", "tester")
    db.commit()
    import_statement(client, statement("a"))
    txn = client.get("/api/bank/transactions?status=needs_review&suggestions=true").json()[0]
    assert txn["suggestion"]["category"] == "ambiguous"
    assert db.scalar(select(func.count(RentPayment.id))) == 0
    assert txn["review_status"] == "unreviewed"
    # Allocation needs explicit confirmation.
    r = client.post(f"/api/bank/transactions/{txn['id']}/allocate",
                    json={"allocations": [{"tenancy_id": t1.id, "period": "2025-11", "amount_paise": 10 * K}]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "confirmation_required"


# 7
def test_manual_matching_updates_rent_due_and_saves_alias(client, db):
    ten = make_tenancy(db, name="Unrelated Name")
    import_statement(client, statement("a"))
    txn = txn_by_ref(db, "0000530500000001")
    r = client.post(f"/api/bank/transactions/{txn.id}/allocate", json={
        "allocations": [{"tenancy_id": ten.id, "period": "2025-11", "amount_paise": 10 * K}], "confirm": True,
        "save_alias": {"tenant_id": ten.tenant_id, "alias": "RAVI SAMPLE"}})
    assert r.status_code == 200, r.text
    d = rent.describe_dues(db, [rent.get_due_for(db, db.get(type(ten), ten.id), "2025-11")])[0]
    assert d["status"] == "paid"
    pay = db.get(RentPayment, r.json()["payment_ids"][0])
    assert pay.bank_transaction_id == txn.id and pay.status == "confirmed" and pay.received_date == dt.date(2025, 11, 1)
    # The saved alias makes the next statement's payment from the same sender a strong match.
    import_statement(client, statement("e"))
    e = client.get("/api/bank/transactions?status=needs_review&suggestions=true").json()[0]
    assert e["suggestion"]["candidates"][0]["tenancy_id"] == ten.id
    assert e["suggestion"]["candidates"][0]["identity_signal"] == 3


# 8
def test_split_across_two_dues_preserves_original_credit(client, db):
    ten = make_tenancy(db)
    import_statement(client, statement("b"))
    txn = txn_by_ref(db, "SBINN000000002")
    r = client.post(f"/api/bank/transactions/{txn.id}/allocate", json={"confirm": True, "allocations": [
        {"tenancy_id": ten.id, "period": "2025-10", "amount_paise": 10 * K},
        {"tenancy_id": ten.id, "period": "2025-11", "amount_paise": 10 * K}]})
    assert r.status_code == 200, r.text
    body = r.json()["transaction"]
    assert body["credit_paise"] == 20 * K and body["allocated_paise"] == 20 * K
    assert body["review_status"] == "allocated"
    db.expire_all()
    assert txn_by_ref(db, "SBINN000000002").credit_paise == 20 * K
    led = client.get("/api/rent/ledger?period=2025-11").json()
    assert led["summary"]["confirmed_paise"] == 10 * K and led["arrears"][0]["period"] != "2025-10"


def test_split_across_two_flats(client, db):
    t1 = make_tenancy(db, label="P-G1")
    t2 = make_tenancy(db, name="Test Tenant B", label="P-G2")
    import_statement(client, statement("b"))
    txn = txn_by_ref(db, "SBINN000000002")
    r = client.post(f"/api/bank/transactions/{txn.id}/allocate", json={"confirm": True, "allocations": [
        {"tenancy_id": t1.id, "period": "2025-11", "amount_paise": 10 * K},
        {"tenancy_id": t2.id, "period": "2025-11", "amount_paise": 10 * K}]})
    assert r.status_code == 200 and len(r.json()["payment_ids"]) == 2


# 9
def test_non_rent_credit_not_counted(client, db):
    make_tenancy(db)
    import_statement(client, statement("d"))
    txn = txn_by_ref(db, "530500000009")
    r = client.post(f"/api/bank/transactions/{txn.id}/not-rent", json={"reason": "shop refund"})
    assert r.status_code == 200 and r.json()["review_status"] == "not_rent"
    led = client.get("/api/rent/ledger?period=2025-11").json()
    assert led["summary"]["confirmed_paise"] == 0
    s = client.get("/api/bank/summary").json()
    assert s["not_rent_paise"] == 10 * K and s["allocated_to_rent_confirmed_paise"] == 0
    assert s["total_credits_paise"] == 10 * K


# 10
def test_reversing_allocation_restores_balance(client, db):
    ten = make_tenancy(db)
    import_statement(client, statement("a"))
    txn = txn_by_ref(db, "0000530500000001")
    pid = client.post(f"/api/bank/transactions/{txn.id}/allocate", json={"confirm": True, "allocations": [
        {"tenancy_id": ten.id, "period": "2025-11", "amount_paise": 10 * K}]}).json()["payment_ids"][0]
    r = client.post(f"/api/payments/{pid}/void", json={"reason": "matched to wrong tenant"})
    assert r.status_code == 200 and r.json()["status"] == "reversed"
    led = client.get("/api/rent/ledger?period=2025-11").json()
    assert led["summary"]["outstanding_paise"] == 10 * K
    t = client.get(f"/api/bank/transactions/{txn.id}").json()
    assert t["review_status"] == "unreviewed" and t["remaining_paise"] == 10 * K


# 11
def test_allocated_transaction_cannot_be_allocated_twice(client, db):
    ten = make_tenancy(db)
    import_statement(client, statement("a"))
    txn = txn_by_ref(db, "0000530500000001")
    body = {"confirm": True, "allocations": [{"tenancy_id": ten.id, "period": "2025-10", "amount_paise": 10 * K}]}
    assert client.post(f"/api/bank/transactions/{txn.id}/allocate", json=body).status_code == 200
    body["allocations"][0]["period"] = "2025-11"
    r = client.post(f"/api/bank/transactions/{txn.id}/allocate", json=body)
    assert r.status_code == 409 and r.json()["error"]["code"] == "over_allocated_credit"
    # Re-importing the statement does not offer it again either.
    import_statement(client, statement("a"))
    assert client.get("/api/bank/transactions?status=needs_review").json() == []


def test_manual_pending_payment_is_flagged_and_linked_not_duplicated(client, db):
    ten = make_tenancy(db, name="Ravi Sample")
    p = rent.record_payment(db, rent.PaymentInput(tenancy_id=ten.id, received_date=dt.date(2025, 11, 1),
                                                  amount_paise=10 * K, method="online", status="pending",
                                                  rent_period="2025-11"), "tester")
    db.commit()
    import_statement(client, statement("a"))
    t = client.get("/api/bank/transactions?status=needs_review&suggestions=true").json()[0]
    assert t["suggestion"]["category"] == "potential_duplicate"
    assert t["suggestion"]["duplicate_payments"][0]["payment_id"] == p.id
    r = client.post(f"/api/bank/transactions/{t['id']}/link-payment", json={"payment_id": p.id})
    assert r.status_code == 200 and r.json()["review_status"] == "allocated"
    db.expire_all()
    assert db.get(RentPayment, p.id).status == "confirmed"
    assert db.scalar(select(func.count(RentPayment.id))) == 1


def test_overpayment_via_bank_held_as_credit(client, db):
    ten = make_tenancy(db)
    import_statement(client, statement("b"))  # 20,000 credit, rent 10,000
    txn = txn_by_ref(db, "SBINN000000002")
    r = client.post(f"/api/bank/transactions/{txn.id}/allocate", json={"confirm": True, "allocations": [
        {"tenancy_id": ten.id, "period": "2025-11", "amount_paise": 10 * K},
        {"tenancy_id": ten.id, "hold_as_credit": True, "amount_paise": 10 * K}]})
    assert r.status_code == 200, r.text
    pay = rent.describe_payment(db.get(RentPayment, r.json()["payment_ids"][0]))
    assert pay["unallocated_paise"] == 10 * K and pay["excess_resolution"] == "advance_credit"


@pytest.mark.parametrize("value,expected", [("1,00,000.50", 100000_50), ("₹ 500", 500_00), ("(250.00)", -250_00),
                                            ("", None), (1234.56, 1234_56), ("12.5", 12_50)])
def test_money_parsing(value, expected):
    from app.money import parse_rupees

    assert parse_rupees(value) == expected


def test_money_parsing_rejects_fractions_of_paise():
    from app.money import MoneyParseError, parse_rupees

    with pytest.raises(MoneyParseError):
        parse_rupees("10.005")
