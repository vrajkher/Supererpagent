"""Job orchestration: read questions, bank reconciliation drafts, approvals, posting, recovery."""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException

from .. import calculations as calc
from ..contracts import (make_execution_envelope, make_job_token, new_id, payload_hash,
                         sha256_hex)
from .bridge import Bridge, BridgeError
from . import provenance
from .db import DB
from .harness import VERSIONS, Harness, ToolSession, ledger_vouchers_paged

APPROVAL_TTL = 30 * 60


def _j(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)


class Service:
    def __init__(self, db: DB, bridge: Bridge, pki, harness: Harness, data_dir: Path):
        self.db, self.bridge, self.pki, self.harness = db, bridge, pki, harness
        self.data_dir = Path(data_dir)

    # ---------------------------------------------------------------- common
    def device_for(self, client_id: str) -> dict:
        d = self.db.one("SELECT * FROM devices WHERE client_id=? AND status='active' ORDER BY created_at DESC LIMIT 1",
                        (client_id,))
        if not d:
            raise HTTPException(409, "no active connector paired for this client")
        return d

    def new_job(self, user: dict, client_id: str, device: dict, jtype: str, request: dict) -> dict:
        jid = new_id()
        now = time.time()
        self.db.x("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (jid, user["org_id"], client_id, device["id"], jtype, "RECEIVED", user["id"], _j(request), None,
                   now, now, _j(VERSIONS)))
        self.db.event(jid, user["email"], "state:RECEIVED", type=jtype)
        return self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))

    def fail(self, job: dict, err: str, state: str = "FAILED") -> None:
        self.db.set_state(job["id"], state, error=err)
        self.db.x("UPDATE jobs SET result=? WHERE id=?", (_j({"error": err}), job["id"]))

    # ---------------------------------------------------------------- ask the books
    def ask(self, user: dict, client: dict, question: str, as_of: str | None) -> dict:
        device = self.device_for(client["id"])
        as_of = as_of or date.today().isoformat()
        job = self.new_job(user, client["id"], device, "read_report", {"question": question, "as_of": as_of})
        self.db.set_state(job["id"], "VALIDATED")
        self.db.set_state(job["id"], "READING")
        try:
            out = self.harness.run_question(job, device, question, as_of, client["name"])
        except BridgeError as e:
            self.fail(job, e.detail, "WAITING_FOR_DEVICE" if e.status in (503, 504) else "FAILED")
            return self.job_view(job["id"])
        except Exception as e:  # noqa: BLE001
            self.fail(job, f"agent error: {type(e).__name__}: {e}")
            return self.job_view(job["id"])
        for rep in out["reports"]:
            self.db.x("INSERT INTO artifacts VALUES (?,?,?,?,?,?)",
                      (new_id(), job["id"], client["id"], "report", _j(rep), time.time()))
        # R4: a figure in the model's text counts only if the tools returned it for this job
        verification = provenance.check(out["answer"], out["reports"], question)
        if verification["status"] == "unverified":
            self.db.event(job["id"], "system", "figures_unverified", unsupported=verification["unsupported"])
        self.db.x("UPDATE jobs SET result=? WHERE id=?",
                  (_j({"answer": out["answer"], "verification": verification}), job["id"]))
        self.db.set_state(job["id"], "COMPLETED")
        return self.job_view(job["id"])

    # ---------------------------------------------------------------- reconciliation
    def reconcile(self, user: dict, client: dict, content: bytes, filename: str, bank_ledger: str,
                  mapping: dict | None, date_format: str | None) -> dict:
        device = self.device_for(client["id"])
        job = self.new_job(user, client["id"], device, "reconcile",
                           {"filename": filename, "bank_ledger": bank_ledger, "mapping": mapping, "date_format": date_format})
        digest = sha256_hex(content)
        d = self.data_dir / "sources" / client["id"]
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{digest}{Path(filename).suffix.lower()[:6]}"
        path.write_bytes(content)
        self.db.x("INSERT INTO sources VALUES (?,?,?,?,?,?,?)",
                  (new_id(), job["id"], client["id"], filename, digest, str(path), time.time()))
        self.db.event(job["id"], user["email"], "source_stored", sha256=digest, filename=filename)

        st = calc.parse_statement(content, filename, mapping, date_format)
        if st.errors:
            self.fail(job, "; ".join(st.errors[:10]), "REJECTED")
            return self.job_view(job["id"])
        self.db.set_state(job["id"], "VALIDATED", rows=len(st.rows), mapping=st.mapping, date_format=st.date_format)

        sess = ToolSession(self.db, self.bridge, self.pki.signing_key, job, device,
                           ("list_ledgers", "ledger_vouchers", "company_info"))
        self.db.set_state(job["id"], "READING")
        try:
            ledgers = sess.tool("list_ledgers")["result"]
            names = [l["name"] for l in ledgers]
            if bank_ledger not in names:
                self.fail(job, f"bank ledger '{bank_ledger}' does not exist in Tally", "REJECTED")
                return self.job_view(job["id"])
            first = min(r["date"] for r in st.rows)
            last = max(r["date"] for r in st.rows)
            comp = sess.tool("company_info")["result"]
            books = comp.get("books_from") or first
            fetch_from = min(books, (date.fromisoformat(first) - timedelta(days=5)).isoformat())
            to = (date.fromisoformat(last) + timedelta(days=5)).isoformat()
            vs, _ = ledger_vouchers_paged(sess, bank_ledger, fetch_from, to)
        except BridgeError as e:
            self.fail(job, e.detail, "WAITING_FOR_DEVICE" if e.status in (503, 504) else "FAILED")
            return self.job_view(job["id"])

        opening = calc.money(next(l["opening"] for l in ledgers if l["name"] == bank_ledger))
        tally_entries, bal_before = [], opening
        for v in vs:
            amt = sum((calc.money(e["signed"]) for e in v["entries"] if e["ledger"] == bank_ledger), Decimal("0.00"))
            if v["date"] < first:
                bal_before += amt
                continue
            tally_entries.append({"voucher_id": v["voucher_id"], "date": v["date"], "amount": calc.fmt(amt),
                                  "ref": v["ref"], "number": v["number"], "narration": v["narration"]})
        matched = calc.match_statement(st.rows, tally_entries)
        used = {vid for r in matched for vid in r["matched_vouchers"] if r["status"] == "matched"}
        tally_only = [t for t in tally_entries if t["voucher_id"] not in used and first <= t["date"] <= last]

        draft_id = new_id()
        counts: dict[str, int] = {}
        for r in matched:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        summary = {
            "statement_opening": st.opening_balance, "statement_closing": st.closing_balance,
            "tally_balance_before_period": calc.fmt(bal_before),
            "opening_difference": calc.fmt(calc.money(st.opening_balance) - bal_before) if st.opening_balance else None,
            "counts": counts, "rows": len(matched), "tally_only": tally_only, "warnings": st.warnings,
            "period": [first, last], "ledgers": names, "source_sha256": digest,
        }
        self.db.x("INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?)",
                  (draft_id, job["id"], client["id"], 1, bank_ledger, user["id"], _j(summary), time.time()))
        for r in matched:
            rid = new_id()
            sugg, voucher, errs = None, None, []
            if r["status"] == "unmatched":
                sugg = calc.suggest_ledger(r["narration"], calc.money(r["signed"]), ledgers)
                if sugg["ledger"]:
                    voucher = calc.draft_voucher_for_row(r, bank_ledger, sugg["ledger"], f"RAJ-{job['id'][:8]}-{r['row_no']}")
                    errs = calc.validate_voucher(voucher, names)
                else:
                    errs = ["choose a ledger"]
            self.db.x("INSERT INTO draft_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (rid, draft_id, r["row_no"], _j(r), r["status"], _j(sugg) if sugg else None,
                       _j(voucher) if voucher else None, _j(errs), 0, "draft" if voucher or r["status"] == "unmatched" else "n/a",
                       None, None, user["id"], time.time()))
        self.db.set_state(job["id"], "DRAFTED", draft_id=draft_id, counts=counts)
        self.db.set_state(job["id"], "WAITING_FOR_APPROVAL")
        return self.job_view(job["id"])

    def _draft(self, draft_id: str, user: dict) -> tuple[dict, dict]:
        d = self.db.one("SELECT * FROM drafts WHERE id=?", (draft_id,))
        if not d:
            raise HTTPException(404, "draft not found")
        job = self.db.one("SELECT * FROM jobs WHERE id=? AND org_id=?", (d["job_id"], user["org_id"]))
        if not job:
            raise HTTPException(404, "draft not found")
        return d, job

    def edit_row(self, user: dict, draft_id: str, row_id: str, ledger: str | None, include: bool | None) -> dict:
        d, job = self._draft(draft_id, user)
        if job["state"] not in ("WAITING_FOR_APPROVAL", "PARTIALLY_COMPLETED", "RESULT_UNKNOWN", "FAILED"):
            raise HTTPException(409, f"job is {job['state']}; cannot edit")
        row = self.db.one("SELECT * FROM draft_rows WHERE id=? AND draft_id=?", (row_id, draft_id))
        if not row or row["match_status"] != "unmatched":
            raise HTTPException(404, "editable row not found")
        if row["post_status"] in ("posted", "unknown", "posting"):
            raise HTTPException(409, "row already sent to Tally")
        names = json.loads(d["summary"])["ledgers"]
        src = json.loads(row["source"])
        voucher = json.loads(row["voucher"]) if row["voucher"] else None
        if ledger is not None:
            if ledger not in names:
                raise HTTPException(422, f"ledger '{ledger}' does not exist in Tally")
            if ledger == d["bank_ledger"]:
                raise HTTPException(422, "contra ledger cannot be the bank ledger itself")
            voucher = calc.draft_voucher_for_row(src, d["bank_ledger"], ledger, f"RAJ-{job['id'][:8]}-{src['row_no']}")
        errs = calc.validate_voucher(voucher, names) if voucher else ["choose a ledger"]
        with self.db.tx() as db:
            db.conn.execute("UPDATE drafts SET version=version+1 WHERE id=?", (draft_id,))
            # any edit invalidates unused approvals for this draft (approval binds draft version)
            db.conn.execute("UPDATE approvals SET revoked=1 WHERE draft_id=? AND used_at IS NULL", (draft_id,))
            db.conn.execute("UPDATE draft_rows SET post_status='draft' WHERE draft_id=? AND post_status='approved'", (draft_id,))
            db.conn.execute("UPDATE draft_rows SET voucher=?, errors=?, include=COALESCE(?, include), edited_by=?, updated_at=? WHERE id=?",
                            (json.dumps(voucher, ensure_ascii=False) if voucher else None, json.dumps(errs),
                             None if include is None else int(include), user["id"], time.time(), row_id))
        self.db.event(job["id"], user["email"], "draft_edited", row_no=src["row_no"], ledger=ledger, include=include)
        return self.job_view(job["id"])

    def approve(self, user: dict, draft_id: str, version: int, row_ids: list[str]) -> dict:
        if user["role"] not in ("reviewer", "admin"):
            raise HTTPException(403, "only reviewers can approve")
        d, job = self._draft(draft_id, user)
        if d["version"] != version:
            raise HTTPException(409, "draft changed since you reviewed it; reload and review again")
        settings = json.loads((self.db.one("SELECT settings FROM orgs WHERE id=?", (user["org_id"],)) or {"settings": "{}"})["settings"])
        device = self.db.one("SELECT * FROM devices WHERE id=?", (job["device_id"],))
        approved = []
        for rid in row_ids:
            row = self.db.one("SELECT * FROM draft_rows WHERE id=? AND draft_id=?", (rid, draft_id))
            if not row or not row["voucher"]:
                raise HTTPException(422, "row has no voucher to approve")
            if json.loads(row["errors"] or "[]"):
                raise HTTPException(422, f"row {row['row_no']} has validation errors")
            if row["post_status"] not in ("draft", "not_posted", "failed"):
                raise HTTPException(409, f"row {row['row_no']} is {row['post_status']}")
            if settings.get("maker_checker") and user["id"] in (d["created_by"], row["edited_by"]):
                raise HTTPException(403, "maker-checker: the preparer cannot approve their own draft")
            voucher = json.loads(row["voucher"])
            if max(Decimal(e["amount"]) for e in voucher["entries"]) > Decimal(str(settings.get("high_value_threshold") or "1e18")) \
                    and user["role"] != "admin":
                raise HTTPException(403, f"row {row['row_no']} exceeds high-value threshold; admin approval needed")
            payload = {**voucher}
            aid = new_id()
            env = make_execution_envelope(self.pki.signing_key, job_id=job["id"], approval_id=aid, draft_id=draft_id,
                                          draft_version=version, row_id=rid, payload=payload,
                                          company_guid=device["company_guid"], device_id=device["id"],
                                          reviewer_id=user["id"], ttl=APPROVAL_TTL)
            self.db.x("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (aid, job["id"], draft_id, version, rid, payload_hash(payload), env, user["id"],
                       time.time() + APPROVAL_TTL, None, 0, time.time()))
            self.db.x("UPDATE draft_rows SET post_status='approved', include=1 WHERE id=?", (rid,))
            approved.append(row["row_no"])
        self.db.event(job["id"], user["email"], "approved", rows=approved, draft_version=version)
        return self.job_view(job["id"])

    def post(self, user: dict, job_id: str) -> dict:
        if user["role"] not in ("reviewer", "admin"):
            raise HTTPException(403, "only reviewers can post")
        job = self.db.one("SELECT * FROM jobs WHERE id=? AND org_id=?", (job_id, user["org_id"]))
        if not job:
            raise HTTPException(404, "job not found")
        device = self.db.one("SELECT * FROM devices WHERE id=?", (job["device_id"],))
        if not device or device["status"] != "active":
            raise HTTPException(409, "connector is not active")
        if not device["write_enabled"]:
            raise HTTPException(403, "writes are not enabled for this connector (admin setting)")
        d = self.db.one("SELECT * FROM drafts WHERE job_id=?", (job_id,))
        rows = self.db.all("SELECT * FROM draft_rows WHERE draft_id=? AND post_status='approved' ORDER BY row_no", (d["id"],))
        if not rows:
            raise HTTPException(409, "nothing approved to post")
        self.db.set_state(job_id, "POSTING", actor=user["email"], rows=[r["row_no"] for r in rows])
        token = make_job_token(self.pki.signing_key, job_id=job_id, org_id=job["org_id"], client_id=job["client_id"],
                               device_id=device["id"], company_guid=device["company_guid"], capability="write",
                               allowed_tools=["create_voucher"])
        for r in rows:
            ap = self.db.one("SELECT * FROM approvals WHERE row_id=? AND used_at IS NULL AND revoked=0 ORDER BY created_at DESC LIMIT 1",
                             (r["id"],))
            if not ap or ap["expires_at"] < time.time() or ap["draft_version"] != d["version"]:
                self.db.x("UPDATE draft_rows SET post_status='draft' WHERE id=?", (r["id"],))
                self.db.event(job_id, "system", "approval_invalid", row_no=r["row_no"])
                continue
            payload = json.loads(r["voucher"])
            if payload_hash(payload) != ap["payload_sha256"]:
                self.db.event(job_id, "system", "approval_hash_mismatch", row_no=r["row_no"])
                continue
            self.db.x("UPDATE draft_rows SET post_status='posting' WHERE id=?", (r["id"],))
            self.db.x("UPDATE approvals SET used_at=? WHERE id=?", (time.time(), ap["id"]))
            try:
                res = self.bridge.call(device["id"], "POST", "/tools/post_voucher", token=token,
                                       json={"envelope": ap["envelope"], "payload": payload})
                outcome = res["state"]
            except BridgeError as e:
                res = {"detail": e.detail}
                outcome = "UNKNOWN" if e.status == 504 else ("OFFLINE" if e.status == 503 else "FAILED")
            self.db.x("INSERT INTO execution_attempts (job_id,row_id,approval_id,ts,outcome,detail) VALUES (?,?,?,?,?,?)",
                      (job_id, r["id"], ap["id"], time.time(), outcome, _j(res)))
            self._apply_outcome(job_id, r, outcome, res)
            if outcome == "UNKNOWN":
                break  # stop the batch: never continue writing past an unknown result
        return self._finish(job_id)

    def _apply_outcome(self, job_id: str, r: dict, outcome: str, res: dict) -> None:
        status = {"VERIFIED": "posted", "REJECTED": "rejected", "NOT_POSTED": "not_posted", "OFFLINE": "approved",
                  "MISMATCH": "unknown", "UNKNOWN": "unknown", "FAILED": "failed"}.get(outcome, "unknown")
        if outcome == "OFFLINE":
            # connection refused: nothing was sent; approval stays usable? No - it was consumed. Re-approve.
            status = "not_posted"
        self.db.x("UPDATE draft_rows SET post_status=?, tally_id=COALESCE(?, tally_id), readback=? WHERE id=?",
                  (status, res.get("tally_id"), _j(res), r["id"]))
        self.db.event(job_id, "gateway", "post_result", row_no=r["row_no"], outcome=outcome,
                      tally_id=res.get("tally_id"), detail=res.get("detail"))

    def _finish(self, job_id: str) -> dict:
        d = self.db.one("SELECT * FROM drafts WHERE job_id=?", (job_id,))
        rows = self.db.all("SELECT post_status FROM draft_rows WHERE draft_id=? AND voucher IS NOT NULL", (d["id"],))
        st = [r["post_status"] for r in rows]
        self.db.set_state(job_id, "VERIFYING")
        if "unknown" in st:
            state = "RESULT_UNKNOWN"
        elif st and all(s == "posted" for s in st):
            state = "COMPLETED"
        elif "posted" in st:
            state = "PARTIALLY_COMPLETED"
        elif any(s in ("approved", "draft", "not_posted") for s in st):
            state = "WAITING_FOR_APPROVAL"
        else:
            state = "FAILED"
        self.db.set_state(job_id, state)
        return self.job_view(job_id)

    def resolve(self, user: dict, job_id: str) -> dict:
        job = self.db.one("SELECT * FROM jobs WHERE id=? AND org_id=?", (job_id, user["org_id"]))
        if not job:
            raise HTTPException(404, "job not found")
        device = self.db.one("SELECT * FROM devices WHERE id=?", (job["device_id"],))
        token = make_job_token(self.pki.signing_key, job_id=job_id, org_id=job["org_id"], client_id=job["client_id"],
                               device_id=device["id"], company_guid=device["company_guid"], capability="read",
                               allowed_tools=["company_info"])
        try:
            res = self.bridge.call(device["id"], "POST", "/tools/reconcile", token=token)
        except BridgeError as e:
            raise HTTPException(e.status if e.status < 500 else 502, e.detail) from e
        for item in res["results"]:
            r = self.db.one("SELECT * FROM draft_rows WHERE id=?", (item["row_id"],))
            if r:
                self._apply_outcome(self.db.one("SELECT job_id FROM drafts WHERE id=?", (r["draft_id"],))["job_id"],
                                    r, item["state"], item)
        self.db.event(job_id, user["email"], "reconciled_unknown", results=res["results"])
        return self._finish(job_id) if self.db.one("SELECT 1 FROM drafts WHERE job_id=?", (job_id,)) else self.job_view(job_id)

    # ---------------------------------------------------------------- views
    def job_view(self, job_id: str) -> dict:
        j = self.db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        j["request"] = json.loads(j["request"])
        j["result"] = json.loads(j["result"]) if j["result"] else None
        j["versions"] = json.loads(j["versions"])
        j["events"] = [{**e, "detail": json.loads(e["detail"] or "{}")} for e in
                       self.db.all("SELECT ts, actor, event, detail FROM job_events WHERE job_id=? ORDER BY id", (job_id,))]
        j["reports"] = [json.loads(a["content"]) for a in self.db.all(
            "SELECT content FROM artifacts WHERE job_id=? AND kind='report' ORDER BY created_at", (job_id,))]
        d = self.db.one("SELECT * FROM drafts WHERE job_id=?", (job_id,))
        if d:
            d["summary"] = json.loads(d["summary"])
            d["rows"] = []
            for r in self.db.all("SELECT * FROM draft_rows WHERE draft_id=? ORDER BY row_no", (d["id"],)):
                for k in ("source", "suggestion", "voucher", "errors", "readback"):
                    r[k] = json.loads(r[k]) if r[k] else None
                d["rows"].append(r)
            j["draft"] = d
        dev = self.db.one("SELECT company_name FROM devices WHERE id=?", (j["device_id"],)) if j["device_id"] else None
        j["company"] = dev["company_name"] if dev else None
        return j
