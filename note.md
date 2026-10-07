# Solar monitoring — IRIV Pi Control (InfluxDB + Telegraf + Grafana)

Operational notes for the solar MQTT → Influx → Grafana stack on
`iriv-pi-control` (`192.168.3.249`).
**Tested with** Deye **SUN-6K-SG06LP1**; other RS485 inverters work once they
publish the MQTT contract below.
Requires **Docker + Compose** on the host.

Project overview: `[README.md](README.md)` (English) · `[README-vn.md](README-vn.md)` (tiếng Việt).

MQTT topic contract is **unchanged** so Home Assistant and ESP32 keep working.
Secrets: use `.env` (never commit) — see `.env.example`.
`deyecloud-data/` is gitignored (keep Excel exports local only).

---

## 1. Architecture

```
Deye SG06
  └─ RS485 ─► IRIV IOC MQTT Gateway (e.g. 192.168.3.178)
                 └─ publish ► Mosquitto (docker) @ 192.168.3.249:1883
                                 topics: iriv/ivt/#   (inverter 1)
                                          iriv/ivt-2/# (inverter 2, later)
                      │
                      ▼
                 Telegraf  →  InfluxDB 2.7  →  Grafana :3000
```

**Mosquitto:** optional service in `docker-compose.yml` — **commented out by
default**. Uncomment only if nothing else already binds `:1883`. Telegraf
subscribes via `tcp://192.168.3.249:1883`.

RS485 → IRIV IOC MQTT Gateway → Mosquitto setup:
https://github.com/vantechcorner/deye-sg06-rs485-monitor/tree/main/iriv


| Piece       | Role                                                         |
| ----------- | ------------------------------------------------------------ |
| MQTT broker | Mosquitto (external or optional Compose service) — HA/ESP32/Telegraf |
| Telegraf    | Subscribe MQTT → tags `device`/`component`/`metric` → Influx |
| InfluxDB    | Time series + downsample tasks                               |
| Grafana     | Flux dashboards (Overview + Electrical)                      |

---

## 2. MQTT contract (do not rename for HA / ESP32)

**Inverter 1 (Deye SG06):** `iriv/ivt/{component}/{metric}`  
**Inverter 2 (future):** `iriv/ivt-2/{component}/{metric}`

Payload: `{"name":"...","value":52.81,"unit":"V","status":"success"}`  
Telegraf keeps only `value`.

Examples:


| Topic                      | device tag | component | metric       |
| -------------------------- | ---------- | --------- | ------------ |
| `iriv/ivt/battery/power`   | `sg06`     | battery   | power        |
| `iriv/ivt/grid/power_ct`   | `sg06`     | grid      | power_ct     |
| `iriv/ivt/pv/energy_today` | `sg06`     | pv        | energy_today |
| `iriv/ivt/status`          | `sg06`     | system    | status       |
| `iriv/ivt-2/...`           | `ivt-2`    | …         | …            |


Grafana variable shows **Deye SG06** for tag `sg06`.

---

## 3. InfluxDB schema


| Item               | Value                                                          |
| ------------------ | -------------------------------------------------------------- |
| `_measurement`     | `solar`                                                        |
| Tags               | `device`, `component`, `metric`                                |
| Field              | `value`                                                        |
| Bucket `solar_raw` | retention **60 days** (high-res)                               |
| Bucket `solar_1h`  | ~**2 years** (hourly mean / last)                              |
| Bucket `solar_1d`  | **forever** (daily last of `*_today`; also DeyeCloud backfill) |


Energy dashboards use `**solar_1d` for completed days** and `**solar_raw` for today**.

**Downsample note:** `downsample_solar_1d` runs at **~00:10 ICT** (`every: 1d, offset: 17h10m`)
and writes the previous ICT day’s EOD `*_today` via `last()`. Do **not** aggregate
`*_today` on UTC day boundaries — that captures post-midnight reset (~0) and corrupts
history. Repair a day from raw: `python3 scripts/repair-solar-1d-day.py --day YYYY-MM-DD`.

### Components

`battery | pv1 | pv2 | pv | grid | inverter | load | house | system`

### Derived metrics (Telegraf Starlark — same as ESP32 P4-7)

**House load (W)** — `component=house, metric=power`:

```
essential   = max(load/power, 0)
grid_import = max(grid/power_ct, 0)
charge      = max(-battery/power, 0)
pv          = max(pv1/power,0) + max(pv2/power,0)

if (charge - pv) >= 40 W:   # charging from grid
    grid_for_house = 0
else:
    grid_for_house = grid_import

house_w = essential + grid_for_house
```

Do **not** use `inverter/power` as house load.  
`load/power` = LOAD / backup port only.

**House energy today (kWh)** — `component=house, metric=energy_today`:

```
PV + buy + discharge − sell − charge
```

---

## 4. Deploy / migrate on the Pi

Copy this repo over `~/solar_monitoring` (or rsync), then:

```bash
cd ~/solar_monitoring
sudo chown -R 472:472 ./grafana/data   # if Grafana was restart-looping
chmod +x scripts/migrate-cleanup.sh influxdb/scripts/setup-buckets.sh
./scripts/migrate-cleanup.sh
```

What the script does:

1. `docker compose up -d`
2. Creates `solar_raw` / `solar_1h` / `solar_1d` + downsample tasks
3. Deletes legacy `mqtt_consumer` data and drops old bucket `solar_data`
4. Restarts Telegraf + Grafana

**Fresh install note:** `DOCKER_INFLUXDB_INIT_`* only runs on an empty `./influxdb/data`.  
Existing installs rely on `setup-buckets.sh`.

Open:

- Grafana: [http://192.168.3.249:3000](http://192.168.3.249:3000) (user/password from `.env` → `GRAFANA_ADMIN_*`)
- Dashboards folder **Solar**: Overview, Electrical, Energy, DeyeCloud, Live V/A/P

---

## 5. Enable inverter 2 later

1. Point the second gateway `baseTopic` to `iriv/ivt-2` (suffixes unchanged).
2. Uncomment the second `[[inputs.mqtt_consumer]]` block in `telegraf/telegraf.conf`.
3. `docker compose restart telegraf`
4. Select **ivt-2** in the dashboard Inverter variable (rename label in JSON if you want).

---

## 6. Flux query cheat sheet

Power series (short legend names):

```flux
from(bucket: "solar_raw")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "solar" and r._field == "value")
  |> filter(fn: (r) => r.device == "sg06")
  |> filter(fn: (r) => r.metric == "power" or r.metric == "power_ct")
  |> map(fn: (r) => ({ r with _field: r.component }))
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)
```

Daily energy bars (correct “month” = sum of these):

```flux
from(bucket: "solar_raw")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "solar" and r._field == "value")
  |> filter(fn: (r) => r.device == "sg06")
  |> filter(fn: (r) => r.metric =~ /today$/)
  |> aggregateWindow(every: 1d, fn: last, createEmpty: false)
```

Never `sum()` raw samples of `*_today` over a month — that double-counts counters.

---

## 7. Backup (128 GB SSD)

### Before any data import / destructive change

```bash
chmod +x scripts/backup-influxdb.sh
./scripts/backup-influxdb.sh /path/to/ssd/influx-backup/pre-deyecloud-$(date +%F)
# prints OK backup at … and size — keep that path
```

### Suggested daily cron on the Pi

```bash
./scripts/backup-influxdb.sh /path/to/ssd/influx-backup/$(date +%F)
# keep last 7 days
```

Also keep this git repo (compose + dashboards + telegraf) as config backup.

### Restore (only if import went badly — replaces DB)

```bash
docker compose stop telegraf
docker cp /path/to/backup/. influxdb:/tmp/influx-restore
docker exec -it influxdb influx restore /tmp/influx-restore \
  --host http://127.0.0.1:8086 \
  --token "$INFLUX_TOKEN" \
  --full
docker compose start telegraf
```

---

## 8. Repo layout

```
README.md                 # project overview (English)
README-vn.md              # tổng quan dự án (tiếng Việt)
docker-compose.yml        # đọc secrets từ .env
.env.example              # mẫu — copy thành .env (không commit .env)
telegraf/
influxdb/scripts/
scripts/                  # backup, import DeyeCloud, migrate, repair solar_1d
deyecloud-data/           # gitignored — local Excel only
grafana/
note.md                   # vận hành chi tiết (file này)
```

---

## 9. Credentials

All passwords/tokens live in `**.env**` (gitignored). Start from `.env.example`:

```bash
cp .env.example .env
# edit INFLUX_* and GRAFANA_ADMIN_*
```

Compose, Telegraf, Grafana datasource provisioning, and helper scripts all read
`INFLUX_TOKEN` / related vars from the environment. Rotate when exposing beyond LAN.

---

## 10. Solar Energy dashboard (HA-style)

Dashboard **Solar Energy** — **only the Grafana time picker** (no Period / bar-size variable).


| Time picker             | Bar resolution (auto)                  |
| ----------------------- | -------------------------------------- |
| ≤ ~2 days (e.g. Today)  | hourly kWh (`difference` of `*_today`) |
| ≤ ~3 months             | daily kWh (`last` of `*_today`)        |
| Longer (e.g. This year) | ~monthly (`sum` of daily)              |


Also: Estimate Cost = Import × `Price ₫/kWh`; Estimate Saving = (PV + Discharge − Charge) × price.

Completed days read from bucket `**solar_1d**` (forever); **today** still from `**solar_raw`**.

---

## 11. DeyeCloud historical energy import

Monthly Excel exports under `deyecloud-data/` already contain **one row per day**.
Import fills gaps into `solar_1d` (does not overwrite days that already have MQTT data).


| DeyeCloud column       | Influx tags                   |
| ---------------------- | ----------------------------- |
| Production             | `pv` / `energy_today`         |
| Grid Feed-in           | `grid` / `sell_today`         |
| Electricity Purchasing | `grid` / `buy_today`          |
| Charging Capacity      | `battery` / `charge_today`    |
| Discharging Capacity   | `battery` / `discharge_today` |
| Consumption            | `house` / `energy_today`      |


On the Pi:

```bash
# 0) backup first
./scripts/backup-influxdb.sh /path/to/ssd/influx-backup/pre-deyecloud-$(date +%F)

# 1) dry-run (needs openpyxl: pip3 install openpyxl)
python3 scripts/import-deyecloud-energy.py --dry-run
# expect Aug 2026 PV sum ≈ 84.90 kWh

# 2) import
python3 scripts/import-deyecloud-energy.py --i-have-backup
```

Flags: `--skip-existing` (default), `--until yesterday` (default), `--also-raw` (optional).
Power / SOC / voltage **cannot** be reconstructed from these reports — only daily kWh.