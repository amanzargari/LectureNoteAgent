"""
Storage abstraction: local filesystem (default) or MinIO/S3.

Set STORAGE_BACKEND=minio and the MINIO_* env vars to enable object storage.
Falls back to local if minio package is not installed or vars are missing.
"""
from __future__ import annotations

import io
import os
from pathlib import Path


_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()
_MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
_MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
_MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
_MINIO_BUCKET = os.getenv("MINIO_BUCKET", "lectureai")
_MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() in {"1", "true", "yes"}

_minio_client = None


def _get_minio():
    global _minio_client
    if _minio_client is not None:
        return _minio_client
    try:
        from minio import Minio
        client = Minio(
            _MINIO_ENDPOINT,
            access_key=_MINIO_ACCESS_KEY,
            secret_key=_MINIO_SECRET_KEY,
            secure=_MINIO_SECURE,
        )
        if not client.bucket_exists(_MINIO_BUCKET):
            client.make_bucket(_MINIO_BUCKET)
        _minio_client = client
        return client
    except Exception as exc:
        raise RuntimeError(f"MinIO init failed: {exc}") from exc


def is_minio_enabled() -> bool:
    return _BACKEND == "minio"


def upload_file(local_path: str, object_key: str) -> str:
    """Upload a local file to the storage backend. Returns object_key."""
    if not is_minio_enabled():
        return ""
    client = _get_minio()
    client.fput_object(_MINIO_BUCKET, object_key, local_path)
    return object_key


def upload_bytes(data: bytes, object_key: str, content_type: str = "application/octet-stream") -> str:
    """Upload raw bytes to the storage backend. Returns object_key."""
    if not is_minio_enabled():
        return ""
    client = _get_minio()
    client.put_object(_MINIO_BUCKET, object_key, io.BytesIO(data), len(data), content_type=content_type)
    return object_key


def download_file(object_key: str, local_path: str) -> None:
    """Download an object from storage to a local path."""
    client = _get_minio()
    client.fget_object(_MINIO_BUCKET, object_key, local_path)


def get_file_bytes(object_key: str) -> bytes:
    """Return file contents as bytes from storage."""
    client = _get_minio()
    resp = client.get_object(_MINIO_BUCKET, object_key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def delete_object(object_key: str) -> None:
    """Delete an object from storage (best-effort)."""
    if not is_minio_enabled() or not object_key:
        return
    try:
        client = _get_minio()
        client.remove_object(_MINIO_BUCKET, object_key)
    except Exception:
        pass


def make_object_key(user_id: int, project_id: int, filename: str, prefix: str = "uploads") -> str:
    return f"{prefix}/{user_id}/{project_id}/{filename}"
