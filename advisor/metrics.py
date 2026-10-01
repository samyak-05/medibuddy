METRICS = {
    "temp_max":         ("°C",   "max temperature"),
    "temp_min":         ("°C",   "min temperature"),
    "feels_like_max":   ("°C",   "max feels-like temperature"),
    "feels_like_min":   ("°C",   "min feels-like temperature"),
    "rain_window":      ("mm",   "rain expected in this window"),
    "rain_24h":         ("mm",   "rain expected over the next 24 hours"),
    "precip_prob_max":  ("%",    "highest chance of rain"),
    "wind_max":         ("km/h", "max wind speed"),
    "wind_gust_max":    ("km/h", "max wind gust"),
    "uv_max":           ("",     "max UV index"),
    "cloud_cover_mean": ("%",    "average cloud cover"),
    "thunder_hours":    ("h",    "hours with thunderstorms forecast"),
}


def fmt(name: str, value: float) -> str:
    unit = METRICS[name][0]
    v = f"{value:g}"
    if unit == "%":
        return f"{v}%"
    return f"{v} {unit}".strip()
