"""Bounded, read-only wait evidence for this run's two disposable databases."""
from __future__ import annotations

import re
import threading
from typing import Any

import psycopg

STATES = frozenset(("active", "idle", "idle in transaction", "idle in transaction (aborted)", "fastpath function call", "disabled"))
WAIT_TYPES = frozenset(("Lock", "LWLock", "IO", "IPC", "Timeout", "Client", "BufferPin", "Activity", "Extension"))
LOCK_EVENTS = frozenset(("transactionid", "tuple", "relation", "virtualxid", "advisory", "extend", "object", "frozenid", "spectoken", "applytransaction", "userlock"))
WAIT_EVENTS = {
    "Lock": LOCK_EVENTS,
    "IO": frozenset(("AioIoCompletion", "BuffileRead", "BuffileWrite", "BuffileTruncate", "CopyFileCopy", "CopyFileRead", "CopyFileWrite", "DataFileExtend", "DataFileFlush", "DataFileImmediateSync", "DataFilePrefetch", "DataFileRead", "DataFileSync", "DataFileTruncate", "DataFileWrite", "WalRead", "WalSync", "WalWrite", "WalInitSync", "WalInitWrite")),
    "IPC": frozenset(("BufferIo", "CheckpointDelayComplete", "CheckpointDelayStart", "CheckpointDone", "CheckpointStart")),
}
QUERY = """SELECT pid,state,wait_event_type,wait_event,
 GREATEST(0,EXTRACT(EPOCH FROM (clock_timestamp()-xact_start))*1000)::bigint,
 GREATEST(0,EXTRACT(EPOCH FROM (clock_timestamp()-query_start))*1000)::bigint,
 pg_blocking_pids(pid)
 FROM pg_stat_activity WHERE datname=ANY(%s) AND backend_type='client backend'
 ORDER BY pid LIMIT 129"""


class DatabaseObserver:
    def __init__(self, options: dict[str, Any], databases: list[str]) -> None:
        match = re.fullmatch(r"render_control_native_([a-f0-9]{12})", databases[0]) if len(databases) == 2 else None
        if not match or databases[1] != "render_sub2api_test_" + match[1] or options.get("host") != "127.0.0.1" or options.get("port") != 55438:
            raise ValueError("database_observer_scope_invalid")
        self._options = {key: options[key] for key in ("host", "port", "user", "password") if key in options}
        self._databases = list(databases)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._aliases: dict[int, str] = {}
        self._observations: dict[tuple[Any, ...], dict[str, Any]] = {}
        self._samples = self._row_limit = self._alias_limit = self._observation_limit = self._blocker_limit = 0
        self._failure: str | None = None

    def __repr__(self) -> str:
        return "DatabaseObserver(redacted)"

    def _alias(self, pid: int) -> str:
        if pid in self._aliases:
            return self._aliases[pid]
        if len(self._aliases) == 256:
            self._alias_limit += 1
            return "<capacity>"
        value = "backend-" + str(len(self._aliases) + 1)
        self._aliases[pid] = value
        return value

    def observe(self, rows: list[tuple[Any, ...]]) -> None:
        # Called only by the sampler, or synchronously by deterministic tests.
        self._samples += 1
        self._row_limit += int(len(rows) > 128)
        rows = rows[:128]
        local = {row[0] for row in rows}
        for pid, state, wait_type, wait_event, transaction_age, query_age, blockers in rows:
            transaction_age = min(max(int(transaction_age or 0), 0), 2**31 - 1)
            query_age = min(max(int(query_age or 0), 0), 2**31 - 1) if state == "active" else 0
            if not blockers and transaction_age < 1000 and query_age < 500:
                continue
            backend = self._alias(pid)
            self._blocker_limit += int(len(blockers) > 128)
            bounded_blockers = tuple(sorted({self._alias(value) if value in local else "<outside-sample>" for value in blockers[:128]}))
            normalized_state = state if state in STATES else "other"
            normalized_type = wait_type if wait_type in WAIT_TYPES else "other" if wait_type else "none"
            normalized_event = wait_event if wait_type == "Lock" and wait_event in LOCK_EVENTS else "other" if wait_event else "none"
            wait = wait_event if wait_event in WAIT_EVENTS.get(wait_type, ()) else "other" if wait_event else "none"
            key = (backend, normalized_state, normalized_type, wait, bounded_blockers)
            observation = self._observations.get(key)
            if observation is None:
                if len(self._observations) == 128:
                    self._observation_limit += 1
                    continue
                observation = {"backend": backend, "state": normalized_state, "wait_type": normalized_type, "wait_event": wait, "lock_event": normalized_event, "blockers": list(bounded_blockers), "samples": 0, "maximum_transaction_milliseconds": 0, "maximum_query_milliseconds": 0}
                self._observations[key] = observation
            observation["samples"] += 1
            observation["maximum_transaction_milliseconds"] = max(transaction_age, observation["maximum_transaction_milliseconds"])
            observation["maximum_query_milliseconds"] = max(query_age, observation["maximum_query_milliseconds"])

    def _run(self) -> None:
        try:
            with psycopg.connect(**self._options, dbname="postgres", sslmode="disable", connect_timeout=2, autocommit=True, options="-c default_transaction_read_only=on -c statement_timeout=1000 -c lock_timeout=100") as connection:
                if connection.execute("SELECT current_database(),host(inet_server_addr()),current_setting('port')").fetchone() != ("postgres", "127.0.0.1", "55438"):
                    self._failure = "database_observer_identity_mismatch"
                    return
                while not self._stop.is_set():
                    self.observe(connection.execute(QUERY, (self._databases,)).fetchall())
                    self._ready.set()
                    self._stop.wait(0.1)
        except Exception:
            self._failure = "database_observer_query_unavailable"
        finally:
            self._ready.set()

    def start(self) -> None:
        if self._thread is not None:
            raise ValueError("database_observer_already_started")
        self._thread = threading.Thread(target=self._run, name="native-owned-database-observer", daemon=True)
        self._thread.start()
        if not self._ready.wait(5) or self._failure:
            raise ValueError("database_observer_start_failed")

    def close(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        closed = self._thread is None or not self._thread.is_alive()
        return {"passed": self._failure is None and self._samples > 0 and closed, "read_only": True, "thread_closed": closed, "failure": self._failure if closed else "database_observer_stop_timeout", "samples": self._samples, "backend_aliases": len(self._aliases), "truncated_samples": self._row_limit, "alias_limit_hits": self._alias_limit, "observation_limit_hits": self._observation_limit, "blocker_limit_hits": self._blocker_limit, "observations": list(self._observations.values())}
