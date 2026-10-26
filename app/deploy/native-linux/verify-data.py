"""Check real PostgreSQL, versioned S3 and native media without creating generation jobs."""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from generation_infrastructure.database import make_sessions
from generation_infrastructure.media import inspect_media
from generation_infrastructure.settings import Settings
from generation_infrastructure.storage import S3Store
from sqlalchemy import text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(_env_file=args.config)
    if settings.environment != "production" or settings.storage_backend != "s3":
        raise SystemExit("Requires the explicit production PostgreSQL/S3 configuration")
    sessions = make_sessions(settings)
    results: dict[str, object] = {"at": datetime.now(UTC).isoformat(), "synthetic_media": True, "generation_acceptance": False}
    try:
        with sessions() as session:
            version = session.scalar(text("SHOW server_version_num"))
            head = session.scalar(text("SELECT version_num FROM alembic_version"))
            if not (180000 <= int(version) < 190000 and head == "3da02e7b5941"):
                raise RuntimeError("Unexpected native database or migration version")
            results.update(postgres_version=int(version), database_head=head, business_table_count=session.scalar(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name != 'alembic_version'")), generation_jobs=session.scalar(text("SELECT count(*) FROM jobs")))
    finally:
        sessions.kw["bind"].dispose()
    store = S3Store(settings)
    store.check_private_versioned_bucket()
    results["private_versioned_bucket"] = True
    key = "deployment-verification/" + uuid4().hex + ".bin"
    objects = []
    try:
        for value in (b"deployment-first-version", b"deployment-second-version"):
            objects.append(store.put(key, io.BytesIO(value), "application/octet-stream", 1024))
        if objects[0].version == objects[1].version:
            raise RuntimeError("S3 did not create distinct immutable versions")
        if b"".join(store.read_complete(key, objects[0].version, objects[0].bytes)) != b"deployment-first-version":
            raise RuntimeError("Old object version changed")
        if b"".join(store.read(key, objects[1].version, 0, 9)) != b"deployment":
            raise RuntimeError("Object range mismatch")
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.get(settings.s3_endpoint.rstrip("/") + "/" + settings.s3_bucket + "/" + key)
            if response.status_code != 403:
                raise RuntimeError("Anonymous object access was not denied")
        results.update(immutable_versions=True, old_version_read=True, ranged_read=True, anonymous_read_denied=True)
    finally:
        for obj in objects:
            store.delete(key, obj.version)
    results["probe_versions_deleted"] = True
    with tempfile.TemporaryDirectory(prefix="deployment-verify-", dir=settings.spool_root) as directory:
        root = Path(directory)
        video, audio = root / "probe.mp4", root / "probe.wav"
        commands = [
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0xbef43c:s=64x64:r=5:d=1", "-an", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(video)],
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", "-threads", "1", str(audio)],
        ]
        for command in commands:
            subprocess.run(command, check=True, capture_output=True, timeout=20)  # noqa: S603 -- fixed native operation with trusted deployment configuration
        results["synthetic_video_probe"] = inspect_media(video, "video/mp4")
        results["synthetic_audio_decode"] = inspect_media(audio, "audio/wav")
    results["passed"] = True
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
