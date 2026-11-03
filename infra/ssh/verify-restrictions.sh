#!/bin/sh
# Run on a disposable SSH test server with a synthetic paired key and unused assigned port.
# Usage: verify-restrictions.sh 127.0.0.1 <key> <assigned_port>
# Optional: RAJ_SSH_PORT, RAJ_SSH_KNOWN_HOSTS_FILE.
set -eu
exec python3 "$(dirname "$0")/verify_restrictions.py" "$@"
