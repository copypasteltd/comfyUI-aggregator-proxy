from __future__ import annotations

import argparse
import json
import logging
import signal
import threading

from generation_infrastructure.database import make_sessions
from generation_infrastructure.maintenance import Maintenance
from generation_infrastructure.memory_reclamation import MemoryReclaimer
from generation_infrastructure.settings import Settings


def main(settings: Settings | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = settings or Settings()
    sessions = make_sessions(settings)
    maintenance = Maintenance(sessions, settings.access_log_retention_days, settings.audit_retention_days)
    memory = MemoryReclaimer(sessions, settings)
    stopped = threading.Event()

    def shutdown(signum: int, frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        while not stopped.is_set():
            try:
                result = maintenance.tick()
                result.update(memory.tick())
                if any(result.values()):
                    logging.info(json.dumps({"event": "maintenance", **result}))
            except Exception as exc:
                logging.error(json.dumps({"event": "maintenance_failed", "exception": type(exc).__name__}))
                if args.once:
                    raise
            if args.once:
                return
            stopped.wait(2)
    finally:
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
