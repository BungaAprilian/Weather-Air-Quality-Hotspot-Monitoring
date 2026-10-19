"""
Scraping data KEJADIAN titik panas (hotspot) dari
NASA FIRMS (Fire Information for Resource Management System). Ini adalah
SUMBER 3 dari 3 sumber data project ini - sengaja BUKAN dari BMKG maupun
Open-Meteo, dan jenis datanya berbeda dari cuaca/kualitas udara (bukan
pengukuran per jam yang halus, tapi kejadian diskrit hasil deteksi
satelit), lalu dipadukan ke 15 kota besar yang sama lewat pencocokan
JARAK TERDEKAT (haversine), bukan lewat titik koordinat kota langsung.

Endpoint: https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{bbox}/{day_range}
"""
import csv
import io
from datetime import datetime

from .config import (
    FIRMS_MAP_KEY, FIRMS_SOURCE, FIRMS_BBOX, FIRMS_DAY_RANGE, FIRE_MAX_DISTANCE_KM,
    CITIES, REQUEST_TIMEOUT, MAX_RETRIES, RETRY_BACKOFF_SEC,
)
from .http_utils import get_text
from .geo_utils import nearest_city

FIRMS_AREA_API_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# VIIRS NRT memberi confidence sebagai huruf (l/n/h = low/nominal/high),
# beda dari MODIS yang numerik 0-100. Dipetakan ke skor numerik supaya
# bisa diagregasi (AVG/MAX) di SQL.
CONFIDENCE_SCORE = {"l": 30, "n": 60, "h": 90}


def _parse_acq_datetime(acq_date: str, acq_time: str):
    """acq_date: 'YYYY-MM-DD'. acq_time: 'HMM'/'HHMM' (jam UTC, kadang
    tanpa leading zero, mis. '52' berarti 00:52 UTC)."""
    try:
        acq_time = str(int(acq_time)).zfill(4)  # normalisasi jadi 4 digit
        hh, mm = acq_time[:2], acq_time[2:]
        return datetime.strptime(f"{acq_date} {hh}:{mm}", "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None


def fetch_fire_hotspots(cities: list = None) -> list[dict]:
    """Ambil titik panas di seluruh Indonesia (bbox) dari NASA
    FIRMS untuk `FIRMS_DAY_RANGE` hari terakhir, lalu cocokkan tiap titik
    ke kota terdekat di antara `cities` (default: semua CITIES).

    Return: list of dict, satu dict = satu titik panas + kota terdekat.
    """
    if not FIRMS_MAP_KEY:
        raise RuntimeError(
            "FIRMS_MAP_KEY belum diisi. Daftar gratis di "
            "https://firms.modaps.eosdis.nasa.gov/api/map_key/ lalu set "
            "env var FIRMS_MAP_KEY sebelum menjalankan pipeline."
        )

    cities = cities or CITIES
    west, south, east, north = FIRMS_BBOX
    bbox_str = f"{west},{south},{east},{north}"
    url = f"{FIRMS_AREA_API_URL}/{FIRMS_MAP_KEY}/{FIRMS_SOURCE}/{bbox_str}/{FIRMS_DAY_RANGE}"

    text = get_text(url, params=None, timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES, backoff=RETRY_BACKOFF_SEC)

    # FIRMS membalas pesan error sebagai baris teks biasa (bukan CSV valid)
    # kalau MAP_KEY salah/kadaluarsa/kuota habis, mis. "Invalid MAP_KEY".
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    if "invalid" in first_line.lower() or "error" in first_line.lower():
        raise RuntimeError(f"NASA FIRMS menolak permintaan: {first_line}")

    reader = csv.DictReader(io.StringIO(text))

    rows = []
    for r in reader:
        try:
            lat, lon = float(r["latitude"]), float(r["longitude"])
        except (KeyError, ValueError):
            continue

        city, dist_km = nearest_city(lat, lon, cities)
        event_dt = _parse_acq_datetime(r.get("acq_date"), r.get("acq_time"))
        confidence_raw = (r.get("confidence") or "").strip().lower()
        try:
            frp = float(r.get("frp")) if r.get("frp") not in (None, "") else None
        except ValueError:
            frp = None
        try:
            brightness = float(r.get("bright_ti4") or r.get("brightness"))
        except (TypeError, ValueError):
            brightness = None

        # confidence: huruf (VIIRS: l/n/h) -> skor; angka (MODIS: 0-100) -> dipakai langsung
        if confidence_raw in CONFIDENCE_SCORE:
            confidence_score = CONFIDENCE_SCORE[confidence_raw]
        elif confidence_raw.isdigit():
            confidence_score = int(confidence_raw)
        else:
            confidence_score = None

        rows.append({
            "event_datetime": event_dt,
            "latitude": lat,
            "longitude": lon,
            "brightness_k": brightness,
            "frp_mw": frp,
            "confidence": confidence_raw,
            "confidence_score": confidence_score,
            "satellite": r.get("satellite"),
            "daynight": r.get("daynight"),
            "nearest_city_id": city["city_id"] if city else None,
            "nearest_city_name": city["city_name"] if city else None,
            "distance_km": round(dist_km, 1) if city else None,
            "is_within_radius": bool(city) and dist_km <= FIRE_MAX_DISTANCE_KM,
        })
    return rows
