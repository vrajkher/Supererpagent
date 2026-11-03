"""TallyPrime XML-over-HTTP adapter (runs only on the client PC, talks to localhost:9000).

Uses inline-TDL collection exports and IMPORTDATA voucher creation. VERIFY every request
shape against the pinned TallyPrime release in the compatibility matrix before go-live.

Tally's sign convention: negative amount == Debit. This module converts everything to the
RAJ convention (debit-positive) at the boundary.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from ..calculations import fmt, money

MAX_RESPONSE_BYTES = 20 * 1024 * 1024


class TallyError(Exception):
    pass


class TallyUnreachable(TallyError):
    pass


class TallyResponseLost(TallyError):
    """The request may have reached Tally but no response was received."""


def _tdate(d: str | date) -> str:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return d.strftime("%Y%m%d")


def _idate(s: str) -> str:
    s = (s or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _txt(el: ET.Element | None, tag: str) -> str:
    if el is None:
        return ""
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else ""


def _tally_amount(el: ET.Element, tag: str) -> Decimal:
    raw = _txt(el, tag)
    if not raw:
        return Decimal("0.00")
    # Tally may emit "1000.00 Dr"/"Cr" in some exports; plain signed numbers in collections.
    return -money(raw) if not raw.endswith(("Dr", "Cr")) else money(raw)


class TallyClient:
    def __init__(self, url: str = "http://127.0.0.1:9000", timeout: float = 30.0):
        self.url = url
        self.timeout = timeout

    # ------------------------------------------------------------ transport
    def _post(self, body: bytes, *, is_write: bool = False) -> ET.Element:
        try:
            with httpx.Client(timeout=self.timeout) as c:
                with c.stream("POST", self.url, content=body,
                              headers={"Content-Type": "text/xml; charset=utf-8"}) as r:
                    chunks, size = [], 0
                    for ch in r.iter_bytes():
                        size += len(ch)
                        if size > MAX_RESPONSE_BYTES:
                            raise TallyError("Tally response too large; narrow the period")
                        chunks.append(ch)
                    data = b"".join(chunks)
        except httpx.ConnectError as e:
            raise TallyUnreachable("Tally is not reachable on the configured port") from e
        except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout) as e:
            if is_write:
                raise TallyResponseLost(str(e)) from e
            raise TallyError(f"Tally read failed: {e}") from e
        try:
            return ET.fromstring(data.decode("utf-8", errors="replace"))
        except ET.ParseError as e:
            raise TallyError("Tally returned malformed XML") from e

    @staticmethod
    def _collection_request(coll_type: str, fields: list[str], company: str | None,
                            from_date: str | None = None, to_date: str | None = None,
                            fetch: list[str] | None = None) -> bytes:
        env = ET.Element("ENVELOPE")
        h = ET.SubElement(env, "HEADER")
        ET.SubElement(h, "VERSION").text = "1"
        ET.SubElement(h, "TALLYREQUEST").text = "Export"
        ET.SubElement(h, "TYPE").text = "Collection"
        ET.SubElement(h, "ID").text = f"RAJ{coll_type}"
        desc = ET.SubElement(ET.SubElement(env, "BODY"), "DESC")
        sv = ET.SubElement(desc, "STATICVARIABLES")
        ET.SubElement(sv, "SVEXPORTFORMAT").text = "$$SysName:XML"
        if company:
            ET.SubElement(sv, "SVCURRENTCOMPANY").text = company
        if from_date:
            ET.SubElement(sv, "SVFROMDATE").text = _tdate(from_date)
        if to_date:
            ET.SubElement(sv, "SVTODATE").text = _tdate(to_date)
        coll = ET.SubElement(ET.SubElement(ET.SubElement(desc, "TDL"), "TDLMESSAGE"), "COLLECTION",
                             NAME=f"RAJ{coll_type}", ISMODIFY="No")
        ET.SubElement(coll, "TYPE").text = coll_type
        for f in fields:
            ET.SubElement(coll, "NATIVEMETHOD").text = f
        for f in fetch or []:
            ET.SubElement(coll, "FETCH").text = f
        return ET.tostring(env, encoding="utf-8")

    # ------------------------------------------------------------ reads
    def open_companies(self) -> list[dict]:
        root = self._post(self._collection_request("Company", ["Name", "GUID", "BooksFrom"], None))
        return [{"name": c.get("NAME") or _txt(c, "NAME"), "guid": _txt(c, "GUID"),
                 "books_from": _idate(_txt(c, "BOOKSFROM"))} for c in root.iter("COMPANY")]

    def ledgers(self, company: str) -> list[dict]:
        root = self._post(self._collection_request(
            "Ledger", ["Name", "Parent", "OpeningBalance", "ClosingBalance"], company))
        out = []
        for l in root.iter("LEDGER"):
            out.append({"name": l.get("NAME") or _txt(l, "NAME"), "parent": _txt(l, "PARENT"),
                        "opening": fmt(_tally_amount(l, "OPENINGBALANCE")),
                        "closing": fmt(_tally_amount(l, "CLOSINGBALANCE"))})
        return out

    def vouchers(self, company: str, from_date: str, to_date: str) -> list[dict]:
        root = self._post(self._collection_request(
            "Voucher", ["Date", "VoucherTypeName", "VoucherNumber", "Reference", "Narration", "GUID",
                        "MasterID"], company, from_date, to_date, fetch=["AllLedgerEntries"]))
        out = []
        for v in root.iter("VOUCHER"):
            entries = []
            for e in v.iter("ALLLEDGERENTRIES.LIST"):
                amt = _tally_amount(e, "AMOUNT")  # debit-positive now
                entries.append({"ledger": _txt(e, "LEDGERNAME"), "side": "dr" if amt > 0 else "cr",
                                "amount": fmt(abs(amt)), "signed": fmt(amt)})
            d = _idate(_txt(v, "DATE"))
            if not (from_date <= d <= to_date):
                continue
            out.append({"voucher_id": _txt(v, "GUID") or _txt(v, "MASTERID"), "master_id": _txt(v, "MASTERID"),
                        "date": d, "voucher_type": _txt(v, "VOUCHERTYPENAME") or v.get("VCHTYPE", ""),
                        "number": _txt(v, "VOUCHERNUMBER"), "ref": _txt(v, "REFERENCE"),
                        "narration": _txt(v, "NARRATION"), "entries": entries})
        return out

    def bills(self, company: str) -> list[dict]:
        root = self._post(self._collection_request(
            "Bill", ["Name", "Parent", "BillDate", "ClosingBalance", "BillDueDate"], company))
        out = []
        for b in root.iter("BILL"):
            out.append({"ref": b.get("NAME") or _txt(b, "NAME"), "party": _txt(b, "PARENT"),
                        "bill_date": _idate(_txt(b, "BILLDATE")),
                        "due_date": _idate(_txt(b, "BILLDUEDATE")) or None,
                        "amount": fmt(_tally_amount(b, "CLOSINGBALANCE"))})
        return out

    # ------------------------------------------------------------ write
    def create_voucher(self, company: str, v: dict[str, Any]) -> dict:
        env = ET.Element("ENVELOPE")
        ET.SubElement(ET.SubElement(env, "HEADER"), "TALLYREQUEST").text = "Import Data"
        imp = ET.SubElement(ET.SubElement(env, "BODY"), "IMPORTDATA")
        rd = ET.SubElement(imp, "REQUESTDESC")
        ET.SubElement(rd, "REPORTNAME").text = "Vouchers"
        ET.SubElement(ET.SubElement(rd, "STATICVARIABLES"), "SVCURRENTCOMPANY").text = company
        msg = ET.SubElement(ET.SubElement(imp, "REQUESTDATA"), "TALLYMESSAGE")
        vel = ET.SubElement(msg, "VOUCHER", VCHTYPE=v["voucher_type"], ACTION="Create")
        ET.SubElement(vel, "DATE").text = _tdate(v["date"])
        ET.SubElement(vel, "VOUCHERTYPENAME").text = v["voucher_type"]
        ET.SubElement(vel, "REFERENCE").text = v["ref"]
        ET.SubElement(vel, "NARRATION").text = f"{v.get('narration', '')} [{v['ref']}]".strip()
        for e in v["entries"]:
            le = ET.SubElement(vel, "ALLLEDGERENTRIES.LIST")
            ET.SubElement(le, "LEDGERNAME").text = e["ledger"]
            is_dr = e["side"] == "dr"
            ET.SubElement(le, "ISDEEMEDPOSITIVE").text = "Yes" if is_dr else "No"
            a = money(e["amount"])
            ET.SubElement(le, "AMOUNT").text = fmt(-a if is_dr else a)
        root = self._post(ET.tostring(env, encoding="utf-8"), is_write=True)
        created = int(_txt(root, "CREATED") or 0) if root.tag == "RESPONSE" else int(_txt(root.find(".//RESPONSE"), "CREATED") or 0)
        resp = root if root.tag == "RESPONSE" else root.find(".//RESPONSE")
        errors = int(_txt(resp, "ERRORS") or 0) if resp is not None else 1
        line_err = _txt(resp, "LINEERROR") if resp is not None else "no RESPONSE element"
        return {"created": created, "errors": errors, "error_text": line_err,
                "last_vch_id": _txt(resp, "LASTVCHID") if resp is not None else ""}

    def find_by_ref(self, company: str, ref: str, on_date: str) -> list[dict]:
        return [v for v in self.vouchers(company, on_date, on_date)
                if v["ref"] == ref or f"[{ref}]" in (v["narration"] or "")]
