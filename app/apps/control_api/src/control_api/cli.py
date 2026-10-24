from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path

from generation_contracts.admin import AdminUserCreate
from generation_domain.values import public_id
from generation_infrastructure.database import make_sessions
from generation_infrastructure.models import AdminUser, Node, Project, Tenant
from generation_infrastructure.security import issue_secret, password_hash, verifier
from generation_infrastructure.settings import Settings
from pydantic import SecretStr
from sqlalchemy import select


def default_limits() -> dict[str, object]:
    values = {"rpm": 120, "burst": 10, "daily_jobs": 1000, "unfinished_jobs": 20, "generating_jobs": 2, "daily_image_count": 1000, "daily_video_milliseconds": 3600000, "daily_audio_milliseconds": 0}
    return {k: {"mode": "bounded", "value": v} for k, v in values.items()}


def main() -> None:
    parser = argparse.ArgumentParser(prog="genctl")
    sub = parser.add_subparsers(dest="command", required=True)
    admin = sub.add_parser("bootstrap-admin")
    admin.add_argument("--username", default="admin")
    admin.add_argument("--password-file", type=Path)
    node = sub.add_parser("provision-node")
    node.add_argument("--name", required=True)
    node.add_argument("--output", type=Path, required=True)
    sub.add_parser("bootstrap-project")
    args = parser.parse_args()
    settings = Settings()
    sessions = make_sessions(settings)
    with sessions.begin() as session:
        if args.command == "bootstrap-admin":
            if session.scalar(select(AdminUser.id).limit(1)):
                raise SystemExit("Admin already exists; bootstrap refused")
            password = args.password_file.read_text(encoding="utf-8").strip() if args.password_file else getpass.getpass("Administrator password: ")
            data = AdminUserCreate(username=args.username, password=SecretStr(password), role="administrator")
            session.add(AdminUser(username=data.username, password_hash=password_hash(password), role=data.role))
            print("Administrator created")
        elif args.command == "bootstrap-project":
            if session.scalar(select(Tenant.id).limit(1)):
                raise SystemExit("Tenant already exists; bootstrap refused")
            tenant = Tenant(name="默认租户", limits=default_limits())
            session.add(tenant)
            session.flush()
            project = Project(tenant_id=tenant.id, name="默认项目", limits=default_limits())
            session.add(project)
            session.flush()
            print(json.dumps({"tenant_id": public_id("ten", tenant.id), "project_id": public_id("prj", project.id)}))
        elif args.command == "provision-node":
            if args.output.exists():
                raise SystemExit("Credential output already exists")
            identifier, token = issue_secret("worker")
            row = Node(name=args.name, credential_identifier=identifier, credential_verifier=verifier(token, settings.credential_pepper.get_secret_value()), pepper_version=settings.credential_pepper_version)
            session.add(row)
            session.flush()
            args.output.write_text(json.dumps({"node_id": public_id("node", row.id), "token": token}), encoding="utf-8")
            print("Worker provisioned; one-time credential written to the specified private file")


if __name__ == "__main__":
    main()
