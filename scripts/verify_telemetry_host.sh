#!/bin/sh
# Host-only bounded lifecycle operations. Never mount Docker's socket in a runner.
set -eu
cd "$(dirname "$0")/.."
project=recruitmatch-cp5-sep14
compose() {
  docker compose --env-file .env.example -p "$project" -f docker-compose.yml -f tests/observability/compose.telemetry.yml "$@"
}
owned() {
  target="$project-$1-1"
  docker inspect "$target" | jq -e --arg p "$project" --arg s "$1" '
    length == 1 and .[0].Config.Labels["com.docker.compose.project"] == $p
    and .[0].Config.Labels["com.docker.compose.service"] == $s' >/dev/null
}
owned otel-collector
owned api
owned worker
owned beat
image=$(docker image inspect "$project-app-test" -f '{{.Id}}')
for service in api worker beat; do
  test "$(docker inspect -f '{{.Image}}' "$project-$service-1")" = "$image"
done
evidence=$(mktemp -d /tmp/recruitmatch-cp5-telemetry.XXXXXX)
chmod 777 "$evidence"
run() {
  compose run --rm --no-deps -v "$evidence:/evidence" test-telemetry python -m scripts.verify_telemetry "$@"
}
restoration_needed=false
restore() {
  if [ "$restoration_needed" = true ]; then
    owned otel-collector
    docker start "$project-otel-collector-1" >/dev/null
  fi
}
trap restore EXIT
trap 'exit 130' INT TERM
sh scripts/verify_telemetry_alerts.sh "$evidence"
run business --output before
owned worker
docker restart --timeout 15 "$project-worker-1" >/dev/null
run business --output after
run outage-prepare
restoration_needed=true
owned otel-collector
docker stop --timeout 10 "$project-otel-collector-1" >/dev/null
test "$(docker inspect -f '{{.State.Running}}' "$project-otel-collector-1")" = false
run outage-check
restore
compose run --rm --no-deps test-telemetry python -c 'from scripts.verify_telemetry import eventually; import httpx; eventually(lambda: httpx.get("http://otel-collector:13133/", timeout=3).status_code == 200, label="collector_restoration")'
restoration_needed=false
for service in api worker beat; do
  owned "$service"
  docker logs "$project-$service-1" >"$evidence/$service.log" 2>&1
done
run logs
run finalize
compose run --rm --no-deps -v "$evidence:/evidence:ro" test-telemetry pytest tests/observability -q
printf 'Synthetic evidence directory: %s\n' "$evidence"
