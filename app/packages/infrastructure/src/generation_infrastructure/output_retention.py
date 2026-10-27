"""Fixed cloud-output retention and bounded, audited adjustment of old outputs."""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.models import Artifact, Asset, Job, ObjectRecord
from generation_infrastructure.transactions import record_change

OUTPUT_RETENTION = timedelta(hours=8)


def shorten_existing_outputs(sessions: sessionmaker[Session], *, apply: bool = False, maximum: int = 100) -> dict[str, int | bool]:
    """Only shorten published successful outputs; never extend or change inputs."""
    if not 1 <= maximum <= 500:
        raise ValueError("Output retention batch must be 1..500")
    targets = (select(Artifact.object_id.label("object_id"), (func.max(Job.terminal_at) + OUTPUT_RETENTION).label("expires_at"))
               .join(Job, Artifact.job_id == Job.id)
               .where(Artifact.deleted_at.is_(None), Job.state == "succeeded", Job.terminal_at.is_not(None))
               .group_by(Artifact.object_id).subquery())
    query = (select(ObjectRecord, targets.c.expires_at).join(targets, targets.c.object_id == ObjectRecord.id)
             .where(ObjectRecord.deleted_at.is_(None), ObjectRecord.expires_at > targets.c.expires_at,
                    ~exists(select(Asset.id).where(Asset.object_id == ObjectRecord.id)))
             .order_by(ObjectRecord.id).limit(maximum))
    if apply:
        query = query.with_for_update(of=ObjectRecord, skip_locked=True)
    request_id = str(uuid4())
    with sessions.begin() as session:
        rows = session.execute(query).all()
        if apply:
            for obj, expires_at in rows:
                obj.expires_at = expires_at
                record_change(session, "system:output_retention", "content.retention_shortened", obj.id, request_id,
                              {"changed_fields": ["expires_at"], "reason": "cloud_output_retention_eight_hours", "cutoff": int(expires_at.timestamp())})
        return {"apply": apply, "selected": len(rows), "updated": len(rows) if apply else 0, "retention_hours": 8, "batch_limit": maximum}


def main() -> None:
    from generation_infrastructure.database import make_sessions
    from generation_infrastructure.settings import Settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Apply one batch; default only previews")
    parser.add_argument("--maximum", type=int, default=100)
    args = parser.parse_args()
    if not args.config.is_file():
        raise SystemExit("An existing explicit environment configuration is required")
    sessions = make_sessions(Settings(_env_file=args.config))
    try:
        print(json.dumps(shorten_existing_outputs(sessions, apply=args.apply, maximum=args.maximum)))
    finally:
        sessions.kw["bind"].dispose()


if __name__ == "__main__":
    main()
