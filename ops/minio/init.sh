#!/bin/sh
set -eu
# No xtrace or raw client output: failures have bounded operational codes.
fail() { echo "minio_init_failed" >&2; exit 1; }
MC_CONFIG_DIR=$(mktemp -d /tmp/minio-init.XXXXXX) || fail
export MC_CONFIG_DIR
trap 'rm -rf "$MC_CONFIG_DIR"' EXIT
[ "$MINIO_ROOT_USER" != "$S3_ACCESS_KEY_ID" ] || fail
mc alias set setup http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1 || fail
attempt=0
until mc ready setup >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 30 ] || fail
    sleep 2
done
mc mb --ignore-existing setup/recruitmatch-artifacts >/dev/null 2>&1 || fail
mc anonymous set none setup/recruitmatch-artifacts >/dev/null 2>&1 || fail
mc anonymous get-json setup/recruitmatch-artifacts > "$MC_CONFIG_DIR/anonymous-policy.json" 2>/dev/null || fail
# A private bucket has no anonymous policy, represented as an empty JSON object.
[ "$(tr -d '[:space:]' < "$MC_CONFIG_DIR/anonymous-policy.json")" = '{}' ] || fail
mc admin user add setup "$S3_ACCESS_KEY_ID" "$S3_SECRET_ACCESS_KEY" >/dev/null 2>&1 || fail
mc admin policy create setup recruitmatch-artifacts-app /init/app-policy.json >/dev/null 2>&1 || fail
mc admin policy attach setup recruitmatch-artifacts-app --user "$S3_ACCESS_KEY_ID" >/dev/null 2>&1 || fail
# Refuse an existing app account whose other policies/groups would broaden access.
info=$(mc admin user info --json setup "$S3_ACCESS_KEY_ID" 2>/dev/null) || fail
info=$(printf '%s' "$info" | tr -d '[:space:]')
case "$info" in *'"policyName":"recruitmatch-artifacts-app"'*) ;; *) fail ;; esac
case "$info" in *'"memberOf":[]'*) ;; *'"memberOf":'*) fail ;; esac
case "$info" in *'"userStatus":"enabled"'*) ;; *) fail ;; esac
echo "minio_init_ready"
