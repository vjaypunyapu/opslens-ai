"""
OpsLens AI — Object Storage Helper
======================================
Thin wrapper around boto3's S3 client for general-purpose app assets
(tenant logos, etc.). Works against real AWS S3 or any S3-compatible
provider (Railway Bucket / Tigris, MinIO, ...) via AWS_ENDPOINT_URL.

Uploaded objects are stored privately — callers get back a time-limited
presigned URL rather than a permanent public link, so no bucket-level
public-read policy is required.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import HTTPException, status

from ..config import settings
from .logging import get_logger

logger = get_logger(__name__)


class StorageNotConfigured(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Object storage is not configured. Set AWS_ACCESS_KEY_ID, "
                "AWS_SECRET_ACCESS_KEY, and AWS_S3_BUCKET_NAME (and "
                "AWS_ENDPOINT_URL for non-AWS providers) to enable uploads."
            ),
        )


@lru_cache(maxsize=1)
def _get_client():
    if not (settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY and settings.AWS_S3_BUCKET_NAME):
        return None

    import boto3  # type: ignore

    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_DEFAULT_REGION,
        endpoint_url=settings.AWS_ENDPOINT_URL or None,
    )


def upload_object(key: str, data: bytes, content_type: str) -> None:
    """Upload bytes to the configured bucket under `key`. Raises 503 if unconfigured."""
    client = _get_client()
    if client is None:
        raise StorageNotConfigured()
    client.put_object(
        Bucket=settings.AWS_S3_BUCKET_NAME,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    logger.info("Uploaded object to bucket=%s key=%s (%d bytes)", settings.AWS_S3_BUCKET_NAME, key, len(data))


def presigned_url(key: str, expires_in: int = 3600) -> str:
    """Return a time-limited GET URL for `key`. Raises 503 if unconfigured."""
    client = _get_client()
    if client is None:
        raise StorageNotConfigured()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.AWS_S3_BUCKET_NAME, "Key": key},
        ExpiresIn=expires_in,
    )
