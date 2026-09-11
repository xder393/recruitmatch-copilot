import base64
import hashlib
from io import BytesIO

import pytest

from app.artifacts.ports import (
    ArtifactAccessDenied,
    ArtifactChecksumMismatch,
    ArtifactInvalidLocation,
    ArtifactLengthMismatch,
    ArtifactLocation,
    ArtifactMissing,
    ArtifactStorageFailure,
    ArtifactTooLarge,
)
from app.artifacts.s3 import S3ArtifactStore, S3ArtifactStoreHealthProbe, S3Settings
from tests.fakes.artifacts import FakeArtifactStore


DATA = b"synthetic artifact"
DIGEST = hashlib.sha256(DATA).hexdigest()
LOCATION = ArtifactLocation("tenant-1", "resumes", "owner-1", "artifact-1")


class Peer:
    """An external peer that can lie about headers or fail during streaming."""

    def __init__(self):
        self.body = BytesIO(DATA)
        self.head = {"ContentLength": len(DATA), "ChecksumSHA256": base64.b64encode(bytes.fromhex(DIGEST)).decode()}
        self.puts = []
        self.heads = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)

    def head_object(self, **kwargs):
        self.heads.append(kwargs)
        return self.head

    def get_object(self, **kwargs):
        return {"Body": self.body, **self.head}

    def head_bucket(self, **kwargs):
        return {}


@pytest.mark.parametrize("store_factory", [FakeArtifactStore, lambda: S3ArtifactStore(Peer())])
def test_store_contract_and_inspection(store_factory):
    store = store_factory()
    store.put(LOCATION, BytesIO(DATA), len(DATA), DIGEST)
    assert store.inspect(LOCATION).size_bytes == len(DATA)
    assert store.inspect(LOCATION).sha256 == DIGEST
    assert store.read_bounded(LOCATION, len(DATA)) == DATA


def test_put_uses_derived_key_and_backend_checksum_then_head():
    peer = Peer()
    S3ArtifactStore(peer).put(LOCATION, BytesIO(DATA), len(DATA), DIGEST)
    assert peer.puts[0]["Key"] == "tenants/tenant-1/resumes/owner-1/artifact-1"
    assert peer.puts[0]["ChecksumSHA256"] == base64.b64encode(bytes.fromhex(DIGEST)).decode()
    assert peer.heads[0]["ChecksumMode"] == "ENABLED"


@pytest.mark.parametrize(
    "field,bad",
    [
        ("namespace", "other"),
        ("namespace", "resumes/x"),
        ("tenant_id", "../x"),
        ("owner_id", "a/b"),
        ("artifact_id", "a\\b"),
        ("tenant_id", ""),
        ("owner_id", "."),
        ("artifact_id", "a%2fb"),
    ],
)
def test_invalid_locations_fail_without_exposing_input(field, bad):
    values = dict(tenant_id="tenant", namespace="resumes", owner_id="owner", artifact_id="artifact")
    values[field] = bad
    with pytest.raises(ArtifactInvalidLocation) as error:
        ArtifactLocation(**values)
    assert str(error.value) == "artifact_invalid_location"


@pytest.mark.parametrize(
    "size,digest,error",
    [
        (len(DATA) + 1, DIGEST, ArtifactLengthMismatch),
        (len(DATA), "0" * 64, ArtifactChecksumMismatch),
        (10 * 1024 * 1024 + 1, DIGEST, ArtifactTooLarge),
    ],
)
def test_put_rejects_invalid_payload_before_writing(size, digest, error):
    peer = Peer()
    with pytest.raises(error):
        S3ArtifactStore(peer).put(LOCATION, BytesIO(DATA), size, digest)
    assert not peer.puts


@pytest.mark.parametrize(
    "head,error",
    [
        ({"ContentLength": len(DATA)}, ArtifactChecksumMismatch),
        ({"ContentLength": len(DATA), "ChecksumSHA256": "not-base64"}, ArtifactChecksumMismatch),
        (
            {"ContentLength": 99, "ChecksumSHA256": base64.b64encode(bytes.fromhex(DIGEST)).decode()},
            ArtifactLengthMismatch,
        ),
    ],
)
def test_put_requires_head_integrity(head, error):
    peer = Peer()
    peer.head = head
    with pytest.raises(error):
        S3ArtifactStore(peer).put(LOCATION, BytesIO(DATA), len(DATA), DIGEST)


def test_read_checks_head_before_fetching_body():
    peer = Peer()
    with pytest.raises(ArtifactTooLarge):
        S3ArtifactStore(peer).read_bounded(LOCATION, 2)
    assert peer.body.tell() == 0


def test_lying_peer_is_read_at_most_bound_plus_one_and_closed():
    class CountingBody(BytesIO):
        consumed = 0

        def read(self, size=-1):
            assert size >= 0
            value = super().read(min(size, 2))
            self.consumed += len(value)
            return value

    peer = Peer()
    peer.head["ContentLength"] = 3
    peer.body = CountingBody(b"x" * 100)
    with pytest.raises(ArtifactTooLarge):
        S3ArtifactStore(peer).read_bounded(LOCATION, 5)
    assert peer.body.consumed == 6
    assert peer.body.closed


@pytest.mark.parametrize(
    "data,error", [(b"short", ArtifactLengthMismatch), (b"x" * len(DATA), ArtifactChecksumMismatch)]
)
def test_read_integrity_failures_close_body(data, error):
    peer = Peer()
    peer.body = BytesIO(data)
    with pytest.raises(error):
        S3ArtifactStore(peer).read_bounded(LOCATION, 100)
    assert peer.body.closed


def test_stream_failure_is_sanitized_and_closed():
    class BrokenBody(BytesIO):
        def read(self, size=-1):
            raise OSError("secret endpoint and filename")

    peer = Peer()
    peer.body = BrokenBody()
    with pytest.raises(ArtifactStorageFailure) as error:
        S3ArtifactStore(peer).read_bounded(LOCATION, 100)
    assert str(error.value) == "artifact_storage_failure"
    assert error.value.__suppress_context__
    assert peer.body.closed


@pytest.mark.parametrize(
    "code,error",
    [
        ("NoSuchKey", ArtifactMissing),
        ("AccessDenied", ArtifactAccessDenied),
        ("SlowDown", ArtifactStorageFailure),
        ("BadDigest", ArtifactChecksumMismatch),
    ],
)
def test_provider_failures_are_stable_and_sanitized(code, error):
    from botocore.exceptions import ClientError

    class FailedPeer(Peer):
        def head_object(self, **kwargs):
            raise ClientError({"Error": {"Code": code, "Message": "secret key/PII"}}, "HeadObject")

    with pytest.raises(error) as caught:
        S3ArtifactStore(FailedPeer()).inspect(LOCATION)
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_health_is_read_only_and_settings_hide_secrets():
    assert S3ArtifactStoreHealthProbe(Peer()).probe() is True
    settings = S3Settings("http://secret-endpoint:9000", "secret-access", "secret-password")
    assert "secret" not in repr(settings)


def test_fake_delete_is_idempotent_and_inspection_reports_missing():
    store = FakeArtifactStore()
    store.put(LOCATION, BytesIO(DATA), len(DATA), DIGEST)
    store.delete(LOCATION)
    store.delete(LOCATION)
    with pytest.raises(ArtifactMissing):
        store.inspect(LOCATION)


@pytest.mark.parametrize(
    "sdk_error,wanted",
    [("FlexibleChecksumError", ArtifactChecksumMismatch), ("IncompleteReadError", ArtifactLengthMismatch)],
)
def test_sdk_stream_integrity_failure_keeps_specific_code(sdk_error, wanted):
    from botocore.exceptions import FlexibleChecksumError, IncompleteReadError

    class BrokenBody(BytesIO):
        def read(self, size=-1):
            if sdk_error == "FlexibleChecksumError":
                raise FlexibleChecksumError(error_msg="private provider body")
            raise IncompleteReadError(actual_bytes=1, expected_bytes=20)

    peer = Peer()
    peer.body = BrokenBody()
    with pytest.raises(wanted):
        S3ArtifactStore(peer).read_bounded(LOCATION, 100)
    assert peer.body.closed


def test_successful_read_closes_body():
    peer = Peer()
    assert S3ArtifactStore(peer).read_bounded(LOCATION, 100) == DATA
    assert peer.body.closed


def test_head_digest_must_equal_requested_digest():
    peer = Peer()
    peer.head["ChecksumSHA256"] = base64.b64encode(b"\x00" * 32).decode()
    with pytest.raises(ArtifactChecksumMismatch):
        S3ArtifactStore(peer).put(LOCATION, BytesIO(DATA), len(DATA), DIGEST)


def test_health_failure_is_false_and_never_writes():
    from botocore.exceptions import EndpointConnectionError

    class Offline:
        def head_bucket(self, **kwargs):
            raise EndpointConnectionError(endpoint_url="private endpoint")

    assert S3ArtifactStoreHealthProbe(Offline()).probe() is False


@pytest.mark.parametrize(
    "endpoint,access,secret",
    [
        ("http://private", "", "private"),
        ("http://private", "private", ""),
        ("invalid-private", "private", "private"),
        ("http://username:private@host", "private", "private"),
    ],
)
def test_settings_reject_missing_identity_or_unsafe_endpoint_without_echo(endpoint, access, secret):
    with pytest.raises(ArtifactStorageFailure) as error:
        S3Settings(endpoint, access, secret)
    assert str(error.value) == "artifact_storage_failure"
