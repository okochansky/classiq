#!/usr/bin/env bash
# Headline reliability proof: a task delivered to a worker that NEVER acks
# (i.e. crashed mid-flight) is recovered by the sweeper and completed.
#
# We construct the "dead worker" scenario deterministically via redis-cli:
#   1. Create a state row for a new task.
#   2. XADD it to the stream.
#   3. A "ghost-worker" consumer XREADGROUPs the entry → it moves into the
#      Pending Entries List (PEL). The ghost never exists, never acks.
#   4. The sweeper's XAUTOCLAIM picks up entries idle > SWEEPER_IDLE_MS and
#      processes them via the shared `process_task` path.
#   5. The API's GET /tasks/<id> eventually reports status=completed.
#
# Usage:
#   ./scripts/chaos.sh
#
# Pre: `docker compose up -d --build` is running.

set -euo pipefail

cd "$(dirname "$0")/.."

if ! curl -fsS http://localhost:8000/healthz >/dev/null; then
  echo "FATAL: API not reachable at http://localhost:8000 — start the stack first:"
  echo "  docker compose up -d --build"
  exit 1
fi

TID="chaos-$(python3 -c 'import uuid; print(uuid.uuid4())')"
QASM='OPENQASM 3.0;
include "stdgates.inc";
qubit[2] q;
bit[2] c;
h q[0];
cx q[0], q[1];
c[0] = measure q[0];
c[1] = measure q[1];
'

echo "==> Task id under test: $TID"

echo
echo "==> Step 1: stop the live worker so the real consumer can't intercept"
docker compose stop worker >/dev/null

echo "==> Step 2: write the state row directly (simulates the API having accepted the task)"
docker compose exec -T redis redis-cli HSET "state:$TID" \
  status pending \
  qc   "$QASM" \
  shots 1024 >/dev/null

echo "==> Step 3: XADD onto tasks:stream"
docker compose exec -T redis redis-cli XADD tasks:stream '*' task_id "$TID" >/dev/null

echo "==> Step 4: ghost-worker XREADGROUPs the entry into its PEL (will never ack)"
docker compose exec -T redis redis-cli XREADGROUP \
  GROUP workers ghost-worker COUNT 1 STREAMS tasks:stream '>' >/dev/null

echo
echo "==> PEL state (should show one entry owned by ghost-worker):"
docker compose exec -T redis redis-cli XPENDING tasks:stream workers

echo
echo "==> Step 5: restart the worker — it will only see NEW entries via XREADGROUP '>',"
echo "    NOT the entry now sitting in ghost-worker's PEL"
docker compose start worker >/dev/null

echo
echo "==> Polling GET /tasks/$TID until the sweeper reclaims and completes it"
echo "    SLA: SWEEPER_IDLE_MS (10s) + SWEEPER_INTERVAL_S (2s) + sim (<1s) + slack"
START=$(date +%s)
DEADLINE=$((START + 25))
FINAL=""
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  STATUS_JSON=$(curl -fsS "http://localhost:8000/tasks/$TID")
  T=$(( $(date +%s) - START ))
  echo "    [${T}s] $STATUS_JSON"
  case "$STATUS_JSON" in
    *'"completed"'*) FINAL="$STATUS_JSON"; break ;;
    *'"error"'*) echo "FAILED: task hit error state"; echo "$STATUS_JSON"; exit 1 ;;
  esac
  sleep 1
done

if [ -z "$FINAL" ]; then
  echo "FAILED: task did not complete within SLA"
  echo "    Inspect: docker compose logs sweeper"
  exit 1
fi

echo
echo "==> Final state"
echo "$FINAL" | python3 -m json.tool

echo "$FINAL" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['status'] == 'completed', d
total = sum(d['result'].values())
keys = set(d['result'].keys())
assert total == 1024, f'expected 1024 shots, got {total}'
assert keys.issubset({'00', '11'}), f'non-Bell outcomes: {keys}'
"

echo
echo "==> PEL after recovery (should be 0):"
docker compose exec -T redis redis-cli XPENDING tasks:stream workers

echo
echo "*** PASS: orphaned PEL entry recovered by sweeper, task completed with valid Bell distribution ***"
