"""Run one finance question as a DeepSeek Harness session.

For each job the server:

1. fetches the connector's read-tool catalog over the mTLS bridge and mints a short-lived read token;
2. writes a private job file and a Cordis patch for the ``sdk-minimal`` profile that
   * removes the shell, file and working-directory tools,
   * sets the finance persona,
   * mounts exactly one MCP server: ``raj.server.agent_mcp`` (stdio) for this job;
3. drives the session through the DeepSeek Harness Python SDK (``deepseek-harness-sdk``) with the
   per-client Harness home, then ingests the reports and tool events the bridge wrote;
4. wipes the token from the job file.

The Harness model therefore sees only RAJ tools, bound to one client, one company and read access.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from ..contracts import READ_TOOLS, make_job_token
from ..pki import device_hostname
from .bridge import Bridge
from .db import DB

PERSONA = ("You are RAJ Finance Agent, assisting a Chartered and Cost Accountant firm in India with its "
           "clients' Tally books. You only read the books through the RAJ tools provided.")

PATCH = """# generated per job by raj.server.dsh_worker - do not edit
- id: persistent-bash
  disabled: true
- id: persistent-pwsh
  disabled: true
- id: tool-fs
  disabled: true
- id: tool-working-directory
  disabled: true
- id: system-prompt
  config:
    includeHarnessIdentity: false
    includeRuntimeContext: false
    personaPrefix: {persona}
- insert:
    - id: mcp-raj
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: raj
        transport: stdio
        command: {python}
        args: ['-m', 'raj.server.agent_mcp']
        env:
          RAJ_AGENT_JOB: {job_file}
          PYTHONPATH: {pythonpath}
        toolCallTimeoutMs: 180000
        failOnStartupError: true
        reconnect:
          enabled: false
"""


def available() -> bool:
    try:
        import deepseek_harness  # noqa: F401
        return True
    except ImportError:
        return False


class DshRunner:
    def __init__(self, db: DB, bridge: Bridge, pki, data_dir: Path, *, model: str | None = None,
                 base_url: str | None = None, api_key: str | None = None, profile: str = "sdk-minimal"):
        self.db, self.bridge, self.pki = db, bridge, pki
        self.data_dir = Path(data_dir).resolve()
        self.model = model or os.environ.get("RAJ_MODEL", "deepseek-flash")
        self.base_url = base_url if base_url is not None else os.environ.get("DEEPSEEK_BASE_URL")
        self.api_key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY")
        self.profile = profile

    def _token(self, job: dict, device: dict, tools: list[str], ttl: int) -> str:
        return make_job_token(self.pki.signing_key, job_id=job["id"], org_id=job["org_id"], client_id=job["client_id"],
                              device_id=device["id"], company_guid=device["company_guid"], capability="read",
                              allowed_tools=tools, ttl=ttl)

    def run_question(self, job: dict, device: dict, question: str, as_of: str, client_name: str) -> dict:
        from deepseek_harness import DeepSeekHarness

        jdir = self.data_dir / "agent-jobs" / job["id"]
        (jdir / "ws").mkdir(parents=True, exist_ok=True)
        home = self.data_dir / "dsh-home" / job["client_id"]   # one Harness home per client: no shared history
        home.mkdir(parents=True, exist_ok=True)

        catalog = self.bridge.call(device["id"], "POST", "/tools/list", token=self._token(job, device, [], 120))["tools"]
        tools = list(READ_TOOLS) + [t["name"] for t in catalog if t.get("kind") == "tally"]
        token = self._token(job, device, tools, 20 * 60)
        job_file = jdir / "job.json"
        spec = {"job_id": job["id"], "client_name": client_name, "company_name": device["company_name"],
                "as_of": as_of, "device_id": device["id"], "gateway_url": self.bridge.route(device["id"]),
                "sni_hostname": device_hostname(device["id"]), "device_ca": self.pki.device_ca_path,
                "bridge_cert": self.pki.bridge_cert_path, "bridge_key": self.pki.bridge_key_path,
                "token": token, "allowed_tools": tools, "catalog": catalog, "out_dir": str(jdir)}
        fd = os.open(job_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(spec, f)
        pythonpath = os.pathsep.join(p for p in [str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH", "")] if p)
        patch = jdir / "raj.patch.yml"
        patch.write_text(PATCH.format(persona=json.dumps(PERSONA), python=json.dumps(sys.executable),
                                      job_file=json.dumps(str(job_file)), pythonpath=json.dumps(pythonpath)), "utf-8")

        self.db.event(job["id"], "harness", "plan", planner="deepseek-harness", model=self.model, profile=self.profile)
        started = time.time()
        try:
            with DeepSeekHarness(dsh_home=str(home), cwd=str(jdir / "ws"), profile=self.profile, patches=(str(patch),),
                                 provider="deepseek-official", model=self.model, base_url=self.base_url,
                                 api_key=self.api_key) as h:
                prompt = f"Report date: {as_of}.\n\n{question}"
                result = h.run(prompt, session_id=f"job-{job['id']}")
            answer, finish = result.final_response or "", result.finish_reason
            if not (jdir / "bridge.started").exists():
                raise RuntimeError("the RAJ tool bridge did not start inside DeepSeek Harness; no answer was produced")
        finally:
            spec["token"] = ""   # the token never outlives the run on disk
            job_file.write_text(json.dumps(spec), "utf-8")
            self._ingest_events(job["id"], jdir)
        self.db.event(job["id"], "harness", "agent_done", finish_reason=finish, seconds=round(time.time() - started, 1))
        reports = []
        rp = jdir / "reports.jsonl"
        if rp.exists():
            reports = [json.loads(line) for line in rp.read_text("utf-8").splitlines() if line.strip()]
        if finish == "error" and not answer:
            answer = "The agent stopped with an error; see the audit trail."
        shutil.rmtree(jdir / "ws", ignore_errors=True)
        return {"answer": answer, "reports": reports}

    def _ingest_events(self, job_id: str, jdir: Path) -> None:
        ev = jdir / "events.jsonl"
        if not ev.exists():
            return
        for line in ev.read_text("utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            self.db.event(job_id, e.get("actor", "agent-bridge"), e.get("event", "?"), **(e.get("detail") or {}))
        ev.unlink()
