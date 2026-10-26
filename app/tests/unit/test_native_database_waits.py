from __future__ import annotations

import json

import pytest

from scripts.native_database_observer import DatabaseObserver


def observer() -> DatabaseObserver:
    suffix = "a" * 12
    return DatabaseObserver({"host": "127.0.0.1", "port": 55438}, ["render_control_native_" + suffix, "render_sub2api_test_" + suffix])


@pytest.mark.parametrize("event", ["AioIoCompletion", "CopyFileCopy", "DataFileRead", "DataFileImmediateSync", "BuffileWrite", "WalSync", "WalWrite", "DataFileSync"])
def test_io_wait_is_preserved_separately_from_legacy_lock_event(event: str) -> None:
    sample = observer()
    sample.observe([(50001, "active", "IO", event, 600, 600, [])])
    report = sample.close()
    assert report["passed"]
    wait = report["observations"][0]
    assert (wait["wait_type"], wait["wait_event"], wait["lock_event"]) == ("IO", event, "other")
    assert "50001" not in json.dumps(report)


@pytest.mark.parametrize("event", ["BufferIo", "CheckpointDone", "CheckpointStart"])
def test_buffer_and_checkpoint_ipc_waits_remain_distinct(event: str) -> None:
    sample = observer()
    sample.observe([(50001, "active", "IPC", event, 600, 600, [])])
    wait = sample.close()["observations"][0]
    assert (wait["wait_type"], wait["wait_event"], wait["lock_event"]) == ("IPC", event, "other")


@pytest.mark.parametrize("kind,event", [("IO", "tuple"), ("Lock", "DataFileRead"), ("IPC", "DataFileRead"), ("PRIVATE-TYPE", "WalSync"), ("IO", "PRIVATE-EVENT\nPRIVATE-SECRET"), ("IO", "WALSync")])
def test_unknown_and_mismatched_wait_labels_are_not_exported(kind: str, event: str) -> None:
    sample = observer()
    sample.observe([(50001, "active", kind, event, 600, 600, [])])
    report = sample.close()
    assert report["observations"][0]["wait_event"] == "other"
    assert report["observations"][0]["lock_event"] == "other"
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.parametrize("event", ["tuple", "transactionid", "relation"])
def test_lock_wait_retains_legacy_field_and_adds_the_same_generic_event(event: str) -> None:
    sample = observer()
    sample.observe([(50001, "active", "Lock", event, 600, 600, [50002]), (50002, "idle in transaction", "Client", "ClientRead", 2000, 0, [])])
    report = sample.close()
    wait = report["observations"][0]
    assert (wait["wait_event"], wait["lock_event"]) == (event, event)
    assert wait["blockers"] == ["backend-2"]


def test_slow_active_query_without_wait_is_explicitly_none() -> None:
    sample = observer()
    sample.observe([(50001, "active", None, None, 600, 600, [])])
    wait = sample.close()["observations"][0]
    assert (wait["wait_type"], wait["wait_event"], wait["lock_event"]) == ("none", "none", "none")


def test_two_io_stages_on_one_backend_do_not_merge() -> None:
    sample = observer()
    for event in ("DataFileRead", "WalSync", "DataFileRead"):
        sample.observe([(50001, "active", "IO", event, 700, 600, [])])
    report = sample.close()
    assert report["backend_aliases"] == 1
    assert {item["wait_event"]: item["samples"] for item in report["observations"]} == {"DataFileRead": 2, "WalSync": 1}


def test_unknown_dynamic_wait_names_cannot_expand_report_cardinality() -> None:
    sample = observer()
    for index in range(1000):
        sample.observe([(50001, "active", "IO", "PRIVATE-" + str(index), 600, 600, [])])
    report = sample.close()
    assert report["passed"] and report["backend_aliases"] == 1
    assert len(report["observations"]) == 1 and report["observation_limit_hits"] == 0
    assert report["observations"][0]["wait_event"] == "other"
    assert report["observations"][0]["samples"] == 1000
    assert "PRIVATE" not in json.dumps(report)
