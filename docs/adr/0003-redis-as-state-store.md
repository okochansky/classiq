# ADR 0003 — Redis as both transport and state store

- **Status:** Accepted (with a clear graduation path)
- **Date:** 2026-05-14
- **Scope:** the 48-hour deliverable
- **Depends on:** [ADR 0001](0001-async-runtime-redis-streams.md)

## Context

Once Redis Streams is the chosen transport (ADR 0001), the next question is
where task state lives. Three fields are essential — `status`, `result`,
`error` — plus the metadata needed for recovery (`qc`, `shots`, `attempts`,
`created_at`, `started_at`, `completed_at`). The state is read by the API
(GET /tasks/{id}) and written by both the worker and the sweeper.

The realistic options for a 48-hour deliverable that is graded on
production-grade *judgment*: keep state in Redis (a hash per task), or
introduce Postgres for state while keeping Redis as the transport.

## Decision

Keep task state in Redis. One hash per task: `state:{task_id}`.

```
HSET state:6c2…
  status      pending
  qc          "OPENQASM 3.0; …"
  shots       1024
  attempts    1
  result      {"00":512,"11":512}   # set on completion
  error       "QASM3 parse failed:" # set on failure
  created_at  2026-05-14T14:34:14Z
  started_at  2026-05-14T14:34:14Z
  completed_at 2026-05-14T14:34:14Z
```

Reads via `HGETALL`. Writes via `HSET … mapping={…}` and `HINCRBY` for the
attempts counter.

## Consequences

**Positive**

- One container in compose. One mental model for failure analysis.
- API GET is a single `HGETALL` — sub-ms p50.
- The state machine (`pending → running → completed/failed`) is trivial;
  Redis transactions aren't necessary because every write is a single
  command and the worker is the sole writer between `started` and
  `completed/failed`.

**Negative**

- No historical query path. Retrieving "all tasks failed in the last
  hour" requires `KEYS state:*` + filtering — fine for diagnostics, wrong
  for prod analytics.
- AOF persistence (`appendsync everysec`) bounds data loss to ~1 s of
  writes on a crash. Postgres WAL would be stronger.
- No relational join with users / circuits / experiments — none of which
  exist in this scope.

## Graduation path

The codebase is shaped to make the future migration mechanical:

- `app/processing.py` is the single place where state transitions happen.
  Moving its `HSET`/`HINCRBY` calls to SQL changes ~30 lines and zero
  consumers.
- The Pydantic response models in `app/api/tasks.py` make no assumption
  about the underlying store — they map a flat dict of fields to the spec
  response shape. A Postgres-backed `TaskRepository.get(task_id)` returning
  the same flat dict drops in without touching the routes.

When to migrate (any one is sufficient):

- audit/retention requirements appear,
- a UI needs queryable task history,
- multi-tenancy adds a user/owner column,
- task volume exceeds Redis-as-primary's memory budget.

## Alternatives considered

| Option | Why rejected for this scope |
| --- | --- |
| **Postgres for state, Redis for transport** | The "right" production split, and ADR 0001 explicitly notes Postgres would have been the queue choice if it were already in the stack. Without that justification, adding Postgres solely for a five-field state machine inverts the cost/benefit. |
| **MongoDB / DynamoDB** | No durability or queryability advantage over a Redis hash for this shape. |
| **In-memory dict on the API process** | Fails as soon as there are multiple API replicas (which the architecture supports) or the API restarts. Trivially fails the no-task-loss requirement. |
