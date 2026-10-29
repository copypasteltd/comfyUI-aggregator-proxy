from __future__ import annotations

import hashlib
import logging
import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from generation_application.ports import AdminIdentity, BusinessIdentity
from generation_application.storage import ObjectStore
from generation_contracts.collections import CollectionQuery
from generation_contracts.content_management import ContentOptionQuery, ContentQuery, ContentTags
from generation_domain.errors import DomainError
from generation_domain.media_types import MODEL3D_MIME
from generation_domain.state import TERMINAL
from generation_domain.values import fingerprint, parse_id, public_id, utcnow
from PIL import Image, UnidentifiedImageError
from sqlalchemy import and_, literal, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, defer, sessionmaker

from generation_infrastructure.admin_base import mutation, require_revision, timestamp
from generation_infrastructure.content_management import (
    PREVIEW_MIME,
    content_filters,
    content_options,
    content_status,
)
from generation_infrastructure.cursors import CursorCodec
from generation_infrastructure.models import (
    ApiKey,
    Artifact,
    Asset,
    Attempt,
    GatewayAdmission,
    Idempotency,
    Job,
    JobAssetReference,
    ObjectRecord,
    OutputUpload,
    Project,
    Tenant,
)
from generation_infrastructure.queue_queries import begin_queue_read
from generation_infrastructure.transactions import record_change

Image.MAX_IMAGE_PIXELS = 25000000


def validate_image(source: BinaryIO, maximum: int = 20 * 1024 * 1024) -> dict[str, Any]:
    source.seek(0, 2)
    size = source.tell()
    if size <= 0 or size > maximum:
        raise DomainError("invalid_image_size", "图片字节数超过接口限制", 413)
    source.seek(0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as image:
                if image.format not in ("PNG", "JPEG", "WEBP") or image.width * image.height > 25000000 or getattr(image, "n_frames", 1) != 1:
                    raise DomainError("unsupported_image", "仅支持 2500 万像素以内的静态 PNG、JPEG、WebP", 422)
                metadata = {"width": image.width, "height": image.height, "media_type": Image.MIME[image.format]}
                image.verify()
        return metadata
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise DomainError("invalid_image", "图片无法安全解码", 422) from exc
    finally:
        source.seek(0)


def requested_range(value: str | None, size: int) -> tuple[int, int, int]:
    if not value:
        return 0, size - 1, 200
    if not value.startswith("bytes=") or "," in value:
        raise DomainError("invalid_range", "仅支持单一 bytes Range", 416)
    begin, separator, finish = value[6:].partition("-")
    try:
        if not separator:
            raise ValueError
        if not begin:
            suffix = int(finish)
            if suffix <= 0:
                raise ValueError
            start, end = max(0, size - suffix), size - 1
        else:
            start, end = int(begin), min(int(finish), size - 1) if finish else size - 1
        if start < 0 or start >= size or end < start:
            raise ValueError
    except ValueError as exc:
        raise DomainError("invalid_range", "请求字节范围无效", 416) from exc
    return start, end, 206


@dataclass(frozen=True)
class AuthorizedDownload:
    data: Iterator[bytes]
    status: int
    headers: dict[str, str]
    media_type: str


def object_in_use(session: Session, identifier: UUID) -> bool:
    inputs = session.scalar(select(JobAssetReference.job_id).join(Asset, JobAssetReference.asset_id == Asset.id).join(Job, JobAssetReference.job_id == Job.id).where(Asset.object_id == identifier, Job.state.not_in(TERMINAL)).limit(1))
    outputs = session.scalar(select(OutputUpload.id).join(Attempt, OutputUpload.attempt_id == Attempt.id).join(Job, Attempt.job_id == Job.id).where(OutputUpload.object_id == identifier, Job.current_attempt_id == Attempt.id, Job.state.not_in(TERMINAL)).limit(1))
    return inputs is not None or outputs is not None


def content_projection(row: Asset | Artifact, obj: ObjectRecord, now: datetime | None = None) -> dict[str, Any]:
    state = content_status(row, obj, now or utcnow())
    return {"tags": row.tags, "metadata_revision": row.metadata_revision, "metadata_updated_at": timestamp(row.metadata_updated_at), "status": state, "preview_supported": state == "available" and obj.media_type in PREVIEW_MIME, "id": public_id("asset" if isinstance(row, Asset) else "content", row.id), "kind": "input" if isinstance(row, Asset) else "output", "tenant_id": public_id("ten", row.tenant_id), "project_id": public_id("prj", row.project_id), "key_id": public_id("key", row.key_id), "job_id": public_id("job", row.job_id) if isinstance(row, Artifact) else None, "bytes": obj.bytes, "media_type": obj.media_type, "sha256": obj.sha256, "expires_at": int(obj.expires_at.timestamp()), "created_at": int(row.created_at.timestamp()), "deleted_at": int(row.deleted_at.timestamp()) if row.deleted_at else None, "object_deleted_at": int(obj.deleted_at.timestamp()) if obj.deleted_at else None, "metadata": {k: v for k, v in obj.metadata_json.items() if k in ("width", "height", "duration_milliseconds", "fps", "has_audio", "container", "codec", "sample_rate", "channels", "format", "mesh_count", "vertex_count", "triangle_count", "material_count", "texture_count", "has_textures")}}


class ContentRepository:
    def __init__(self, sessions: sessionmaker[Session], store: ObjectStore) -> None:
        self.sessions, self.store = sessions, store

    def _uploaded_asset(self, session: Session, identity: BusinessIdentity, identifier: UUID) -> dict[str, Any]:
        try:
            asset, obj = self._object(session, identity, identifier, asset=True)
        except DomainError as exc:
            raise DomainError("asset_replay_unavailable", "原上传素材已到期或删除，不能用相同幂等键复活", 410) from exc
        return {"id": public_id("asset", asset.id), "object": "asset", "status": "ready", "bytes": obj.bytes, "sha256": obj.sha256, "media_type": obj.media_type, "width": obj.metadata_json["width"], "height": obj.metadata_json["height"], "expires_at": int(obj.expires_at.timestamp())}

    def _upload_identity(self, session: Session, identity: BusinessIdentity) -> None:
        key = session.scalar(select(ApiKey).where(ApiKey.id == identity.key_id).with_for_update(key_share=True))
        tenant, project = session.get(Tenant, identity.tenant_id), session.get(Project, identity.project_id)
        if not key or key.tenant_id != identity.tenant_id or key.project_id != identity.project_id or not key.enabled or key.deleted_at or (key.expires_at and key.expires_at <= utcnow()) or not tenant or not tenant.enabled or not project or not project.enabled:
            raise DomainError("key_disabled", "上传身份已失效", 403)
        BusinessIdentity(identity.tenant_id, identity.project_id, identity.key_id, key.policy).require("assets:upload")

    def _discard_unpublished(self, key: str, version: str, key_id: UUID) -> None:
        # A failed COMMIT acknowledgement does not prove that publication failed.
        # Wait for the publisher's existing key lock before a fresh absence lookup.
        try:
            with self.sessions.begin() as session:
                owner = session.scalar(select(ApiKey.id).where(ApiKey.id == key_id).with_for_update(key_share=True))
                if owner is None:
                    return
                published = session.scalar(select(ObjectRecord.id).where(ObjectRecord.object_key == key, ObjectRecord.version_id == version).limit(1))
            if published is None:
                self.store.delete(key, version)
        except Exception:
            # Preserve potentially committed bytes; never mask the original failure.
            logging.getLogger(__name__).warning("asset_cleanup_deferred_pending_publication_verification")

    def upload_asset(self, identity: BusinessIdentity, source: BinaryIO, request_id: str, token: str | None = None) -> dict[str, Any]:
        identity.require("assets:upload")
        if token is not None and (not 1 <= len(token) <= 128 or any(not 33 <= ord(c) <= 126 for c in token)):
            raise DomainError("invalid_idempotency_key", "上传幂等键格式无效", 400)
        metadata = validate_image(source)
        size = source.seek(0, 2)
        source.seek(0)
        digest = hashlib.sha256()
        for block in iter(lambda: source.read(64 * 1024), b""):
            digest.update(block)
        sha256 = digest.hexdigest()
        source.seek(0)
        request_hash = fingerprint({**metadata, "bytes": size, "sha256": sha256})
        token_hash = hashlib.sha256(token.encode()).hexdigest() if token is not None else None
        if token_hash is not None:
            with self.sessions.begin() as session:
                self._upload_identity(session, identity)
                previous = session.scalar(select(Idempotency).where(Idempotency.key_id == identity.key_id, Idempotency.operation == "upload_asset", Idempotency.token_digest == token_hash))
                if previous:
                    if previous.request_fingerprint != request_hash:
                        raise DomainError("idempotency_conflict", "同一上传幂等键对应不同文件", 409)
                    if previous.resource_id is None:
                        raise DomainError("asset_upload_pending", "原上传尚未完成，请使用相同幂等键重试", 409)
                    return self._uploaded_asset(session, identity, previous.resource_id)
        key = f"inputs/{identity.tenant_id.hex}/{uuid4().hex}"
        stored = self.store.put(key, source, metadata["media_type"], 20 * 1024 * 1024)
        try:
            if stored.sha256 != sha256 or stored.bytes != size or stored.media_type != metadata["media_type"]:
                raise DomainError("asset_integrity_mismatch", "上传对象与已验证素材不一致", 503)
            with self.sessions.begin() as session:
                self._upload_identity(session, identity)
                idem = None
                if token_hash is not None:
                    session.execute(insert(Idempotency).values(tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=identity.key_id, operation="upload_asset", token_digest=token_hash, request_fingerprint=request_hash, expires_at=utcnow() + timedelta(days=7)).on_conflict_do_nothing())
                    idem = session.scalar(select(Idempotency).where(Idempotency.key_id == identity.key_id, Idempotency.operation == "upload_asset", Idempotency.token_digest == token_hash).with_for_update())
                    assert idem is not None
                    if idem.request_fingerprint != request_hash:
                        raise DomainError("idempotency_conflict", "同一上传幂等键对应不同文件", 409)
                    if idem.resource_id is not None:
                        return self._uploaded_asset(session, identity, idem.resource_id)
                obj = ObjectRecord(tenant_id=identity.tenant_id, project_id=identity.project_id, object_key=stored.key, version_id=stored.version, bytes=stored.bytes, sha256=stored.sha256, media_type=stored.media_type, verified_at=utcnow(), metadata_json=metadata, expires_at=utcnow() + timedelta(hours=24))
                session.add(obj)
                session.flush()
                asset = Asset(tenant_id=identity.tenant_id, project_id=identity.project_id, key_id=identity.key_id, object_id=obj.id)
                session.add(asset)
                session.flush()
                if idem is not None:
                    idem.resource_id = asset.id
                record_change(session, public_id("key", identity.key_id), "asset.uploaded", asset.id, request_id)
                return self._uploaded_asset(session, identity, asset.id)
        finally:
            self._discard_unpublished(stored.key, stored.version, identity.key_id)

    def _content_filter(self, identity: BusinessIdentity, entity: Any) -> Any:
        grant = identity.policy["content"]
        owner = entity.key_id == identity.key_id
        if grant["mode"] == "owner_only":
            return and_(entity.tenant_id == identity.tenant_id, entity.project_id == identity.project_id, owner)
        projects = [parse_id("prj", p) for p in grant["project_ids"]]
        shared = entity.project_id.in_(projects)
        if grant["mode"] == "shared_creator_keys":
            shared = and_(shared, entity.key_id.in_([parse_id("key", k) for k in grant["creator_key_ids"]]))
        return and_(entity.tenant_id == identity.tenant_id, or_(and_(entity.project_id == identity.project_id, owner), shared))

    def _object(self, session: Session, identity: BusinessIdentity, identifier: UUID, *, asset: bool = False) -> tuple[Any, ObjectRecord]:
        entity = Asset if asset else Artifact
        query = select(entity, ObjectRecord).join(ObjectRecord, entity.object_id == ObjectRecord.id).where(entity.id == identifier, entity.deleted_at.is_(None), ObjectRecord.expires_at > utcnow(), ObjectRecord.deleted_at.is_(None), ObjectRecord.delete_requested_at.is_(None), ObjectRecord.verified_at.is_not(None))
        if asset:
            query = query.where(entity.tenant_id == identity.tenant_id, entity.project_id == identity.project_id, entity.key_id == identity.key_id)
        else:
            query = query.where(self._content_filter(identity, entity))
        row = session.execute(query).one_or_none()
        if not row:
            raise DomainError("not_found", "内容不存在或未授权", 404)
        return row[0], row[1]

    def input_asset(self, identity: BusinessIdentity, identifier: UUID) -> None:
        identity.require("jobs:create")
        with self.sessions() as session:
            self._object(session, identity, identifier, asset=True)

    def metadata(self, identity: BusinessIdentity, identifier: UUID, *, asset: bool = False) -> dict[str, Any]:
        identity.require("assets:read" if asset else "content:read")
        with self.sessions() as session:
            row, obj = self._object(session, identity, identifier, asset=asset)
            return {"id": public_id("asset" if asset else "content", row.id), "bytes": obj.bytes, "media_type": obj.media_type, "sha256": obj.sha256, "expires_at": int(obj.expires_at.timestamp()), "metadata": {k: v for k, v in obj.metadata_json.items() if k in ("width", "height", "duration_milliseconds", "fps", "has_audio", "container", "codec", "sample_rate", "channels", "format", "mesh_count", "vertex_count", "triangle_count", "material_count", "texture_count", "has_textures")}}

    def list_content(self, identity: BusinessIdentity, query: CollectionQuery, codec: CursorCodec, *, gateway_id: UUID | None = None) -> dict[str, Any]:
        identity.require("content:read")
        scope = codec.scope(identity, "content", {**query.model_dump(exclude={"cursor"}), "gateway": str(gateway_id) if gateway_id else None})
        position = codec.read(query.cursor, scope)
        with self.sessions() as session:
            statement = select(Artifact, ObjectRecord, Job.model_alias, Job.task_type, GatewayAdmission.external_task_ref if gateway_id else literal(None)).options(defer(ObjectRecord.metadata_json)).join(ObjectRecord, Artifact.object_id == ObjectRecord.id).join(Job, Artifact.job_id == Job.id).where(self._content_filter(identity, Artifact), Artifact.created_at <= position.through, Artifact.deleted_at.is_(None), ObjectRecord.verified_at.is_not(None), ObjectRecord.deleted_at.is_(None), ObjectRecord.delete_requested_at.is_(None), ObjectRecord.expires_at > utcnow())
            if gateway_id is not None:
                statement = statement.join(GatewayAdmission, Artifact.job_id == GatewayAdmission.job_id).where(GatewayAdmission.gateway_id == gateway_id)
            if query.model is not None:
                statement = statement.where(Job.model_alias == query.model)
            if position.after_time is not None:
                statement = statement.where(tuple_(Artifact.created_at, Artifact.id) < tuple_(literal(position.after_time), literal(position.after_id)))
            rows = list(session.execute(statement.order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(query.limit + 1)))
            selected_rows = rows[:query.limit]
            more = len(rows) > query.limit
            items = []
            for artifact, obj, model, task, external in selected_rows:
                item = {"id": public_id("content", artifact.id), "job_id": public_id("job", artifact.job_id), "bytes": obj.bytes, "media_type": obj.media_type, "sha256": obj.sha256, "created_at": int(artifact.created_at.timestamp()), "expires_at": int(obj.expires_at.timestamp())}
                if gateway_id is not None:
                    item.update({"external_task_ref": external, "model": model, "task_type": task})
                items.append(item)
            return {"object": "list", "data": items, "has_more": more, "next_cursor": codec.next(position, scope, selected_rows[-1][0].created_at, selected_rows[-1][0].id) if more else None}

    def download(self, identity: BusinessIdentity, identifier: UUID, range_header: str | None, *, asset: bool = False, head: bool = False) -> AuthorizedDownload:
        identity.require("content:download")
        identity.require("assets:read" if asset else "content:read")
        if asset:
            identity.require("content:sensitive")
        with self.sessions() as session:
            row, obj = self._object(session, identity, identifier, asset=asset)
            return self._download(row, obj, range_header, head)

    def _download(self, row: Asset | Artifact, obj: ObjectRecord, range_header: str | None, head: bool) -> AuthorizedDownload:
        start, end, status = requested_range(range_header, obj.bytes)
        filename = public_id("asset" if isinstance(row, Asset) else "content", row.id) + (".glb" if obj.media_type == MODEL3D_MIME else "")
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "private, no-store", "ETag": '"' + obj.sha256 + '"'}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{obj.bytes}"
        assert obj.version_id
        data = iter(()) if head else self.store.read(obj.object_key, obj.version_id, start, end)
        return AuthorizedDownload(data, status, headers, obj.media_type)

    def delete(self, identity: BusinessIdentity, identifier: UUID, request_id: str, *, asset: bool = False) -> dict[str, Any]:
        identity.require("content:delete")
        entity = Asset if asset else Artifact
        with self.sessions.begin() as session:
            row = session.execute(select(entity).where(entity.id == identifier, entity.tenant_id == identity.tenant_id, entity.project_id == identity.project_id, entity.key_id == identity.key_id).with_for_update()).scalar_one_or_none()
            if not row:
                raise DomainError("not_found", "内容不存在或无删除权限", 404)
            assert isinstance(row, (Asset, Artifact))
            return self._delete(session, row, public_id("key", identity.key_id), request_id)

    def _delete(self, session: Session, row: Asset | Artifact, actor: str, request_id: str) -> dict[str, Any]:
        obj = session.execute(select(ObjectRecord).where(ObjectRecord.id == row.object_id).with_for_update()).scalar_one()
        if row.deleted_at:
            return {"id": public_id("asset" if isinstance(row, Asset) else "content", row.id), "status": "delete_requested", "physical_delete_complete": obj.deleted_at is not None}
        if object_in_use(session, obj.id) or (isinstance(row, Artifact) and session.scalar(select(Job.state).where(Job.id == row.job_id)) not in TERMINAL):
            raise DomainError("content_in_use", "内容仍被未结束任务引用，不能删除", 409)
        row.deleted_at = utcnow()
        session.flush()
        assets = session.scalar(select(Asset.id).where(Asset.object_id == obj.id, Asset.deleted_at.is_(None)).limit(1))
        outputs = session.scalar(select(Artifact.id).where(Artifact.object_id == obj.id, Artifact.deleted_at.is_(None)).limit(1))
        if not assets and not outputs:
            obj.delete_requested_at = utcnow()
        record_change(session, actor, "content.delete_requested", row.id, request_id)
        return {"id": public_id("asset" if isinstance(row, Asset) else "content", row.id), "status": "delete_requested", "physical_delete_complete": obj.deleted_at is not None}

    def admin_list(self, query: ContentQuery) -> dict[str, Any]:
        entity = Asset if query.kind == "input" else Artifact
        with self.sessions.begin() as session:
            begin_queue_read(session)
            now = utcnow()
            statement = select(entity, ObjectRecord).join(ObjectRecord, entity.object_id == ObjectRecord.id).where(*content_filters(query, entity, now))
            rows = list(session.execute(statement.order_by(entity.created_at.desc(), entity.id).offset((query.page-1)*50).limit(51)))
            return {"data": [content_projection(row, obj, now) for row, obj in rows[:50]], "page": query.page, "has_more": len(rows) > 50, "snapshot_at": int(now.timestamp()), "filters": query.filters()}

    def admin_filter_options(self, query: ContentOptionQuery) -> dict[str, Any]:
        with self.sessions.begin() as session:
            begin_queue_read(session)
            return content_options(session, query)

    def admin_tags(self, actor: AdminIdentity, identifier: UUID, data: ContentTags, token: str, request_id: str, *, asset: bool = False) -> dict[str, Any]:
        entity = Asset if asset else Artifact
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "content.tags:" + str(identifier), token, {"asset": asset, **data.model_dump()})
            if idem.result is not None:
                return idem.result
            row = session.scalar(select(entity).where(entity.id == identifier).with_for_update())
            if not row:
                raise DomainError("not_found", "内容不存在", 404)
            assert isinstance(row, (Asset, Artifact))
            require_revision(row.metadata_revision, data.expected_revision)
            row.tags = data.tags
            row.metadata_revision += 1
            row.metadata_updated_at = utcnow()
            obj = session.get(ObjectRecord, row.object_id)
            assert obj is not None
            record_change(session, public_id("usr", actor.user_id), "content.tags_changed", row.id, request_id, {"revision": row.metadata_revision, "tag_count": len(row.tags)}, revision_before=data.expected_revision, revision_after=row.metadata_revision)
            idem.result = content_projection(row, obj)
            return idem.result

    def admin_metadata(self, identifier: UUID, *, asset: bool = False) -> dict[str, Any]:
        entity = Asset if asset else Artifact
        with self.sessions() as session:
            result = session.execute(select(entity, ObjectRecord).join(ObjectRecord, entity.object_id == ObjectRecord.id).where(entity.id == identifier)).one_or_none()
            if not result:
                raise DomainError("not_found", "内容不存在", 404)
            return content_projection(result[0], result[1])

    def admin_download(self, identifier: UUID, range_header: str | None, *, asset: bool = False, head: bool = False, preview: bool = False) -> AuthorizedDownload:
        entity = Asset if asset else Artifact
        with self.sessions() as session:
            result = session.execute(select(entity, ObjectRecord).join(ObjectRecord, entity.object_id == ObjectRecord.id).where(entity.id == identifier, entity.deleted_at.is_(None), ObjectRecord.deleted_at.is_(None), ObjectRecord.delete_requested_at.is_(None), ObjectRecord.expires_at > utcnow(), ObjectRecord.verified_at.is_not(None), ObjectRecord.version_id.is_not(None), ObjectRecord.version_id != "")).one_or_none()
            if not result:
                raise DomainError("not_found", "内容不存在或已到期", 404)
            if preview and result[1].media_type not in PREVIEW_MIME:
                raise DomainError("preview_not_supported", "此文件类型不支持预览，请使用授权下载", 415)
            download = self._download(result[0], result[1], range_header, head)
            if preview:
                download.headers["Content-Disposition"] = f'inline; filename="{public_id("asset" if asset else "content", identifier)}"'
                download.headers["X-Content-Type-Options"] = "nosniff"
                download.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
            return download

    def admin_delete(self, actor: AdminIdentity, identifier: UUID, token: str, request_id: str, *, asset: bool = False) -> dict[str, Any]:
        entity = Asset if asset else Artifact
        with self.sessions.begin() as session:
            idem = mutation(session, actor, "content.delete:" + str(identifier), token, {"asset": asset})
            if idem.result:
                return idem.result
            row = session.execute(select(entity).where(entity.id == identifier).with_for_update()).scalar_one_or_none()
            if not row:
                raise DomainError("not_found", "内容不存在", 404)
            assert isinstance(row, (Asset, Artifact))
            idem.result = self._delete(session, row, public_id("usr", actor.user_id), request_id)
            return idem.result
