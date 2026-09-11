"""Bounded single-part S3 storage. SDK details and object keys stay in this adapter."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import os
import re
from typing import BinaryIO
from urllib.parse import urlsplit

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
    FlexibleChecksumError,
    IncompleteReadError,
)

from app.artifacts.ports import (
    MAX_ARTIFACT_BYTES,
    ArtifactAccessDenied,
    ArtifactChecksumMismatch,
    ArtifactError,
    ArtifactInspection,
    ArtifactInvalidLocation,
    ArtifactLengthMismatch,
    ArtifactLocation,
    ArtifactMissing,
    ArtifactStorageFailure,
    ArtifactTooLarge,
)

BUCKET = "recruitmatch-artifacts"


@contextmanager
def _storage_errors():
    try:
        yield
    except ArtifactError:
        raise
    except FlexibleChecksumError:
        raise ArtifactChecksumMismatch() from None
    except IncompleteReadError:
        raise ArtifactLengthMismatch() from None
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        error = {
            "NoSuchKey": ArtifactMissing,
            "NotFound": ArtifactMissing,
            "404": ArtifactMissing,
            "NoSuchBucket": ArtifactMissing,
            "AccessDenied": ArtifactAccessDenied,
            "403": ArtifactAccessDenied,
            "InvalidAccessKeyId": ArtifactAccessDenied,
            "SignatureDoesNotMatch": ArtifactAccessDenied,
            "BadDigest": ArtifactChecksumMismatch,
            "XAmzContentChecksumMismatch": ArtifactChecksumMismatch,
        }.get(code, ArtifactStorageFailure)
        raise error() from None
    except (BotoCoreError, OSError, ValueError, TypeError, KeyError):
        raise ArtifactStorageFailure() from None


@dataclass(frozen=True)
class S3Settings:
    endpoint_url: str = field(repr=False)
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)

    def __post_init__(self) -> None:
        with _storage_errors():
            endpoint = urlsplit(self.endpoint_url)
            if (
                endpoint.scheme not in ("http", "https")
                or not endpoint.hostname
                or endpoint.username
                or endpoint.password
                or endpoint.query
                or endpoint.fragment
                or not self.access_key_id.strip()
                or not self.secret_access_key.strip()
            ):
                raise ArtifactStorageFailure()

    @classmethod
    def from_env(cls) -> S3Settings:
        with _storage_errors():
            return cls(
                os.environ["S3_ENDPOINT_URL"], os.environ["S3_ACCESS_KEY_ID"], os.environ["S3_SECRET_ACCESS_KEY"]
            )

    def client(self):
        with _storage_errors():
            return boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name="us-east-1",
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    connect_timeout=3,
                    read_timeout=10,
                    retries={"mode": "standard", "total_max_attempts": 2},
                ),
            )


def _key(location: ArtifactLocation) -> str:
    if not isinstance(location, ArtifactLocation):
        raise ArtifactInvalidLocation()
    location.__post_init__()
    return f"tenants/{location.tenant_id}/{location.namespace}/{location.owner_id}/{location.artifact_id}"


def _checksum(head: dict) -> str:
    try:
        digest = base64.b64decode(head.get("ChecksumSHA256", ""), validate=True)
        if len(digest) != 32:
            raise ValueError
        return digest.hex()
    except (ValueError, TypeError):
        raise ArtifactChecksumMismatch() from None


def _size(head: dict) -> int:
    value = head.get("ContentLength")
    if type(value) is not int or value < 0:
        raise ArtifactLengthMismatch()
    return value


def _read_limit(stream: BinaryIO, limit: int) -> bytes:
    chunks = []
    remaining = limit + 1
    while remaining:
        chunk = stream.read(min(64 * 1024, remaining))
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise ArtifactStorageFailure()
        if len(chunk) > remaining:
            raise ArtifactTooLarge()
        chunks.append(chunk)
        remaining -= len(chunk)
    value = b"".join(chunks)
    if len(value) > limit:
        raise ArtifactTooLarge()
    return value


class S3ArtifactStore:
    def __init__(self, client):
        self._client = client

    def inspect(self, location: ArtifactLocation) -> ArtifactInspection:
        key = _key(location)
        with _storage_errors():
            head = self._client.head_object(Bucket=BUCKET, Key=key, ChecksumMode="ENABLED")
            size = _size(head)
            if size > MAX_ARTIFACT_BYTES:
                raise ArtifactTooLarge()
            return ArtifactInspection(size, _checksum(head))

    def put(self, location: ArtifactLocation, stream: BinaryIO, size_bytes: int, sha256: str) -> None:
        key = _key(location)
        if type(size_bytes) is not int or size_bytes < 0:
            raise ArtifactLengthMismatch()
        if size_bytes > MAX_ARTIFACT_BYTES:
            raise ArtifactTooLarge()
        if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ArtifactChecksumMismatch()
        with _storage_errors():
            # The caller owns its input stream. This bounded copy also supports non-seekable streams.
            data = _read_limit(stream, MAX_ARTIFACT_BYTES)
            if len(data) != size_bytes:
                raise ArtifactLengthMismatch()
            if hashlib.sha256(data).hexdigest() != sha256:
                raise ArtifactChecksumMismatch()
            self._client.put_object(
                Bucket=BUCKET,
                Key=key,
                Body=data,
                ContentLength=size_bytes,
                ChecksumSHA256=base64.b64encode(bytes.fromhex(sha256)).decode("ascii"),
            )
            info = self.inspect(location)
            if info.size_bytes != size_bytes:
                raise ArtifactLengthMismatch()
            if info.sha256 != sha256:
                raise ArtifactChecksumMismatch()

    def read_bounded(self, location: ArtifactLocation, max_bytes: int) -> bytes:
        if type(max_bytes) is not int or max_bytes < 0:
            raise ArtifactTooLarge()
        limit = min(max_bytes, MAX_ARTIFACT_BYTES)
        info = self.inspect(location)
        if info.size_bytes > limit:
            raise ArtifactTooLarge()
        with _storage_errors():
            response = self._client.get_object(Bucket=BUCKET, Key=_key(location), ChecksumMode="ENABLED")
            body = response["Body"]
            try:
                if _size(response) > limit:
                    raise ArtifactTooLarge()
                data = _read_limit(body, limit)
                if len(data) != info.size_bytes or len(data) != _size(response):
                    raise ArtifactLengthMismatch()
                digest = hashlib.sha256(data).hexdigest()
                if digest != info.sha256 or digest != _checksum(response):
                    raise ArtifactChecksumMismatch()
                return data
            finally:
                body.close()

    def delete(self, location: ArtifactLocation) -> None:
        key = _key(location)
        with _storage_errors():
            self._client.delete_object(Bucket=BUCKET, Key=key)


class S3ArtifactStoreHealthProbe:
    def __init__(self, client):
        self._client = client

    def probe(self) -> bool:
        try:
            with _storage_errors():
                self._client.head_bucket(Bucket=BUCKET)
            return True
        except ArtifactError:
            return False
