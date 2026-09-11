#!/bin/sh
set -eu
# One-shot synthetic integration fixture only; never part of normal deployment.
fail() { echo "minio_test_setup_failed" >&2; exit 1; }
MC_CONFIG_DIR=$(mktemp -d /tmp/minio-test.XXXXXX) || fail
export MC_CONFIG_DIR
trap 'rm -rf "$MC_CONFIG_DIR"' EXIT
mc alias set setup http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1 || fail
mc mb --ignore-existing setup/synthetic-other >/dev/null 2>&1 || fail
mc anonymous set none setup/synthetic-other >/dev/null 2>&1 || fail
echo "minio_test_setup_ready"
