#!/usr/bin/env bash
# Full portable backup of InfluxDB 2 (all buckets: solar_raw, solar_1h, solar_1d, …).
# Run on the Pi BEFORE any DeyeCloud import / destructive migrate.
#
# Usage:
#   ./scripts/backup-influxdb.sh
#   ./scripts/backup-influxdb.sh /path/to/ssd/influx-backup/pre-deyecloud-$(date +%F)
#
# Env: INFLUX_TOKEN (required). Optional: load from repo .env
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
CONTAINER="${INFLUX_CONTAINER:-influxdb}"
DEST="${1:-$ROOT/backups/influx-$(date +%Y%m%d-%H%M%S)}"
TMP_IN_CONTAINER="/tmp/influx-backup-$$"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "ERROR: container '$CONTAINER' is not running" >&2
  exit 1
fi

echo "==> Backup InfluxDB → $DEST"
mkdir -p "$DEST"

docker exec "$CONTAINER" rm -rf "$TMP_IN_CONTAINER"
docker exec "$CONTAINER" influx backup "$TMP_IN_CONTAINER" \
  --host http://127.0.0.1:8086 \
  --token "$TOKEN"

# Copy contents into DEST (portable backup tree)
docker cp "$CONTAINER:$TMP_IN_CONTAINER/." "$DEST/"
docker exec "$CONTAINER" rm -rf "$TMP_IN_CONTAINER"

if [[ -z "$(ls -A "$DEST" 2>/dev/null || true)" ]]; then
  echo "ERROR: backup directory is empty: $DEST" >&2
  exit 1
fi

echo "==> Sample files:"
find "$DEST" -type f | head -n 20
echo "==> Size:"
du -sh "$DEST"
echo "OK backup at $DEST"
echo ""
echo "Restore (only if import went badly — replaces DB):"
echo "  docker compose stop telegraf"
echo "  docker cp \"$DEST/.\" $CONTAINER:/tmp/influx-restore"
echo "  docker exec -it $CONTAINER influx restore /tmp/influx-restore \\"
echo "    --host http://127.0.0.1:8086 --token \"\$INFLUX_TOKEN\" --full"
echo "  docker compose start telegraf"
