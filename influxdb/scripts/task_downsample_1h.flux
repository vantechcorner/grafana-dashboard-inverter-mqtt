option task = {name: "downsample_solar_1h", every: 1h, offset: 5m}

from(bucket: "solar_raw")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "solar" and r._field == "value")
  |> filter(fn: (r) =>
      r.metric == "power" or r.metric == "power_ct" or
      r.metric == "voltage" or r.metric == "current" or
      r.metric == "frequency" or r.metric == "temperature" or
      r.metric == "soc"
  )
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> set(key: "_measurement", value: "solar")
  |> to(bucket: "solar_1h", org: "iriv_org")

from(bucket: "solar_raw")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "solar" and r._field == "value")
  |> filter(fn: (r) => r.metric =~ /today$/ or r.metric == "status")
  |> aggregateWindow(every: 1h, fn: last, createEmpty: false)
  |> set(key: "_measurement", value: "solar")
  |> to(bucket: "solar_1h", org: "iriv_org")
