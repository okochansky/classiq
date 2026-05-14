# ADR 0002 — No-task-loss via a sweeper + XAUTOCLAIM

- **Status:** Accepted
- **Date:** 2026-05-14
- **Scope:** the 48-hour deliverable
- **Depends on:** [ADR 0001](0001-async-runtime-redis-streams.md)

## Context

The brief is explicit: no submitted task is lost during the processing
lifecycle. Within ADR 0001's Redis-Streams substrate, the failure modes
that have to survive are:

1. The worker container crashes (SIGKILL, OOM, host failure) between
   `XREADGROUP` and `XACK`.
2. The worker completes the simulation and writes the result, but is killed
   before `XACK` fires.
3. The broker (Redis) restarts.
4. A submitted task is for some reason malformed or unprocessable.

The mechanism must be visible (an operator can prove tasks aren't lost), and
the recovery path must be deterministically testable.

## Decision

Add a **dedicated `sweeper` service** that periodically reclaims stale PEL
entries via `XAUTOCLAIM` and processes them via the same `process_task`
function the worker uses.

Components:

- `app/sweeper/main.py` runs the loop. Every `SWEEPER_INTERVAL_S` (default
  2 s), call `XAUTOCLAIM tasks:stream workers sweeper-1 MIN-IDLE-TIME
  SWEEPER_IDLE_MS START 0-0 COUNT 10`. For each entry returned, run
  `process_task`.
- `process_task` is idempotent on terminal state: re-deliveries of a task
  in `completed`/`failed` are `XACK`'d without re-execution (covers failure
  mode 2).
- Per-task `attempts` counter (`HINCRBY state:{id} attempts 1`) caps
  retries at `MAX_ATTEMPTS_DEFAULT` (3). The fourth delivery transitions
  the task to `failed` with `max_retries_exceeded`.
- Sweeper gauges (`stream_pending`, `stream_length`) make the queue visible
  in Grafana, so an operator can see backlog and reclaim rate at a glance.

## Consequences

**Positive**

- Single mechanism handles all four failure modes. The chaos test
  (`scripts/chaos.sh`) drives a deterministic recovery in ~12 s end-to-end.
- Sweeper is a separate process, so a buggy worker can't take down recovery
  (and vice versa). Both run from the same image — operationally cheap.
- `XAUTOCLAIM` is the right primitive: it iterates the PEL in cursor order,
  returns entries inline (no separate `XCLAIM` + `XREADGROUP` dance), and
  the delivery counter rides with the entry.

**Negative**

- Recovery latency is bounded by `SWEEPER_IDLE_MS` — too low triggers
  reclaim storms on slow circuits, too high stretches the SLA. Demo
  defaults (10 s idle, 2 s interval) make for a snappy chaos test;
  production would tune toward 60–120 s idle.
- `attempts` is in the state hash, not on the stream entry. A re-delivery
  burns one attempt even if it's just a transient network blip mid-XACK.
  Acceptable for this scope; finer-grained transient-vs-terminal
  classification is future work.

## Alternatives considered

| Option | Why rejected |
| --- | --- |
| **Worker reclaims its own PEL on restart via XREADGROUP `0`** | Works for "the same worker comes back" but not for "the worker container is gone forever and the new one has a different consumer name." A dedicated sweeper covers both. |
| **Have each worker periodically sweep** | Couples failure modes. A bug in worker shutdown could prevent the sweep. Operational clarity wins from a single recovery process. |
| **Push retries back onto the stream via XADD** | Doubles entries, breaks the link between original `task_id`-on-stream and PEL bookkeeping. Cleaner to reclaim in place. |
| **Sweeper just XCLAIMs and re-deposits to the stream** | Same XADD-storm problem; also creates a window where the entry exists on the stream AND in the (now stale) PEL. |
| **At-most-once + DLQ** | Wrong default for a system that explicitly requires no task loss. |

## Test plan

- Unit (fakeredis): `tests/test_sweeper.py` covers reclaim, retry cap,
  terminal-state ack, and the empty-PEL no-op.
- E2E (live compose): `scripts/chaos.sh` constructs a "dead worker"
  scenario deterministically — `HSET` + `XADD` + ghost-consumer
  `XREADGROUP` puts an orphan in the PEL, then asserts the API reports
  `completed` within SLA.
