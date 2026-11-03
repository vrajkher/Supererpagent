"""Tool-backed numeric provenance for model answers (roadmap R4).

The model may explain results, but a figure it writes is accepted only if the same amount occurs in
the data the tools returned for this job (the saved reports). Anything else is listed as unsupported
and the answer is marked ``unverified``; the portal shows the report tables as the source of truth.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

# amounts: Indian/Western grouping, or decimals, or 4+ digit integers; not parts of dates, ids or words
AMOUNT = re.compile(r"(?<![\w.\-/])(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+\.\d{1,2}|\d{4,})(?![\w\-/]|\.\d)")


def _norm(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(",", "")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def figures(text: str) -> dict[str, Decimal]:
    out = {}
    for m in AMOUNT.finditer(text or ""):
        tok = m.group(1)
        d = _norm(tok)
        if d is None:
            continue
        if "," not in tok and "." not in tok and 1900 <= d <= 2100:   # a bare year
            continue
        out[tok] = d
    return out


def _numbers(obj: Any, acc: set[Decimal]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        d = _norm(repr(obj))
        if d is not None:
            acc.update({d, abs(d)})
    elif isinstance(obj, str):
        for d in figures(obj).values():
            acc.update({d, abs(d)})
        d = _norm(obj.strip()) if re.fullmatch(r"-?[\d,]+(\.\d+)?", obj.strip() or "x") else None
        if d is not None:
            acc.update({d, abs(d)})
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers(v, acc)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers(v, acc)


def check(answer: str, reports: list[dict], question: str = "") -> dict:
    """Returns {"status": verified | unverified | no-figures, "unsupported": [...], "checked": n}."""
    found = figures(answer)
    asked = set(figures(question).values())
    claimed = {tok: d for tok, d in found.items() if d not in asked}
    if not claimed:
        return {"status": "no-figures", "unsupported": [], "checked": 0}
    supported: set[Decimal] = set()
    _numbers(reports, supported)
    bad = [tok for tok, d in claimed.items() if d not in supported]
    return {"status": "unverified" if bad else "verified", "unsupported": bad, "checked": len(claimed)}
