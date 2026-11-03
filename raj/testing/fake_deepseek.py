"""Fake DeepSeek Messages API (Anthropic-style SSE) for offline tests of the DeepSeek Harness worker.

NOT a model. A scripted policy: on a user turn it calls the first tool whose name matches the
question's keywords; once tool results are present it answers with a short text that quotes them.
Every request body is kept in ``requests`` so tests can inspect what the Harness sent.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# keyword -> preferred tool-name suffix, checked in order
ROUTES = (
    (r"receivabl|debtor|લેણ", "raj_receivables_ageing"),
    (r"payabl|creditor|owe", "raj_payables_ageing"),
    (r"trial", "raj_trial_balance"),
    (r"gst", "tally_gst_summary"),
    (r"profit|p&l|p ?& ?l", "tally_profit_loss"),
    (r"balance sheet", "tally_balance_sheet"),
    (r"ledger", "raj_ledger_statement"),
    (r"post|create voucher|delete", "tally_create_voucher"),
)


class FakeDeepSeek:
    def __init__(self):
        self.requests: list[dict] = []
        self.forced_tool: tuple[str, dict] | None = None   # (tool suffix, input) to force a call

    def respond(self, body: dict) -> list[tuple[str, dict]]:
        self.requests.append(body)
        msgs = body.get("messages", [])
        tools = [t["name"] for t in body.get("tools", []) if "name" in t]
        last = msgs[-1] if msgs else {}
        results = [b for b in (last.get("content") if isinstance(last.get("content"), list) else [])
                   if isinstance(b, dict) and b.get("type") == "tool_result"]
        if results:
            text = json.dumps(results[0].get("content"), ensure_ascii=False)
            nums = re.findall(r"\d[\d,]*\.\d\d", text)
            return self._text(f"Done. Key figures from the tool: {', '.join(nums[:3]) or 'see report'}.")
        question = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                c = m.get("content")
                question = c if isinstance(c, str) else " ".join(b.get("text", "") for b in c if isinstance(b, dict))
                break
        want, args = None, {}
        if self.forced_tool:
            want, args = self.forced_tool
        else:
            for pat, suffix in ROUTES:
                if re.search(pat, question, re.I):
                    want = suffix
                    break
        match = next((t for t in tools if want and t.endswith(want)), None)
        if not match:
            return self._text("I cannot do that with the tools available to me.")
        if want.endswith("ageing") and not args:
            m = re.search(r"(\d+)\s*(days|દિવસ)", question)
            args = {"min_days": int(m.group(1)) + 1} if m else {}
        return self._tool(match, args)

    @staticmethod
    def _start():
        return ("message_start", {"type": "message_start", "message": {
            "id": "msg_" + uuid.uuid4().hex[:12], "type": "message", "role": "assistant", "model": "deepseek-flash",
            "content": [], "stop_reason": None, "usage": {"input_tokens": 10, "output_tokens": 1}}})

    def _text(self, text: str):
        return [self._start(),
                ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}}),
                ("message_stop", {"type": "message_stop"})]

    def _tool(self, name: str, args: dict):
        return [self._start(),
                ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {
                    "type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:10], "name": name, "input": {}}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                         "delta": {"type": "input_json_delta", "partial_json": json.dumps(args)}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 5}}),
                ("message_stop", {"type": "message_stop"})]


def serve(fake: FakeDeepSeek, port: int = 0):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 - model listing, if asked
            body = json.dumps({"data": [{"id": "deepseek-flash", "type": "model"}]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("content-length", 0)))
            if not self.path.rstrip("/").endswith("/messages"):
                self.send_response(404)
                self.send_header("content-length", "0")
                self.end_headers()
                return
            events = fake.respond(json.loads(raw))
            out = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]
