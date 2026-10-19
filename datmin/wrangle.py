"""
DATA WRANGLING: pembersihan & pelengkapan data SEBELUM masuk ke database.

Setiap fungsi clean_* menerima list[dict] hasil scraping dan mengembalikan
(DataFrame bersih, dict statistik). Statistik dicatat ke tabel `dq_log`
di tiap database sumber, sehingga kualitas data bisa diaudit per run.

Langkah yang dilakukan (urut):
  1. Standarisasi tipe   : timestamp -> datetime, angka -> numerik (error -> NULL)
  2. Validasi rentang    : nilai fisik mustahil -> NULL (bukan dibuang barisnya)
  3. Validasi kunci/area : baris tanpa kunci / di luar wilayah Indonesia -> dibuang
  4. Deduplikasi         : kunci alami (kota+waktu, atau waktu+koordinat)
  5. Pelengkapan (impute): celah kecil deret waktu per kota diisi interpolasi linear
  6. Normalisasi kategori: confidence FIRMS (l/n/h/angka) -> skor numerik 0-100
"""
import pandas as pd

# (kolom -> (min, max)). Di luar rentang ini dianggap error sensor/parsing.
WEATHER_RANGES = {
    "temperature_c": (-10, 50),
    "humidity_pct": (0, 100),
    "precipitation_mm": (0, 500),
    "wind_speed_kmh": (0, 200),
    "wind_dir_deg": (0, 360),
    "cloud_cover_pct": (0, 100),
}
AIR_RANGES = {
    "pm10": (0, 2000),
    "pm2_5": (0, 1000),
    "carbon_monoxide": (0, 50000),
    "nitrogen_dioxide": (0, 2000),
    "sulphur_dioxide": (0, 2000),
    "ozone": (0, 1000),
    "us_aqi": (0, 500),
}
FIRE_RANGES = {"frp_mw": (0, 5000), "brightness_k": (200, 500)}

# Titik panas di luar kotak Indonesia (lihat FIRMS_BBOX) dianggap tidak valid.
INDONESIA_LAT = (-11.5, 6.5)
INDONESIA_LON = (94.5, 141.5)

# Celah maksimum (jumlah langkah waktu berturut-turut) yang boleh diisi interpolasi.
IMPUTE_LIMIT_WEATHER = 2   # data 3-jaman  -> maksimal 6 jam
IMPUTE_LIMIT_AIR = 3       # data per jam  -> maksimal 3 jam

CONFIDENCE_SCORE = {"l": 30, "n": 60, "h": 90}


def _empty_stats(n_in):
    return {"rows_in": n_in, "rows_out": 0, "duplicates_dropped": 0,
            "invalid_dropped": 0, "out_of_range_nulled": 0, "values_imputed": 0}


def _apply_ranges(df, ranges, stats):
    for col, (lo, hi) in ranges.items():
        if col not in df:
            continue
        bad = df[col].notna() & ((df[col] < lo) | (df[col] > hi))
        stats["out_of_range_nulled"] += int(bad.sum())
        df.loc[bad, col] = None
    return df


def _impute(df, cols, group_col, order_col, limit, stats):
    """Interpolasi linear per kota untuk celah pendek (tidak mengisi celah panjang)."""
    df = df.sort_values([group_col, order_col]).reset_index(drop=True)
    for col in cols:
        before = int(df[col].isna().sum())
        df[col] = df.groupby(group_col)[col].transform(
            lambda s: s.interpolate(method="linear", limit=limit, limit_area="inside"))
        stats["values_imputed"] += before - int(df[col].isna().sum())
    return df


def clean_weather(rows):
    stats = _empty_stats(len(rows))
    if not rows:
        return pd.DataFrame(), stats
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    for col in WEATHER_RANGES:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["visibility_m"] = pd.to_numeric(df["visibility_m"], errors="coerce")
    df["weather_code"] = pd.to_numeric(df["weather_code"], errors="coerce").astype("Int64")
    for col in ("weather_desc", "weather_desc_en"):
        df[col] = df[col].astype("string").str.strip()

    n0 = len(df)
    df = df.dropna(subset=["city_id", "ts"])
    stats["invalid_dropped"] += n0 - len(df)

    df = _apply_ranges(df, WEATHER_RANGES, stats)

    n0 = len(df)
    df = df.drop_duplicates(subset=["city_id", "ts"], keep="last")
    stats["duplicates_dropped"] = n0 - len(df)

    df = _impute(df, ["temperature_c", "humidity_pct", "cloud_cover_pct", "wind_speed_kmh"],
                 "city_id", "ts", IMPUTE_LIMIT_WEATHER, stats)
    stats["rows_out"] = len(df)
    return df, stats


def clean_air_quality(rows):
    stats = _empty_stats(len(rows))
    if not rows:
        return pd.DataFrame(), stats
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["ts"], errors="coerce")
    for col in AIR_RANGES:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    n0 = len(df)
    df = df.dropna(subset=["city_id", "ts"])
    stats["invalid_dropped"] += n0 - len(df)

    df = _apply_ranges(df, AIR_RANGES, stats)

    n0 = len(df)
    df = df.drop_duplicates(subset=["city_id", "ts"], keep="last")
    stats["duplicates_dropped"] = n0 - len(df)

    df = _impute(df, list(AIR_RANGES), "city_id", "ts", IMPUTE_LIMIT_AIR, stats)
    stats["rows_out"] = len(df)
    return df, stats


def clean_fire(rows):
    stats = _empty_stats(len(rows))
    if not rows:
        return pd.DataFrame(), stats
    df = pd.DataFrame(rows)
    df["event_datetime"] = pd.to_datetime(df["event_datetime"], errors="coerce")
    for col in ("latitude", "longitude", "brightness_k", "frp_mw", "distance_km"):
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    n0 = len(df)
    df = df.dropna(subset=["event_datetime", "latitude", "longitude"])
    inside = (df["latitude"].between(*INDONESIA_LAT)) & (df["longitude"].between(*INDONESIA_LON))
    df = df[inside]
    stats["invalid_dropped"] += n0 - len(df)

    df = _apply_ranges(df, FIRE_RANGES, stats)

    # normalisasi confidence -> huruf kecil + skor numerik
    df["confidence"] = df["confidence"].astype("string").str.strip().str.lower()
    mapped = df["confidence"].map(CONFIDENCE_SCORE)
    numeric = pd.to_numeric(df["confidence"], errors="coerce")
    df["confidence_score"] = mapped.fillna(numeric).astype(float)

    n0 = len(df)
    df = df.drop_duplicates(subset=["event_datetime", "latitude", "longitude"], keep="last")
    stats["duplicates_dropped"] = n0 - len(df)
    stats["rows_out"] = len(df)
    return df, stats
