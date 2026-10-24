from __future__ import annotations

from generation_contracts.admin import ROLE_PERMISSIONS
from generation_domain.errors import DomainError

from generation_application.ports import AdminIdentity


def require_permission(identity: AdminIdentity, permission: str) -> None:
    if permission not in ROLE_PERMISSIONS.get(identity.role, []):
        raise DomainError("permission_denied", "管理账号缺少权限：" + permission, 403)
