"""
Scraping data cuaca per kota dari BMKG (prakiraan-cuaca resmi, per
kelurahan/adm4). Ini adalah SUMBER 1 dari 3 sumber data project ini.

Endpoint: https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4=<kode>
Data yang dikembalikan: prakiraan 3 hari ke depan, tiap 3 jam (8 titik
waktu per hari), TIDAK ada arsip historis publik. Karena itu `past_days`
tidak dipakai di sini (parameter tetap ada di signature agar kompatibel
dengan pemanggilan di pipeline.py, tapi diabaikan) -> volume data untuk
sumber ini bertambah lewat AKUMULASI tiap kali pipeline dijalankan ulang,
bukan lewat parameter historis seperti pada Open-Meteo.
"""
from .config import BMKG_WEATHER_API_URL, REQUEST_TIMEOUT, MAX_RETRIES, RETRY_BACKOFF_SEC
from .http_utils import get_json


def _flatten_cuaca(payload):
    """BMKG mengembalikan salah satu dari dua bentuk berikut, tergantung
    versi/endpoint:
      a) {"lokasi": {...}, "cuaca": [[{...}, ...], [...], [...]]}
      b) {"lokasi": {...}, "data": [{"lokasi": {...}, "cuaca": [[...]]}]}
    Fungsi ini menormalkan keduanya menjadi list flat dict per titik waktu.
    """
    entries = payload.get("data") if isinstance(payload.get("data"), list) else [payload]

    flat = []
    for entry in entries:
        cuaca = entry.get("cuaca", [])
        for day_group in cuaca:
            if isinstance(day_group, dict):
                day_group = [day_group]
            for point in day_group:
                flat.append(point)
    return flat


def fetch_weather(city: dict, past_days: int = None) -> list[dict]:
    """Ambil prakiraan cuaca 3 harian (per 3 jam) untuk satu kota dari BMKG.

    `past_days` diabaikan namun tetap diterima supaya
    signature sama dengan pemanggil di pipeline.py.

    Return: list of dict, satu dict = satu titik waktu (per 3 jam).
    """
    adm4 = city.get("bmkg_adm4")
    if not adm4:
        raise RuntimeError(f"Kota {city['city_name']} tidak punya kode bmkg_adm4 di config.py")

    params = {"adm4": adm4}
    payload = get_json(BMKG_WEATHER_API_URL, params, REQUEST_TIMEOUT, MAX_RETRIES, RETRY_BACKOFF_SEC)
    points = _flatten_cuaca(payload)

    rows = []
    for p in points:
        ts = p.get("local_datetime") or p.get("utc_datetime") or p.get("datetime")
        if not ts:
            continue
        rows.append({
            "city_id": city["city_id"],
            "ts": ts,
            "temperature_c": p.get("t"),
            "humidity_pct": p.get("hu"),
            "precipitation_mm": p.get("tp"),
            "wind_speed_kmh": p.get("ws"),
            "wind_dir_deg": p.get("wd_deg"),
            "cloud_cover_pct": p.get("tcc"),
            "weather_code": p.get("weather"),
            "weather_desc": p.get("weather_desc"),
            "weather_desc_en": p.get("weather_desc_en"),
            "visibility_m": p.get("vs"),
            "analysis_date": p.get("analysis_date"),
        })
    return rows
