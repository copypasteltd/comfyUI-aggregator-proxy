from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from generation_application.storage import ObjectStore, StoredObject
from generation_contracts.outputs import Complete, UploadAuthorization
from generation_domain.errors import DomainError
from generation_domain.media_types import MAX_AUDIO_BYTES, task_media_kind
from generation_domain.state import transition
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from generation_infrastructure.jobs import terminal_accounting
from generation_infrastructure.leases import LeaseRepository
from generation_infrastructure.media import check_specification, inspect_media
from generation_infrastructure.models import (
    Artifact,
    Asset,
    Attempt,
    Job,
    JobAssetReference,
    ObjectRecord,
    OutputUpload,
)
from generation_infrastructure.spec_isolation import verified_success
from generation_infrastructure.transactions import record_change


class OutputRepository:
    def __init__(self, sessions: sessionmaker[Session], store: ObjectStore, leases: LeaseRepository, spool_root: Path) -> None:
        self.sessions, self.store, self.leases, self.spool = sessions, store, leases, spool_root
        self.spool.mkdir(parents=True, exist_ok=True)

    def input_object(self, node_id: UUID, attempt_id: UUID, asset_id: UUID, boot_id: UUID, fence: int) -> ObjectRecord:
        with self.sessions.begin() as session:
            job, _, _ = self.leases._lock_attempt(session, node_id, boot_id, attempt_id, fence)
            asset = session.get(Asset, asset_id)
            obj = session.get(ObjectRecord, asset.object_id) if asset else None
            if not asset or asset.key_id != job.key_id or not obj or obj.deleted_at or obj.delete_requested_at or not session.get(JobAssetReference, (job.id, asset_id)) or job.execution["asset_hashes"].get(public_id("asset", asset.id)) != obj.sha256 or not obj.version_id:
                raise DomainError("input_not_authorized", "素材不属于该执行的冻结输入", 404)
            session.expunge(obj)
            return obj

    def authorize(self, node_id: UUID, attempt_id: UUID, data: UploadAuthorization) -> dict[str, Any]:
        specs = [item.model_dump() for item in data.outputs]
        with self.sessions.begin() as session:
            job, attempt, _ = self.leases._lock_attempt(session, node_id, UUID(data.boot_id), attempt_id, data.fencing_token)
            if job.state != "uploading" or not attempt.generation_released_at or not attempt.generation_evidence:
                raise DomainError("outputs_not_ready", "输出尚未稳定或生成槽位尚未释放")
            if attempt.generation_evidence.get("manifest_digest") != fingerprint(specs) or attempt.generation_evidence.get("output_count") != len(specs) or len(specs) != job.execution["parameters"].get("n", 1):
                raise DomainError("manifest_mismatch", "上传清单与稳定输出证据或购买数量不符", 422)
            kind = task_media_kind(job.task_type)
            maximum = {"image": 32 * 1024 * 1024, "video": 2147483648, "audio": MAX_AUDIO_BYTES}[kind]
            if any(item["bytes"] > maximum or not item["media_type"].startswith(kind + "/") for item in specs):
                raise DomainError("invalid_output", "输出文件类型或大小不符合任务", 422)
            rows = list(session.scalars(select(OutputUpload).where(OutputUpload.attempt_id == attempt.id).order_by(OutputUpload.position)))
            if rows and [r.specification for r in rows] != specs:
                raise DomainError("manifest_conflict", "同一执行的上传清单不可变")
            if not rows:
                rows = [OutputUpload(attempt_id=attempt.id, position=spec["position"], specification=spec, object_key=f"outputs/{job.tenant_id.hex}/{uuid4().hex}") for spec in specs]
                session.add_all(rows)
                session.flush()
            return {"manifest_digest": fingerprint(specs), "uploads": [{"id": public_id("upl", row.id), "position": row.position, "uploaded": row.object_id is not None, "url": "/internal/v1/uploads/" + public_id("upl", row.id) + "/content"} for row in rows]}

    def upload_spec(self, node_id: UUID, upload_id: UUID, boot_id: UUID, fence: int) -> dict[str, Any]:
        with self.sessions.begin() as session:
            upload = session.get(OutputUpload, upload_id)
            if not upload:
                raise DomainError("not_found", "上传记录不存在", 404)
            job, _, _ = self.leases._lock_attempt(session, node_id, boot_id, upload.attempt_id, fence)
            if job.state != "uploading":
                raise DomainError("invalid_state", "任务不允许上传")
            return {**upload.specification, "uploaded": upload.object_id is not None}

    def accept(self, node_id: UUID, upload_id: UUID, boot_id: UUID, fence: int, source: Path) -> dict[str, Any]:
        spec = self.upload_spec(node_id, upload_id, boot_id, fence)
        with self.sessions() as session:
            upload = session.get(OutputUpload, upload_id)
            assert upload
            object_key, attempt_id = upload.object_key, upload.attempt_id
        digest, size = hashlib.sha256(), 0
        with source.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        if size != spec["bytes"] or digest.hexdigest() != spec["sha256"]:
            raise DomainError("output_digest_mismatch", "输出字节数或摘要不符", 422)
        metadata = inspect_media(source, spec["media_type"])
        with source.open("rb") as file:
            stored = self.store.put(object_key, file, spec["media_type"], spec["bytes"])
        committed = False
        try:
            if stored.bytes != spec["bytes"] or stored.sha256 != spec["sha256"]:
                raise DomainError("cloud_object_mismatch", "写入存储后的输出摘要不符", 503)
            self._verify_stored(stored)
            with self.sessions.begin() as session:
                job, _, _ = self.leases._lock_attempt(session, node_id, boot_id, attempt_id, fence)
                if job.state != "uploading":
                    raise DomainError("invalid_state", "任务已停止接收上传")
                check_specification(metadata, job.execution["parameters"], audio_spec=job.execution.get("audio_spec"))
                upload = session.execute(select(OutputUpload).where(OutputUpload.id == upload_id).with_for_update()).scalar_one()
                if upload.object_id:
                    result = {"id": public_id("upl", upload.id), "verified": True}
                else:
                    obj = ObjectRecord(tenant_id=job.tenant_id, project_id=job.project_id, object_key=object_key, version_id=stored.version, bytes=stored.bytes, sha256=stored.sha256, media_type=stored.media_type, verified_at=utcnow(), metadata_json=metadata, expires_at=utcnow() + timedelta(days=7))
                    session.add(obj)
                    session.flush()
                    upload.object_id = obj.id
                    result = {"id": public_id("upl", upload.id), "verified": True}
                    committed = True
            return result
        except Exception:
            committed = False
            raise
        finally:
            if not committed:
                self.store.delete(stored.key, stored.version)

    def _verify_stored(self, stored: StoredObject) -> None:
        digest, size = hashlib.sha256(), 0
        for chunk in self.store.read(stored.key, stored.version, 0, stored.bytes - 1):
            digest.update(chunk)
            size += len(chunk)
        if size != stored.bytes or digest.hexdigest() != stored.sha256:
            raise DomainError("cloud_object_mismatch", "云端不可变对象未通过完整性验证", 503)

    def complete(self, node_id: UUID, attempt_id: UUID, data: Complete) -> dict[str, Any]:
        identifiers = [parse_id("upl", value) for value in data.upload_ids]
        with self.sessions.begin() as session:
            # Completed replay still verifies the Worker identity and original lease tuple.
            attempt = session.get(Attempt, attempt_id)
            if attempt and attempt.node_id == node_id and attempt.boot_id == UUID(data.boot_id) and attempt.fence == data.fencing_token:
                existing = session.get(Job, attempt.job_id)
                if existing and existing.state == "succeeded":
                    if not existing.result_manifest or existing.result_manifest["digest"] != data.manifest_digest or existing.result_manifest["upload_ids"] != data.upload_ids:
                        raise DomainError("manifest_conflict", "重复完成的清单不同")
                    return existing.result_manifest
                session.expunge(attempt)
                if existing:
                    session.expunge(existing)
            job, attempt, buckets = self.leases._lock_attempt(session, node_id, UUID(data.boot_id), attempt_id, data.fencing_token)
            rows = list(session.scalars(select(OutputUpload).where(OutputUpload.attempt_id == attempt.id).order_by(OutputUpload.position)))
            if [row.id for row in rows] != identifiers or not rows or any(row.object_id is None for row in rows) or fingerprint([row.specification for row in rows]) != data.manifest_digest:
                raise DomainError("manifest_not_verified", "完成清单没有全部通过云端验证")
            job.state = transition(job.state, "verifying", "complete_manifest", {"current_lease", "outputs_stable"})
            artifacts = []
            audio_milliseconds = 0
            for row in rows:
                obj = session.get(ObjectRecord, row.object_id)
                if not obj or not obj.verified_at or not obj.version_id:
                    raise DomainError("object_not_verified", "缺少已验证的不可变对象")
                obj.expires_at = utcnow() + timedelta(days=7)
                if job.task_type == "text_to_audio":
                    check_specification(obj.metadata_json, job.execution["parameters"], audio_spec=job.execution.get("audio_spec"))
                    audio_milliseconds += obj.metadata_json["duration_milliseconds"]
                artifact = Artifact(tenant_id=job.tenant_id, project_id=job.project_id, key_id=job.key_id, job_id=job.id, object_id=obj.id, position=row.position)
                session.add(artifact)
                session.flush()
                artifacts.append({"id": public_id("content", artifact.id), "position": row.position, "bytes": obj.bytes, "sha256": obj.sha256, "media_type": obj.media_type, "download_url": "/v1/content/" + public_id("content", artifact.id) + "/download"})
            job.state = transition(job.state, "succeeded", "finalize_verified_manifest", {"current_lease", "cloud_manifest_verified", "terminal_commit_once", "resources_accounted"})
            terminal_accounting(session, job, buckets, "succeeded", audio_milliseconds=audio_milliseconds)
            attempt.closed_at = utcnow()
            job.result_manifest = {"job_id": public_id("job", job.id), "digest": data.manifest_digest, "upload_ids": data.upload_ids, "artifacts": artifacts}
            verified_success(session, job, attempt, data.manifest_digest)
            record_change(session, public_id("node", node_id), "job.completed", job.id, data.manifest_digest)
            return job.result_manifest
