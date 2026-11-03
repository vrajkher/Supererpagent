from datetime import date
from pathlib import Path

from raj import calculations as c

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def test_money_indian_formats():
    assert c.money("3,20,000.00") == c.Decimal("320000.00")
    assert c.money("1,234.5 Cr") == c.Decimal("-1234.50")
    assert c.money("(500)") == c.Decimal("-500.00")


def test_trial_balance_balanced_and_unbalanced():
    led = [{"name": "A", "parent": "X", "closing": "100.00"}, {"name": "B", "parent": "Y", "closing": "-100.00"}]
    assert c.trial_balance_summary(led)["tie_out"] == "balanced"
    led[0]["closing"] = "100.01"
    s = c.trial_balance_summary(led)
    assert s["tie_out"] == "NOT BALANCED" and s["difference"] == "0.01"


def test_ageing_buckets_and_filter():
    bills = [{"party": "P", "ref": "1", "bill_date": "2026-01-01", "amount": "100.00"},
             {"party": "P", "ref": "2", "bill_date": "2026-10-20", "amount": "50.00"},
             {"party": "Q", "ref": "3", "bill_date": "2026-01-01", "amount": "-70.00"}]
    a = c.ageing(bills, date(2026, 10, 31))
    assert a["total"] == "150.00"
    old = c.ageing(bills, date(2026, 10, 31), min_days=91)
    assert old["total"] == "100.00" and old["detail"][0]["bucket"] == ">180"
    assert c.ageing(bills, date(2026, 10, 31), kind="payable")["total"] == "70.00"


def test_voucher_validation():
    v = {"voucher_type": "Payment", "date": "2026-10-15", "ref": "R1", "entries": [
        {"ledger": "Bank Charges", "side": "dr", "amount": "590.00"},
        {"ledger": "HDFC Bank", "side": "cr", "amount": "590.00"}]}
    assert c.validate_voucher(v, ["Bank Charges", "HDFC Bank"]) == []
    v["entries"][0]["amount"] = "591.00"
    assert any("not balanced" in e for e in c.validate_voucher(v))
    v["entries"][0].update(amount="590.00", ledger="Nope")
    assert any("does not exist" in e for e in c.validate_voucher(v, ["Bank Charges", "HDFC Bank"]))
    v["voucher_type"] = "Delete"
    assert any("not allowed" in e for e in c.validate_voucher(v))


def test_parse_statement_and_balance_check():
    raw = (FIX / "hdfc_oct_2026.csv").read_bytes()
    st = c.parse_statement(raw, "s.csv")
    assert not st.errors and len(st.rows) == 6
    assert st.date_format == "%d/%m/%Y" and st.opening_balance == "300000.00" and st.closing_balance == "328644.50"
    assert st.mapping["debit"] == 3 and st.mapping["credit"] == 4
    bad = raw.replace(b'"3,05,000.00"', b'"3,05,001.00"')
    assert any("running balance" in e for e in c.parse_statement(bad, "s.csv").errors)


def test_ambiguous_dates_need_confirmation():
    raw = b"Date,Narration,Withdrawal,Deposit\n01/02/2026,A,10,\n03/04/2026,B,,5\n"
    st = c.parse_statement(raw, "x.csv")
    assert any("ambiguous" in n for n in st.needs_confirmation)


def test_matching_rules():
    rows = [
        {"row_no": 1, "date": "2026-10-05", "narration": "NEFT", "reference": "UTR1", "signed": "100.00"},
        {"row_no": 2, "date": "2026-10-06", "narration": "X", "reference": "", "signed": "-50.00"},
        {"row_no": 3, "date": "2026-10-06", "narration": "X", "reference": "", "signed": "-50.00"},
        {"row_no": 4, "date": "2026-10-09", "narration": "Y", "reference": "", "signed": "300.00"},
        {"row_no": 5, "date": "2026-10-20", "narration": "Z", "reference": "", "signed": "999.00"},
    ]
    tally = [
        {"voucher_id": "a", "date": "2026-10-07", "amount": "100.00", "ref": "UTR1", "number": "", "narration": ""},
        {"voucher_id": "b", "date": "2026-10-06", "amount": "-50.00", "ref": "", "number": "", "narration": ""},
        {"voucher_id": "c", "date": "2026-10-09", "amount": "100.00", "ref": "", "number": "", "narration": ""},
        {"voucher_id": "d", "date": "2026-10-09", "amount": "200.00", "ref": "", "number": "", "narration": ""},
        {"voucher_id": "e", "date": "2026-11-30", "amount": "999.00", "ref": "", "number": "", "narration": ""},
    ]
    out = {r["row_no"]: r for r in c.match_statement(rows, tally)}
    assert out[1]["status"] == "matched" and out[1]["matched_vouchers"] == ["a"]
    assert out[2]["status"] == "matched"
    assert out[3]["status"] == "duplicate_source"
    assert out[4]["status"] == "matched" and sorted(out[4]["matched_vouchers"]) == ["c", "d"]
    assert out[5]["status"] == "unmatched"  # same amount but outside date window: never amount-only


def test_suggestions():
    led = [{"name": n} for n in ("Bank Charges", "Patel Textiles", "Suspense", "Interest Received")]
    assert c.suggest_ledger("SMS CHARGES INCL GST", c.Decimal("-590"), led)["ledger"] == "Bank Charges"
    assert c.suggest_ledger("NEFT CR-PATEL TEXTILES", c.Decimal("25000"), led)["ledger"] == "Patel Textiles"
    assert c.suggest_ledger("INT CR FOR QTR", c.Decimal("1"), led)["ledger"] == "Interest Received"
    s = c.suggest_ledger("UPI/XYZ STORE", c.Decimal("-2000"), led)
    assert s["ledger"] == "Suspense" and s["confidence"] == "low"


def test_competing_rows_stay_ambiguous_in_any_order():
    """R3: two bank rows competing for one voucher must both stay ambiguous, whatever the row order."""
    from itertools import permutations
    rows = [
        {"row_no": 1, "date": "2026-10-06", "narration": "A", "reference": "", "signed": "-50.00"},
        {"row_no": 2, "date": "2026-10-07", "narration": "B", "reference": "", "signed": "-50.00"},
        {"row_no": 3, "date": "2026-10-20", "narration": "C", "reference": "", "signed": "70.00"},
    ]
    tally = [{"voucher_id": "v", "date": "2026-10-06", "amount": "-50.00", "ref": "", "number": "", "narration": ""},
             {"voucher_id": "w", "date": "2026-10-20", "amount": "70.00", "ref": "", "number": "", "narration": ""}]
    results = set()
    for order in permutations(rows):
        out = {r["row_no"]: r["status"] for r in c.match_statement(list(order), tally)}
        results.add(tuple(sorted(out.items())))
    assert results == {((1, "ambiguous"), (2, "ambiguous"), (3, "matched"))}


def test_competing_references_and_one_to_many_are_order_independent():
    from itertools import permutations
    rows = [
        {"row_no": 1, "date": "2026-10-05", "narration": "x", "reference": "UTR1", "signed": "100.00"},
        {"row_no": 2, "date": "2026-10-06", "narration": "y", "reference": "UTR1", "signed": "100.00"},
        {"row_no": 3, "date": "2026-10-09", "narration": "p", "reference": "", "signed": "300.00"},
        {"row_no": 4, "date": "2026-10-09", "narration": "q", "reference": "", "signed": "300.00"},
    ]
    tally = [{"voucher_id": "a", "date": "2026-10-05", "amount": "100.00", "ref": "UTR1", "number": "", "narration": ""},
             {"voucher_id": "c", "date": "2026-10-09", "amount": "100.00", "ref": "", "number": "", "narration": ""},
             {"voucher_id": "d", "date": "2026-10-09", "amount": "200.00", "ref": "", "number": "", "narration": ""}]
    seen = set()
    for order in permutations(rows):
        seen.add(tuple(sorted((r["row_no"], r["status"]) for r in c.match_statement(list(order), tally))))
    assert len(seen) == 1
    assert dict(seen.pop()) == {1: "ambiguous", 2: "ambiguous", 3: "ambiguous", 4: "ambiguous"}


def test_unfiltered_ageing_includes_not_due_bills():
    """R5: a bill due in the future is outstanding and belongs in the unfiltered total."""
    bills = [{"party": "P", "ref": "1", "bill_date": "2026-10-01", "due_date": "2026-12-01", "amount": "500.00"},
             {"party": "P", "ref": "2", "bill_date": "2026-01-01", "amount": "100.00"}]
    a = c.ageing(bills, date(2026, 10, 31))
    assert a["total"] == "600.00"
    assert {b["bucket"]: b["amount"] for b in a["buckets"]}["not due"] == "500.00"
    assert c.ageing(bills, date(2026, 10, 31), min_days=91)["total"] == "100.00"


def test_provenance_checks_only_amounts_against_tool_data():
    from raj.server import provenance as p
    reports = [{"totals": {"Total": "6254.00"}, "sections": [{"rows": [{"pending": 2714.0}, {"amount": "3,540.00"}]}]}]
    assert p.check("Total ₹6,254.00: Patel 2714 and Walk-in 3540.00 for FY 2026", reports)["status"] == "verified"
    r = p.check("Total 6254.00, plus 12,345.67 elsewhere, as of 2026-11-03, bills older than 90 days", reports)
    assert r["status"] == "unverified" and r["unsupported"] == ["12,345.67"]
    assert p.check("No amounts here, just 2026 and section 40A(3).", reports)["status"] == "no-figures"
    assert p.check("Amounts above 50000 are flagged", reports, question="flag above 50000")["status"] == "no-figures"
