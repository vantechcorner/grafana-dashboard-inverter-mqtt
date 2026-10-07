#!/usr/bin/env bash
# One-shot migration on IRIV Pi Control after copying this repo to ~/solar_monitoring
# - Creates new buckets + tasks
# - Removes legacy mqtt_consumer data / solar_data bucket
# - Restarts stack
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

TOKEN="${INFLUX_TOKEN:?Set INFLUX_TOKEN in .env or environment}"
ORG="${INFLUX_ORG:-iriv_org}"

echo "==> Pull / recreate TIG containers (Mosquitto is external — leave it alone)"
docker compose up -d

echo "==> Wait for InfluxDB"
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:8086/health" >/dev/null; then
    break
  fi
  sleep 2
done

echo "==> Setup buckets + downsample tasks"
docker compose exec -T influxdb bash -lc '
  export INFLUX_HOST=http://127.0.0.1:8086
  export INFLUX_TOKEN="'"$TOKEN"'"
  export INFLUX_ORG="'"$ORG"'"
  bash /scripts/setup-buckets.sh
'

echo "==> Delete legacy measurement mqtt_consumer from any bucket (best-effort)"
docker compose exec -T influxdb bash -lc '
  influx delete --bucket solar_data \
    --start 1970-01-01T00:00:00Z --stop $(date -u +%Y-%m-%dT%H:%M:%SZ) \
    --predicate "_measurement=\"mqtt_consumer\"" \
    --org "'"$ORG"'" --token "'"$TOKEN"'" --host http://127.0.0.1:8086 2>/dev/null || true
'

echo "==> Drop legacy bucket solar_data if present"
docker compose exec -T influxdb bash -lc '
  influx bucket delete --name solar_data --org "'"$ORG"'" --token "'"$TOKEN"'" --host http://127.0.0.1:8086 --force 2>/dev/null || true
'

echo "==> Restart Telegraf + Grafana"
docker compose restart telegraf grafana

echo ""
echo "Migration complete."
echo "  Grafana:  http://192.168.3.249:3000"
echo "  Influx:   http://192.168.3.249:8086"
echo "  Buckets:  solar_raw (60d), solar_1h (~2y), solar_1d (forever)"
echo "  Device:   sg06  (dashboard title: Deye SG06)"
echo ""
echo "MQTT topics unchanged: iriv/ivt/#  (HA / ESP32 keep working)"
