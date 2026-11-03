"""Independent review regression probes, added locally; no live Tally or model calls."""
from datetime import date, timedelta
from itertools import permutations
import subprocess
import sys
from pathlib import Path

import pytest

from raj import calculations as calc
from test_e2e import ask, reconcile


def test_competing_rows_must_not_gain_a_unique_match_by_iteration_order():
    rows = [
        {"row_no": 1, "date": "2026-10-05", "narration": "First", "reference": "", "signed": "100.00"},
        {"row_no": 2, "date": "2026-10-06", "narration": "Second", "reference": "", "signed": "100.00"},
    ]
    vouchers = [{"voucher_id": "v1", "date": "2026-10-05", "amount": "100.00", "ref": "", "number": "", "narration": ""}]
    result = calc.match_statement(rows, vouchers)
    assert [r["status"] for r in result] == ["ambiguous", "ambiguous"], result


def test_unfiltered_receivables_include_outstanding_not_yet_due():
    bills = [{"party": "P", "ref": "FUTURE", "bill_date": "2026-10-01", "due_date": "2026-11-30", "amount": "100.00"}]
    result = calc.ageing(bills, date(2026, 10, 31))
    assert result["total"] == "100.00", result


def test_company_with_multi_year_books_can_reconcile_one_month(env):
    env.tally.companies[0]["books_from"] = "2024-04-01"
    cid, cfg, gw = env.add_client("Older books")
    result = reconcile(env, cid)
    assert result["state"] == "WAITING_FOR_APPROVAL", result.get("result")


def test_model_numeric_answer_without_book_evidence_is_not_completed(env):
    cid, cfg, gw = env.add_client("Client A")

    class UnsupportedAnswer:
        def chat(self, messages, tools):
            return {"role": "assistant", "content": "Receivables total INR 999999999.00."}

    env.app.state.svc.harness.llm = UnsupportedAnswer()
    result = ask(env, cid, "Show receivables total")
    assert result["state"] != "COMPLETED" or result["reports"], result["result"]


def test_bad_csr_does_not_burn_unused_enrollment_code(env):
    from raj import pki
    cid = env.http.post("/api/clients", json={"name": "Enrollment retry"}, headers=env.admin).json()["id"]
    code = env.http.post(f"/api/clients/{cid}/enrollment", headers=env.admin).json()["code"]
    body = {"code": code, "company": {"name": "Test", "guid": "test-guid"}, "csr_pem": "invalid CSR"}
    assert env.http.post("/api/enroll", json=body).status_code == 422
    body["csr_pem"] = pki.make_csr(pki.new_key(), "test").decode()
    response = env.http.post("/api/enroll", json=body)
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("mode", ["date", "reference", "combination"])
def test_matching_competition_is_invariant_under_row_order(mode):
    amount = "300.00" if mode == "combination" else "100.00"
    ref = "REF1" if mode == "reference" else ""
    rows = [{"row_no": n, "date": f"2026-10-0{n}", "narration": f"row {n}", "reference": ref, "signed": amount}
            for n in (1, 2)]
    vouchers = [{"voucher_id": "a", "date": "2026-10-01", "amount": "100.00", "ref": ref, "number": "", "narration": ""}]
    if mode == "combination":
        vouchers.append({**vouchers[0], "voucher_id": "b", "amount": "200.00"})
    for order in permutations(rows):
        result = calc.match_statement(list(order), vouchers)
        assert {r["row_no"]: r["status"] for r in result} == {1: "ambiguous", 2: "ambiguous"}


def test_combination_limit_never_claims_uniqueness_from_truncated_pool():
    row = {"row_no": 1, "date": "2026-10-01", "narration": "x", "reference": "", "signed": "300.00"}
    vouchers = [{"voucher_id": str(n), "date": row["date"], "amount": value, "ref": "", "number": "", "narration": ""}
                for n, value in enumerate(["100.00", "200.00"] + ["999.00"] * 11)]
    result = calc.match_statement([row], vouchers)[0]
    assert result["status"] == "ambiguous" and "exceeds" in result["evidence"]


@pytest.mark.parametrize("kind,amount", [("receivable", "100.00"), ("payable", "-100.00")])
def test_not_due_bucket_and_explicit_overdue_filter(kind, amount):
    bills = [{"party": "P", "ref": "future", "bill_date": "2026-10-01", "due_date": "2026-11-30", "amount": amount}]
    result = calc.ageing(bills, date(2026, 10, 31), kind=kind)
    assert result["total"] == "100.00"
    assert {r["bucket"]: r["amount"] for r in result["buckets"]}["not due"] == "100.00"
    assert calc.ageing(bills, date(2026, 10, 31), kind=kind, min_days=1)["total"] == "0.00"


def test_multi_year_ledger_preserves_opening_balance(env):
    env.tally.companies[0]["books_from"] = "2024-04-01"
    cid, _, _ = env.add_client("Older ledger")
    result = ask(env, cid, "ledger of HDFC Bank")
    assert result["state"] == "COMPLETED", result["result"]
    assert result["reports"][0]["totals"] == {"Opening (Dr+)": "300000.00", "Closing (Dr+)": "305000.00"}


def test_history_pages_are_bounded_non_overlapping_and_reject_duplicate_identity():
    from raj.contracts import generate_signing_key, MAX_VOUCHER_PERIOD_DAYS
    from raj.server.bridge import BridgeError
    from raj.server.harness import ToolSession

    class Events:
        def event(self, *args, **kwargs):
            pass

    class Pages:
        def __init__(self):
            self.requests = []
            self.duplicate = False
        def call(self, *args, json, **kwargs):
            self.requests.append(json["args"])
            identity = "same" if self.duplicate else str(len(self.requests))
            return {"company": "C", "extracted_at": 1, "result": [{"voucher_id": identity}]}

    bridge = Pages()
    job = {"id": "J", "org_id": "O", "client_id": "C"}
    device = {"id": "D", "company_guid": "G"}
    session = ToolSession(Events(), bridge, generate_signing_key(), job, device, ("ledger_vouchers",))
    result = session.ledger_vouchers("Bank", "2024-01-01", "2026-10-31")
    assert len(result["result"]) == len(bridge.requests) == 3
    previous = None
    for request in bridge.requests:
        start, end = date.fromisoformat(request["from_date"]), date.fromisoformat(request["to_date"])
        assert (end - start).days <= MAX_VOUCHER_PERIOD_DAYS
        if previous:
            assert start == previous + timedelta(days=1)
        previous = end
    bridge.duplicate = True
    with pytest.raises(BridgeError, match="repeated voucher identity"):
        session.ledger_vouchers("Bank", "2024-01-01", "2026-10-31")


def test_fabricated_model_total_after_valid_tool_is_not_published(env):
    cid, _, _ = env.add_client("Grounded model")

    class FabricatedConclusion:
        def __init__(self):
            self.calls = 0
        def chat(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "1", "type": "function", "function": {"name": "payables_ageing", "arguments": "{}"}}]}
            return {"role": "assistant", "content": "Payables total INR 999999999.00."}

    env.app.state.svc.harness.llm = FabricatedConclusion()
    result = ask(env, cid, "What do we owe?")
    assert result["state"] == "COMPLETED"
    assert "80000.00" in result["result"]["answer"]
    assert "999999999" not in result["result"]["answer"]


def test_enrollment_redemption_is_atomic_under_concurrency(env):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from raj import pki
    cid = env.http.post("/api/clients", json={"name": "Concurrent enrollment"}, headers=env.admin).json()["id"]
    code = env.http.post(f"/api/clients/{cid}/enrollment", headers=env.admin).json()["code"]
    body = {"code": code, "company": {"name": "C", "guid": "G"}, "csr_pem": pki.make_csr(pki.new_key(), "test").decode()}
    barrier = threading.Barrier(2)
    def redeem():
        barrier.wait(timeout=5)
        return env.http.post("/api/enroll", json=body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(lambda _: redeem(), range(2)))
    assert statuses == [200, 403]
    assert env.app.state.db.one("SELECT COUNT(*) n FROM devices WHERE client_id=?", (cid,))["n"] == 1


def test_frozen_entry_script_also_runs_without_package_context():
    entry = Path(__file__).resolve().parents[1] / "raj/connector/__main__.py"
    result = subprocess.run([sys.executable, str(entry), "--help"], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "raj-connector" in result.stdout
