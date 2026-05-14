"""Stand-alone Prometheus /metrics HTTP server for non-FastAPI processes.

prometheus_client's start_http_server runs a daemon thread, which plays
nicely with asyncio event loops in the worker and sweeper.
"""
from prometheus_client import start_http_server


def start_metrics_server(port: int) -> None:
    start_http_server(port)
