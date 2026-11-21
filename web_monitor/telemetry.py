"""Read-only mount and weather telemetry for the web monitor."""

import math
from pathlib import Path
from threading import Event, Thread
from time import time

def read_pointing(status):
    def number(key):
        try:
            value = float(status[key])
            return value if math.isfinite(value) else None
        except (KeyError, ValueError, TypeError):
            return None

    lst = number("site.lmst_hours")
    if status.get("mount.is_connected", "").lower() != "true":
        return {"lst": lst}
    ra = number("mount.ra_apparent_hours")
    return {
        "lst": lst,
        "alt": number("mount.altitude_degs"),
        "az": number("mount.azimuth_degs"),
        "ra": ra,
        "dec": number("mount.dec_apparent_degs"),
        "ha": (lst - ra + 12) % 24 - 12 if lst is not None and ra is not None else None,
    }


def read_clarity(path):
    if path is None:
        return {"message": "Not configured"}
    try:
        with path.open(encoding="ascii") as source:
            fields = source.readline(1024).split()
        wind_units = {"K": "km/h", "M": "mph", "m": "m/s"}
        modern = fields[3] in wind_units
        sky = float(fields[4 if modern else 3])
        since = float(fields[13 if modern else 8])
        if fields[2] not in ("C", "F") or not math.isfinite(since) or since < 0:
            return {"message": "Invalid reading"}
        if time() - path.stat().st_mtime > 30 or since > 30:
            return {"message": "Stale"}
        def number(index):
            try:
                value = float(fields[index])
                return value if math.isfinite(value) else None
            except (IndexError, ValueError):
                return None

        def temperature(index):
            value = number(index)
            if value is None or value in (-999, -998, 999):
                return None
            return value if fields[2] == "C" else (value - 32) * 5 / 9

        def condition(index, labels):
            return labels.get(fields[index]) if index < len(fields) else None

        invalid_sky = not math.isfinite(sky) or sky in (-999, -998, 999)
        wet = fields[12 if modern else 7] == "1"
        weather = {
            "sky_delta": None if invalid_sky else sky if fields[2] == "C" else sky * 5 / 9,
            "sky_condition": condition(15 if modern else 10, {"1": "Clear", "2": "Cloudy", "3": "Very cloudy"}),
            "outside": temperature(5 if modern else 4),
            "sensor": temperature(6 if modern else 5),
            "heater": number(10 if modern else 6),
            "rain": "Wet" if wet else "Dry",
        }
        if modern:
            wind = number(7)
            weather.update(
                wind=wind if wind is not None and wind >= 0 else None,
                wind_unit=wind_units[fields[3]],
                wind_condition=condition(16, {"1": "Calm", "2": "Windy", "3": "Very windy"}),
                humidity=number(8),
                dew_point=temperature(9),
                rain=("Rain" if fields[11] == "1" else
                      condition(17, {"1": "Dry", "2": "Wet", "3": "Rain"}) or weather["rain"]),
                light=condition(18, {"1": "Dark", "2": "Light", "3": "Very light"}),
            )
            if wet and weather["rain"] == "Dry":
                weather["rain"] = "Wet"
        return weather
    except (OSError, UnicodeError, ValueError, IndexError):
        return {"message": "Unavailable"}


def start_telemetry(monitor, get_mount_status, clarity_file):
    stop = Event()
    path = Path(clarity_file) if clarity_file else None

    def poll():
        while not stop.is_set():
            try:
                pointing = read_pointing(get_mount_status())
            except Exception:
                pointing = None
            weather = read_clarity(path)
            monitor.publish(pointing=pointing, weather=weather)
            stop.wait(2)

    Thread(target=poll, name="web-status-telemetry", daemon=True).start()
    return stop
