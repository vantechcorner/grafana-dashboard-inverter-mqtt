#!/usr/bin/env bash
# Creates retention buckets + downsample tasks. Safe to re-run.
set -euo pipefail

HOST="${INFLUX_HOST:-http://127.0.0.1:8086}"
TOKEN="${INFLUX_TOKEN:?Set INFLUX_TOKEN in environment}"
ORG="${INFLUX_ORG:-iriv_org}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

influx_cmd() {
  influx "$@" --host "$HOST" --token "$TOKEN" --org "$ORG"
}

# task delete does not accept --org
delete_task() {
  influx task delete --id "$1" --host "$HOST" --token "$TOKEN"
}

ensure_bucket() {
  local name="$1"
  local retention="$2"
  if influx_cmd bucket list --name "$name" --hide-headers 2>/dev/null | grep -q .; then
    echo "bucket exists: $name (updating retention if supported)"
    influx_cmd bucket update --name "$name" --retention "$retention" 2>/dev/null || true
  else
    echo "creating bucket: $name (retention=$retention)"
    influx_cmd bucket create --name "$name" --retention "$retention"
  fi
}

echo "==> Ensuring buckets"
ensure_bucket "solar_raw" "1440h"   # 60 days
ensure_bucket "solar_1h"  "17520h"  # ~2 years
ensure_bucket "solar_1d"  "0"       # forever

echo "==> Replacing downsample tasks"
for name in downsample_solar_1h downsample_solar_1d; do
  while read -r tid _; do
    [[ -n "$tid" ]] || continue
    echo "deleting task $name id=$tid"
    delete_task "$tid" || true
  done < <(influx_cmd task list --hide-headers 2>/dev/null | awk -v n="$name" '$2==n {print $1}')
done

influx_cmd task create --file "$SCRIPT_DIR/task_downsample_1h.flux"
influx_cmd task create --file "$SCRIPT_DIR/task_downsample_1d.flux"

echo "==> Done"
influx_cmd bucket list
influx_cmd task list
