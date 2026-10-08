#!/usr/bin/env bash
# Recreate downsample_solar_1d + repair missing ICT day(s). Run on the Pi.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

TOKEN="${INFLUX_TOKEN:?Set INFLUX_TOKEN}"
ORG="${INFLUX_ORG:-iriv_org}"
HOST="${INFLUX_HOST:-http://127.0.0.1:8086}"

# Prefer host influx CLI; fallback docker exec.
# Note: some subcommands (task delete) reject --org — pass only when needed.
influx_auth=(--host "$HOST" --token "$TOKEN")
influx_cmd() {
  if command -v influx >/dev/null 2>&1; then
    influx "$@" "${influx_auth[@]}"
  else
    docker exec -i influxdb influx "$@" --host http://127.0.0.1:8086 --token "$TOKEN"
  fi
}
influx_org() {
  influx_cmd "$@" --org "$ORG"
}

echo "==> Install updated task_downsample_1d.flux"
mkdir -p "$ROOT/influxdb/scripts"
# file may already be copied via scp
sed -i 's/\r$//' "$ROOT/influxdb/scripts/task_downsample_1d.flux" "$ROOT/scripts/repair-solar-1d-day.py" 2>/dev/null || true

echo "==> Delete existing downsample_solar_1d tasks"
while read -r tid rest; do
  [[ -n "${tid:-}" ]] || continue
  echo "deleting $tid"
  influx_cmd task delete --id "$tid" || true
done < <(influx_org task list --hide-headers 2>/dev/null | awk '$2=="downsample_solar_1d" {print $1}')

echo "==> Create downsample_solar_1d (cron 10 17 * * * UTC = 00:10 ICT)"
if command -v influx >/dev/null 2>&1; then
  influx_org task create --file "$ROOT/influxdb/scripts/task_downsample_1d.flux"
else
  docker cp "$ROOT/influxdb/scripts/task_downsample_1d.flux" influxdb:/tmp/task_downsample_1d.flux
  docker exec influxdb influx task create --host http://127.0.0.1:8086 --token "$TOKEN" --org "$ORG" \
    --file /tmp/task_downsample_1d.flux
fi

echo "==> Repair ICT days that are missing from solar_1d (Oct 7)"
export INFLUX_HOST="$HOST" INFLUX_TOKEN="$TOKEN" INFLUX_ORG="$ORG"
# Use container python if host has no deps
if command -v python3 >/dev/null 2>&1; then
  python3 "$ROOT/scripts/repair-solar-1d-day.py" --day 2026-10-07 --host "$HOST" --token "$TOKEN" --org "$ORG"
else
  docker run --rm --network container:influxdb \
    -v "$ROOT:/data" -w /data \
    -e INFLUX_TOKEN="$TOKEN" -e INFLUX_ORG="$ORG" \
    python:3.12-slim \
    python scripts/repair-solar-1d-day.py --day 2026-10-07 --host http://127.0.0.1:8086
fi

echo "==> Verify solar_1d Oct 6–7"
docker exec -i influxdb influx query --host http://127.0.0.1:8086 --token "$TOKEN" --org "$ORG" <<'FLUX'
import "timezone"
option location = timezone.fixed(offset: 7h)
from(bucket: "solar_1d")
  |> range(start: 2026-10-05T17:00:00Z, stop: 2026-10-07T17:00:00Z)
  |> filter(fn: (r) => r._measurement == "solar" and r._field == "value")
  |> filter(fn: (r) => r.device == "sg06")
  |> filter(fn: (r) =>
      (r.component == "pv" and r.metric == "energy_today") or
      (r.component == "house" and r.metric == "energy_today")
  )
  |> keep(columns: ["_time", "_value", "component", "metric"])
FLUX

echo "==> Task list"
influx_org task list
echo "Done."
