from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from generation_application.storage import StoredObject
from generation_domain.errors import DomainError
from generation_domain.workflows import relative_file

from generation_infrastructure.settings import Settings

CHUNK = 1024 * 1024
S3_RANGE = re.compile(r"bytes ([0-9]{1,20})-([0-9]{1,20})/([0-9]{1,20})")


def _version(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 1024 or value == "null" or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise DomainError("invalid_object_version", "对象版本无效", 422)
    return value


def _close_body(body: Any) -> None:
    try:
        body.close()
    except Exception:
        # Cleanup must neither mask the original failure nor expose SDK details.
        logging.getLogger(__name__).warning("s3_body_close_failed")


class LocalDevelopmentStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str, version: str) -> Path:
        relative_file(key)
        if len(version) != 32 or any(c not in "0123456789abcdef" for c in version):
            raise DomainError("invalid_object_version", "对象版本无效", 422)
        path = (self.root / key / version).resolve()
        if self.root not in path.parents:
            raise DomainError("unsafe_path", "对象路径越界", 422)
        return path

    def put(self, key: str, source: BinaryIO, media_type: str, maximum: int) -> StoredObject:
        version = uuid4().hex
        path = self._path(key, version)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest, size = hashlib.sha256(), 0
        try:
            with path.open("xb") as target:
                while data := source.read(CHUNK):
                    size += len(data)
                    if size > maximum:
                        raise DomainError("file_too_large", "文件超过大小限制", 413)
                    digest.update(data)
                    target.write(data)
                target.flush()
                os.fsync(target.fileno())
            if size == 0:
                raise DomainError("empty_file", "文件不能为空", 422)
            return StoredObject(key, version, size, digest.hexdigest(), media_type)
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def read(self, key: str, version: str, start: int, end: int) -> Iterator[bytes]:
        return self._read(key, version, start, end, complete=False)

    def read_complete(self, key: str, version: str, size: int) -> Iterator[bytes]:
        return self._read(key, version, 0, size - 1, complete=True)

    def _read(self, key: str, version: str, start: int, end: int, *, complete: bool) -> Iterator[bytes]:
        with self._path(key, version).open("rb") as source:
            if complete and os.fstat(source.fileno()).st_size != end + 1:
                raise DomainError("storage_response_invalid", "对象存储响应与请求不一致", 503)
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                data = source.read(min(CHUNK, remaining))
                if not data:
                    raise DomainError("object_truncated", "对象数据不完整", 503)
                remaining -= len(data)
                yield data

    def delete(self, key: str, version: str) -> None:
        self._path(key, version).unlink(missing_ok=True)

    def signed_url(self, key: str, version: str, seconds: int) -> str:
        raise DomainError("signed_url_unavailable", "本地开发存储仅支持鉴权代理下载", 422)


class S3Store:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.spool_root = settings.spool_root
        self.spool_root.mkdir(parents=True, exist_ok=True)
        assert self.bucket and settings.s3_access_key and settings.s3_secret_key
        self.client: Any = boto3.client("s3", endpoint_url=settings.s3_endpoint, region_name=settings.s3_region, aws_access_key_id=settings.s3_access_key.get_secret_value(), aws_secret_access_key=settings.s3_secret_key.get_secret_value(), config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 1}, s3={"addressing_style": "path"}))

    def check_private_versioned_bucket(self) -> None:
        try:
            if self.client.get_bucket_versioning(Bucket=self.bucket).get("Status") != "Enabled":
                raise DomainError("storage_versioning_required", "私有对象桶必须启用版本控制", 503)
            acl = self.client.get_bucket_acl(Bucket=self.bucket)
            if any("AllUsers" in g.get("Grantee", {}).get("URI", "") or "AuthenticatedUsers" in g.get("Grantee", {}).get("URI", "") for g in acl.get("Grants", [])):
                raise DomainError("public_bucket_denied", "对象桶不能开放公共读取", 503)
            if self.client.get_bucket_policy_status(Bucket=self.bucket).get("PolicyStatus", {}).get("IsPublic") is not False:
                raise DomainError("bucket_privacy_unverified", "无法确认对象桶策略为私有", 503)
        except (BotoCoreError, ClientError):
            raise DomainError("storage_unavailable", "对象存储暂不可用", 503) from None

    def put(self, key: str, source: BinaryIO, media_type: str, maximum: int) -> StoredObject:
        relative_file(key)
        # Bounded spooling permits S3 length/hash validation without buffering a video in RAM.
        from tempfile import SpooledTemporaryFile
        digest, size = hashlib.sha256(), 0
        with SpooledTemporaryFile(max_size=8 * CHUNK, mode="w+b", dir=str(self.spool_root)) as buffer:
            while data := source.read(CHUNK):
                size += len(data)
                if size > maximum:
                    raise DomainError("file_too_large", "文件超过大小限制", 413)
                digest.update(data)
                buffer.write(data)
            if not size:
                raise DomainError("empty_file", "文件不能为空", 422)
            buffer.seek(0)
            try:
                response = self.client.put_object(Bucket=self.bucket, Key=key, Body=buffer, ContentLength=size, ContentType=media_type, Metadata={"sha256": digest.hexdigest()})
            except (BotoCoreError, ClientError):
                raise DomainError("storage_unavailable", "对象存储暂不可用", 503) from None
        version = response.get("VersionId")
        try:
            version = _version(version)
        except DomainError:
            raise DomainError("storage_versioning_required", "对象没有不可变版本标识", 503) from None
        actual = hashlib.sha256()
        total = 0
        for data in self.read_complete(key, version, size):
            actual.update(data)
            total += len(data)
        if total != size or actual.hexdigest() != digest.hexdigest():
            raise DomainError("object_hash_mismatch", "云端对象校验失败", 503)
        return StoredObject(key, version, size, digest.hexdigest(), media_type)

    def read(self, key: str, version: str, start: int, end: int) -> Iterator[bytes]:
        relative_file(key)
        return self._read(key, version, start, end, complete=False)

    def read_complete(self, key: str, version: str, size: int) -> Iterator[bytes]:
        return self._read(key, version, 0, size - 1, complete=True)

    def _read(self, key: str, version: str, start: int, end: int, *, complete: bool) -> Iterator[bytes]:
        relative_file(key)
        _version(version)
        if type(start) is not int or type(end) is not int or not 0 <= start <= end:
            raise DomainError("invalid_object_range", "对象读取范围无效", 422)
        body = None
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key, VersionId=version, Range=f"bytes={start}-{end}")
            body = response.get("Body")
            declared = response.get("ContentRange")
            interval = S3_RANGE.fullmatch(declared) if isinstance(declared, str) else None
            remaining = end - start + 1
            if body is None or response.get("VersionId") != version or response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 206 or type(response.get("ContentLength")) is not int or response["ContentLength"] != remaining or interval is None or tuple(map(int, interval.groups()[:2])) != (start, end) or int(interval.group(3)) <= end:
                raise DomainError("storage_response_invalid", "对象存储响应与请求不一致", 503)
            if complete and int(interval.group(3)) != end + 1:
                raise DomainError("storage_response_invalid", "对象存储响应与请求不一致", 503)
            while remaining:
                requested = min(CHUNK, remaining)
                data = body.read(requested)
                if not data:
                    raise DomainError("object_truncated", "对象数据不完整", 503)
                if len(data) > requested:
                    raise DomainError("storage_response_invalid", "对象存储响应与请求不一致", 503)
                remaining -= len(data)
                yield data
            if body.read(1):
                raise DomainError("storage_response_invalid", "对象存储响应与请求不一致", 503)
        except (BotoCoreError, ClientError, OSError):
            raise DomainError("storage_unavailable", "对象存储暂不可用", 503) from None
        finally:
            if body is not None:
                _close_body(body)

    def delete(self, key: str, version: str) -> None:
        relative_file(key)
        _version(version)
        try:
            response = self.client.delete_object(Bucket=self.bucket, Key=key, VersionId=version)
        except (BotoCoreError, ClientError):
            raise DomainError("storage_unavailable", "对象存储暂不可用", 503) from None
        if response.get("VersionId") != version or response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 204:
            raise DomainError("storage_response_invalid", "对象存储响应与请求不一致", 503)

    def signed_url(self, key: str, version: str, seconds: int) -> str:
        relative_file(key)
        _version(version)
        if type(seconds) is not int or seconds <= 0:
            raise DomainError("invalid_url_lifetime", "签名有效期无效", 422)
        try:
            return str(self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key, "VersionId": version}, ExpiresIn=min(seconds, 900)))
        except (BotoCoreError, ClientError):
            raise DomainError("storage_unavailable", "对象存储暂不可用", 503) from None


def make_store(settings: Settings) -> LocalDevelopmentStore | S3Store:
    if settings.storage_backend == "local_development":
        return LocalDevelopmentStore(settings.local_storage_root)
    return S3Store(settings)
