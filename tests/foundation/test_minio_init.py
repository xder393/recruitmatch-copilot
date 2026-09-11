"""Execute the initializer with controlled administrative responses."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "policy,groups,expected",
    [
        ("recruitmatch-artifacts-app", [], 0),
        ("readwrite,recruitmatch-artifacts-app", [], 1),
        ("recruitmatch-artifacts-app", ["administrators"], 1),
    ],
)
def test_init_rejects_extra_identity_permissions(tmp_path, policy, groups, expected):
    client = tmp_path / "mc"
    client.write_text(
        f"#!{sys.executable}\n"
        + """
import json, os, sys
args = sys.argv[1:]
if "get-json" in args:
    print("{}")
elif "info" in args:
    print(json.dumps({"policyName": os.environ["TEST_POLICY"],
                      "memberOf": json.loads(os.environ["TEST_GROUPS"]), "userStatus": "enabled"}))
"""
    )
    client.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "MINIO_ROOT_USER": "synthetic-root",
        "MINIO_ROOT_PASSWORD": "synthetic-root-password",
        "S3_ACCESS_KEY_ID": "synthetic-app",
        "S3_SECRET_ACCESS_KEY": "synthetic-app-password",
        "TEST_POLICY": policy,
        "TEST_GROUPS": json.dumps(groups),
    }
    result = subprocess.run(["/bin/sh", str(Path("ops/minio/init.sh"))], env=env, capture_output=True, text=True)
    assert result.returncode == expected
    assert "password" not in result.stdout + result.stderr
