"""Gateway on RAJTALLYFINLMCP: locked stdio children against RAJTALLYFINLMCP's own demo Tally."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import ADMIN, free_port, login, start_gateway
from raj.connector.cli import pair
from raj.contracts import make_execution_envelope, make_job_token
from raj.server.app import Settings, create_app, init_org
from raj.server.bridge import BridgeError

demo_tally = pytest.importorskip("rajtally_mcp.demo_tally")
COMPANY = "Demo Traders Pvt Ltd"


@pytest.fixture
def mcpenv(tmp_path, monkeypatch):
    monkeypatch.setenv("RAJ_ALLOW_DIRECT_GATEWAY", "1")
    monkeypatch.setenv("RAJ_AGENT_RUNTIME", "rules")
    port = free_port()
    srv, data = demo_tally.start(port)
    sdir = tmp_path / "server"
    sdir.mkdir()
    init_org(sdir, "Kher & Co", *ADMIN)
    app = create_app(Settings(sdir), llm=None)
    http = TestClient(app)
    admin = login(http, *ADMIN)
    cid = http.post("/api/clients", json={"name": "Demo"}, headers=admin).json()["id"]
    code = http.post(f"/api/clients/{cid}/enrollment", headers=admin).json()["code"]
    gport = free_port()
    cfg = pair("http://testserver", code, tmp_path / "conn", f"http://127.0.0.1:{port}", COMPANY, gport,
               direct_url=f"https://127.0.0.1:{gport}", http=http, assume_yes=True, backend="mcp")
    gw, gsrv = start_gateway(cfg)
    yield type("E", (), dict(app=app, http=http, admin=admin, cid=cid, cfg=cfg, gw=gw, data=data))
    gsrv.should_exit = True
    gw.tally.close()
    srv.shutdown()


def token(e, tools, cap="read", job="J"):
    return make_job_token(e.app.state.pki.signing_key, job_id=job, org_id=e.cfg.org_id, client_id=e.cid,
                          device_id=e.cfg.device_id, company_guid=e.cfg.company_guid, capability=cap,
                          allowed_tools=tools)


def test_pairing_binds_company_guid_from_mcp(mcpenv):
    assert mcpenv.cfg.company_guid == "demo-company" and mcpenv.cfg.tally_backend == "mcp"


def test_reports_through_mcp(mcpenv):
    e = mcpenv
    j = e.http.post(f"/api/clients/{e.cid}/ask", json={"question": "trial balance"}, headers=e.admin).json()
    assert j["state"] == "COMPLETED", j
    rep = j["reports"][0]
    assert rep["meta"]["tie_out"] == "balanced" and rep["totals"]["Total debit"] == "609314.00"
    j = e.http.post(f"/api/clients/{e.cid}/ask", json={"question": "receivables"}, headers=e.admin).json()
    assert j["reports"][0]["totals"]["Total"] == "6254.00"


def test_full_read_capabilities_pass_through_and_writes_do_not(mcpenv):
    e = mcpenv
    b = e.app.state.bridge
    tools = {t["name"] for t in b.call(e.cfg.device_id, "POST", "/tools/list", token=token(e, []))["tools"]}
    assert {"tally_profit_loss", "tally_balance_sheet", "tally_gst_summary", "tally_gstr1", "tally_tds_summary",
            "tally_audit_checks", "tally_stock_summary", "tally_bank_reconciliation"} <= tools
    assert not tools & {"tally_create_voucher", "tally_raw_xml", "tally_sql", "tally_delete_voucher",
                        "tally_use_company", "tally_settings_update"}
    t = token(e, ["tally_gst_summary", "tally_profit_loss", "tally_raw_xml"])
    gst = b.call(e.cfg.device_id, "POST", "/tools/call", token=t, json={"tool": "tally_gst_summary", "args": {}})
    assert "result" in gst and gst["company"] == COMPANY
    pl = b.call(e.cfg.device_id, "POST", "/tools/call", token=t,
                json={"tool": "tally_profit_loss", "args": {"company": "Someone Else"}})  # company arg stripped
    assert "result" in pl
    with pytest.raises(BridgeError) as ei:
        b.call(e.cfg.device_id, "POST", "/tools/call", token=t, json={"tool": "tally_raw_xml", "args": {"xml": "x"}})
    assert ei.value.status == 400


def _payload(ref):
    return {"voucher_type": "Payment", "date": "2026-05-10", "ref": ref, "narration": "Office rent",
            "entries": [{"ledger": "Rent", "side": "dr", "amount": "1500.00"},
                        {"ledger": "HDFC Bank", "side": "cr", "amount": "1500.00"}]}


def _post(e, payload, row="R1"):
    pki = e.app.state.pki
    env_ = make_execution_envelope(pki.signing_key, job_id="J", approval_id="A" + row, draft_id="D", draft_version=1,
                                   row_id=row, payload=payload, company_guid=e.cfg.company_guid,
                                   device_id=e.cfg.device_id, reviewer_id="U")
    return e.app.state.bridge.call(e.cfg.device_id, "POST", "/tools/post_voucher",
                                   token=token(e, ["create_voucher"], "write"),
                                   json={"envelope": env_, "payload": payload})


def test_post_through_mcp_writer_with_readback(mcpenv):
    e = mcpenv
    e.gw.cfg.write_enabled_local = True
    e.cfg.write_enabled_local = True
    e.cfg.save(Path(e.cfg.data_dir) / "config.json")
    res = _post(e, _payload("RAJ-mcp-1"))
    assert res["state"] == "VERIFIED", res
    assert res["readback"]["ref"] == "RAJ-mcp-1"
    assert e.gw.tally.writer is not None and "tally_alter_voucher" not in e.gw.tally.writer.tools


def test_lost_mcp_response_is_reconciled_not_retried(mcpenv, monkeypatch):
    e = mcpenv
    e.gw.cfg.write_enabled_local = True
    e.cfg.write_enabled_local = True
    e.cfg.save(Path(e.cfg.data_dir) / "config.json")
    real = e.gw.tally.create_voucher
    calls = []

    def lossy(company, v):
        calls.append(1)
        real(company, v)
        from raj.tally.xml_client import TallyResponseLost
        raise TallyResponseLost("simulated: response lost after Tally created the voucher")
    monkeypatch.setattr(e.gw.tally, "create_voucher", lossy)
    res = _post(e, _payload("RAJ-mcp-2"), row="R2")
    assert res["state"] == "VERIFIED" and len(calls) == 1
    found = e.gw.tally.find_by_ref(COMPANY, "RAJ-mcp-2", "2026-05-10")
    assert len(found) == 1
