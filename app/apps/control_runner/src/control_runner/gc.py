from __future__ import annotations

import argparse
import json
import logging
import signal
import threading

from generation_infrastructure.database import make_sessions
from generation_infrastructure.garbage_collection import ObjectGarbageCollector
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import S3Store, make_store


def main(settings: Settings | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = settings or Settings()
    sessions = make_sessions(settings)
    store = make_store(settings)
    stopped = threading.Event()

    def shutdown(signum: int, frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        if isinstance(store, S3Store):
            store.check_private_versioned_bucket()
        collector = ObjectGarbageCollector(sessions, store)
        while not stopped.is_set():
            try:
                result = collector.tick()
                if result["claimed"]:
                    logging.info(json.dumps({"event": "object_gc", **result}))
            except Exception as exc:
                logging.error(json.dumps({"event": "object_gc_failed", "exception": type(exc).__name__}))
                if arguments.once:
                    raise
            if arguments.once:
                return
            stopped.wait(5)
    finally:
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
