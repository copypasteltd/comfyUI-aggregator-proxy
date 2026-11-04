from __future__ import annotations

import ctypes
import hashlib
import ipaddress
import socket
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from generation_contracts.recovery import (
    HOST_RESTART_PROTOCOL,
    HostRecoveryCheckpoint,
    HostRestartProof,
)
from generation_domain.errors import DomainError
from generation_domain.values import fingerprint

from windows_worker.configuration import LocalChannel


@dataclass(frozen=True)
class NativeHostObservation:
    host_identity_digest: str
    uptime_ms: int
    gpu_hardware_id: str
    channel_digest: str

    def checkpoint(self) -> dict[str, Any]:
        return HostRecoveryCheckpoint(protocol=HOST_RESTART_PROTOCOL, nonce=uuid4().hex, host_identity_digest=self.host_identity_digest, uptime_ms=self.uptime_ms, gpu_hardware_id=self.gpu_hardware_id, channel_digest=self.channel_digest, locality="same-host-tcp-v1").model_dump()


def local_endpoint(base_url: str) -> None:
    """Use the connected socket, never a DNS name or a declared local mode alone."""
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise DomainError("host_recovery_unavailable", "主机恢复要求受控的本地渠道")
    try:
        with socket.create_connection((parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)), timeout=2) as connection:
            local, peer = connection.getsockname(), connection.getpeername()
            if ipaddress.ip_address(local[0]) != ipaddress.ip_address(peer[0]) or (len(local) == 4 and local[3] != peer[3]):
                raise DomainError("host_recovery_not_local", "渠道连接不属于同一主机")
    except (OSError, ValueError):
        raise DomainError("host_recovery_unavailable", "无法核验本地主机连接") from None


def windows_host() -> tuple[str, int]:
    if sys.platform != "win32":
        raise DomainError("host_recovery_unavailable", "此恢复证明要求原生 Windows 节点")
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, kind = winreg.QueryValueEx(key, "MachineGuid")
        if kind != winreg.REG_SZ or not isinstance(value, str) or len(value) != 36:
            raise ValueError("invalid installation identity")
        identifier = UUID(value)
        if identifier.int == 0:
            raise ValueError("invalid installation identity")
        tick = ctypes.WinDLL("kernel32", use_last_error=True).GetTickCount64
        tick.argtypes = []
        tick.restype = ctypes.c_uint64
        uptime = int(tick())
        if not 1 <= uptime <= 2**63 - 1:
            raise ValueError("invalid uptime")
        identity = hashlib.sha256(("windows-machine-guid-v1:" + str(identifier)).encode("ascii")).hexdigest()
        return identity, uptime
    except (OSError, ValueError, TypeError, AttributeError):
        raise DomainError("host_recovery_unavailable", "无法取得可靠的主机身份与运行计数") from None


def observe_native_host(local: LocalChannel) -> NativeHostObservation:
    if local.connection_mode != "local" or local.comfy_root is None:
        raise DomainError("host_recovery_not_local", "HTTP-only 渠道不能使用主机重启证明")
    local_endpoint(local.base_url)
    identity, uptime = windows_host()
    return NativeHostObservation(identity, uptime, local.hardware_id, fingerprint({"base_url": local.base_url.rstrip("/")}))


def host_recovery_fields(sample: NativeHostObservation, record: dict[str, Any]) -> dict[str, Any]:
    baseline = HostRecoveryCheckpoint.model_validate(record["checkpoint"])
    if (sample.host_identity_digest, sample.gpu_hardware_id, sample.channel_digest) != (baseline.host_identity_digest, baseline.gpu_hardware_id, baseline.channel_digest):
        raise DomainError("host_checkpoint_conflict", "当前主机、GPU 或渠道与原检查点不符")
    if record["server_digest"] and sample.uptime_ms + 1000 <= baseline.uptime_ms:
        proof = HostRestartProof(protocol=HOST_RESTART_PROTOCOL, checkpoint_digest=record["server_digest"], host_identity_digest=sample.host_identity_digest, uptime_ms=sample.uptime_ms, comfy_observation="absent")
        return {"observation": "host_restarted", "source": "native_host_uptime", "host_restart": proof.model_dump()}
    return {"host_checkpoint": baseline.model_dump()}
