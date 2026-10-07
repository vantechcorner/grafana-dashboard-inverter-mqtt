# House Load + House Energy Today (derived)
# Matches deye-mqtt-dashboard-p4-7 telemetry_house_power_w / home_energy_today.
#
# Starlark notes:
# - `load` is a RESERVED keyword — never use as a variable name
# - No Python ternary (x = a if c else b)
# - `state` only inside apply()

CHARGE_FROM_GRID_THRESH_W = 40.0

def _key(device, component, metric):
    return device + "|" + component + "|" + metric

def _get(cache, device, component, metric):
    return cache.get(_key(device, component, metric))

def _house_power(cache, device):
    load_p = _get(cache, device, "load", "power")
    grid_p = _get(cache, device, "grid", "power_ct")
    if load_p == None and grid_p == None:
        return None

    essential = 0.0
    if load_p != None:
        essential = load_p
    if essential < 0.0:
        essential = 0.0

    grid_import = 0.0
    if grid_p != None:
        if grid_p > 0.0:
            grid_import = grid_p

    charge = 0.0
    batt = _get(cache, device, "battery", "power")
    if batt != None:
        if batt < 0.0:
            charge = 0.0 - batt

    pv = 0.0
    pv1 = _get(cache, device, "pv1", "power")
    pv2 = _get(cache, device, "pv2", "power")
    if pv1 != None:
        if pv1 > 0.0:
            pv += pv1
    if pv2 != None:
        if pv2 > 0.0:
            pv += pv2

    charge_from_grid = charge - pv
    if charge_from_grid < 0.0:
        charge_from_grid = 0.0

    grid_for_house = grid_import
    if charge_from_grid >= CHARGE_FROM_GRID_THRESH_W:
        grid_for_house = 0.0

    return essential + grid_for_house

def _house_energy_today(cache, device):
    total = 0.0
    any_ok = False

    v = _get(cache, device, "pv", "energy_today")
    if v != None:
        total += v
        any_ok = True

    v = _get(cache, device, "grid", "buy_today")
    if v != None:
        total += v
        any_ok = True

    v = _get(cache, device, "battery", "discharge_today")
    if v != None:
        total += v
        any_ok = True

    v = _get(cache, device, "grid", "sell_today")
    if v != None:
        total -= v
        any_ok = True

    v = _get(cache, device, "battery", "charge_today")
    if v != None:
        total -= v
        any_ok = True

    if not any_ok:
        return None
    if total < 0.0:
        total = 0.0
    return total

def apply(metric):
    if "cache" not in state:
        state["cache"] = {}

    device = metric.tags.get("device")
    component = metric.tags.get("component")
    mname = metric.tags.get("metric")
    if device == None or component == None or mname == None:
        return metric
    if "value" not in metric.fields:
        return metric

    cache = state["cache"]
    cache[_key(device, component, mname)] = float(metric.fields["value"])

    out = [metric]

    if mname == "power" or mname == "power_ct":
        hw = _house_power(cache, device)
        if hw != None:
            h = Metric("solar")
            h.time = metric.time
            h.tags["device"] = device
            h.tags["component"] = "house"
            h.tags["metric"] = "power"
            h.fields["value"] = float(hw)
            out.append(h)

    if mname.find("today") >= 0:
        he = _house_energy_today(cache, device)
        if he != None:
            e = Metric("solar")
            e.time = metric.time
            e.tags["device"] = device
            e.tags["component"] = "house"
            e.tags["metric"] = "energy_today"
            e.fields["value"] = float(he)
            out.append(e)

    return out
