# Solar monitoring — MQTT → InfluxDB → Grafana

Hệ thống theo dõi năng lượng mặt trời: thu thập realtime qua MQTT, lưu time-series InfluxDB, hiển thị Grafana; có thể **bù lịch sử** từ báo cáo Excel đám mây (vd. DeyeCloud).

![Solar Overview dashboard](img/solar-overview-2026-10-09-08_35_33.png)

**Đã thử nghiệm với:** biến tần Deye **SUN-6K-SG06LP1** (hybrid, tag `sg06` / nhãn “Deye SG06” trên dashboard) trên **IRIV Pi Control** (`192.168.3.249`).

![Deye SUN-6K-SG06LP1 với IRIV Pi Control](img/deye-sun-6k-sg06lp1-setup-4-iriv.jpg)

**Có thể dùng với** các biến tần RS485 khác (mọi thương hiệu) **miễn là dữ liệu đã được chuyển sang MQTT** theo contract topic/payload bên dưới — repo này **không** nói chuyện RS485 trực tiếp, chỉ consume MQTT.

> English overview: [README.md](README.md)

---

## Yêu cầu

- Đã cài **Docker** và **Docker Compose** trên máy host (vd. Pi) trước khi chạy stack
- Đã có MQTT broker đang publish metric biến tần (Mosquitto trên LAN, hoặc bỏ comment service tùy chọn trong `docker-compose.yml`)
- Tùy chọn: Python 3 + `openpyxl` (hoặc Docker Python) để import Excel DeyeCloud

---

## Mục tiêu

| Mục tiêu | Cách đạt |
|----------|----------|
| Live monitoring | Công suất PV / pin / lưới / nhà, SOC, V/A/Hz |
| Báo cáo năng lượng | kWh ngày / tuần / tháng / năm, chi phí & tiết kiệm ước tính |
| Tương thích HA / ESP32 | **Không đổi** MQTT topic `iriv/ivt/#` |
| Lịch sử dài hạn | Bucket `solar_1d` (forever) + import Excel DeyeCloud |
| An toàn vận hành | Backup Influx trước khi migrate / import |

---

## Kiến trúc

```
Deye SG06 (RS485)
    └─► IRIV IOC MQTT Gateway (vd. 192.168.3.178)
            └─► Mosquitto @ 192.168.3.249:1883
                    topics: iriv/ivt/#     (inverter 1)
                            iriv/ivt-2/#   (inverter 2, tùy chọn)
                    │
                    ▼
              Telegraf  →  InfluxDB 2.7  →  Grafana :3000
                 │              │
                 │              ├─ solar_raw  (60 ngày, độ phân giải cao)
                 │              ├─ solar_1h   (~2 năm)
                 │              └─ solar_1d   (vĩnh viễn — energy daily)
                 └─ Starlark: house/power + house/energy_today (derived)
```

- **Mosquitto** có trong [`docker-compose.yml`](docker-compose.yml) nhưng **đang comment** (mặc định tắt) để tránh xung đột cổng 1883 với broker sẵn có (HA / ESP32). Bỏ comment chỉ khi bạn muốn Compose tự chạy broker.
- Hướng dẫn RS485 → IRIV IOC MQTT Gateway → Mosquitto: [`iriv-ioc-mqtt-gateway`](https://github.com/vantechcorner/deye-sg06-inverter-rs485-monitor/tree/main/iriv-ioc-mqtt-gateway).

---

## Luồng dữ liệu

1. Gateway poll inverter → publish JSON `{"value": …}` lên MQTT.
2. Telegraf `mqtt_consumer` tách topic thành tags `device` / `component` / `metric`, ghi field `value` vào measurement `solar`.
3. Starlark tính **House load (W)** và **House energy today (kWh)** (cùng công thức dashboard ESP32).
4. Task Influx downsample: raw → `solar_1h` / `solar_1d`.
5. Grafana đọc Flux: realtime từ `solar_raw`; energy ngày đã qua ưu tiên `solar_1d`.

### Schema Influx

| | |
|--|--|
| Measurement | `solar` |
| Tags | `device`, `component`, `metric` |
| Field | `value` |
| Device chính | `sg06` (nhãn Grafana: **Deye SG06**) |

Ví dụ: `iriv/ivt/battery/power` → `device=sg06, component=battery, metric=power`.

---

## Dashboards Grafana (folder Solar)

| Dashboard | Nội dung |
|-----------|----------|
| **Solar Overview** | Gauge / power / energy hôm nay |
| **Solar Electrical** | V, A, Hz, nhiệt độ |
| **Solar Live V/A/P** | Chuỗi live chi tiết |
| **Solar Energy** | Tổng kWh theo time picker + PV day/week/month/year; cost / saving |
| **Solar DeyeCloud** | Layout kiểu DeyeCloud (consumption / production / history) |

Energy lịch sử: **`solar_1d` + `solar_raw` (hôm nay)**. Không `sum()` thô các mẫu `*_today` trong cả tháng (sẽ nhân đôi).

![Solar Energy — production vs consumption dài hạn](img/energy-overview-long-term-2026-10-09-08_37_22.png)

![Solar Live V/A/P](img/live-vap-2026-10-09-08_37_22.png)

---

## Backfill DeyeCloud

Nếu bạn setup Grafana + InfluxDB **sau** khi biến tần đã chạy (vài tuần / vài tháng), MQTT chỉ có dữ liệu từ lúc bật Telegraf. Lịch sử kWh ngày trước đó vẫn nằm trên cloud nhà sản xuất (vd. **DeyeCloud**). Export báo cáo đó rồi import vào đây để dashboard Solar Energy / DeyeCloud có timeline liền mạch.

**Cách làm**

1. Trên DeyeCloud (hoặc tương đương), export báo cáo năng lượng **theo ngày** dạng Excel cho các tháng cần bù (thường ghi là export tháng — mỗi dòng vẫn là **1 ngày**: Production, Buy/Sell, Charge/Discharge, Consumption).
2. Copy các file `.xlsx` vào `deyecloud-data/` trên máy host (thư mục này đã gitignore).
3. Trên Pi (hoặc host chạy stack), **backup Influx trước**, chạy dry-run, rồi import thật:

![Solar DeyeCloud dashboard](img/deyecloud-2026-10-09-08_37_22.png)

```bash
./scripts/backup-influxdb.sh ./backups/pre-import-$(date +%F)
python3 scripts/import-deyecloud-energy.py --dry-run          # hoặc qua docker python + openpyxl
python3 scripts/import-deyecloud-energy.py --i-have-backup
```

Chỉ ghi **kWh daily** vào `solar_1d` (lấp ngày thiếu; ngày đã có từ MQTT giữ nguyên). Không khôi phục được đường cong power/SOC từ Excel.

Chi tiết mapping & verify: [`note.md`](note.md) §11.

---

## Secrets / credentials

**Không commit mật khẩu hay token.** Copy mẫu rồi điền giá trị thật:

```bash
cp .env.example .env
# sửa .env — file này đã có trong .gitignore
docker compose up -d
```

Biến chính: `INFLUX_USERNAME`, `INFLUX_PASSWORD`, `INFLUX_TOKEN`, `GRAFANA_ADMIN_USER`, `GRAFANA_ADMIN_PASSWORD`.

Giữ file `.env` cạnh `docker-compose.yml` trong thư mục đã clone.

---

## Deploy nhanh (Pi)

Điều kiện trên Pi: đã cài **Git**, **Docker Engine**, và plugin **Compose**.

Dùng thư mục clone `grafana-dashboard-inverter-mqtt` (trùng tên repo GitHub). Không dùng đường dẫn cá nhân kiểu `~/solar_monitoring` — tên đó chỉ dành cho máy Pi của tác giả.

```bash
# 1) Clone repo
cd ~
git clone https://github.com/vantechcorner/grafana-dashboard-inverter-mqtt.git
cd ~/grafana-dashboard-inverter-mqtt

# 2) Tạo secrets (không commit .env)
cp .env.example .env
nano .env   # điền INFLUX_* và GRAFANA_ADMIN_*

# 3) Chạy stack
chmod +x scripts/*.sh influxdb/scripts/setup-buckets.sh
docker compose up -d

# 4) Tạo bucket + task downsample (lần đầu / sau khi pull cập nhật)
./influxdb/scripts/setup-buckets.sh
# Migrate từ layout cũ: ./scripts/migrate-cleanup.sh
```

Sau đó mở:

- Grafana: `http://<ip-pi>:3000` (user/password admin lấy từ `.env`)
- InfluxDB: `http://<ip-pi>:8086`

Trỏ Telegraf tới MQTT broker của bạn (`telegraf/telegraf.conf`). Service Mosquitto trong `docker-compose.yml` đang comment — chỉ bật nếu chưa có broker nào chiếm cổng `1883`.

Hướng dẫn RS485 → IRIV IOC MQTT Gateway: [iriv-ioc-mqtt-gateway](https://github.com/vantechcorner/deye-sg06-inverter-rs485-monitor/tree/main/iriv-ioc-mqtt-gateway).

Backup / restore / Flux cheat sheet / inverter 2: xem [note.md](note.md).

---

## Cấu trúc repo

```
docker-compose.yml          # Influx + Telegraf + Grafana (+ Mosquitto đang comment)
.env.example                # mẫu secrets
telegraf/                   # MQTT → Influx + house_derived.star
influxdb/scripts/           # buckets + downsample tasks
grafana/dashboards/         # JSON provisioned
grafana/provisioning/
scripts/                    # backup, import DeyeCloud, migrate, repair solar_1d
deyecloud-data/             # Excel local (gitignored)
note.md                     # vận hành chi tiết
README-vn.md                # tổng quan (tiếng Việt)
README.md                   # overview (English)
```