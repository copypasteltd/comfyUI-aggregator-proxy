from __future__ import annotations

import hashlib
import hmac
import secrets
from ipaddress import ip_address, ip_network
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet
from generation_domain.errors import DomainError

passwords = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def password_hash(password: str) -> str:
    if len(password) < 12 or len(password) > 1024:
        raise DomainError("password_length", "密码长度要求为 12～1024 字符", 422)
    return passwords.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return passwords.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def issue_secret(realm: str) -> tuple[str, str]:
    identifier = secrets.token_hex(16)
    return identifier, f"{realm}_{identifier}.{secrets.token_urlsafe(32)}"


def secret_identifier(realm: str, token: str) -> str:
    prefix, separator, secret = token.partition(".")
    identifier = prefix.removeprefix(realm + "_")
    if not separator or not prefix.startswith(realm + "_") or len(identifier) != 32 or len(secret) < 43:
        raise DomainError("invalid_credentials", "凭证无效", 401)
    return identifier


def verifier(token: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()


def verify_secret(token: str, expected: str, pepper: str) -> bool:
    return hmac.compare_digest(verifier(token, pepper), expected)


class SecretVault:
    def __init__(self, encryption_key: str) -> None:
        self._fernet = Fernet(encryption_key.encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()


def check_ip(policy: dict[str, Any], peer: str) -> None:
    if policy["mode"] == "any":
        return
    try:
        address = ip_address(peer)
    except ValueError as exc:
        raise DomainError("ip_denied", "无法确认来源地址", 403) from exc
    if not any(address in ip_network(cidr, strict=True) for cidr in policy["values"]):
        raise DomainError("ip_denied", "来源地址不在授权范围", 403)
