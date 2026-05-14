"""Structured JSON logging via structlog.

Every log line is a JSON object on stdout: ELK / Loki / journald can all
parse it directly. Each log call attaches keyword fields as top-level
keys, so `log.info("task.completed", task_id=tid, duration_s=0.42)`
yields a clean record without ad-hoc string formatting.

Use `bind_contextvars(task_id=...)` inside a coroutine / request handler
to thread a correlation id through every subsequent log line in the same
async context without passing it explicitly.
"""
import logging
import os
import sys

import structlog


def configure_logging(service: str) -> None:
    """Wire structlog into stdlib logging so foreign loggers (uvicorn,
    qiskit, redis) also emit JSON. Idempotent."""
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    numeric_level = getattr(logging, log_level, logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(numeric_level)

    # Quiet third-party loggers that emit per-operation chatter at INFO
    for noisy in ("qiskit", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Inject service id into every log line for multi-process aggregation
    structlog.contextvars.bind_contextvars(service=service)
