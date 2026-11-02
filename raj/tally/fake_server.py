"""A fake TallyPrime XML server for development, demos and tests. NOT Tally.

Understands the request shapes produced by ``xml_client`` and supports fault injection:
``drop_after_import`` (create the voucher, then drop the connection) and switching the open
company to simulate a user changing company in Tally.
"""
from __future__ import annotations

import json
import socket
import threading
import xml.etree.ElementTree as ET
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..calculations import fmt, money

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "demo_company.json"


class FakeTally:
    def __init__(self, fixture: Path | dict = FIXTURE):
        data = json.loads(Path(fixture).read_text("utf-8")) if not isinstance(fixture, dict) else fixture
        self.companies = data["companies"]
        self.open = [c["name"] for c in self.companies]
        self.ledgers = {c["name"]: {l["name"]: dict(l) for l in c["ledgers"]} for c in self.companies}
        self.vouchers = {c["name"]: list(c.get("vouchers", [])) for c in self.companies}
        self.bills = {c["name"]: list(c.get("bills", [])) for c in self.companies}
        self.drop_after_import = 0
        self.import_count = 0
        self._next_id = 1000
        self.lock = threading.Lock()

    # ---------------------------------------------------------------- helpers
    def closing(self, company: str, ledger: str) -> Decimal:
        bal = money(self.ledgers[company][ledger].get("opening", "0"))
        for v in self.vouchers[company]:
            for e in v["entries"]:
                if e["ledger"] == ledger:
                    bal += money(e["amount"]) if e["side"] == "dr" else -money(e["amount"])
        return bal

    def handle(self, body: bytes) -> tuple[bytes, bool]:
        root = ET.fromstring(body)
        req = (root.findtext("HEADER/TALLYREQUEST") or "").strip()
        company = root.findtext(".//SVCURRENTCOMPANY")
        if company is not None and company not in self.open:
            return self._resp(f"<RESPONSE><LINEERROR>Could not set SVCurrentCompany to {company}</LINEERROR><ERRORS>1</ERRORS></RESPONSE>"), False
        if req == "Export":
            return self._export(root, company), False
        if req == "Import Data":
            return self._import(root, company)
        return self._resp("<RESPONSE><LINEERROR>Unknown Request</LINEERROR></RESPONSE>"), False

    @staticmethod
    def _resp(s: str) -> bytes:
        return f"<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DATA>{s}</DATA></BODY></ENVELOPE>".encode()

    def _export(self, root: ET.Element, company: str | None) -> bytes:
        ctype = root.findtext(".//COLLECTION/TYPE")
        coll = ET.Element("COLLECTION")
        neg = lambda d: fmt(-d)  # noqa: E731 -- Tally: debit negative
        if ctype == "Company":
            for c in self.companies:
                if c["name"] in self.open:
                    e = ET.SubElement(coll, "COMPANY", NAME=c["name"])
                    ET.SubElement(e, "GUID").text = c["guid"]
                    ET.SubElement(e, "BOOKSFROM").text = c["books_from"].replace("-", "")
        elif ctype == "Ledger":
            for name, l in self.ledgers[company].items():
                e = ET.SubElement(coll, "LEDGER", NAME=name)
                ET.SubElement(e, "PARENT").text = l["parent"]
                ET.SubElement(e, "OPENINGBALANCE").text = neg(money(l.get("opening", "0")))
                ET.SubElement(e, "CLOSINGBALANCE").text = neg(self.closing(company, name))
        elif ctype == "Voucher":
            fd = root.findtext(".//SVFROMDATE") or "00000000"
            td = root.findtext(".//SVTODATE") or "99999999"
            for v in self.vouchers[company]:
                d = v["date"].replace("-", "")
                if not (fd <= d <= td):
                    continue
                e = ET.SubElement(coll, "VOUCHER", VCHTYPE=v["voucher_type"])
                for tag, key in (("DATE", None), ("VOUCHERTYPENAME", "voucher_type"), ("VOUCHERNUMBER", "number"),
                                 ("REFERENCE", "ref"), ("NARRATION", "narration"), ("GUID", "guid"),
                                 ("MASTERID", "master_id")):
                    ET.SubElement(e, tag).text = d if tag == "DATE" else str(v.get(key, ""))
                for le in v["entries"]:
                    x = ET.SubElement(e, "ALLLEDGERENTRIES.LIST")
                    ET.SubElement(x, "LEDGERNAME").text = le["ledger"]
                    a = money(le["amount"])
                    ET.SubElement(x, "ISDEEMEDPOSITIVE").text = "Yes" if le["side"] == "dr" else "No"
                    ET.SubElement(x, "AMOUNT").text = fmt(-a if le["side"] == "dr" else a)
        elif ctype == "Bill":
            for b in self.bills[company]:
                e = ET.SubElement(coll, "BILL", NAME=b["ref"])
                ET.SubElement(e, "PARENT").text = b["party"]
                ET.SubElement(e, "BILLDATE").text = b["bill_date"].replace("-", "")
                if b.get("due_date"):
                    ET.SubElement(e, "BILLDUEDATE").text = b["due_date"].replace("-", "")
                ET.SubElement(e, "CLOSINGBALANCE").text = neg(money(b["amount"]))
        return self._resp(ET.tostring(coll, encoding="unicode"))

    def _import(self, root: ET.Element, company: str | None) -> tuple[bytes, bool]:
        created, errors, err_text, last = 0, 0, "", ""
        for v in root.iter("VOUCHER"):
            entries = []
            ok = True
            for le in v.iter("ALLLEDGERENTRIES.LIST"):
                name = le.findtext("LEDGERNAME")
                if name not in self.ledgers[company]:
                    ok, err_text = False, f"Ledger '{name}' does not exist!"
                amt = money(le.findtext("AMOUNT"))
                entries.append({"ledger": name, "side": "dr" if amt < 0 else "cr", "amount": fmt(abs(amt))})
            if ok and sum((money(e["amount"]) for e in entries if e["side"] == "dr"), Decimal(0)) != \
                    sum((money(e["amount"]) for e in entries if e["side"] == "cr"), Decimal(0)):
                ok, err_text = False, "Voucher totals do not match!"
            if not ok:
                errors += 1
                continue
            with self.lock:
                self._next_id += 1
                d = v.findtext("DATE")
                self.vouchers[company].append({
                    "guid": f"fake-guid-{self._next_id}", "master_id": str(self._next_id),
                    "date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "voucher_type": v.findtext("VOUCHERTYPENAME"),
                    "number": str(self._next_id), "ref": v.findtext("REFERENCE") or "",
                    "narration": v.findtext("NARRATION") or "", "entries": entries})
                created += 1
                last = str(self._next_id)
        self.import_count += 1
        drop = False
        if created and self.drop_after_import > 0:
            self.drop_after_import -= 1
            drop = True
        xml = (f"<RESPONSE><CREATED>{created}</CREATED><ALTERED>0</ALTERED><ERRORS>{errors}</ERRORS>"
               f"<LASTVCHID>{last}</LASTVCHID><LINEERROR>{err_text}</LINEERROR></RESPONSE>")
        return xml.encode(), drop


def serve(fake: FakeTally, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            out, drop = fake.handle(body)
            if drop:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):  # quiet
            pass

    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Fake Tally XML server (development only)")
    p.add_argument("--port", type=int, default=9000)
    a = p.parse_args()
    srv, port = serve(FakeTally(), port=a.port)
    print(f"Fake Tally listening on 127.0.0.1:{port}")
    threading.Event().wait()
