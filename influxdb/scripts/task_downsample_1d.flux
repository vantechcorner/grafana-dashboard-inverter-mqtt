option task = {name: "downsample_solar_1d", every: 1d, offset: 17h10m}

// Runs ~17:10 UTC (= 00:10 ICT). Capture previous ICT day's EOD counters.
// Window ends 11m before run (~16:59 UTC) so we do not see the midnight reset.
from(bucket: "solar_raw")
  |> range(start: -25h, stop: -11m)
  |> filter(fn: (r) => r._measurement == "solar" and r._field == "value")
  |> filter(fn: (r) => r.metric =~ /today$/)
  |> last()
  |> set(key: "_measurement", value: "solar")
  |> to(bucket: "solar_1d", org: "iriv_org")
