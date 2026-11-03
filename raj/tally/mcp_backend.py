"""Tally through RAJTALLYFINLMCP, run by the gateway as locked stdio MCP servers.

Two child processes with separate privileges, both pinned to the bound company:

* reader  ``--lock --tool-profile read --access read_only``  — every read, report, GST, TDS and audit tool
* writer  ``--lock --tool-profile post --access read_write --company-guid <guid>`` — read tools plus
  ``tally_create_voucher`` only; started only when the local write switch is on

The gateway's own checks (signed approvals, journal, read-back) sit in front of both. RAJTALLYFINLMCP's
policy is a second, independent wall: even a bug in the gateway cannot reach another company, raw XML,
SQL, TDL, alter or delete through these processes.

Amounts cross this boundary as floats (RAJTALLYFINLMCP's JSON); they are converted to 2-dp Decimals at once.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from contextlib import AsyncExitStack
from decimal import Decimal
from typing import Any

from ..calculations import fmt, money
from .xml_client import TallyError, TallyResponseLost, TallyUnreachable

PAGE = 5000


def _dec(v: Any) -> Decimal:
    return money(repr(v) if isinstance(v, float) else v)


def mcp_command() -> tuple[str, list[str]]:
    """How to start RAJTALLYFINLMCP: ``python -m rajtally_mcp``, or the frozen connector exe's
    ``tally-mcp`` subcommand (PyInstaller build), which runs the same entry point."""
    if getattr(sys, "frozen", False):
        return sys.executable, ["tally-mcp"]
    return sys.executable, ["-m", "rajtally_mcp"]


class _Session:
    """One MCP stdio session kept open on a private event loop thread."""

    def __init__(self, args: list[str], env: dict[str, str], call_timeout: float):
        self.args, self.env, self.call_timeout = args, env, call_timeout
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True, name="rajtally-mcp")
        self.thread.start()
        self.stack: AsyncExitStack | None = None
        self.client = None
        self.tools: set[str] = set()
        self.lock = threading.Lock()

    def _run(self, coro, timeout: float):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _open(self):
        from mcp import StdioServerParameters
        from mcp.client import Client
        self.stack = AsyncExitStack()
        command, prefix = mcp_command()
        params = StdioServerParameters(command=command, args=[*prefix, *self.args], env=self.env)
        self.client = await self.stack.enter_async_context(Client(params))
        self.tools = {t.name for t in (await self.client.list_tools()).tools}

    async def _close(self):
        if self.stack:
            await self.stack.aclose()
        self.stack, self.client = None, None

    def ensure(self) -> None:
        with self.lock:
            if self.client is None:
                try:
                    self._run(self._open(), 60)
                except Exception as e:  # noqa: BLE001
                    self.client = None
                    raise TallyError(f"could not start RAJTALLYFINLMCP: {type(e).__name__}: {e}") from e

    def call(self, tool: str, args: dict, *, is_write: bool = False) -> Any:
        self.ensure()
        if tool not in self.tools:
            raise TallyError(f"tool not available under this connector's policy: {tool}")
        try:
            res = self._run(self.client.call_tool(tool, args), self.call_timeout)
        except Exception as e:  # noqa: BLE001 - transport died or timed out
            self.restart()
            if is_write:
                raise TallyResponseLost(f"MCP {type(e).__name__}") from e
            raise TallyError(f"RAJTALLYFINLMCP call failed: {type(e).__name__}") from e
        if res.is_error:
            text = res.content[0].text if res.content else "error"
            if "Cannot reach Tally" in text:
                raise TallyUnreachable(text)
            if is_write and "did not answer within" in text:
                raise TallyResponseLost(text)
            raise TallyError(text)
        if res.structured_content is not None:
            return res.structured_content
        import json
        txt = res.content[0].text if res.content else ""
        try:
            return json.loads(txt)
        except ValueError:
            return txt

    def restart(self) -> None:
        with self.lock:
            try:
                self._run(self._close(), 10)
            except Exception:  # noqa: BLE001
                pass
            self.client = None

    def close(self) -> None:
        self.restart()
        self.loop.call_soon_threadsafe(self.loop.stop)


class McpTallyBackend:
    """Same interface as ``TallyClient``, backed by RAJTALLYFINLMCP."""

    def __init__(self, *, company: str, company_guid: str, host: str = "127.0.0.1", port: int = 9000,
                 timeout: int = 60, extra_env: dict[str, str] | None = None):
        self.company, self.company_guid = company, company_guid
        base = {k: v for k, v in os.environ.items() if not k.startswith(("TALLY_", "RAJTALLY_"))}
        base.update({"TALLY_HOST": host, "TALLY_PORT": str(port), "TALLY_TIMEOUT": str(timeout),
                     "TALLY_COMPANY": company, "RAJTALLY_SETTINGS": os.devnull, **(extra_env or {})})
        lock = ["--lock", "--company", company]
        self.reader = _Session(lock + ["--tool-profile", "read", "--access", "read_only",
                                       "--access-ceiling", "read_only"], base, timeout + 15)
        self._writer_args = lock + ["--tool-profile", "post", "--access", "read_write", "--access-ceiling",
                                    "read_write", "--company-guid", company_guid]
        self._env, self._timeout = base, timeout
        self.writer: _Session | None = None

    # ------------------------------------------------------------ helpers
    def _writer(self) -> _Session:
        if self.writer is None:
            self.writer = _Session(self._writer_args, self._env, self._timeout + 15)
        return self.writer

    def read_tools(self) -> set[str]:
        self.reader.ensure()
        return set(self.reader.tools)

    def call(self, tool: str, args: dict | None = None) -> Any:
        """Pass-through for any tool in the reader's (read-only) profile."""
        return self.reader.call(tool, args or {})

    def _pages(self, tool: str, key: str, args: dict) -> list[dict]:
        out, offset = [], 0
        while True:
            r = self.reader.call(tool, {**args, "limit": PAGE, "offset": offset})
            rows = r.get(key, [])
            out += rows
            if not r.get("more") or not rows:
                return out
            offset += len(rows)

    @staticmethod
    def _entries(v: dict) -> list[dict]:
        out = []
        for e in v.get("entries", []):
            signed = _dec(e.get("dr", 0)) - _dec(e.get("cr", 0))
            out.append({"ledger": e["ledger"], "side": "dr" if signed > 0 else "cr",
                        "amount": fmt(abs(signed)), "signed": fmt(signed)})
        return out

    def _voucher(self, v: dict) -> dict:
        return {"voucher_id": v.get("guid") or v.get("master_id"), "master_id": v.get("master_id"),
                "date": v["date"], "voucher_type": v.get("type", ""), "number": v.get("number", ""),
                "ref": v.get("reference", "") or "", "narration": v.get("narration", "") or "",
                "entries": self._entries(v)}

    # ------------------------------------------------------------ TallyClient interface
    def open_companies(self) -> list[dict]:
        r = self.reader.call("tally_list_companies", {})
        return [{"name": c["name"], "guid": c.get("guid", ""), "books_from": c.get("books_from", "")}
                for c in r.get("companies", [])]

    def ledgers(self, company: str) -> list[dict]:
        out = []
        for m in self._pages("tally_list_masters", "masters", {"master_type": "ledger"}):
            def signed(amount_key, type_key):
                a = _dec(m.get(amount_key, 0))
                return -a if (m.get(type_key) or "").lower() == "cr" else a
            out.append({"name": m["name"], "parent": m.get("parent", ""),
                        "opening": fmt(signed("opening_balance", "opening_balance_type")),
                        "closing": fmt(signed("closing_balance", "closing_balance_type"))})
        return out

    def vouchers(self, company: str, from_date: str, to_date: str) -> list[dict]:
        rows = self._pages("tally_list_vouchers", "vouchers",
                           {"from_date": from_date, "to_date": to_date, "include_entries": True})
        return [self._voucher(v) for v in rows if not v.get("cancelled") and not v.get("optional")]

    def bills(self, company: str) -> list[dict]:
        out = []
        for kind, sign in (("receivable", 1), ("payable", -1)):
            r = self.reader.call("tally_outstandings", {"kind": kind})
            for b in r.get("bills", []):
                out.append({"ref": b.get("bill", ""), "party": b["party"], "bill_date": b["bill_date"],
                            "due_date": b.get("due_date") or None, "amount": fmt(_dec(b["pending"]) * sign)})
        return out

    def create_voucher(self, company: str, v: dict) -> dict:
        entries = [{"ledger": e["ledger"], e["side"]: e["amount"]} for e in v["entries"]]
        r = self._writer().call("tally_create_voucher", {
            "voucher_type": v["voucher_type"], "date": v["date"], "entries": entries,
            "narration": f"{v.get('narration', '')} [{v['ref']}]".strip(), "reference": v["ref"]}, is_write=True)
        errs = r.get("line_errors") or []
        return {"created": int(r.get("created", 0)), "errors": int(r.get("errors", 0)) + len(errs),
                "error_text": "; ".join(errs) or r.get("note", ""), "last_vch_id": r.get("master_id", "")}

    def find_by_ref(self, company: str, ref: str, on_date: str) -> list[dict]:
        r = self.reader.call("tally_list_vouchers", {"from_date": on_date, "to_date": on_date, "search": ref,
                                                    "include_entries": True, "limit": 50})
        return [self._voucher(v) for v in r.get("vouchers", [])
                if not v.get("cancelled") and ((v.get("reference") or "") == ref or f"[{ref}]" in (v.get("narration") or ""))]

    def close(self) -> None:
        self.reader.close()
        if self.writer:
            self.writer.close()


def list_companies(host: str = "127.0.0.1", port: int = 9000, timeout: int = 30) -> list[dict]:
    """Open companies, for pairing (before a company is bound): a short-lived, unlocked, read-only session."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TALLY_", "RAJTALLY_"))}
    env.update({"TALLY_HOST": host, "TALLY_PORT": str(port), "RAJTALLY_SETTINGS": os.devnull})
    s = _Session(["--tool-profile", "read", "--access", "read_only", "--access-ceiling", "read_only",
                  "--allow-tools", "tally_list_companies"], env, timeout)
    try:
        r = s.call("tally_list_companies", {})
        return [{"name": c["name"], "guid": c.get("guid", ""), "books_from": c.get("books_from", "")}
                for c in r.get("companies", [])]
    finally:
        s.close()
