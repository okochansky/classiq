# Classiq QASM Runner

Production-grade async API for executing QASM3 quantum circuits on the Qiskit AerSimulator.

## Status

Block A — Spine. Stub endpoints, three-service compose, no task processing yet.

## How to run

```bash
docker compose up --build
```

The API listens on `http://localhost:8000`.

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/healthz` | GET | Liveness |
| `/tasks` | POST | Submit a QASM3 circuit (`{"qc": "..."}`) |
| `/tasks/{task_id}` | GET | Retrieve task status / result |

## Architecture (placeholder — populated in Block I)

```
client ──▶ FastAPI ──XADD──▶ Redis Stream ──▶ Aer Worker ──HSET state──▶ Redis
                                                                  ▲
                                                                  │
                            Sweeper ──XPENDING/XCLAIM───────────┘
```

Single Redis instance hosts the work queue (Streams with consumer groups), the in-flight Pending Entries List (PEL), and the task-state hash. A separate sweeper process reclaims entries idle beyond the visibility timeout — the no-task-loss guarantee.

Full design decisions, alternatives considered, and reliability guarantees: see `docs/` (Block I).
