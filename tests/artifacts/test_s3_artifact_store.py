"""Real MinIO contract; required in the Compose integration lane."""

import base64
import hashlib
import os
import subprocess
from io import BytesIO
from uuid import uuid4

import boto3
import pytest
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError

from app.artifacts.ports import ArtifactLocation, ArtifactMissing, ArtifactTooLarge
from app.artifacts.s3 import S3ArtifactStore, S3ArtifactStoreHealthProbe, S3Settings


@pytest.fixture
def client():
    return S3Settings.from_env().client()


@pytest.fixture
def location(client):
    loc = ArtifactLocation("synthetic", "knowledge", "owner", uuid4().hex)
    yield loc
    S3ArtifactStore(client).delete(loc)


def test_real_put_head_get_delete_and_private_object(client, location):
    data = b"synthetic private artifact"
    digest = hashlib.sha256(data).hexdigest()
    store = S3ArtifactStore(client)
    store.put(location, BytesIO(data), len(data), digest)
    assert store.inspect(location).sha256 == digest
    assert store.read_bounded(location, 100) == data
    with pytest.raises(ArtifactTooLarge):
        store.read_bounded(location, 2)
    anonymous = boto3.client(
        "s3", endpoint_url=os.environ["S3_ENDPOINT_URL"], config=Config(signature_version=UNSIGNED)
    )
    key = f"tenants/synthetic/knowledge/owner/{location.artifact_id}"
    with pytest.raises(ClientError) as denied:
        anonymous.get_object(Bucket="recruitmatch-artifacts", Key=key)
    assert denied.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403
    store.delete(location)
    store.delete(location)
    with pytest.raises(ArtifactMissing):
        store.inspect(location)
    assert S3ArtifactStoreHealthProbe(client).probe()


def test_server_rejects_wrong_sha256(client, location):
    key = f"tenants/synthetic/knowledge/owner/{location.artifact_id}"
    with pytest.raises(ClientError) as rejected:
        client.put_object(
            Bucket="recruitmatch-artifacts",
            Key=key,
            Body=b"synthetic",
            ChecksumSHA256=base64.b64encode(b"\x00" * 32).decode(),
        )
    assert rejected.value.response["Error"]["Code"] == "XAmzContentChecksumMismatch"
    with pytest.raises(ArtifactMissing):
        S3ArtifactStore(client).inspect(location)


@pytest.mark.parametrize(
    "operation,arguments",
    [
        ("create_bucket", {"Bucket": "synthetic-forbidden"}),
        (
            "put_bucket_policy",
            {"Bucket": "recruitmatch-artifacts", "Policy": '{"Version":"2012-10-17","Statement":[]}'},
        ),
        ("delete_bucket", {"Bucket": "recruitmatch-artifacts"}),
        ("put_object", {"Bucket": "recruitmatch-artifacts", "Key": "forbidden/object", "Body": b"synthetic"}),
        ("put_object", {"Bucket": "synthetic-other", "Key": "tenants/object", "Body": b"synthetic"}),
        ("get_object", {"Bucket": "recruitmatch-artifacts", "Key": "forbidden/object"}),
        ("delete_object", {"Bucket": "recruitmatch-artifacts", "Key": "forbidden/object"}),
        ("head_bucket", {"Bucket": "synthetic-other"}),
    ],
)
def test_application_iam_denies_management_and_other_resources(client, operation, arguments):
    with pytest.raises(ClientError) as denied:
        getattr(client, operation)(**arguments)
    assert denied.value.response["ResponseMetadata"]["HTTPStatusCode"] == 403


@pytest.mark.parametrize(
    "command",
    [
        ["admin", "user", "list", "app"],
        ["admin", "policy", "create", "app", "forbidden", "/app/ops/minio/app-policy.json"],
    ],
)
def test_application_cannot_manage_users_or_iam(tmp_path, command):
    env = {**os.environ, "MC_CONFIG_DIR": str(tmp_path)}
    setup = subprocess.run(
        [
            "mc",
            "alias",
            "set",
            "app",
            os.environ["S3_ENDPOINT_URL"],
            os.environ["S3_ACCESS_KEY_ID"],
            os.environ["S3_SECRET_ACCESS_KEY"],
        ],
        env=env,
        capture_output=True,
    )
    assert setup.returncode == 0
    result = subprocess.run(["mc", "--json", *command], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Access Denied" in result.stdout or "AccessDenied" in result.stdout


def test_headbucket_mapping_allows_only_artifact_bucket_listing(client):
    result = client.list_objects_v2(Bucket="recruitmatch-artifacts", MaxKeys=1)
    assert result["ResponseMetadata"]["HTTPStatusCode"] == 200
    assert result["KeyCount"] <= 1
    assert [bucket["Name"] for bucket in client.list_buckets()["Buckets"]] == ["recruitmatch-artifacts"]
