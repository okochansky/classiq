import logging
import os
import signal
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("classiq.worker")

_stop = False


def _handle_sigterm(signum, frame):
    global _stop
    _stop = True
    logger.info("worker.sigterm signum=%d", signum)


def main() -> None:
    worker_id = os.environ.get("WORKER_ID", "worker")
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    logger.info("worker.alive worker_id=%s", worker_id)
    while not _stop:
        time.sleep(1)
    logger.info("worker.exiting worker_id=%s", worker_id)


if __name__ == "__main__":
    main()
