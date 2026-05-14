"""Prometheus metrics shared across api / worker / sweeper.

Each process maintains its own counter/histogram state in memory and
exposes /metrics over HTTP. Prometheus scrapes all three jobs and
differentiates via the `job` label from prometheus.yml.
"""
import re

from prometheus_client import Counter, Gauge, Histogram

# Task lifecycle counters
tasks_total = Counter(
    "tasks_total",
    "Tasks transitioning through lifecycle states",
    ["event"],  # accepted, started, completed, failed, reclaimed, idempotent_ack, max_retries
)

# Execution duration, bucketed by qubit count so per-circuit-size SLOs are
# tractable. Buckets span sub-100ms (toy) to a minute (large statevector).
task_duration_seconds = Histogram(
    "task_duration_seconds",
    "Quantum-circuit execution duration on Aer",
    ["qubit_bucket"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)

# Stream backlog — set by the sweeper on every cycle from XPENDING
stream_pending = Gauge(
    "stream_pending",
    "Pending Entries List size (in-flight or stuck claims)",
)
stream_length = Gauge(
    "stream_length",
    "Total entries in tasks:stream (XLEN)",
)

# HTTP request timing populated by middleware in app/main.py
http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "API request duration",
    ["method", "route", "status"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0),
)


_QUBIT_DECL = re.compile(r"qubit\[(\d+)\]")


def qubit_bucket(qc: str) -> str:
    """Categorize a QASM3 circuit by its qubit count for histogram labels."""
    matches = _QUBIT_DECL.findall(qc)
    total = sum(int(n) for n in matches) if matches else 0
    if total <= 5:
        return "1-5"
    if total <= 10:
        return "6-10"
    if total <= 20:
        return "11-20"
    return "20+"
