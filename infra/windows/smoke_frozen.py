"""Smoke-test a built raj-connector executable (CI job windows-connector; also works on Linux).

    python infra/windows/smoke_frozen.py dist/raj-connector.exe

Runs --help, the bundled RAJTALLYFINLMCP entry (tally-mcp --help), and `companies --backend mcp`
against RAJTALLYFINLMCP's demo Tally, so the frozen EXE must start, import everything and speak MCP
over stdio to its own child. Exits non-zero on any failure.
"""
import socket
import subprocess
import sys

from rajtally_mcp import demo_tally


def run(args, expect):
    r = subprocess.run(args, capture_output=True, text=True, timeout=180)
    ok = r.returncode == 0 and expect in r.stdout
    print(("PASS" if ok else "FAIL"), " ".join(args[1:]) or "(exe)", "" if ok else f"\n{r.stdout[-800:]}\n{r.stderr[-1500:]}")
    return ok


def main() -> int:
    exe = sys.argv[1]
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv, data = demo_tally.start(port)
    try:
        results = [
            run([exe, "--help"], "raj-connector"),
            run([exe, "tally-mcp", "--help"], "rajtally-mcp"),
            run([exe, "companies", "--tally", f"http://127.0.0.1:{port}", "--backend", "mcp"], "Demo Traders Pvt Ltd"),
        ]
    finally:
        srv.shutdown()
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
