import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import requests
from advisor.metrics import METRICS

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_FIELDS = [
    "temperature_2m", "apparent_temperature", "precipitation",
    "precipitation_probability", "wind_speed_10m", "wind_gusts_10m",
    "uv_index", "cloud_cover", "weather_code",
]
THUNDER_CODES = {95, 96, 99}

WINDOWS = {
    "now": (None, None, 0),
    "today": (6,22,0),
    "this_morning": (6,12,0),
    "this_afternoon":     (12, 17, 0),
    "this_evening":       (17, 21, 0),
    "tonight":            (21, 24, 0),
    "tomorrow":           (6, 22, 1),
    "tomorrow_morning":   (6, 12, 1),
    "tomorrow_afternoon": (12, 17, 1),
    "tomorrow_evening":   (17, 21, 1),
}

class WeatherError(Exception):
    """Anything that means we don't have a forecast we can stand behind."""

@dataclass
class Place:
    name: str
    country: str
    lat: float
    lon: float
    candidates: int = 1

    @property
    def label(self) -> str:
        return f"{self.name}, {self.country}" if self.country else self.name

@dataclass
class WeatherSnapshot:
    place: Place
    window: str
    window_label: str
    start: str
    end: str
    metrics: dict[str, float]
    observed_at: str
    note: str = ""
    hours: list[dict] = field(default_factory=list)

class OpenMeteo:
    def __init__(self, timeout: float = 8.0, cache_ttl: int = 600):
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._cache: dict[tuple, tuple[float, dict]] = {}

    def _get(self, url: str, params: dict) -> dict:
        try:
            r = requests.get(url, params=params, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            raise WeatherError(f"weather service unreachable ({type(e).__name__})") from e

    def geocode(self, name: str) -> Place:
        data = self._get(GEOCODE_URL, {"name": name, "count": 5, "language": "en", "format": "json"})
        results = data.get("results") or []
        if not results:
            raise WeatherError(f"could not find a place called '{name}'")
        top = results[0]
        return Place(top["name"], top.get("country", ""), top["latitude"], top["longitude"], len(results))

    def forecast(self, lat: float, lon: float) -> dict:
        key = (round(lat, 3), round(lon, 3))
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < self.cache_ttl:
            return hit[1]
        data = self._get(FORECAST_URL, {
            "latitude": lat, "longitude": lon,
            "hourly": ",".join(HOURLY_FIELDS),
            "current": "temperature_2m,precipitation,wind_speed_10m",
            "timezone": "auto", "forecast_days": 3,
        })
        if "hourly" not in data or "current" not in data:
            raise WeatherError("weather service returned no forecast values")
        self._cache[key] = (time.time(), data)
        return data


def _window_bounds(window: str, now: datetime) -> tuple[datetime, datetime, str, str]:
    if window not in WINDOWS:
        window = "now"
    if window == "now":
        start = now.replace(minute=0)
        return start, start + timedelta(hours=3), "next 3 hours", ""

    h0, h1, day = WINDOWS[window]
    base = now.replace(hour=0, minute=0) + timedelta(days=day)
    start, end = base + timedelta(hours=h0), base + timedelta(hours=h1)
    label = window.replace("_", " ")
    note = ""

    if end <= now:  # asked about a part of today that's already over
        start, end = start + timedelta(days=1), end + timedelta(days=1)
        note = f"{label} has already passed, so this is for tomorrow instead"
        label = "tomorrow " + label.replace("this ", "").replace("today", "")
        label = label.strip()
    elif start < now:  # partly over; only look at what's left
        start = now.replace(minute=0)
    return start, end, label, note


def _sum(xs):
    return round(sum(x for x in xs if x is not None), 1)


def _max(xs):
    xs = [x for x in xs if x is not None]
    return round(max(xs), 1) if xs else None


def _min(xs):
    xs = [x for x in xs if x is not None]
    return round(min(xs), 1) if xs else None


def build_snapshot(place: Place, raw: dict, window: str) -> WeatherSnapshot:
    now = datetime.fromisoformat(raw["current"]["time"])
    start, end, label, note = _window_bounds(window, now)

    h = raw["hourly"]
    times = [datetime.fromisoformat(t) for t in h["time"]]
    idx = [i for i, t in enumerate(times) if start <= t < end]
    idx24 = [i for i, t in enumerate(times) if start <= t < start + timedelta(hours=24)]
    if not idx:
        raise WeatherError("forecast doesn't cover the requested time window")

    col = lambda k, ids=idx: [h[k][i] for i in ids]
    codes = [c for c in col("weather_code") if c is not None]
    clouds = [c for c in col("cloud_cover") if c is not None]

    m = {
        "temp_max": _max(col("temperature_2m")),
        "temp_min": _min(col("temperature_2m")),
        "feels_like_max": _max(col("apparent_temperature")),
        "feels_like_min": _min(col("apparent_temperature")),
        "rain_window": _sum(col("precipitation")),
        "rain_24h": _sum(col("precipitation", idx24)),
        "precip_prob_max": _max(col("precipitation_probability")),
        "wind_max": _max(col("wind_speed_10m")),
        "wind_gust_max": _max(col("wind_gusts_10m")),
        "uv_max": _max(col("uv_index")),
        "cloud_cover_mean": round(sum(clouds) / len(clouds)) if clouds else None,
        "thunder_hours": sum(1 for c in codes if c in THUNDER_CODES),
    }
    missing = [k for k, v in m.items() if v is None]
    if missing:
        # A rule silently skipping because a value is absent is a hidden failure. Better to say we can't answer.
        raise WeatherError(f"forecast is missing values for: {', '.join(missing)}")

    hours = [{k: h[k][i] for k in ["time"] + HOURLY_FIELDS} for i in idx]
    return WeatherSnapshot(
        place=place, window=window, window_label=label,
        start=start.strftime("%a %d %b %H:%M"), end=end.strftime("%H:%M"),
        metrics=m, observed_at=raw["current"]["time"], note=note, hours=hours,
    )


def snapshot_to_dict(s: WeatherSnapshot) -> dict:
    from dataclasses import asdict
    return asdict(s)


def snapshot_from_dict(d: dict) -> WeatherSnapshot:
    d = dict(d)
    d["place"] = Place(**d["place"])
    return WeatherSnapshot(**d)
