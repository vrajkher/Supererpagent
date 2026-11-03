#!/bin/sh
# Save the RAJ Super-agent settings through dsh-superagent's local Management API.
# Usage: infra/superagent/apply-settings.sh [port]   (requires curl and jq; edit settings/*.json first)
set -eu
api="http://127.0.0.1:${1:-3082}/orchestrator/api"
dir=$(cd "$(dirname "$0")/settings" && pwd)
h='x-orchestrator-request: 1'
if grep -q '<' "$dir"/*.json; then echo "Replace every <placeholder> in $dir first." >&2; exit 2; fi
wait_up() { for _ in $(seq 60); do curl -fs -H "$h" "$api/state" >/dev/null 2>&1 && return 0; sleep 2; done; echo "API not up" >&2; exit 1; }
save() { # block file revision-jq
  rev=$(curl -fs -H "$h" "$api/settings" | jq "$3")
  out=$(curl -s -X PUT -H "$h" -H 'content-type: application/json' "$api/settings/$1" \
        -d "{\"value\": $(cat "$dir/$2"), \"revision\": $rev}")
  echo "$1: $out"
  echo "$out" | jq -e '.restarting == true' >/dev/null 2>&1 && { sleep 3; wait_up; } || true
}
wait_up
save source:github source-github.json '.sources[] | select(.id == "github") | .revision'
save policy policy.json '.blocks.policy.revision'
save executor executor.json '.blocks.executor.revision'
echo "Now paste infra/superagent/prompts/*.md into Settings -> Prompts (team sections) in the panel."
