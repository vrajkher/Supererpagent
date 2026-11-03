#!/bin/bash
# Isolated OpenSSH integration test for the RAJ tunnel account (roadmap Step 2 / R2).
#
# Starts a throwaway sshd on 127.0.0.1:${PORT:-2222} with infra/ssh/sshd_raj_tunnel.conf, a synthetic
# device key and database, and a synthetic gateway; never touches the host's own sshd configuration.
# Every check is an explicit assertion; the script exits non-zero if any fails.
#
# Needs root (sshd, test users) and: openssh-server, openssh-client, python3, curl, iproute2 (ss).
for c in sshd ssh sftp ssh-keygen ss curl python3; do command -v $c >/dev/null || PATH=$PATH:/usr/sbin; command -v $c >/dev/null || { echo "missing: $c"; exit 2; }; done
# Usage: sudo infra/ssh/ci-sshd-test.sh
set -u
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY all_proxy NO_PROXY no_proxy   # probes decide their own routing
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PORT=${PORT:-2222}
T=$(mktemp -d /tmp/raj-sshd-XXXXXX)
ROOT=/opt/raj-sshd-test            # root-owned, as sshd requires for AuthorizedKeysCommand
ASSIGNED=20001 OTHER=20002 GW=18765
FAIL=0
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAIL=1; }

cleanup() {
  [ -n "${GW_PID:-}" ] && kill "$GW_PID" 2>/dev/null
  [ -n "${TUN_PID:-}" ] && kill "$TUN_PID" 2>/dev/null
  [ -f "$T/sshd.pid" ] && kill "$(cat "$T/sshd.pid")" 2>/dev/null
  [ -n "${KEEP:-}" ] && cp "$T/sshd.log" /tmp/claude-0/sshd-daemon.log; rm -rf "$T" "$ROOT"
}
trap cleanup EXIT

id rajtunnel >/dev/null 2>&1 || useradd -m -s /usr/sbin/nologin rajtunnel
id raj-keys >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin raj-keys
passwd -d rajtunnel >/dev/null 2>&1 || true   # unlocked account (key-only), as in production setup
mkdir -p /run/sshd "$ROOT" && chmod 755 "$ROOT"
install -m 755 -o root -g root "$REPO/infra/ssh/raj-authorized-keys" "$ROOT/raj-authorized-keys"
install -m 755 -o root -g root "$REPO/infra/ssh/raj-kill-tunnel" "$ROOT/raj-kill-tunnel"

ssh-keygen -q -t ed25519 -N '' -f "$T/host_key"
ssh-keygen -q -t ed25519 -N '' -C device-a -f "$T/device_a"
ssh-keygen -q -t ed25519 -N '' -C device-b -f "$T/device_b"   # never registered
DB="$ROOT/raj.sqlite3"
python3 - "$DB" "$(cat "$T/device_a.pub")" "$ASSIGNED" <<'EOF'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("CREATE TABLE devices (id TEXT, ssh_pubkey TEXT, tunnel_port INTEGER, status TEXT)")
db.execute("INSERT INTO devices VALUES ('dev-a', ?, ?, 'active')", (sys.argv[2].strip(), int(sys.argv[3])))
db.commit()
EOF
chown root:raj-keys "$DB"; chmod 640 "$DB"

sed -e "s#/opt/raj/infra/ssh/raj-authorized-keys %u /var/lib/raj/server/raj.sqlite3#$ROOT/raj-authorized-keys %u $DB#" \
    "${CONF:-$REPO/infra/ssh/sshd_raj_tunnel.conf}" > "$T/match.conf"
cat > "$T/sshd_config" <<EOF
Port $PORT
ListenAddress 127.0.0.1
HostKey $T/host_key
PidFile $T/sshd.pid
UsePAM no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
LogLevel VERBOSE
Subsystem sftp internal-sftp
$(cat "$T/match.conf")
EOF
/usr/sbin/sshd -t -f "$T/sshd_config" && pass "sshd accepts the RAJ configuration" || { fail "sshd -t"; exit 1; }
/usr/sbin/sshd -f "$T/sshd_config" -E "$T/sshd.log"
sleep 1

# synthetic gateway on the "client PC"
python3 -c "
import http.server, socketserver
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'raj-gateway-ok')
    def log_message(self, *a): pass
socketserver.TCPServer.allow_reuse_address = True
socketserver.TCPServer(('127.0.0.1', $GW), H).serve_forever()" &
GW_PID=$!
for _ in $(seq 40); do [ "$(curl --noproxy "*" -s --max-time 1 "http://127.0.0.1:$GW/")" = "raj-gateway-ok" ] && break; sleep 0.25; done
[ "$(curl --noproxy "*" -s --max-time 1 "http://127.0.0.1:$GW/")" = "raj-gateway-ok" ] || { fail "synthetic gateway did not start"; exit 1; }

echo "$(echo -n "[127.0.0.1]:$PORT "; cut -d' ' -f1,2 "$T/host_key.pub")" > "$T/known_hosts"
SSH="ssh -F none -p $PORT -o BatchMode=yes -o ConnectTimeout=5 -o UserKnownHostsFile=$T/known_hosts -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes"

# 1. the assigned loopback reverse forward works end to end
$SSH -i "$T/device_a" -N -R "127.0.0.1:$ASSIGNED:127.0.0.1:$GW" rajtunnel@127.0.0.1 &
TUN_PID=$!
for _ in $(seq 40); do ss -ltnH "sport = :$ASSIGNED" | grep -q ":$ASSIGNED" && break; sleep 0.25; done
if kill -0 "$TUN_PID" 2>/dev/null && [ "$(curl --noproxy "*" -s --max-time 3 "http://127.0.0.1:$ASSIGNED/")" = "raj-gateway-ok" ]; then
  pass "assigned loopback reverse forward reaches the gateway"
else fail "assigned reverse forward"; curl --noproxy "*" -sv --max-time 3 "http://127.0.0.1:$ASSIGNED/" 2>&1 | tail -5; kill -0 "$TUN_PID" && echo tunnel-alive; fi
if ss -ltnH "sport = :$ASSIGNED" | grep -q "127.0.0.1:$ASSIGNED"; then pass "listener bound to loopback only"; else fail "listener address"; fi

# 2. another port is refused
if timeout 10 $SSH -i "$T/device_a" -N -R "127.0.0.1:$OTHER:127.0.0.1:$GW" rajtunnel@127.0.0.1 2>/dev/null; then
  fail "a different remote port was accepted"; else
  ss -ltnH "sport = :$OTHER" | grep -q ":$OTHER" && fail "port $OTHER is listening" || pass "a different remote port is refused"; fi

# 3. a public listener request cannot produce a public listener (GatewayPorts no + permitlisten)
timeout 6 $SSH -i "$T/device_a" -N -R "0.0.0.0:$OTHER:127.0.0.1:$GW" rajtunnel@127.0.0.1 2>/dev/null &
sleep 3
if ss -ltnH | grep -E "(0\.0\.0\.0|\*|\[::\]):$OTHER\b" >/dev/null; then fail "a public listener was created"; else pass "no public listener can be created"; fi
wait $! 2>/dev/null

# 4. no command execution
OUT=$(timeout 10 $SSH -i "$T/device_a" rajtunnel@127.0.0.1 'echo RAJ-SHELL-OPEN' 2>&1)
echo "$OUT" | grep -q RAJ-SHELL-OPEN && fail "a remote command ran" || pass "remote commands do not run"

# 5. no PTY / interactive shell
OUT=$(timeout 10 $SSH -tt -i "$T/device_a" rajtunnel@127.0.0.1 </dev/null 2>&1)
echo "$OUT" | grep -qi "PTY allocation request failed\|not available\|nologin" && pass "no interactive shell or PTY" || {
  echo "$OUT" | grep -q '\$ \|# ' && fail "interactive shell" || pass "no interactive shell or PTY"; }

# 6. no SFTP
timeout 10 sftp -F none -P "$PORT" -o BatchMode=yes -o UserKnownHostsFile="$T/known_hosts" -o IdentitiesOnly=yes \
  -i "$T/device_a" rajtunnel@127.0.0.1 <<<"ls" >/dev/null 2>&1 && fail "SFTP session opened" || pass "SFTP is refused"

# 7. local forwarding: open a connection THROUGH the listener (creating the listener alone proves nothing)
timeout 8 $SSH -i "$T/device_a" -N -o ExitOnForwardFailure=no -L "127.0.0.1:19999:127.0.0.1:$GW" rajtunnel@127.0.0.1 2>/dev/null &
sleep 2
[ "$(curl --noproxy "*" -s --max-time 3 http://127.0.0.1:19999/ 2>/dev/null)" = "raj-gateway-ok" ] && fail "local forward reached a server-side destination" \
  || pass "local forwarding cannot reach anything"
wait $! 2>/dev/null

# 8. dynamic (SOCKS) forwarding
timeout 8 $SSH -i "$T/device_a" -N -o ExitOnForwardFailure=no -D 127.0.0.1:19998 rajtunnel@127.0.0.1 2>/dev/null &
sleep 2
curl -s --max-time 3 --socks5-hostname 127.0.0.1:19998 "http://127.0.0.1:$GW/" 2>/dev/null | grep -q raj-gateway-ok \
  && fail "dynamic forwarding reached a destination" || pass "dynamic forwarding cannot reach anything"
wait $! 2>/dev/null

# 9. agent forwarding is refused
OUT=$(timeout 10 $SSH -A -v -i "$T/device_a" -N -o ExitOnForwardFailure=no rajtunnel@127.0.0.1 2>&1 & sleep 3; kill %1 2>/dev/null)
grep -q "auth-agent" "$T/sshd.log" && grep -qi "agent forwarding disabled\|refused" "$T/sshd.log" && pass "agent forwarding refused" \
  || { grep -qi "auth-agent-req@openssh.com.*accepted\|Agent forwarding enabled" "$T/sshd.log" && fail "agent forwarding accepted" || pass "agent forwarding not granted"; }

# 10. an unregistered key and a wrong host key are refused
timeout 10 $SSH -i "$T/device_b" -N -R "127.0.0.1:$OTHER:127.0.0.1:$GW" rajtunnel@127.0.0.1 2>/dev/null && fail "unregistered key accepted" || pass "unregistered key refused"
ssh-keygen -q -t ed25519 -N '' -f "$T/other_host"
echo "[127.0.0.1]:$PORT $(cut -d' ' -f1,2 "$T/other_host.pub")" > "$T/wrong_known_hosts"
timeout 10 ssh -F none -p "$PORT" -o BatchMode=yes -o UserKnownHostsFile="$T/wrong_known_hosts" -o StrictHostKeyChecking=yes \
  -i "$T/device_a" -N rajtunnel@127.0.0.1 2>/dev/null && fail "host-key mismatch accepted" || pass "host-key mismatch refused"

# 11. revocation: new logins refused AND the established tunnel is closed
python3 -c "import sqlite3,sys; db=sqlite3.connect(sys.argv[1]); db.execute(\"UPDATE devices SET status='revoked'\"); db.commit()" "$DB"
timeout 10 $SSH -i "$T/device_a" -N -R "127.0.0.1:$OTHER:127.0.0.1:$GW" rajtunnel@127.0.0.1 2>/dev/null && fail "revoked key accepted" || pass "revoked key refused for new logins"
"$ROOT/raj-kill-tunnel" "$ASSIGNED" >/dev/null
for _ in $(seq 40); do kill -0 "$TUN_PID" 2>/dev/null || break; sleep 0.25; done
kill -0 "$TUN_PID" 2>/dev/null && fail "established tunnel survived revocation" || pass "established tunnel closed on revocation"
curl --noproxy "*" -s --max-time 2 "http://127.0.0.1:$ASSIGNED/" >/dev/null && fail "listener still answers" || pass "listener gone after revocation"

echo
if [ "$FAIL" -ne 0 ]; then echo "sshd integration: FAILED"; tail -30 "$T/sshd.log"; exit 1; fi
echo "sshd integration: all checks passed"
