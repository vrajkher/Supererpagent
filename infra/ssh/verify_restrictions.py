"""Black-box SSH restrictions: synthetic loopback traffic, never a Tally write."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import os
import socket
import socketserver
import subprocess
import threading
import time


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def ssh_options(host, key, port=22, user="rajtunnel", known_hosts=None):
    opts = ["ssh", "-p", str(port), "-i", str(key), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ExitOnForwardFailure=yes"]
    if known_hosts:
        opts += ["-o", f"UserKnownHostsFile={known_hosts}"]
    return opts, f"{user}@{host}"


def stop_process(process):
    if process.poll() is None:
        process.terminate()
    try:
        _, error = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        _, error = process.communicate(timeout=5)
    return error or ""


def wait_listener(port, process, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("SSH exited before listener: " + stop_process(process))
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("SSH listener did not become ready")


@contextmanager
def reverse_forward(opts, destination, remote_port, local_port):
    process = subprocess.Popen(opts + ["-N", "-T", "-R", f"127.0.0.1:{remote_port}:127.0.0.1:{local_port}", destination],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        wait_listener(remote_port, process)
        yield process
    finally:
        stop_process(process)


def check_denied(opts, arguments, expected):
    result = subprocess.run(opts + arguments, capture_output=True, text=True, timeout=8)
    assert result.returncode != 0, "restricted operation succeeded"
    stderr = result.stderr.lower()
    assert "permission denied" not in stderr, "authentication failed; restriction was not exercised"
    assert any(fragment in stderr for fragment in expected), result.stderr


def run_checks(host, key, assigned, port=22, user="rajtunnel", known_hosts=None):
    opts, destination = ssh_options(host, key, port, user, known_hosts)

    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                self.request.settimeout(3)
                data = self.request.recv(128)
                if data:
                    self.request.sendall(data)
            except OSError:
                pass

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo) as echo:
        echo.daemon_threads = True
        thread = threading.Thread(target=echo.serve_forever, daemon=True)
        thread.start()
        local = echo.server_address[1]
        try:
            with reverse_forward(opts, destination, assigned, local):
                with socket.create_connection((host, assigned), timeout=3) as sock:
                    sock.sendall(b"raj-ssh-check")
                    assert sock.recv(128) == b"raj-ssh-check", "assigned forward did not deliver data"
            print("OK assigned loopback reverse forward delivers data", flush=True)
            denied = ("administratively prohibited", "open failed", "request failed")
            check_denied(opts, ["-T", destination, "true"], denied)
            check_denied(opts, ["-tt", destination, "true"], denied)
            check_denied(opts, ["-s", destination, "sftp"], denied)
            print("OK command, PTY and SFTP requests denied", flush=True)
            other = free_port()
            while other == assigned:
                other = free_port()
            for spec in (f"127.0.0.1:{other}:127.0.0.1:{local}", f"0.0.0.0:{assigned}:127.0.0.1:{local}"):
                check_denied(opts, ["-N", "-R", spec, destination], ("remote port forwarding failed",))
            print("OK wrong remote port and wildcard listener denied", flush=True)
            for kind in ("-L", "-D"):
                listener = free_port()
                spec = f"127.0.0.1:{listener}:127.0.0.1:{local}" if kind == "-L" else f"127.0.0.1:{listener}"
                process = subprocess.Popen(opts + ["-N", kind, spec, destination], stdout=subprocess.DEVNULL,
                                           stderr=subprocess.PIPE, text=True)
                try:
                    wait_listener(listener, process)
                    with socket.create_connection(("127.0.0.1", listener), timeout=3) as sock:
                        if kind == "-D":
                            sock.sendall(b"\x05\x01\x00")
                            assert sock.recv(2) == b"\x05\x00"
                            sock.sendall(b"\x05\x01\x00\x01\x7f\x00\x00\x01" + local.to_bytes(2, "big"))
                        else:
                            sock.sendall(b"must-not-arrive")
                        try:
                            response = sock.recv(128)
                        except ConnectionResetError:
                            response = b""
                        assert response == b"" or (kind == "-D" and len(response) >= 2 and response[1] != 0), response
                finally:
                    error = stop_process(process)
                assert "administratively prohibited" in error.lower(), error
                print(f"OK {kind} forwarding channel denied after actual connection", flush=True)
        finally:
            echo.shutdown()
            thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", choices=["127.0.0.1", "localhost"])
    parser.add_argument("key")
    parser.add_argument("assigned_port", type=int)
    parser.add_argument("--port", type=int, default=int(os.environ.get("RAJ_SSH_PORT", "22")))
    parser.add_argument("--user", default=os.environ.get("RAJ_SSH_USER", "rajtunnel"))
    parser.add_argument("--known-hosts", default=os.environ.get("RAJ_SSH_KNOWN_HOSTS_FILE"))
    args = parser.parse_args()
    run_checks(args.host, args.key, args.assigned_port, args.port, args.user, args.known_hosts)


if __name__ == "__main__":
    main()
