#!/usr/bin/env python3
"""Repair solar_1d for a given ICT day from solar_raw EOD (last of *_today)."""
from __future__ import annotations

import argparse
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")
ICT = timezone(timedelta(hours=7))


def ict_day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time(0, 0), tzinfo=ICT).astimezone(timezone.utc)
    stop = start + timedelta(days=1)
    return start, stop


def end_of_ict_day_utc(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=ICT).astimezone(timezone.utc)


def flux_query(host: str, token: str, org: str, flux: str) -> str:
    url = f"{host.rstrip('/')}/api/v2/query?org={urllib.parse.quote(org)}"
    req = urllib.request.Request(
        url,
        data=flux.encode(),
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def parse_last_values(csv_text: str) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    header = None
    for line in csv_text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if header is None:
            header = parts
            continue
        if "_value" in line and "component" in line and parts[0] == "":
            # might be header row of annotated CSV
            if any(p == "_value" for p in parts):
                header = parts
                continue
        try:
            # annotated CSV: ,result,table,_start,_stop,_time,_value,...component,metric
            idx_val = header.index("_value") if header and "_value" in header else None
            idx_comp = header.index("component") if header and "component" in header else None
            idx_met = header.index("metric") if header and "metric" in header else None
            if idx_val is None:
                continue
            # fallback positional search
            comp = parts[idx_comp] if idx_comp is not None else None
            met = parts[idx_met] if idx_met is not None else None
            val = float(parts[idx_val])
            if comp and met:
                out[(comp, met)] = val
        except (ValueError, IndexError):
            continue
    # Robust fallback: scan rows for known metrics
    if not out:
        for line in csv_text.splitlines():
            if "energy_today" not in line and "buy_today" not in line and "sell_today" not in line and "charge_today" not in line and "discharge_today" not in line:
                continue
            parts = [p.strip() for p in line.split(",")]
            nums = []
            for p in parts:
                try:
                    nums.append(float(p))
                except ValueError:
                    pass
            if not nums:
                continue
            val = nums[-1]
            if "pv" in parts and "energy_today" in parts:
                out[("pv", "energy_today")] = val
            elif "house" in parts and "energy_today" in parts:
                out[("house", "energy_today")] = val
            elif "load" in parts and "energy_today" in parts:
                out[("load", "energy_today")] = val
            elif "grid" in parts and "buy_today" in parts:
                out[("grid", "buy_today")] = val
            elif "grid" in parts and "sell_today" in parts:
                out[("grid", "sell_today")] = val
            elif "battery" in parts and "charge_today" in parts:
                out[("battery", "charge_today")] = val
            elif "battery" in parts and "discharge_today" in parts:
                out[("battery", "discharge_today")] = val
    return out


def delete_range(host: str, token: str, org: str, bucket: str, start: datetime, stop: datetime) -> None:
    url = f"{host.rstrip('/')}/api/v2/delete?org={urllib.parse.quote(org)}&bucket={urllib.parse.quote(bucket)}"
    body = (
        f'{{"start":"{start.strftime("%Y-%m-%dT%H:%M:%SZ")}",'
        f'"stop":"{stop.strftime("%Y-%m-%dT%H:%M:%SZ")}",'
        f'"predicate":"_measurement=\\"solar\\""}}'
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            if resp.status not in (204, 200):
                raise RuntimeError(f"delete status {resp.status}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode()) from e


def write_lp(host: str, token: str, org: str, bucket: str, lines: list[str]) -> None:
    url = (
        f"{host.rstrip('/')}/api/v2/write?org={urllib.parse.quote(org)}"
        f"&bucket={urllib.parse.quote(bucket)}&precision=ns"
    )
    req = urllib.request.Request(
        url,
        data=("\n".join(lines) + "\n").encode(),
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "text/plain; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        if resp.status not in (204, 200):
            raise RuntimeError(f"write status {resp.status}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--day", required=True, help="ICT calendar day YYYY-MM-DD")
    p.add_argument("--host", default=os.environ.get("INFLUX_HOST", "http://127.0.0.1:8086"))
    p.add_argument("--token", default=os.environ.get("INFLUX_TOKEN", ""))
    p.add_argument("--org", default=os.environ.get("INFLUX_ORG", "iriv_org"))
    p.add_argument("--device", default="sg06")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if not args.token:
        print("ERROR: INFLUX_TOKEN required", file=sys.stderr)
        return 2

    d = date.fromisoformat(args.day)
    day_start, day_stop = ict_day_bounds_utc(d)
    # skip first hour after midnight reset
    q_start = day_start + timedelta(hours=1)
    q_stop = day_stop - timedelta(seconds=1)
    ts = end_of_ict_day_utc(d)

    flux = f"""
from(bucket: "solar_raw")
  |> range(start: {q_start.strftime("%Y-%m-%dT%H:%M:%SZ")}, stop: {q_stop.strftime("%Y-%m-%dT%H:%M:%SZ")})
  |> filter(fn: (r) => r._measurement == "solar" and r._field == "value")
  |> filter(fn: (r) => r.device == "{args.device}")
  |> filter(fn: (r) => r.metric =~ /today$/)
  |> group(columns: ["component", "metric"])
  |> last()
"""
    csv = flux_query(args.host, args.token, args.org, flux)
    vals = parse_last_values(csv)
    if not vals:
        print("ERROR: no EOD values parsed from solar_raw", file=sys.stderr)
        print(csv[:2000], file=sys.stderr)
        return 1

    print(f"EOD {d} (from solar_raw):")
    for (c, m), v in sorted(vals.items()):
        print(f"  {c}/{m} = {v}")

    ns = int(ts.timestamp() * 1_000_000_000)
    lines = [
        f"solar,device={args.device},component={c},metric={m} value={v} {ns}"
        for (c, m), v in sorted(vals.items())
    ]

    if args.dry_run:
        print("DRY-RUN — would delete solar_1d in ICT day then write:")
        for lp in lines:
            print(" ", lp)
        return 0

    print(f"Deleting solar_1d {day_start} .. {day_stop}")
    delete_range(args.host, args.token, args.org, "solar_1d", day_start, day_stop)
    write_lp(args.host, args.token, args.org, "solar_1d", lines)
    print(f"Wrote {len(lines)} points @ {ts.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
