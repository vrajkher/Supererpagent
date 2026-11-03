"""DeepSeek Harness worker, end to end, with a scripted fake model (no API key, no network):

portal API -> DeepSeek Harness runtime (sdk-minimal, patched) -> RAJ MCP bridge (stdio)
           -> mTLS bridge -> gateway -> RAJTALLYFINLMCP (locked, read) -> RAJTALLYFINLMCP demo Tally
"""
import json

import pytest

pytest.importorskip("deepseek_harness")
from test_mcp_backend import mcpenv  # noqa: E402,F401 - fixture

from raj.server.dsh_worker import DshRunner  # noqa: E402
from raj.testing.fake_deepseek import FakeDeepSeek, serve  # noqa: E402


@pytest.fixture
def dsh(mcpenv):  # noqa: F811
    fake = FakeDeepSeek()
    srv, port = serve(fake)
    st = mcpenv.app.state
    st.svc.harness.dsh = DshRunner(st.db, st.bridge, st.pki, st.settings.data_dir,
                                   base_url=f"http://127.0.0.1:{port}", api_key="test-key")
    yield mcpenv, fake
    srv.shutdown()


def ask(e, q):
    r = e.http.post(f"/api/clients/{e.cid}/ask", json={"question": q, "as_of": "2026-11-03"}, headers=e.admin)
    assert r.status_code == 200, r.text
    return r.json()


def test_harness_runs_finance_skill_and_saves_report(dsh):
    e, fake = dsh
    j = ask(e, "Show receivables older than 90 days")
    assert j["state"] == "COMPLETED", j["events"][-4:]
    assert j["reports"] and j["reports"][0]["title"].startswith("Receivables ageing")
    assert j["reports"][0]["totals"]["Total"] == "6254.00"
    assert "6254.00" in j["result"]["answer"]
    evs = [(x["actor"], x["event"]) for x in j["events"]]
    assert ("harness", "plan") in evs and ("agent-bridge", "tool_call") in evs and ("harness", "agent_done") in evs
    # the model was offered RAJ tools only: no shell, no files, no writes
    tools = {t["name"] for t in fake.requests[0]["tools"]}
    assert "mcp__raj__raj_receivables_ageing" in tools and "mcp__raj__tally_gst_summary" in tools
    assert not {t for t in tools if "bash" in t or "pwsh" in t or t in ("read_file", "write_file", "edit")}
    assert not {t for t in tools if "create_voucher" in t or "raw_xml" in t or "delete" in t or "sql" in t}
    system = json.dumps(fake.requests[0].get("system"))
    assert "RAJ Finance Agent" in system and "Demo Traders Pvt Ltd" in system


def test_harness_uses_full_tally_read_tools(dsh):
    e, fake = dsh
    j = ask(e, "What is our GST position?")
    assert j["state"] == "COMPLETED"
    calls = [x["detail"].get("tool") for x in j["events"] if x["event"] == "tool_call"]
    assert "tally_gst_summary" in calls
    rep = j["reports"][0]
    assert rep["title"] == "Gst summary" and rep["meta"]["company"] == "Demo Traders Pvt Ltd"
    assert rep["totals"] or rep["sections"]


def test_harness_cannot_post(dsh):
    e, fake = dsh
    j = ask(e, "Please post a payment voucher of 5000 to rent")
    assert j["state"] == "COMPLETED"
    assert "cannot" in j["result"]["answer"].lower()
    assert not [x for x in j["events"] if x["event"] == "tool_call"]


def test_job_token_is_wiped_after_run(dsh):
    e, fake = dsh
    j = ask(e, "trial balance")
    assert j["reports"][0]["meta"]["tie_out"] == "balanced"
    spec = json.loads((e.app.state.settings.data_dir / "agent-jobs" / j["id"] / "job.json").read_text())
    assert spec["token"] == ""


def test_fabricated_figure_is_flagged_not_verified(dsh):
    """R4: an amount the model writes that no tool returned cannot become a verified result."""
    e, fake = dsh
    fake.fabricate = "Also, cash in hand is ₹9,99,999.00 as of 2026-11-03."
    j = ask(e, "Show receivables older than 90 days")
    v = j["result"]["verification"]
    assert v["status"] == "unverified" and v["unsupported"] == ["9,99,999.00"]
    assert any(x["event"] == "figures_unverified" for x in j["events"])
    fake.fabricate = None
    j = ask(e, "Show receivables older than 90 days")
    assert j["result"]["verification"]["status"] == "verified"
