#!/bin/sh
# Isolated R14 rule-engine acceptance: fixed project, no published ports/data.
set -eu
cd "$(dirname "$0")/.."
project=recruitmatch-cp5-alerts-sep14
compose() { docker compose -p "$project" -f tests/observability/compose.alerts.yml "$@"; }
for service in prometheus stimulus; do
  target="$project-$service-1"
  if docker inspect "$target" >/dev/null 2>&1; then
    docker inspect "$target" | jq -e --arg p "$project" --arg s "$service" '
      .[0].Config.Labels["com.docker.compose.project"] == $p
      and .[0].Config.Labels["com.docker.compose.service"] == $s' >/dev/null
  fi
done
if [ "$#" -eq 1 ]; then
  case "$1" in /tmp/recruitmatch-cp5-telemetry.*) evidence=$1 ;; *) exit 2 ;; esac
  test -d "$evidence"
else
  test "$#" -eq 0
  evidence=$(mktemp -d /tmp/recruitmatch-cp5-alerts.XXXXXX)
fi
chmod 777 "$evidence"
cleanup() { compose stop --timeout 10 prometheus stimulus >/dev/null; }
trap cleanup EXIT
trap 'exit 130' INT TERM
# tmpfs starts empty; no historic sample or alert can satisfy this run.
compose up -d --force-recreate prometheus stimulus
compose run --rm --no-deps -v "$evidence:/evidence" verify
printf 'Synthetic alert evidence directory: %s\n' "$evidence"
