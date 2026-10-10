#!/usr/bin/env python3
"""Solar Energy: Production 2dp; Cost/Saving thousand separators."""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "grafana/dashboards/solar-energy.json"


def main() -> None:
    d = json.loads(PATH.read_text(encoding="utf-8"))

    for panel in d["panels"]:
        title = panel.get("title", "")
        if title == "Solar" and panel.get("type") == "stat":
            panel["fieldConfig"]["defaults"]["decimals"] = 2
            print("Solar decimals -> 2")
        if title in ("Estimate Cost", "Estimate Saving"):
            fc = panel["fieldConfig"]["defaults"]
            # Grafana "Locale format" → 1,000 / 1,000,000 when UI locale is en-*
            fc["unit"] = "locale"
            fc["decimals"] = 0
            print(f"{title}: unit=locale decimals=0")
        if panel.get("id") == 80 and panel.get("type") == "volkovlabs-echarts-panel":
            go = panel["options"].get("getOption", "")
            needle = "axisPointer: { type: 'shadow' },\n  },"
            insert = (
                "axisPointer: { type: 'shadow' },\n"
                "    valueFormatter: (v) => "
                "(v == null || v === '' ? '-' : Number(v).toFixed(2) + ' kWh'),\n"
                "  },"
            )
            if "valueFormatter" not in go and needle in go:
                panel["options"]["getOption"] = go.replace(needle, insert, 1)
                print("ECharts tooltip 2dp added")
            elif "valueFormatter" in go:
                print("ECharts tooltip already has valueFormatter")
            else:
                print("WARN: ECharts tooltip pattern not found")

    d["version"] = int(d.get("version", 20)) + 1
    PATH.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"ok version={d['version']}")


if __name__ == "__main__":
    main()
