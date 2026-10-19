"""Scraping data kualitas udara (hourly) per kota dari Open-Meteo Air Quality API."""
from .config import (
    AIR_QUALITY_API_URL, AIR_QUALITY_HOURLY_VARS, AIR_QUALITY_FORECAST_DAYS,
    REQUEST_TIMEOUT, MAX_RETRIES, RETRY_BACKOFF_SEC,
)
from .http_utils import get_json


def fetch_air_quality(city: dict, past_days: int) -> list[dict]:
    """Ambil data kualitas udara per jam untuk satu kota, `past_days` hari ke belakang.

    Return: list of dict, satu dict = satu baris (satu jam) data kualitas udara.
    """
    params = {
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "hourly": ",".join(AIR_QUALITY_HOURLY_VARS),
        "past_days": past_days,
        "forecast_days": AIR_QUALITY_FORECAST_DAYS,
        "timezone": "Asia/Jakarta",
    }
    data = get_json(AIR_QUALITY_API_URL, params, REQUEST_TIMEOUT, MAX_RETRIES, RETRY_BACKOFF_SEC)
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])

    rows = []
    for i, t in enumerate(times):
        row = {"city_id": city["city_id"], "ts": t}
        for var in AIR_QUALITY_HOURLY_VARS:
            values = hourly.get(var, [])
            row[var] = values[i] if i < len(values) else None
        rows.append(row)
    return rows
