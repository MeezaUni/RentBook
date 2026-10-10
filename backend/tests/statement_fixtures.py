"""Synthetic HDFC statements that reproduce the *layout* of a real HDFC legacy-XLS export.

The structure (metadata block, star separators, header position, text dd/mm/yy dates, numeric
amounts with empty cells, 16-character text references with leading zeros, value-date boundary
rows, interest credit, "STATEMENT SUMMARY" footer with totals and Dr/Cr counts) was taken from
a real statement. Every value here is invented: no real names, accounts, addresses or amounts.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

FAKE_ACCOUNT_NO = "50100987654321"
FAKE_CUST_ID = "88776655"
FAKE_EMAIL = "holder@example.invalid"
HEADER = ["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]
STARS = "*" * 10


@dataclass
class Txn:
    date: str            # dd/mm/yy text, as HDFC writes it
    narration: str
    ref: str             # text, keeps leading zeros
    value_date: str
    debit: float | None  # rupees, numeric cell (or empty)
    credit: float | None


OPENING = 25000.00  # invented

TXNS: list[Txn] = [
    # Boundary: transaction dated the day before the period, value date inside it.
    Txn("30/06/26", "UPI-SHOPKEEPER ONE-shop1@okaxis-UTIB0000001-618100000001-UPI", "0000618100000001", "01/07/26", 250, None),
    Txn("02/07/26", "UPI-RAVI SAMPLE-ravi.sample@okicici-ICIC0000002-618300000002-RENT JULY", "0000618300000002", "02/07/26", None, 12000),
    Txn("02/07/26", "UPI-MILK SUPPLIER-milk@ybl-YESB0000003-618300000003-UPI", "0000618300000003", "02/07/26", 1000, None),
    Txn("05/07/26", "NEFT CR-SBIN0000004-MEERA FAMILYMEMBER-RENT-SBINN52026070500004", "SBINN52026070500", "05/07/26", None, 9500),
    Txn("05/07/26", "UPI-MILK SUPPLIER-milk@ybl-YESB0000003-618600000005-UPI", "0000618600000005", "05/07/26", 1000, None),
    Txn("10/07/26", "IMPS P2A CHARGES 0#0/0/0-GST0", "MIR2619100000006", "10/07/26", 5.9, None),
    Txn("01/08/26", "UPI-RAVI SAMPLE-ravi.sample@okicici-ICIC0000002-621300000007-RENT AUG", "0000621300000007", "01/08/26", None, 12000),
    Txn("01/08/26", "UPI-RAVI SAMPLE-ravi.sample@okicici-ICIC0000002-621300000008-RENT AUG", "0000621300000008", "01/08/26", None, 12000),
    Txn("14/08/26", "IMPS-622600000009-ELECTRIC SHOP-HDFC-XXXXXXX1234-WIRES", "0000622600000009", "14/08/26", 3200.5, None),
    Txn("03/09/26", "UPI-SOMEONE ELSE-someone@paytm-PYTM0000010-624600000010-GIFT", "0000624600000010", "03/09/26", None, 500),
    # Boundary: interest credited the day after the period, value date on its last day.
    Txn("01/10/26", "INTEREST PAID TILL 30-SEP-2026", "000000000000000", "30/09/26", None, 121),
]


def _balances(txns: list[Txn], opening: float) -> list[float]:
    out, bal = [], round(opening * 100)
    for t in txns:
        bal += round((t.credit or 0) * 100) - round((t.debit or 0) * 100)
        out.append(bal / 100)
    return out


def _summary(txns: list[Txn], opening: float) -> dict:
    debits = sum(round((t.debit or 0) * 100) for t in txns) / 100
    credits = sum(round((t.credit or 0) * 100) for t in txns) / 100
    return {"opening": opening, "debits": debits, "credits": credits,
            "closing": round(opening * 100 + credits * 100 - debits * 100) / 100,
            "dr": sum(1 for t in txns if t.debit), "cr": sum(1 for t in txns if t.credit)}


def hdfc_rows(txns: list[Txn] | None = None, opening: float = OPENING, summary: dict | None = None) -> list[list]:
    """Cell matrix in the same positions as the real export (124-row layout scaled down)."""
    txns = TXNS if txns is None else txns
    s = summary or _summary(txns, opening)
    rows: list[list] = [
        ["HDFC BANK Ltd.          Page No .: 1          Statement of accounts"] + [""] * 6,
        [""] * 7, [""] * 7, [""] * 7,
        ["", "", "", "", "Account Branch : SAMPLE BRANCH", "", ""],
        ["MR SAMPLE ACCOUNT HOLDER", "", "", "", "Address : 1 SAMPLE ROAD", "", ""],
        ["1 SAMPLE ROAD", "", "", "", "City : SAMPLETOWN", "", ""],
        ["SAMPLETOWN 560000", "", "", "", f"Email : {FAKE_EMAIL}", "", ""],
        ["JOINT HOLDERS :", "", "", "", f"Cust ID : {FAKE_CUST_ID}", "", ""],
        ["", "", "", "", f"Account No : {FAKE_ACCOUNT_NO}", "", ""],
        ["Statement From : 01/07/2026  To : 30/09/2026", "", "", "", "A/C Open Date : 01/01/2020", "", ""],
        [STARS] + [""] * 6,
        HEADER,
        [STARS] * 7,
    ]
    for t, bal in zip(txns, _balances(txns, opening)):
        rows.append([t.date, t.narration, t.ref, t.value_date, "" if t.debit is None else t.debit,
                     "" if t.credit is None else t.credit, bal])
    rows += [
        [""] * 7,
        [STARS] * 7,
        [STARS] + [""] * 6,
        [""] * 7,
        ["STATEMENT SUMMARY  :-"] + [""] * 6,
        ["Opening Balance", "", "", "", "Debits", "Credits", "Closing Bal"],
        [s["opening"], "", "", "", s["debits"], s["credits"], s["closing"]],
        [""] * 7,
        ["", "", "", "", "Dr Count", "Cr Count", ""],
        ["", "", "", "", s["dr"], s["cr"], ""],
        [""] * 7,
        ["Generated On:", "10-Oct-2026 10:00:00", "Generated By:", FAKE_CUST_ID, "Requesting Branch Code:", "NET", ""],
        ["This is a computer generated statement", "", "", "", "", "", ""],
    ]
    return rows


def to_xls(rows: list[list]) -> bytes:
    """Write a genuine BIFF8 .xls (same container as HDFC's download) using xlwt."""
    import xlwt

    wb = xlwt.Workbook()
    ws = wb.add_sheet("Sheet 1")
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            if v != "":
                ws.write(i, j, v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_csv(rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    for row in rows:
        w.writerow(["" if v == "" else repr(v) if isinstance(v, float) else v for v in row])
    return buf.getvalue().encode()


def hdfc_xls(**kw) -> bytes:
    return to_xls(hdfc_rows(**kw))


def table_only(txns: list[Txn] | None = None, opening: float = OPENING) -> list[list]:
    """Just the header and transaction rows: the shape of a plain CSV export (header on row 0)."""
    txns = TXNS if txns is None else txns
    return [HEADER] + [[t.date, t.narration, t.ref, t.value_date, "" if t.debit is None else t.debit,
                        "" if t.credit is None else t.credit, bal] for t, bal in zip(txns, _balances(txns, opening))]
