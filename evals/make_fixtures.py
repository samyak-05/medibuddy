import json
from datetime import datetime, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 3, 7, 30)


def series(base, evening=None, hours=72):
    start = NOW.replace(hour=0, minute=0)
    times, rows = [], []
    for i in range(hours):
        t = start + timedelta(hours=i)
        times.append(t.strftime("%Y-%m-%dT%H:%M"))
        row = dict(base)
        if evening and 17 <= t.hour < 21 and t.date() == NOW.date():
            row.update(evening)
        if t.hour < 6 or t.hour >= 19:
            row["uv_index"] = 0.0
        rows.append(row)
    hourly = {"time": times}
    for k in base:
        hourly[k] = [r[k] for r in rows]
    return {
        "latitude": 0, "longitude": 0, "timezone": "Asia/Kolkata",
        "current": {"time": NOW.strftime("%Y-%m-%dT%H:%M"), "temperature_2m": base["temperature_2m"]},
        "hourly": hourly,
    }


CALM = dict(temperature_2m=26.0, apparent_temperature=27.5, precipitation=0.0,
            precipitation_probability=10, wind_speed_10m=8.0, wind_gusts_10m=14.0,
            uv_index=6.0, cloud_cover=35, weather_code=2)

fixtures = {
    # Organised rain belt: ~5 mm every hour, high probability, gusty.
    "monsoon_low.json": series({**CALM, "temperature_2m": 24.1, "apparent_temperature": 27.9,
                                "precipitation": 4.6, "precipitation_probability": 95,
                                "wind_speed_10m": 24.0, "wind_gusts_10m": 52.0,
                                "uv_index": 1.0, "cloud_cover": 100, "weather_code": 63}),
    "calm.json": series(CALM),
    # Dry but gusty, high UV: two SOPs at once for cyclists.
    "windy_sunny.json": series({**CALM, "wind_speed_10m": 33.0, "wind_gusts_10m": 49.0,
                                "uv_index": 9.2, "cloud_cover": 5}),
    # Fine morning, storm in the evening. Used for the follow-up test.
    "evening_storm.json": series(CALM, evening={"precipitation": 6.0, "precipitation_probability": 90,
                                                "wind_gusts_10m": 58.0, "weather_code": 95}),
    "heatwave.json": series({**CALM, "temperature_2m": 41.0, "apparent_temperature": 44.5,
                             "uv_index": 10.5, "cloud_cover": 0, "weather_code": 0}),
}

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, data in fixtures.items():
        (OUT / name).write_text(json.dumps(data))
        print("wrote", OUT / name)