"""Deterministic finance calculations. All money is Decimal; the model never supplies totals.

Internal sign convention: signed amounts are **debit-positive**.
"""
from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import combinations
from typing import Any, Iterable

TWO = Decimal("0.01")
ALLOWED_VOUCHER_TYPES = ("Payment", "Receipt", "Contra", "Journal")


def money(v: Any) -> Decimal:
    if v is None or v == "":
        return Decimal("0.00")
    if isinstance(v, float):
        v = repr(v)
    s = str(v).strip().replace(",", "").replace("₹", "").replace("INR", "").strip()
    neg = False
    if s.endswith(("Dr", "DR", "dr")):
        s = s[:-2].strip()
    elif s.endswith(("Cr", "CR", "cr")):
        s, neg = s[:-2].strip(), True
    if s.startswith("(") and s.endswith(")"):
        s, neg = s[1:-1], not neg
    try:
        d = Decimal(s)
    except InvalidOperation as e:
        raise ValueError(f"not an amount: {v!r}") from e
    d = d.quantize(TWO, rounding=ROUND_HALF_UP)
    return -d if neg else d


def fmt(d: Decimal) -> str:
    return str(d.quantize(TWO, rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------- trial balance

def trial_balance_summary(ledgers: list[dict]) -> dict:
    """ledgers: [{name, parent, closing}] closing debit-positive."""
    total_dr = sum((money(l["closing"]) for l in ledgers if money(l["closing"]) > 0), Decimal("0.00"))
    total_cr = sum((-money(l["closing"]) for l in ledgers if money(l["closing"]) < 0), Decimal("0.00"))
    groups: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    rows = []
    for l in sorted(ledgers, key=lambda x: (x.get("parent") or "", x["name"])):
        c = money(l["closing"])
        if c == 0:
            continue
        groups[l.get("parent") or "(none)"] += c
        rows.append({"ledger": l["name"], "group": l.get("parent") or "",
                     "debit": fmt(c) if c > 0 else "", "credit": fmt(-c) if c < 0 else ""})
    diff = total_dr - total_cr
    return {
        "rows": rows,
        "groups": [{"group": g, "net_debit": fmt(v)} for g, v in sorted(groups.items())],
        "total_debit": fmt(total_dr), "total_credit": fmt(total_cr), "difference": fmt(diff),
        "tie_out": "balanced" if diff == 0 else "NOT BALANCED",
    }


# ---------------------------------------------------------------- ageing

BUCKETS = ((0, 30), (31, 60), (61, 90), (91, 180), (181, None))


def bucket_label(lo: int, hi: int | None) -> str:
    return f"{lo}-{hi}" if hi is not None else f">{lo - 1}"


def ageing(bills: list[dict], as_of: date, kind: str = "receivable", min_days: int = 0) -> dict:
    """bills: [{party, ref, bill_date (iso), due_date?, amount}] amount debit-positive.

    receivable -> keeps debit balances; payable -> keeps credit balances (shown positive).
    Age measured from due date when present, else bill date.
    """
    sign = Decimal(1) if kind == "receivable" else Decimal(-1)
    buckets = {bucket_label(lo, hi): Decimal("0.00") for lo, hi in BUCKETS}
    parties: dict[str, dict] = {}
    detail = []
    for b in bills:
        amt = money(b["amount"]) * sign
        if amt <= 0:
            continue
        ref_date = b.get("due_date") or b["bill_date"]
        days = (as_of - date.fromisoformat(ref_date)).days
        if days < min_days:
            continue
        label = next(bucket_label(lo, hi) for lo, hi in BUCKETS if days >= lo and (hi is None or days <= hi)) \
            if days >= 0 else "not due"
        buckets.setdefault(label, Decimal("0.00"))
        buckets[label] += amt
        p = parties.setdefault(b["party"], {"party": b["party"], "total": Decimal("0.00"), "oldest_days": 0, "bills": 0})
        p["total"] += amt
        p["bills"] += 1
        p["oldest_days"] = max(p["oldest_days"], days)
        detail.append({"party": b["party"], "ref": b.get("ref", ""), "bill_date": b["bill_date"],
                       "days": days, "bucket": label, "amount": fmt(amt)})
    total = sum(buckets.values(), Decimal("0.00"))
    return {
        "kind": kind, "as_of": as_of.isoformat(), "min_days": min_days,
        "buckets": [{"bucket": k, "amount": fmt(v)} for k, v in buckets.items()],
        "parties": sorted(({**p, "total": fmt(p["total"])} for p in parties.values()),
                          key=lambda p: -Decimal(p["total"])),
        "detail": sorted(detail, key=lambda d: -d["days"]),
        "total": fmt(total),
    }


# ---------------------------------------------------------------- vouchers

def validate_voucher(v: dict, known_ledgers: Iterable[str] | None = None) -> list[str]:
    """Returns list of errors (empty == valid). v = {voucher_type, date, ref, narration,
    entries: [{ledger, side: 'dr'|'cr', amount: str}]}"""
    errs: list[str] = []
    if v.get("voucher_type") not in ALLOWED_VOUCHER_TYPES:
        errs.append(f"voucher type not allowed: {v.get('voucher_type')}")
    try:
        date.fromisoformat(v.get("date", ""))
    except ValueError:
        errs.append("invalid date")
    entries = v.get("entries") or []
    if len(entries) < 2:
        errs.append("voucher needs at least two entries")
    dr = cr = Decimal("0.00")
    known = set(known_ledgers) if known_ledgers is not None else None
    for e in entries:
        try:
            a = money(e.get("amount"))
        except ValueError:
            errs.append(f"bad amount for {e.get('ledger')}")
            continue
        if a <= 0:
            errs.append(f"amount must be positive for {e.get('ledger')}")
        if str(e.get("amount")) != fmt(a):
            errs.append(f"amount must have exactly 2 decimals for {e.get('ledger')}")
        if e.get("side") == "dr":
            dr += a
        elif e.get("side") == "cr":
            cr += a
        else:
            errs.append("side must be dr or cr")
        if not e.get("ledger"):
            errs.append("missing ledger")
        elif known is not None and e["ledger"] not in known:
            errs.append(f"ledger does not exist in Tally: {e['ledger']}")
    if dr != cr:
        errs.append(f"voucher not balanced: dr {fmt(dr)} != cr {fmt(cr)}")
    if not v.get("ref"):
        errs.append("missing correlation reference")
    return errs


def voucher_matches(expected: dict, found: dict) -> list[str]:
    """Compare a read-back voucher with what we intended. Returns differences."""
    diffs = []
    for k in ("voucher_type", "date", "ref"):
        if str(expected.get(k)) != str(found.get(k)):
            diffs.append(f"{k}: expected {expected.get(k)!r} got {found.get(k)!r}")
    def norm(es):
        return sorted((e["ledger"], e["side"], fmt(money(e["amount"]))) for e in es)
    if norm(expected["entries"]) != norm(found.get("entries", [])):
        diffs.append("ledger entries differ")
    return diffs


# ---------------------------------------------------------------- bank statements

DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d/%m/%y",
                "%d-%m-%y", "%m/%d/%Y", "%d.%m.%Y", "%d-%b-%y")

COLUMN_HINTS = {
    "date": ("txn date", "transaction date", "value date", "date", "tran date"),
    "narration": ("narration", "description", "particulars", "remarks", "details"),
    "reference": ("chq", "cheque", "ref", "reference", "utr"),
    "debit": ("withdrawal", "debit", "dr amount", "withdrawals", "paid out"),
    "credit": ("deposit", "credit", "cr amount", "deposits", "paid in"),
    "amount": ("amount",),
    "balance": ("balance", "closing balance"),
}


@dataclass
class Statement:
    mapping: dict[str, int]
    headers: list[str]
    date_format: str
    rows: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    needs_confirmation: list[str] = field(default_factory=list)
    opening_balance: str | None = None
    closing_balance: str | None = None


def read_table(content: bytes, filename: str) -> list[list[str]]:
    if filename.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl  # formulas are not evaluated: data_only reads cached values only
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        out = []
        for r in ws.iter_rows(values_only=True):
            out.append(["" if c is None else (c.strftime("%Y-%m-%d") if isinstance(c, (datetime, date)) else str(c))
                        for c in r])
        return out
    text = content.decode("utf-8-sig", errors="replace")
    return [r for r in csv.reader(io.StringIO(text))]


def _find_header(table: list[list[str]]) -> int:
    for i, row in enumerate(table[:30]):
        low = [c.strip().lower() for c in row]
        if any(any(h in c for h in COLUMN_HINTS["date"]) for c in low) and \
           any(any(h in c for h in COLUMN_HINTS["narration"] + COLUMN_HINTS["amount"] + COLUMN_HINTS["debit"]) for c in low):
            return i
    raise ValueError("could not find a header row with date and amount columns")


def detect_mapping(headers: list[str]) -> dict[str, int]:
    low = [h.strip().lower() for h in headers]
    mapping: dict[str, int] = {}
    for key in ("date", "narration", "reference", "debit", "credit", "balance", "amount"):
        for hint in COLUMN_HINTS[key]:
            for i, h in enumerate(low):
                if i in mapping.values():
                    continue
                if hint == h or (hint in h and not (key == "amount" and ("balance" in h))):
                    if key == "date" and "value" in h and any("txn" in x or "transaction" in x for x in low):
                        continue
                    mapping[key] = i
                    break
            if key in mapping:
                break
    return mapping


def _parse_date(s: str, fmt_: str) -> date:
    s = s.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}( 00:00:00)?$", s):
        return date.fromisoformat(s[:10])
    return datetime.strptime(s, fmt_).date()


def detect_date_format(values: list[str]) -> tuple[str | None, bool]:
    """Returns (format, ambiguous)."""
    vals = [v.strip() for v in values if v and v.strip()]
    ok = []
    for f in DATE_FORMATS:
        try:
            for v in vals:
                _parse_date(v, f)
            ok.append(f)
        except ValueError:
            continue
    if not ok:
        return None, False
    dmy = [f for f in ok if f != "%m/%d/%Y"]
    ambiguous = "%d/%m/%Y" in ok and "%m/%d/%Y" in ok
    return (dmy[0] if dmy else ok[0]), ambiguous


def parse_statement(content: bytes, filename: str, mapping: dict[str, int] | None = None,
                    date_format: str | None = None) -> Statement:
    table = read_table(content, filename)
    h = _find_header(table)
    headers = [c.strip() for c in table[h]]
    auto = detect_mapping(headers)
    m = mapping or auto
    st = Statement(mapping=m, headers=headers, date_format="")
    if "date" not in m or not ({"debit", "credit"} <= m.keys() or "amount" in m):
        st.errors.append("need a date column and either debit+credit or amount columns")
        return st
    body = [r for r in table[h + 1:] if any(c.strip() for c in r)]
    body = [r for r in body if len(r) > m["date"] and r[m["date"]].strip()]
    fmt_, amb = (date_format, False) if date_format else detect_date_format([r[m["date"]] for r in body])
    if not fmt_:
        st.errors.append("could not parse dates")
        return st
    st.date_format = fmt_
    if amb:
        st.needs_confirmation.append("date order is ambiguous (DD/MM vs MM/DD); confirm date_format")
    if mapping is None:
        st.needs_confirmation.append("confirm detected column mapping")

    def cell(r, k):
        i = m.get(k)
        return r[i] if i is not None and i < len(r) else ""

    prev_bal = None
    for idx, r in enumerate(body, start=1):
        try:
            d = _parse_date(cell(r, "date"), fmt_)
        except ValueError:
            st.warnings.append(f"row {idx}: skipped, unparseable date {cell(r, 'date')!r}")
            continue
        if "amount" in m and not ({"debit", "credit"} <= m.keys()):
            amt = money(cell(r, "amount"))  # signed: + credit to bank (deposit)
            dr, cr = (-amt, Decimal("0.00")) if amt < 0 else (Decimal("0.00"), amt)
        else:
            dr, cr = abs(money(cell(r, "debit"))), abs(money(cell(r, "credit")))
        if dr and cr:
            st.errors.append(f"row {idx}: both withdrawal and deposit present")
        bal = money(cell(r, "balance")) if "balance" in m and cell(r, "balance").strip() else None
        row = {"row_no": idx, "date": d.isoformat(), "narration": cell(r, "narration").strip(),
               "reference": cell(r, "reference").strip(), "withdrawal": fmt(dr), "deposit": fmt(cr),
               "signed": fmt(cr - dr), "balance": fmt(bal) if bal is not None else None}
        if bal is not None:
            if prev_bal is None:
                st.opening_balance = fmt(bal - (cr - dr))
            elif prev_bal + cr - dr != bal:
                st.errors.append(f"row {idx}: running balance mismatch (expected {fmt(prev_bal + cr - dr)}, file says {fmt(bal)})")
            prev_bal = bal
        st.rows.append(row)
    if prev_bal is not None:
        st.closing_balance = fmt(prev_bal)
    if not st.rows:
        st.errors.append("no transactions found")
    return st


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match_statement(rows: list[dict], tally_entries: list[dict], window_days: int = 3) -> list[dict]:
    """rows: parsed statement rows. tally_entries: [{voucher_id, date, amount (bank-ledger debit-positive),
    ref, number, narration}]. Returns rows with status matched|unmatched|duplicate_source|ambiguous.

    Rules (in order): duplicate source rows; reference + amount; amount + date window (unique);
    one-to-many (2..3 Tally entries same window summing exactly). Never amount alone.
    """
    used: set[str] = set()
    seen: dict[tuple, int] = {}
    out = []
    for r in rows:
        r = dict(r, status="unmatched", matched_vouchers=[], evidence="")
        key = (r["date"], r["signed"], _norm(r["narration"]), _norm(r["reference"]))
        if key in seen:
            r["status"], r["evidence"] = "duplicate_source", f"identical to row {seen[key]}"
            out.append(r)
            continue
        seen[key] = r["row_no"]
        out.append(r)

    def within(r, t):
        return abs((date.fromisoformat(r["date"]) - date.fromisoformat(t["date"])).days) <= window_days

    # pass 1: reference
    for r in out:
        if r["status"] != "unmatched" or not _norm(r["reference"]):
            continue
        ref = _norm(r["reference"])
        cands = [t for t in tally_entries if t["voucher_id"] not in used and money(t["amount"]) == money(r["signed"])
                 and ref and (ref == _norm(t.get("ref")) or ref == _norm(t.get("number")) or (len(ref) >= 5 and ref in _norm(t.get("narration"))))]
        if len(cands) == 1:
            used.add(cands[0]["voucher_id"])
            r.update(status="matched", matched_vouchers=[cands[0]["voucher_id"]], evidence=f"reference {r['reference']} + amount")
        elif len(cands) > 1:
            r.update(status="ambiguous", matched_vouchers=[c["voucher_id"] for c in cands], evidence="reference matches several vouchers")
    # pass 2: amount + date window, unique both ways
    for r in out:
        if r["status"] != "unmatched":
            continue
        cands = [t for t in tally_entries if t["voucher_id"] not in used and money(t["amount"]) == money(r["signed"]) and within(r, t)]
        if len(cands) == 1:
            rivals = [o for o in out if o is not r and o["status"] == "unmatched" and money(o["signed"]) == money(r["signed"]) and within(o, cands[0])]
            if rivals:
                r.update(status="ambiguous", matched_vouchers=[cands[0]["voucher_id"]], evidence="several statement rows fit the same voucher")
                continue
            used.add(cands[0]["voucher_id"])
            r.update(status="matched", matched_vouchers=[cands[0]["voucher_id"]], evidence=f"amount + date within {window_days} days")
        elif len(cands) > 1:
            r.update(status="ambiguous", matched_vouchers=[c["voucher_id"] for c in cands], evidence="same amount on several vouchers in window")
    # pass 3: one statement row = 2..3 Tally entries
    for r in out:
        if r["status"] != "unmatched":
            continue
        pool = [t for t in tally_entries if t["voucher_id"] not in used and within(r, t)
                and (money(t["amount"]) > 0) == (money(r["signed"]) > 0)][:12]
        hits = [c for n in (2, 3) for c in combinations(pool, n) if sum(money(t["amount"]) for t in c) == money(r["signed"])]
        if len(hits) == 1:
            for t in hits[0]:
                used.add(t["voucher_id"])
            r.update(status="matched", matched_vouchers=[t["voucher_id"] for t in hits[0]], evidence="one-to-many: sum of vouchers")
        elif len(hits) > 1:
            r.update(status="ambiguous", evidence="several voucher combinations fit")
    return out


DEFAULT_RULES = (
    (r"\b(charges?|fee|sms chg|amc|gst on|min bal)\b", "Bank Charges"),
    (r"\b(int(erest)?\.? ?(cr|credit|pd|paid)|interest)\b", "Interest Received"),
    (r"\b(salary|sal)\b", "Salary Payable"),
    (r"\b(gst|cgst|sgst|igst)\b.*\b(challan|pmt|payment)\b", "GST Payable"),
    (r"\b(tds)\b", "TDS Payable"),
    (r"\b(cash wdl|atm|cash withdrawal)\b", "Cash"),
)


def suggest_ledger(narration: str, signed: Decimal, ledgers: list[dict]) -> dict:
    """Rule- and name-based suggestion; returns {ledger, confidence, evidence}. Never authoritative."""
    names = {l["name"] for l in ledgers}
    low = (narration or "").lower()
    for pat, led in DEFAULT_RULES:
        if re.search(pat, low) and led in names:
            return {"ledger": led, "confidence": "medium", "evidence": f"narration keyword rule /{pat}/"}
    toks = set(re.findall(r"[a-z0-9]{3,}", low))
    best, score = None, 0
    for l in ledgers:
        lt = set(re.findall(r"[a-z0-9]{3,}", l["name"].lower())) - {"ltd", "pvt", "limited", "and", "the", "co"}
        if not lt:
            continue
        s = len(lt & toks) / len(lt)
        if s > score:
            best, score = l["name"], s
    if best and score >= 0.5:
        return {"ledger": best, "confidence": "high" if score == 1 else "medium",
                "evidence": f"ledger name tokens found in narration ({score:.0%})"}
    return {"ledger": "Suspense" if "Suspense" in names else "", "confidence": "low",
            "evidence": "no rule or ledger name matched; review required"}


def draft_voucher_for_row(row: dict, bank_ledger: str, contra_ledger: str, ref: str) -> dict:
    amt = money(row["signed"])
    a = fmt(abs(amt))
    if amt > 0:  # deposit: Dr bank, Cr contra
        vt, entries = "Receipt", [{"ledger": bank_ledger, "side": "dr", "amount": a},
                                  {"ledger": contra_ledger, "side": "cr", "amount": a}]
    else:
        vt, entries = "Payment", [{"ledger": contra_ledger, "side": "dr", "amount": a},
                                  {"ledger": bank_ledger, "side": "cr", "amount": a}]
    if contra_ledger == "Cash":
        vt = "Contra"
    narration = (row.get("narration") or "")[:200]
    return {"voucher_type": vt, "date": row["date"], "ref": ref, "narration": narration, "entries": entries}


def date_window(as_of: date, days: int) -> tuple[date, date]:
    return as_of - timedelta(days=days), as_of
