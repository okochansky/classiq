# ADR 0001 — Async runtime: Redis Streams + custom Python workers

- **Status:** Accepted
- **Date:** 2026-05-14
- **Scope:** the 48-hour deliverable

## Context

The API must accept quantum-circuit submissions in milliseconds and process
them asynchronously on Qiskit's AerSimulator, which is CPU-bound and can run
from tens of milliseconds (a Bell pair) to many seconds (a 20-qubit
parameterized circuit). The brief explicitly requires that no submitted task
is lost during the processing lifecycle, that the system is containerized
with Docker Compose, and that integration tests cover the full lifecycle.

The Python ecosystem offers several reasonable substrates for this pattern:
Celery + RabbitMQ + a result backend, RQ on Redis, Dramatiq, a Kafka topic
with a custom consumer, or a Postgres-backed queue using `SELECT … FOR
UPDATE SKIP LOCKED`.

## Decision

Build the async runtime on **Redis Streams with consumer groups**, with
custom Python workers that own the consume loop directly.

- Producer (API): `XADD tasks:stream * task_id <id>` after the state row
  has been written. The state hash carries the QASM payload and the result;
  only the task id rides on the stream.
- Consumer (worker): `XREADGROUP GROUP workers <consumer> COUNT 1 BLOCK 2000
  STREAMS tasks:stream >`, then dispatches to a shared `process_task`
  function. `XACK` only after the terminal state is committed.
- Crash recovery is delegated to a separate component (see ADR 0002).

## Consequences

**Positive**

- One container in the docker-compose file justifies queue + result store +
  in-flight bookkeeping (Pending Entries List). Reviewers see proportional
  infrastructure for the problem size.
- No framework learning curve under deadline pressure. The consume loop is
  ~25 lines of obvious code; failure modes are inspectable end-to-end.
- Redis Streams' PEL is the right primitive for the no-task-loss guarantee:
  every claim is visible in `XPENDING`, reclaimable by `XAUTOCLAIM`, and the
  delivery counter rides with the entry.
- BLOCK semantics let the worker respond to SIGTERM within 2 s without a
  poll-spin.

**Negative**

- Single point of failure on Redis. AOF (`appendsync everysec`) bounds data
  loss to ~1 s of writes; production would graduate to Sentinel / managed
  Redis or split queue (RabbitMQ/Kafka) from state (Postgres).
- Custom consume code is custom — no framework to take the blame. Mitigated
  by the unit-test coverage of `process_task` and the chaos test of
  end-to-end recovery.

## Alternatives considered

| Option | Why rejected |
| --- | --- |
| **Celery + RabbitMQ + Redis result backend** | Two extra containers, two extra failure modes, and a framework I have not shipped before. Wrong shape for a 48-hour deliverable graded on judgment. |
| **RQ** | Lighter than Celery; same "framework I don't operate" concern. Weaker visibility-timeout / redelivery story than Streams' PEL. |
| **Dramatiq** | Same family of objections as RQ/Celery; smaller community for production examples. |
| **Kafka with manual offset commits** | Production-grade and in my toolbox, but for a single-producer / single-logical-consumer-group workload at home-assignment throughput the JVM compose footprint signals over-provisioning rather than rigor. Reserved for the "production scaling path" in [README §Future work](../../README.md#future-work). |
| **Postgres `SELECT … FOR UPDATE SKIP LOCKED`** | Elegant single-datastore design (Que, Oban, GoodJob all do this) with ACID guarantees for free. Wrong fit *here* because Postgres is not otherwise justified — adding it solely to host a queue is the wrong direction. Becomes the right answer the moment task state needs audit/retention/relational queries. |
| **`asyncio.Queue` / `BackgroundTasks` in-process** | Fails the "no task loss" requirement on the first container restart. |

## Notes

- The `process_task` boundary is intentionally decoupled from the consume
  mechanism: it takes `(redis_client, task_id, entry_id)` and is invoked
  by both the worker (after `XREADGROUP`) and the sweeper (after
  `XAUTOCLAIM`). Migrating the transport later — Kafka in particular —
  reduces to a different producer/consumer wrapping the same function.
