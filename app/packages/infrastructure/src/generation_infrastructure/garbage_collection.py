from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from generation_application.storage import ObjectStore
from generation_domain.values import public_id, utcnow
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.content import object_in_use
from generation_infrastructure.models import Artifact, Asset, ObjectRecord
from generation_infrastructure.transactions import record_change


@dataclass(frozen=True)
class DeletionClaim:
    object_id: UUID
    token: UUID
    key: str
    version: str


class ObjectGarbageCollector:
    def __init__(self, sessions: sessionmaker[Session], store: ObjectStore) -> None:
        self.sessions, self.store = sessions, store

    def claim(self, maximum: int = 10) -> list[DeletionClaim]:
        if not 1 <= maximum <= 50:
            raise ValueError("GC batch must be 1..50")
        now = utcnow()
        claims = []
        with self.sessions.begin() as session:
            rows = session.scalars(select(ObjectRecord).where(ObjectRecord.deleted_at.is_(None), ObjectRecord.version_id.is_not(None), or_(ObjectRecord.delete_requested_at.is_not(None), ObjectRecord.expires_at <= now), or_(ObjectRecord.gc_claim_until.is_(None), ObjectRecord.gc_claim_until <= now)).order_by(ObjectRecord.expires_at, ObjectRecord.id).with_for_update(skip_locked=True).limit(maximum))
            for row in rows:
                # Active frozen inputs and the current upload remain pinned even
                # during unknown execution. No object-store IO holds this lock.
                if object_in_use(session, row.id):
                    row.gc_claim_token = None
                    row.gc_claim_until = now + timedelta(seconds=30)
                    continue
                if row.expires_at > now:
                    visible_asset = session.scalar(select(Asset.id).where(Asset.object_id == row.id, Asset.deleted_at.is_(None)).limit(1))
                    visible_output = session.scalar(select(Artifact.id).where(Artifact.object_id == row.id, Artifact.deleted_at.is_(None)).limit(1))
                    if visible_asset or visible_output:
                        row.gc_claim_until = now + timedelta(seconds=30)
                        continue
                row.gc_claim_token = uuid4()
                row.gc_claim_until = now + timedelta(seconds=90)
                assert row.version_id
                claims.append(DeletionClaim(row.id, row.gc_claim_token, row.object_key, row.version_id))
        return claims

    def collect(self, claim: DeletionClaim) -> bool:
        # Version-specific deletion is idempotent. A lost acknowledgement or
        # expired claim can repeat this exact operation without deleting a new version.
        try:
            self.store.delete(claim.key, claim.version)
        except Exception:
            with self.sessions.begin() as session:
                row = session.scalar(select(ObjectRecord).where(ObjectRecord.id == claim.object_id, ObjectRecord.gc_claim_token == claim.token).with_for_update())
                if row:
                    row.gc_claim_token = None
                    row.gc_claim_until = utcnow() + timedelta(seconds=30)
            return False
        with self.sessions.begin() as session:
            row = session.scalar(select(ObjectRecord).where(ObjectRecord.id == claim.object_id, ObjectRecord.gc_claim_token == claim.token).with_for_update())
            if not row:
                return False
            row.deleted_at = utcnow()
            row.gc_claim_token = None
            row.gc_claim_until = None
            record_change(session, "system:object_gc", "content.physical_deleted", row.id, public_id("gc", claim.token))
        return True

    def tick(self, maximum: int = 10) -> dict[str, int]:
        claims = self.claim(maximum)
        deleted = sum(self.collect(claim) for claim in claims)
        return {"claimed": len(claims), "deleted": deleted, "retry": len(claims) - deleted}
