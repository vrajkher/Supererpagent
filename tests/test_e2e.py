"""Full path: portal API -> harness -> bridge (mTLS) -> gateway -> fake Tally, plus write controls."""
import json
from pathlib import Path

import httpx
import pytest

from conftest import login
from raj.contracts import make_execution_envelope, make_job_token

FIX = Path(__file__).resolve().parents[1] / "fixtures"
STMT = (FIX / "hdfc_oct_2026.csv").read_bytes()
MAPPING = json.dumps({"date": 0, "narration": 1, "reference": 2, "debit": 3, "credit": 4, "balance": 5})


def ask(env, cid, q, headers=None):
    r = env.http.post(f"/api/clients/{cid}/ask", json={"question": q, "as_of": "2026-10-31"}, headers=headers or env.admin)
    assert r.status_code == 200, r.text
    return r.json()


def reconcile(env, cid):
    r = env.http.post(f"/api/clients/{cid}/reconcile", headers=env.admin,
                      files={"file": ("hdfc.csv", STMT, "text/csv")},
                      data={"bank_ledger": "HDFC Bank", "mapping": MAPPING, "date_format": "%d/%m/%Y"})
    assert r.status_code == 200, r.text
    return r.json()


def rows_by_no(job):
    return {r["row_no"]: r for r in job["draft"]["rows"]}


def enable_writes(env, cfg, gw):
    env.http.post(f"/api/devices/{cfg.device_id}/writes", json={"enabled": True}, headers=env.admin)
    gw.cfg.write_enabled_local = True
    cfg.write_enabled_local = True
    cfg.save(Path(cfg.data_dir) / "config.json")


# ------------------------------------------------------------------ reads

def test_receivables_over_90_days(env):
    cid, cfg, gw = env.add_client("Client A")
    j = ask(env, cid, "Client A na 90 days thi vadhu baki receivables batavo")
    assert j["state"] == "COMPLETED", j
    rep = j["reports"][0]
    assert rep["title"].startswith("Receivables ageing")
    parties = {p["party"]: p["total"] for p in rep["sections"][1]["rows"]}
    # S-101 (60000, May), P-88 (80000, Jul), Gujarati party G-12 (45000, Mar); recent bills excluded
    assert parties == {"Patel Textiles": "80000.00", "Shree Ram Enterprises": "60000.00", "ગુજરાત ટ્રેડર્સ": "45000.00"}
    assert rep["totals"]["Total"] == "185000.00"
    assert rep["meta"]["company"] == "Demo Traders Pvt Ltd" and rep["meta"]["extracted_at"]


def test_ledger_statement_running_balance(env):
    cid, cfg, gw = env.add_client("Client A")
    j = ask(env, cid, "ledger of HDFC Bank")
    rep = j["reports"][0]
    assert rep["totals"] == {"Opening (Dr+)": "300000.00", "Closing (Dr+)": "305000.00"}


def test_device_health_four_statuses(env):
    cid, cfg, gw = env.add_client("Client A")
    h = env.http.get(f"/api/devices/{cfg.device_id}/health", headers=env.admin).json()
    assert h["ssh_connected"] and h["gateway"] == "ok" and h["tally"] == "running" and h["company"] == "ready"
    env.tally.open = []  # user closed the company in Tally
    h = env.http.get(f"/api/devices/{cfg.device_id}/health", headers=env.admin).json()
    assert h["gateway"] == "ok" and h["company"] == "not open"
    j = ask(env, cid, "trial balance")
    assert j["state"] == "FAILED" and "not open" in j["result"]["error"]   # no fabricated report
    assert j["reports"] == []


# ------------------------------------------------------------------ isolation

def test_cross_client_isolation(env):
    a, cfg_a, gw_a = env.add_client("Client A")
    b, cfg_b, gw_b = env.add_client("Client B", company="Other Client Co")
    # staff assigned only to B cannot see A
    env.http.post("/api/users", headers=env.admin, json={"email": "b@firm.test", "name": "B staff", "password": "longpassword1",
                                                         "role": "preparer", "client_ids": [b]})
    hb = login(env.http, "b@firm.test", "longpassword1")
    assert env.http.post(f"/api/clients/{a}/ask", json={"question": "trial balance"}, headers=hb).status_code == 403
    assert [c["name"] for c in env.http.get("/api/clients", headers=hb).json()] == ["Client B"]
    # a token for B's device is rejected by A's gateway
    pki = env.app.state.pki
    tok_b = make_job_token(pki.signing_key, job_id="x", org_id=cfg_b.org_id, client_id=b, device_id=cfg_b.device_id,
                           company_guid=cfg_b.company_guid, capability="read", allowed_tools=["trial_balance"])
    with pytest.raises(Exception) as ei:
        env.app.state.bridge.call(cfg_a.device_id, "POST", "/tools/call", token=tok_b, json={"tool": "trial_balance"})
    assert ei.value.status == 403
    # routing A's device id to B's gateway port fails TLS identity verification
    env.app.state.db.x("UPDATE devices SET gateway_url=? WHERE id=?", (f"https://127.0.0.1:{cfg_b.listen_port}", cfg_a.device_id))
    j = ask(env, a, "trial balance")
    assert j["state"] == "FAILED" and "identity" in j["result"]["error"]
    # a client without a bridge certificate cannot talk to a gateway at all
    with pytest.raises((httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError)):
        httpx.get(f"https://127.0.0.1:{cfg_b.listen_port}/health", verify=False)


def test_read_token_cannot_write_and_tools_are_allow_listed(env):
    cid, cfg, gw = env.add_client("Client A")
    pki = env.app.state.pki
    tok = make_job_token(pki.signing_key, job_id="x", org_id=cfg.org_id, client_id=cid, device_id=cfg.device_id,
                         company_guid=cfg.company_guid, capability="read", allowed_tools=["company_info"])
    with pytest.raises(Exception) as ei:
        env.app.state.bridge.call(cfg.device_id, "POST", "/tools/call", token=tok, json={"tool": "trial_balance"})
    assert ei.value.status == 403
    with pytest.raises(Exception) as ei:
        env.app.state.bridge.call(cfg.device_id, "POST", "/tools/post_voucher", token=tok, json={"envelope": "x", "payload": {}})
    assert ei.value.status == 403


# ------------------------------------------------------------------ reconciliation + posting

def test_reconcile_classifies_every_row(env):
    cid, cfg, gw = env.add_client("Client A")
    j = reconcile(env, cid)
    assert j["state"] == "WAITING_FOR_APPROVAL", j["events"][-1]
    rows = rows_by_no(j)
    assert len(rows) == 6
    assert rows[1]["match_status"] == "matched" and rows[2]["match_status"] == "matched"
    assert {rows[n]["match_status"] for n in (3, 4, 5, 6)} == {"unmatched"}
    assert rows[3]["voucher"]["entries"][0]["ledger"] == "Bank Charges"
    assert rows[4]["suggestion"]["ledger"] == "Patel Textiles"
    assert rows[6]["suggestion"]["confidence"] == "low"
    s = j["draft"]["summary"]
    assert s["statement_opening"] == "300000.00" and s["opening_difference"] == "0.00"


def test_full_posting_flow_with_readback(env):
    cid, cfg, gw = env.add_client("Client A")
    j = reconcile(env, cid)
    d = j["draft"]
    rows = rows_by_no(j)
    ids = [rows[n]["id"] for n in (3, 4, 5)]
    r = env.http.post(f"/api/drafts/{d['id']}/approve", json={"version": d["version"], "row_ids": ids}, headers=env.admin)
    assert r.status_code == 200, r.text
    # writes disabled server-side by default
    assert env.http.post(f"/api/jobs/{j['id']}/post", headers=env.admin).status_code == 403
    env.http.post(f"/api/devices/{cfg.device_id}/writes", json={"enabled": True}, headers=env.admin)
    # still blocked locally (approvals consumed -> rows need re-approval)
    j2 = env.http.post(f"/api/jobs/{j['id']}/post", headers=env.admin).json()
    assert all(rows_by_no(j2)[n]["post_status"] == "failed" for n in (3, 4, 5)), [rows_by_no(j2)[n]["readback"] for n in (3,4,5)]
    enable_writes(env, cfg, gw)
    r = env.http.post(f"/api/drafts/{d['id']}/approve", json={"version": d["version"], "row_ids": ids}, headers=env.admin)
    assert r.status_code == 200, r.text
    j3 = env.http.post(f"/api/jobs/{j['id']}/post", headers=env.admin).json()
    rr = rows_by_no(j3)
    assert [rr[n]["post_status"] for n in (3, 4, 5)] == ["posted"] * 3, [rr[n]["readback"] for n in (3, 4, 5)]
    assert j3["state"] == "PARTIALLY_COMPLETED"  # row 6 (suspense) not approved
    assert rr[3]["tally_id"].startswith("fake-guid")
    # Tally now has the bank charges voucher; trial balance still ties out
    tb = ask(env, cid, "trial balance")["reports"][0]
    assert tb["meta"]["tie_out"] == "balanced"
    assert {r["ledger"]: r["debit"] for r in tb["sections"][0]["rows"]}["Bank Charges"] == "590.00"
    # re-approving a posted row is refused
    r = env.http.post(f"/api/drafts/{d['id']}/approve", json={"version": d["version"], "row_ids": [ids[0]]}, headers=env.admin)
    assert r.status_code == 409


def test_edit_after_approval_requires_reapproval(env):
    cid, cfg, gw = env.add_client("Client A")
    enable_writes(env, cfg, gw)
    j = reconcile(env, cid)
    d, row = j["draft"], rows_by_no(j)[6]
    r = env.http.patch(f"/api/drafts/{d['id']}/rows/{row['id']}", json={"ledger": "Nope"}, headers=env.admin)
    assert r.status_code == 422
    env.http.post(f"/api/drafts/{d['id']}/approve", json={"version": 1, "row_ids": [row["id"]]}, headers=env.admin)
    r = env.http.patch(f"/api/drafts/{d['id']}/rows/{row['id']}", json={"ledger": "Purchase"}, headers=env.admin)
    assert r.json()["draft"]["version"] == 2
    assert rows_by_no(r.json())[6]["post_status"] == "draft"
    assert env.http.post(f"/api/drafts/{d['id']}/approve", json={"version": 1, "row_ids": [row["id"]]}, headers=env.admin).status_code == 409
    assert env.http.post(f"/api/jobs/{j['id']}/post", headers=env.admin).status_code == 409  # nothing approved


def test_gateway_rejects_tampered_replayed_and_wrong_company(env):
    cid, cfg, gw = env.add_client("Client A")
    enable_writes(env, cfg, gw)
    pki, bridge = env.app.state.pki, env.app.state.bridge
    payload = {"voucher_type": "Payment", "date": "2026-10-15", "ref": "RAJ-test-1", "narration": "t",
               "entries": [{"ledger": "Bank Charges", "side": "dr", "amount": "590.00"},
                           {"ledger": "HDFC Bank", "side": "cr", "amount": "590.00"}]}
    tok = make_job_token(pki.signing_key, job_id="J", org_id=cfg.org_id, client_id=cid, device_id=cfg.device_id,
                         company_guid=cfg.company_guid, capability="write", allowed_tools=["create_voucher"])
    env_ = make_execution_envelope(pki.signing_key, job_id="J", approval_id="A", draft_id="D", draft_version=1, row_id="R",
                                   payload=payload, company_guid=cfg.company_guid, device_id=cfg.device_id, reviewer_id="U")
    tampered = {**payload, "entries": [{**payload["entries"][0], "amount": "5900.00"}, {**payload["entries"][1], "amount": "5900.00"}]}
    with pytest.raises(Exception) as ei:
        bridge.call(cfg.device_id, "POST", "/tools/post_voucher", token=tok, json={"envelope": env_, "payload": tampered})
    assert ei.value.status == 409 and "approved" in ei.value.detail
    # company switched in Tally -> blocked before any write
    env.tally.open = ["Other Client Co"]
    with pytest.raises(Exception) as ei:
        bridge.call(cfg.device_id, "POST", "/tools/post_voucher", token=tok, json={"envelope": env_, "payload": payload})
    assert ei.value.status == 409 and "not open" in ei.value.detail
    env.tally.open = ["Demo Traders Pvt Ltd", "Other Client Co"]
    assert bridge.call(cfg.device_id, "POST", "/tools/post_voucher", token=tok, json={"envelope": env_, "payload": payload})["state"] == "VERIFIED"
    with pytest.raises(Exception) as ei:  # replay of the same approval
        bridge.call(cfg.device_id, "POST", "/tools/post_voucher", token=tok, json={"envelope": env_, "payload": payload})
    assert ei.value.status == 409
    # forged envelope (signed by another key)
    from raj.contracts import generate_signing_key
    forged = make_execution_envelope(generate_signing_key(), job_id="J", approval_id="B", draft_id="D", draft_version=1, row_id="R2",
                                     payload=payload, company_guid=cfg.company_guid, device_id=cfg.device_id, reviewer_id="U")
    with pytest.raises(Exception) as ei:
        bridge.call(cfg.device_id, "POST", "/tools/post_voucher", token=tok, json={"envelope": forged, "payload": payload})
    assert ei.value.status == 401


def test_lost_response_is_recovered_without_duplicate(env):
    cid, cfg, gw = env.add_client("Client A")
    enable_writes(env, cfg, gw)
    j = reconcile(env, cid)
    d, rows = j["draft"], rows_by_no(j)
    env.http.post(f"/api/drafts/{d['id']}/approve", json={"version": 1, "row_ids": [rows[3]["id"], rows[4]["id"]]}, headers=env.admin)
    before = len(env.tally.vouchers["Demo Traders Pvt Ltd"])
    env.tally.drop_after_import = 1   # Tally creates voucher then the connection dies
    j2 = env.http.post(f"/api/jobs/{j['id']}/post", headers=env.admin).json()
    rr = rows_by_no(j2)
    # gateway found the voucher by its correlation reference and verified it instead of retrying
    assert rr[3]["post_status"] == "posted", rr[3]["readback"]
    assert rr[4]["post_status"] == "posted"
    assert len(env.tally.vouchers["Demo Traders Pvt Ltd"]) == before + 2
    assert env.tally.import_count == 2


def test_unknown_result_blocks_further_writes_until_reconciled(env):
    cid, cfg, gw = env.add_client("Client A")
    enable_writes(env, cfg, gw)
    j = reconcile(env, cid)
    d, rows = j["draft"], rows_by_no(j)
    env.http.post(f"/api/drafts/{d['id']}/approve", json={"version": 1, "row_ids": [rows[3]["id"], rows[4]["id"]]}, headers=env.admin)
    # simulate a crash after SENT: put an unresolved journal entry in place
    gw.journal.intent(nonce="n1", job_id=j["id"], row_id=rows[3]["id"], approval_id="a", ref="RAJ-ghost-1",
                      voucher_date="2026-10-15", payload={}, payload_sha256="x")
    gw.journal.set("n1", "UNKNOWN")
    j2 = env.http.post(f"/api/jobs/{j['id']}/post", headers=env.admin).json()
    assert rows_by_no(j2)[3]["post_status"] == "failed" and "reconcile" in rows_by_no(j2)[3]["readback"]["detail"]
    j3 = env.http.post(f"/api/jobs/{j['id']}/resolve", headers=env.admin).json()
    assert gw.journal.get("n1")["state"] == "NOT_POSTED"
    assert gw.journal.blocking() == []
    assert j3["state"] in ("WAITING_FOR_APPROVAL", "FAILED", "PARTIALLY_COMPLETED")


def test_pause_blocks_new_work(env):
    cid, cfg, gw = env.add_client("Client A")
    cfg.paused = True
    cfg.save(Path(cfg.data_dir) / "config.json")
    j = ask(env, cid, "trial balance")
    assert j["state"] == "FAILED" and "paused" in j["result"]["error"]


def test_maker_checker(env):
    cid, cfg, gw = env.add_client("Client A")
    env.http.patch("/api/org/settings", json={"maker_checker": True}, headers=env.admin)
    j = reconcile(env, cid)  # prepared by admin
    d, rows = j["draft"], rows_by_no(j)
    r = env.http.post(f"/api/drafts/{d['id']}/approve", json={"version": 1, "row_ids": [rows[3]["id"]]}, headers=env.admin)
    assert r.status_code == 403
    env.http.post("/api/users", headers=env.admin, json={"email": "rev@firm.test", "name": "Reviewer", "password": "longpassword1",
                                                         "role": "reviewer", "client_ids": [cid]})
    hr = login(env.http, "rev@firm.test", "longpassword1")
    r = env.http.post(f"/api/drafts/{d['id']}/approve", json={"version": 1, "row_ids": [rows[3]["id"]]}, headers=hr)
    assert r.status_code == 200, r.text


def test_enrollment_code_single_use(env):
    cid = env.http.post("/api/clients", json={"name": "C"}, headers=env.admin).json()["id"]
    code = env.http.post(f"/api/clients/{cid}/enrollment", headers=env.admin).json()["code"]
    from raj import pki
    body = {"code": code, "company": {"name": "Demo Traders Pvt Ltd", "guid": "g"}, "csr_pem": pki.make_csr(pki.new_key(), "x").decode()}
    assert env.http.post("/api/enroll", json=body).status_code == 200
    assert env.http.post("/api/enroll", json=body).status_code == 403


def test_model_loop_uses_tools_and_cannot_post(env, monkeypatch):
    """A scripted model: asks for a tool, then answers. Unknown/write tools are refused."""
    cid, cfg, gw = env.add_client("Client A")

    class FakeLLM:
        def __init__(self):
            self.n = 0
        def chat(self, messages, tools):
            self.n += 1
            names = {t["function"]["name"] for t in tools}
            assert "create_voucher" not in names and "post_voucher" not in names
            if self.n == 1:
                return {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "1", "type": "function", "function": {"name": "payables_ageing", "arguments": "{}"}},
                    {"id": "2", "type": "function", "function": {"name": "create_voucher", "arguments": "{}"}}]}
            tool_msgs = [m for m in messages if m["role"] == "tool"]
            assert "80000.00" in tool_msgs[0]["content"] and "unknown skill" in tool_msgs[1]["content"]
            return {"role": "assistant", "content": "Payables total ₹80000.00 (Om Suppliers)."}
    env.app.state.svc.harness.llm = FakeLLM()
    j = ask(env, cid, "What do we owe?")
    assert j["state"] == "COMPLETED" and "80000.00" in j["result"]["answer"]
    assert j["reports"][0]["totals"]["Total"] == "80000.00"
