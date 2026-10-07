#!/usr/bin/env python3
"""
Import daily energy kWh from deyecloud-data/*.xlsx into InfluxDB bucket solar_1d.

Mapping (DeyeCloud column → tags):
  Production              → pv / energy_today
  Grid Feed-in            → grid / sell_today
  Electricity Purchasing  → grid / buy_today
  Charging Capacity       → battery / charge_today
  Discharging Capacity    → battery / discharge_today
  Consumption             → house / energy_today

Timestamp: end of Vietnam day (ICT) = YYYY-MM-DDT16:59:59Z

Usage (on Pi, after backup):
  python3 scripts/import-deyecloud-energy.py --dry-run
  python3 scripts/import-deyecloud-energy.py --i-have-backup
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv(Path(__file__).resolve().parents[1] / ".env")

try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl required — pip install openpyxl", file=sys.stderr)
    sys.exit(1)

import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = ROOT / "deyecloud-data"

# ICT = UTC+7; end of local day in UTC
ICT = timezone(timedelta(hours=7))

COLUMN_MAP = {
    "Production(kWh)": ("pv", "energy_today"),
    "Grid Feed-in(kWh)": ("grid", "sell_today"),
    "Electricity Purchasing(kWh)": ("grid", "buy_today"),
    "Charging Capacity(kWh)": ("battery", "charge_today"),
    "Discharging Capacity(kWh)": ("battery", "discharge_today"),
    "Consumption(kWh)": ("house", "energy_today"),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    p.add_argument("--host", default=os.environ.get("INFLUX_HOST", "http://127.0.0.1:8086"))
    p.add_argument("--token", default=os.environ.get("INFLUX_TOKEN", ""))
    p.add_argument("--org", default=os.environ.get("INFLUX_ORG", "iriv_org"))
    p.add_argument("--bucket", default="solar_1d", help="Target bucket (forever daily)")
    p.add_argument("--device", default="sg06")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--i-have-backup",
        action="store_true",
        help="Required for real writes — confirm ./scripts/backup-influxdb.sh was run",
    )
    p.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip days that already have pv/energy_today in the target bucket (default: true)",
    )
    p.add_argument(
        "--until",
        default="yesterday",
        help="Last day to import: 'yesterday' (default) or YYYY-MM-DD",
    )
    p.add_argument(
        "--also-raw",
        action="store_true",
        help="Also write the same points into solar_raw (optional; expires in 60d)",
    )
    return p.parse_args()


def end_of_ict_day_utc(d: date) -> datetime:
    """Point at 23:59:59 ICT → stored as UTC."""
    local = datetime.combine(d, time(23, 59, 59), tzinfo=ICT)
    return local.astimezone(timezone.utc)


def parse_until(spec: str) -> date:
    if spec == "yesterday":
        # "today" in ICT
        today_ict = datetime.now(ICT).date()
        return today_ict - timedelta(days=1)
    return date.fromisoformat(spec)


def load_xlsx_rows(data_dir: Path) -> list[dict]:
    files = sorted(data_dir.glob("*.xlsx"))
    if not files:
        raise FileNotFoundError(f"No .xlsx in {data_dir}")

    by_day: dict[date, dict] = {}
    for path in files:
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        header = [str(c).strip() if c is not None else "" for c in rows[0]]
        col_idx = {name: header.index(name) for name in COLUMN_MAP if name in header}
        missing = set(COLUMN_MAP) - set(col_idx)
        if missing:
            raise ValueError(f"{path.name}: missing columns {missing}")

        time_i = header.index("Time")
        for row in rows[1:]:
            if not row or row[time_i] is None:
                continue
            raw_t = row[time_i]
            if isinstance(raw_t, datetime):
                d = raw_t.date()
            elif isinstance(raw_t, date):
                d = raw_t
            else:
                d = datetime.strptime(str(raw_t).strip().replace("-", "/"), "%Y/%m/%d").date()

            metrics = {}
            for col, (comp, metric) in COLUMN_MAP.items():
                val = float(row[col_idx[col]])
                metrics[(comp, metric)] = val
            by_day[d] = {"date": d, "metrics": metrics, "file": path.name}

    return [by_day[d] for d in sorted(by_day)]


def line_protocol(device: str, component: str, metric: str, value: float, ts: datetime) -> str:
    # nanosecond timestamp
    ns = int(ts.timestamp() * 1_000_000_000)
    return f"solar,device={device},component={component},metric={metric} value={value} {ns}"


def influx_query(host: str, token: str, org: str, flux: str) -> list[dict]:
    url = f"{host.rstrip('/')}/api/v2/query?org={urllib.parse.quote(org)}"
    req = urllib.request.Request(
        url,
        data=flux.encode("utf-8"),
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Flux query failed HTTP {e.code}: {body}") from e

    # Parse annotated CSV lightly: collect _time values
    times: list[datetime] = []
    for line in text.splitlines():
        if not line or line.startswith("#") or line.startswith(",result"):
            continue
        # skip header row
        if "_time" in line and "_value" in line:
            continue
        parts = line.split(",")
        # annotated CSV: empty first col, then result, table, _start, _stop, _time, ...
        # Find ISO time field
        for part in parts:
            part = part.strip()
            if len(part) >= 20 and part[4] == "-" and "T" in part:
                try:
                    times.append(datetime.fromisoformat(part.replace("Z", "+00:00")))
                    break
                except ValueError:
                    continue
    return [{"_time": t} for t in times]


def existing_days(host: str, token: str, org: str, bucket: str, device: str, start: date, stop: date) -> set[date]:
    """Days that already have pv/energy_today in bucket (ICT calendar day)."""
    start_dt = datetime.combine(start, time(0, 0), tzinfo=ICT).astimezone(timezone.utc)
    # exclusive stop = day after last at 00:00 ICT
    stop_dt = datetime.combine(stop + timedelta(days=1), time(0, 0), tzinfo=ICT).astimezone(timezone.utc)
    flux = f"""
import "timezone"
option location = timezone.fixed(offset: 7h)

from(bucket: "{bucket}")
  |> range(start: {start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")}, stop: {stop_dt.strftime("%Y-%m-%dT%H:%M:%SZ")})
  |> filter(fn: (r) => r._measurement == "solar" and r._field == "value")
  |> filter(fn: (r) => r.device == "{device}")
  |> filter(fn: (r) => r.component == "pv" and r.metric == "energy_today")
  |> aggregateWindow(every: 1d, fn: last, createEmpty: false, timeSrc: "_start")
"""
    rows = influx_query(host, token, org, flux)
    days: set[date] = set()
    for r in rows:
        # Convert UTC instant to ICT date via window start semantics
        local = r["_time"].astimezone(ICT)
        days.add(local.date())
    return days


def write_line_protocol(host: str, token: str, org: str, bucket: str, lines: list[str]) -> None:
    if not lines:
        return
    url = f"{host.rstrip('/')}/api/v2/write?org={urllib.parse.quote(org)}&bucket={urllib.parse.quote(bucket)}&precision=ns"
    body = ("\n".join(lines) + "\n").encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            if resp.status not in (204, 200):
                raise RuntimeError(f"Write unexpected status {resp.status}")
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Write failed HTTP {e.code}: {err}") from e


def main() -> int:
    args = parse_args()
    until = parse_until(args.until)

    if not args.token:
        print(
            "ERROR: INFLUX_TOKEN missing — set it in .env or pass --token",
            file=sys.stderr,
        )
        return 2

    if not args.dry_run and not args.i_have_backup:
        print(
            "ERROR: refusing to write without --i-have-backup\n"
            "  1) ./scripts/backup-influxdb.sh\n"
            "  2) python3 scripts/import-deyecloud-energy.py --dry-run\n"
            "  3) python3 scripts/import-deyecloud-energy.py --i-have-backup",
            file=sys.stderr,
        )
        return 2

    rows = load_xlsx_rows(args.data_dir)
    rows = [r for r in rows if r["date"] <= until]
    if not rows:
        print("No rows to import (after --until filter).")
        return 0

    start_d, stop_d = rows[0]["date"], rows[-1]["date"]
    print(f"Excel days in range: {start_d} .. {stop_d} ({len(rows)} days), until={until}")

    skip: set[date] = set()
    if args.skip_existing and not args.dry_run:
        try:
            skip = existing_days(args.host, args.token, args.org, args.bucket, args.device, start_d, stop_d)
            print(f"Existing days in {args.bucket}: {len(skip)}")
        except Exception as e:
            print(f"WARNING: could not query existing days ({e}); continuing without skip", file=sys.stderr)
    elif args.skip_existing and args.dry_run:
        try:
            skip = existing_days(args.host, args.token, args.org, args.bucket, args.device, start_d, stop_d)
            print(f"Existing days in {args.bucket}: {len(skip)}")
        except Exception as e:
            print(f"NOTE: dry-run without Influx skip list ({e})")

    to_write: list[dict] = []
    skipped = 0
    for r in rows:
        if r["date"] in skip:
            skipped += 1
            continue
        to_write.append(r)

    # Sanity: Aug PV total for reporting
    aug = [r for r in rows if r["date"].month == 8 and r["date"].year == 2026]
    aug_pv = sum(r["metrics"][("pv", "energy_today")] for r in aug)
    print(f"Sanity Aug 2026 PV sum (Excel, ≤ until): {aug_pv:.2f} kWh")

    lines: list[str] = []
    for r in to_write:
        ts = end_of_ict_day_utc(r["date"])
        for (comp, metric), val in r["metrics"].items():
            lines.append(line_protocol(args.device, comp, metric, val, ts))

    print(f"Days to import: {len(to_write)}  skipped(existing): {skipped}  points: {len(lines)}")
    if to_write:
        print(f"  first: {to_write[0]['date']}  last: {to_write[-1]['date']}")

    if args.dry_run:
        print("DRY-RUN — no writes.")
        # Preview a few lines
        for lp in lines[:6]:
            print("  ", lp)
        if len(lines) > 6:
            print(f"  ... ({len(lines) - 6} more)")
        return 0

    write_line_protocol(args.host, args.token, args.org, args.bucket, lines)
    print(f"Wrote {len(lines)} points → {args.bucket}")
    if args.also_raw:
        write_line_protocol(args.host, args.token, args.org, "solar_raw", lines)
        print(f"Wrote {len(lines)} points → solar_raw")

    print("Done. Verify in Grafana Solar Energy (past days) or Flux sum Aug PV ≈ 84.9 kWh.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
