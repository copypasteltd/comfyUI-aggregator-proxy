"""Read-only verification of this project's explicitly named browser QA data."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from generation_infrastructure.models import (
    AdminUser,
    ApiKey,
    Asset,
    GatewayService,
    ModelArtifact,
    ObjectRecord,
)
from generation_infrastructure.settings import Settings
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    settings = Settings()
    url = make_url(settings.database_url.get_secret_value())
    if settings.environment != "development" or url.host != "127.0.0.1" or url.port != 55438 or url.database != "render_control":
        raise SystemExit("Browser resource verification requires this project's loopback development database")
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        with Session(engine) as session, session.begin():
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            keys = select(ApiKey.id).where(ApiKey.name.startswith("浏览器验收 Key "))
            queries = {
                "enabled_browser_admins": select(func.count()).select_from(AdminUser).where(AdminUser.username.startswith("browser-qa-"), AdminUser.enabled.is_(True)),
                "unarchived_browser_keys": select(func.count()).select_from(ApiKey).where(ApiKey.id.in_(keys), ApiKey.deleted_at.is_(None)),
                "enabled_browser_gateways": select(func.count()).select_from(GatewayService).where(GatewayService.name.startswith("浏览器验收网关 "), GatewayService.enabled.is_(True)),
                "unarchived_browser_model_files": select(func.count()).select_from(ModelArtifact).where(ModelArtifact.name.startswith("浏览器合成文件登记 "), ModelArtifact.deleted_at.is_(None)),
                "undeleted_browser_assets": select(func.count()).select_from(Asset).where(Asset.key_id.in_(keys), Asset.deleted_at.is_(None)),
                "uncollected_browser_objects": select(func.count()).select_from(ObjectRecord).join(Asset, Asset.object_id == ObjectRecord.id).where(Asset.key_id.in_(keys), ObjectRecord.deleted_at.is_(None)),
            }
            counts = {name: int(session.scalar(query) or 0) for name, query in queries.items()}
    finally:
        engine.dispose()
    passed = all(value == 0 for value in counts.values())
    report = {"at": datetime.now(UTC).isoformat(), "scope": "Read-only app database counts for exact browser QA naming prefixes", "counts": counts, "passed": passed, "mutations_performed": False}
    target = root.parent / "docs/evidence" / datetime.now().strftime("%Y-%m-%d") / "browser-cleanup-verification.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": passed, "counts": counts, "mutations_performed": False}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
