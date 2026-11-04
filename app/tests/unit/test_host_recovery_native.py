from __future__ import annotations

import socket
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from generation_contracts.recovery import (
    HOST_RESTART_PROTOCOL,
    HostRecoveryCheckpoint,
    Reconciliation,
)
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint
from pydantic import ValidationError
from windows_worker.configuration import LocalChannel
from windows_worker.host_recovery import (
    NativeHostObservation,
    host_recovery_fields,
    local_endpoint,
    observe_native_host,
    windows_host,
)
from windows_worker.journal import Journal


def sample(uptime: int = 900000) -> NativeHostObservation:
    return NativeHostObservation("a" * 64, uptime, "GPU-SYNTHETIC", fingerprint({"base_url": "http://127.0.0.1:8188"}))


@pytest.mark.parametrize("uptime,restarted", [(900100, False), (900000, False), (899001, False), (899000, True), (1000, True)])
def test_only_strict_uptime_reset_produces_a_stop_proof(uptime: int, restarted: bool) -> None:
    record = {"checkpoint": sample().checkpoint(), "server_digest": "b" * 64}
    result = host_recovery_fields(sample(uptime), record)
    assert (result.get("observation") == "host_restarted") is restarted
    assert ("host_restart" in result) is restarted
    if not restarted:
        assert result == {"host_checkpoint": record["checkpoint"]}


def test_unacknowledged_checkpoint_cannot_authorize_a_stop_after_reset() -> None:
    original = sample().checkpoint()
    assert host_recovery_fields(sample(1000), {"checkpoint": original, "server_digest": None}) == {"host_checkpoint": original}


@pytest.mark.parametrize("field,value", [("host_identity_digest", "c" * 64), ("gpu_hardware_id", "GPU-OTHER"), ("channel_digest", "c" * 64)])
def test_reset_on_a_different_host_gpu_or_channel_is_rejected(field: str, value: str) -> None:
    original = sample().checkpoint()
    altered = {**sample(1000).__dict__, field: value}
    with pytest.raises(DomainError, match="主机、GPU 或渠道"):
        host_recovery_fields(NativeHostObservation(**altered), {"checkpoint": original, "server_digest": "b" * 64})


@pytest.mark.parametrize("uptime", [0, -1, True, 1.5, 2**63])
def test_checkpoint_requires_a_bounded_native_integer(uptime: Any) -> None:
    with pytest.raises(ValidationError):
        HostRecoveryCheckpoint.model_validate({**sample().checkpoint(), "uptime_ms": uptime})


def test_checkpoint_survives_restart_and_cannot_be_replaced(tmp_path: Path) -> None:
    baseline = sample().checkpoint()
    with_journal = Journal(tmp_path)
    with_journal.accept_claim({"attempt_id": "att_fixture", "execution_digest": "a" * 64})
    with_journal.save_host_checkpoint("att_fixture", baseline)
    with_journal.acknowledge_host_checkpoint("att_fixture", "b" * 64)
    with_journal.close()
    journal = Journal(tmp_path)
    try:
        assert journal.host_checkpoint("att_fixture") == {"checkpoint": baseline, "server_digest": "b" * 64}
        assert journal.save_host_checkpoint("att_fixture", baseline)["server_digest"] == "b" * 64
        with pytest.raises(DomainError):
            journal.save_host_checkpoint("att_fixture", sample(900010).checkpoint())
        with pytest.raises(DomainError):
            journal.acknowledge_host_checkpoint("att_fixture", "c" * 64)
        journal.finish("att_fixture")
        with pytest.raises(DomainError):
            journal.host_checkpoint("att_fixture")
    finally:
        journal.close()


def test_locality_uses_a_connected_socket() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        local_endpoint(f"http://127.0.0.1:{server.getsockname()[1]}")


def test_remote_socket_is_not_a_local_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    class Connection:
        def getsockname(self) -> tuple[str, int]:
            return "127.0.0.1", 12345

        def getpeername(self) -> tuple[str, int]:
            return "192.0.2.10", 8188

    @contextmanager
    def connect(*args: Any, **kwargs: Any) -> Any:
        yield Connection()

    monkeypatch.setattr(socket, "create_connection", connect)
    with pytest.raises(DomainError, match="同一主机"):
        local_endpoint("http://configured.example:8188")


def test_http_only_never_observes_a_host(tmp_path: Path) -> None:
    channel = LocalChannel(base_url="http://127.0.0.1:8188", hardware_id="GPU-SYNTHETIC", connection_mode="remote_http")
    with pytest.raises(DomainError, match="HTTP-only"):
        observe_native_host(channel)


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows observation")
def test_actual_native_identity_is_stable_and_uptime_does_not_reset() -> None:
    first, tick1 = windows_host()
    second, tick2 = windows_host()
    assert first == second and len(first) == 64 and tick2 >= tick1 > 0


def test_reconciliation_cannot_mix_stop_and_output_evidence() -> None:
    body = {"boot_id": "11111111-1111-4111-8111-111111111111", "attempt_id": "att_fixture", "previous_boot_id": "22222222-2222-4222-8222-222222222222", "previous_fencing_token": 1, "association_id": None, "prompt_id": None, "evidence_digest": "a" * 64, "observation": "found", "source": "history", "host_restart": {"protocol": HOST_RESTART_PROTOCOL, "checkpoint_digest": "b" * 64, "host_identity_digest": "a" * 64, "uptime_ms": 1000, "comfy_observation": "absent"}}
    with pytest.raises(ValidationError):
        Reconciliation.model_validate(body)
