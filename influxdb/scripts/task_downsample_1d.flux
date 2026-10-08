import "timezone"
import "date"

// 17:10 UTC = 00:10 ICT — after previous ICT day is complete, before morning traffic.
// Use cron (not every+offset): the previous every/offset task never produced runs after recreate.
option task = {name: "downsample_solar_1d", cron: "10 17 * * *"}
option location = timezone.fixed(offset: 7h)

dayStart = today()
prevStart = date.add(d: -1d, to: dayStart)
// Skip first hour after midnight reset; stop 1m before next midnight.
qStart = date.add(d: 1h, to: prevStart)
qStop = date.add(d: -1m, to: dayStart)
eod = date.add(d: -1s, to: dayStart)

from(bucket: "solar_raw")
  |> range(start: qStart, stop: qStop)
  |> filter(fn: (r) => r._measurement == "solar" and r._field == "value")
  |> filter(fn: (r) => r.metric =~ /today$/)
  |> group(columns: ["device", "component", "metric", "_field", "_measurement"])
  |> last()
  |> map(fn: (r) => ({ r with _time: eod }))
  |> set(key: "_measurement", value: "solar")
  |> to(bucket: "solar_1d", org: "iriv_org")
