"""Regression tests for the HDFC legacy-XLS layout, found while validating a real statement.

Fixtures are synthetic (see statement_fixtures.py) but reproduce the real file's structure.
"""

from __future__ import annotations

import datetime as dt
import json

from app.services import bank, bank_parse

from . import statement_fixtures as fx
from .test_bank import commit, preview


def _prepare(blob: bytes, name: str = "Acct Statement.xls"):
    return bank.prepare(blob, name, None, None)


def test_legacy_xls_layout_is_parsed_like_the_real_statement():
    p = _prepare(fx.hdfc_xls())
    assert p.content_type == "application/vnd.ms-excel"
    assert p.mapping["header_row"] == 12  # metadata block and a star separator come first
    assert p.mapping["columns"] == {"date": 0, "narration": 1, "reference": 2, "value_date": 3, "debit": 4,
                                    "credit": 5, "balance": 6}
    good = [r for r in p.rows if not r.errors]
    assert len(p.rows) == len(good) == len(fx.TXNS)  # separators, summary and footer are not transactions
    assert sum(1 for r in good if r.debit_paise) == 5 and sum(1 for r in good if r.credit_paise) == 6
    first = good[0]
    assert (first.txn_date, first.value_date, first.debit_paise, first.credit_paise) == \
        (dt.date(2026, 6, 30), dt.date(2026, 7, 1), 250_00, 0)
    assert good[1].reference == "0000618300000002"          # text reference keeps leading zeros
    assert good[5].debit_paise == 5_90                       # fractional numeric cell
    assert good[-1].reference is None                       # all-zero reference = no reference
    assert good[-1].narration == "INTEREST PAID TILL 30-SEP-2026"


def test_statement_summary_is_read_and_cross_checked():
    p = _prepare(fx.hdfc_xls())
    info = p.info
    assert (info.period_start, info.period_end) == (dt.date(2026, 7, 1), dt.date(2026, 9, 30))
    assert (info.debit_count, info.credit_count) == (5, 6)
    assert info.opening_paise == 25_000_00
    status = {c["code"]: c["status"] for c in p.checks}
    assert status == {"counts": "ok", "totals": "ok", "balance_equation": "ok", "running_balance": "ok",
                      "boundary_rows": "info"}


def test_boundary_rows_are_kept_and_flagged_by_value_date_rule():
    p = _prepare(fx.hdfc_xls())
    boundary = next(c for c in p.checks if c["code"] == "boundary_rows")
    assert boundary["rows"] == [15, 25]  # 30 Jun (value 1 Jul) and 1 Oct interest (value 30 Sep)
    assert "value date is inside" in boundary["message"]
    flagged = [r.row_number for r in p.rows if bank_parse.is_outside_period(r, p.info)]
    assert flagged == [15, 25]


def test_boundary_row_outside_by_value_date_too_is_a_warning():
    txns = [*fx.TXNS, fx.Txn("02/10/26", "UPI-LATE ONE-late@ybl-YESB0000009-627500000099-X", "0000627500000099",
                              "02/10/26", None, 100)]
    p = _prepare(fx.hdfc_xls(txns=txns))
    boundary = next(c for c in p.checks if c["code"] == "boundary_rows")
    assert boundary["status"] == "warn" and boundary["rows"] == [15, 25, 26]


def test_missing_row_is_detected_by_running_balance_and_counts():
    rows = fx.hdfc_rows()
    del rows[16]  # drop the third transaction but keep the bank's summary
    p = _prepare(fx.to_xls(rows))
    status = {c["code"]: c["status"] for c in p.checks}
    assert status["counts"] == "fail" and status["totals"] == "fail" and status["running_balance"] == "fail"
    rb = next(c for c in p.checks if c["code"] == "running_balance")
    assert rb["rows"] == [17]  # the row after the gap no longer follows on


def test_csv_with_header_on_first_row_has_no_phantom_error_row():
    """Regression: header_row == 0 was treated as 'no header' and parsed as a transaction."""
    p = _prepare(fx.to_csv(fx.table_only()), "export.csv")
    assert p.mapping["header_row"] == 0
    assert [r.row_number for r in p.rows if r.errors] == []
    assert len(p.rows) == len(fx.TXNS)


def test_preview_never_returns_account_holder_details(client):
    body = preview(client, fx.hdfc_xls(), "Acct Statement.xls").json()
    text = json.dumps(body)
    for secret in (fx.FAKE_ACCOUNT_NO, fx.FAKE_CUST_ID, fx.FAKE_EMAIL, "SAMPLE ACCOUNT HOLDER", "1 SAMPLE ROAD"):
        assert secret not in text, secret
    hr = body["mapping"]["header_row"]
    assert all(cell in ("", bank_parse.HIDDEN) for row in body["raw_preview"][:hr] for cell in row)
    assert body["header"] == fx.HEADER
    assert body["statement"]["debit_count"] == 5 and body["checks"]


def test_preview_masks_details_when_no_header_is_detected():
    rows = [["Account No : 50100987654321", "mail holder@example.invalid"], ["random", "text"]]
    out = bank_parse.redact_preview_rows(rows, None, None, str)
    assert "50100987654321" not in str(out) and "holder@example.invalid" not in str(out)


def test_xls_and_csv_of_the_same_statement_do_not_duplicate(client):
    xls = fx.hdfc_xls()
    first = commit(client, xls, preview(client, xls, "a.xls").json()["mapping"], "a.xls").json()
    assert first["rows_new"] == len(fx.TXNS)
    csv_blob = fx.to_csv(fx.table_only())
    again = commit(client, csv_blob, preview(client, csv_blob, "a.csv").json()["mapping"], "a.csv").json()
    assert again["rows_new"] == 0 and again["rows_duplicate"] == len(fx.TXNS)


def test_overlapping_statements_from_real_layout(client):
    early = fx.hdfc_xls(txns=fx.TXNS[:7])
    late_txns = fx.TXNS[4:]
    opening_late = fx._balances(fx.TXNS[:4], fx.OPENING)[-1]
    late = fx.hdfc_xls(txns=late_txns, opening=opening_late)
    a = commit(client, early, preview(client, early, "q1.xls").json()["mapping"], "q1.xls").json()
    b = commit(client, late, preview(client, late, "q2.xls").json()["mapping"], "q2.xls").json()
    assert (a["rows_new"], b["rows_new"], b["rows_duplicate"]) == (7, 4, 3)


def test_import_stores_statement_checks(client):
    xls = fx.hdfc_xls()
    commit(client, xls, preview(client, xls, "a.xls").json()["mapping"], "a.xls")
    imp = client.get("/api/bank/imports").json()[0]
    assert imp["statement"]["credit_count"] == 6
    assert {c["code"] for c in imp["checks"]} >= {"counts", "running_balance", "boundary_rows"}


def test_debits_never_enter_the_rent_review_queue(client):
    xls = fx.hdfc_xls()
    commit(client, xls, preview(client, xls, "a.xls").json()["mapping"], "a.xls")
    queue = client.get("/api/bank/transactions?status=needs_review").json()
    assert len(queue) == 6 and all(t["credit_paise"] > 0 and t["debit_paise"] == 0 for t in queue)


def test_sender_extraction_on_real_narration_shapes():
    assert bank.extract_sender(fx.TXNS[1].narration) == "RAVI SAMPLE"
    assert bank.extract_sender(fx.TXNS[3].narration) == "MEERA FAMILYMEMBER"
    assert bank.extract_sender(fx.TXNS[8].narration) == "ELECTRIC SHOP"
    assert bank.extract_sender(fx.TXNS[5].narration) is None   # bank charge line
    assert bank.extract_sender(fx.TXNS[-1].narration) is None  # interest credit
