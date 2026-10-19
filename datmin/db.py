"""
Penyimpanan & query (SQL via DuckDB).

ARSITEKTUR DUA LAPIS
  Lapis 1 - "bebek" per sumber (staging/raw), TANPA tabel dimensi duplikat:
      weather.duckdb       <- BMKG
      air_quality.duckdb   <- Open-Meteo
      fire_hotspot.duckdb  <- NASA FIRMS
      Isi tiap bebek: tabel raw_*, scrape_log, dq_log.

  Lapis 2 - MART bintang (star schema) di DuckLake, dibangun dari 3 bebek:
      dim_city, dim_date  (dimensi, SATU kali)
      fact_weather_daily, fact_air_quality_daily, fact_hotspot_event, fact_fire_daily
      -> view analisis v_daily_environment (tanpa menyimpan salinan data)

Backend dipilih lewat env DATMIN_BACKEND (local | motherduck).
"""

import os

import duckdb
import pandas as pd

from .config import (
    AIR_QUALITY_DB_PATH,
    CITIES,
    DATMIN_BACKEND,
    DUCKLAKE_ALIAS,
    DUCKLAKE_CATALOG_PATH,
    DUCKLAKE_DATA_PATH,
    FIRE_HOTSPOT_DB_PATH,
    MOTHERDUCK_DBS,
    MOTHERDUCK_TOKEN,
    WEATHER_DB_PATH,
)
from .wrangle import clean_air_quality, clean_fire, clean_weather

SOURCES = ("weather", "air_quality", "fire_hotspot")
LOCAL_PATHS = {"weather": WEATHER_DB_PATH, "air_quality": AIR_QUALITY_DB_PATH, "fire_hotspot": FIRE_HOTSPOT_DB_PATH}
ATTACH_ALIAS = {"weather": "weather_db", "air_quality": "air_db", "fire_hotspot": "fire_db"}

# ============================================================
# LAPIS 1: SKEMA "BEBEK" PER SUMBER
# ============================================================
_COMMON_DDL = """
CREATE TABLE IF NOT EXISTS scrape_log (
    run_id VARCHAR, source VARCHAR, city_id INTEGER, city_name VARCHAR,
    start_time TIMESTAMP, end_time TIMESTAMP, duration_sec DOUBLE,
    n_records INTEGER, status VARCHAR, error_message VARCHAR
);
CREATE TABLE IF NOT EXISTS dq_log (
    run_id VARCHAR, source VARCHAR, city_id INTEGER, logged_at TIMESTAMP,
    rows_in INTEGER, rows_out INTEGER, duplicates_dropped INTEGER,
    invalid_dropped INTEGER, out_of_range_nulled INTEGER, values_imputed INTEGER
);
"""

SCHEMAS = {
    "weather": """
CREATE TABLE IF NOT EXISTS raw_weather_bmkg_hourly (
    city_id INTEGER, ts TIMESTAMP,
    temperature_c DOUBLE, humidity_pct DOUBLE, precipitation_mm DOUBLE,
    wind_speed_kmh DOUBLE, wind_dir_deg DOUBLE, cloud_cover_pct DOUBLE,
    weather_code INTEGER, weather_desc VARCHAR, weather_desc_en VARCHAR,
    visibility_m DOUBLE, analysis_date VARCHAR, scraped_at TIMESTAMP
);""" + _COMMON_DDL,
    "air_quality": """
CREATE TABLE IF NOT EXISTS raw_air_quality_hourly (
    city_id INTEGER, ts TIMESTAMP,
    pm10 DOUBLE, pm2_5 DOUBLE, carbon_monoxide DOUBLE, nitrogen_dioxide DOUBLE,
    sulphur_dioxide DOUBLE, ozone DOUBLE, us_aqi DOUBLE, scraped_at TIMESTAMP
);""" + _COMMON_DDL,
    "fire_hotspot": """
CREATE TABLE IF NOT EXISTS raw_fire_hotspot (
    event_datetime TIMESTAMP, latitude DOUBLE, longitude DOUBLE,
    brightness_k DOUBLE, frp_mw DOUBLE, confidence VARCHAR, confidence_score DOUBLE,
    satellite VARCHAR, daynight VARCHAR,
    nearest_city_id INTEGER, distance_km DOUBLE, is_within_radius BOOLEAN,
    scraped_at TIMESTAMP
);""" + _COMMON_DDL,
}


def _resolve_path(path):
    if os.path.isabs(path):
        return path
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, path)


def is_motherduck():
    return DATMIN_BACKEND == "motherduck"


def _check_source(source):
    if source not in SOURCES:
        raise ValueError(f"Source tidak dikenal: {source}. Gunakan: {list(SOURCES)}")


def source_uri(source):
    _check_source(source)
    if is_motherduck():
        return f"md:{MOTHERDUCK_DBS[source]}"
    return _resolve_path(LOCAL_PATHS[source])


def get_connection(source="weather", read_only=False):
    """Koneksi ke SATU bebek (weather | air_quality | fire_hotspot)."""
    _check_source(source)
    if is_motherduck():
        if not MOTHERDUCK_TOKEN:
            raise RuntimeError("MOTHERDUCK_TOKEN belum di-set (env var).")
        boot = duckdb.connect("md:")
        boot.execute(f"CREATE DATABASE IF NOT EXISTS {MOTHERDUCK_DBS[source]}")
        boot.close()
        return duckdb.connect(source_uri(source))
    path = source_uri(source)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return duckdb.connect(path, read_only=read_only)


def source_exists(source):
    if is_motherduck():
        return bool(MOTHERDUCK_TOKEN)
    return os.path.exists(source_uri(source))


def init_schema(con, source):
    """
    Membuat tabel bebek + MENGUPGRADE bebek versi lama (idempotent):
      - view fact_*_daily dihapus (agregasi kini hanya di mart)
      - dim_city dihapus (dimensi hanya satu: mart.dim_city)
      - kolom nearest_city_name dihapus dari raw_fire_hotspot (nama diambil via join)
    """
    _check_source(source)
    for v in ("fact_weather_daily", "fact_air_quality_daily", "fact_fire_daily"):
        con.execute(f"DROP VIEW IF EXISTS {v}")
    con.execute("DROP TABLE IF EXISTS dim_city")
    con.execute(SCHEMAS[source])
    if source == "fire_hotspot":
        cols = [r[0] for r in con.execute("DESCRIBE raw_fire_hotspot").fetchall()]
        if "nearest_city_name" in cols:
            con.execute("ALTER TABLE raw_fire_hotspot DROP COLUMN nearest_city_name")


# ============================================================
# INSERT (dengan wrangling) + LOG
# ============================================================

def log_dq(con, run_id, source, city_id, stats):
    con.execute(
        "INSERT INTO dq_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [run_id, source, city_id, pd.Timestamp.utcnow().tz_localize(None).to_pydatetime(),
         stats["rows_in"], stats["rows_out"], stats["duplicates_dropped"],
         stats["invalid_dropped"], stats["out_of_range_nulled"], stats["values_imputed"]],
    )


def _scraped_now():
    return pd.Timestamp.utcnow().tz_localize(None)


def insert_weather_rows(con, rows, run_id=None):
    df, stats = clean_weather(rows)
    if run_id:
        log_dq(con, run_id, "bmkg-cuaca", int(rows[0]["city_id"]) if rows else None, stats)
    if df.empty:
        return 0
    df["scraped_at"] = _scraped_now()
    city_id = int(df["city_id"].iloc[0])
    con.execute("DELETE FROM raw_weather_bmkg_hourly WHERE city_id = ? AND ts BETWEEN ? AND ?",
                [city_id, df["ts"].min(), df["ts"].max()])
    con.register("df_weather_tmp", df)
    con.execute("""
        INSERT INTO raw_weather_bmkg_hourly
        SELECT city_id, ts, temperature_c, humidity_pct, precipitation_mm, wind_speed_kmh,
               wind_dir_deg, cloud_cover_pct, weather_code, weather_desc, weather_desc_en,
               visibility_m, analysis_date, scraped_at
        FROM df_weather_tmp""")
    con.unregister("df_weather_tmp")
    return len(df)


def insert_air_quality_rows(con, rows, run_id=None):
    df, stats = clean_air_quality(rows)
    if run_id:
        log_dq(con, run_id, "open-meteo-air-quality", int(rows[0]["city_id"]) if rows else None, stats)
    if df.empty:
        return 0
    df["scraped_at"] = _scraped_now()
    city_id = int(df["city_id"].iloc[0])
    con.execute("DELETE FROM raw_air_quality_hourly WHERE city_id = ? AND ts BETWEEN ? AND ?",
                [city_id, df["ts"].min(), df["ts"].max()])
    con.register("df_air_tmp", df)
    con.execute("""
        INSERT INTO raw_air_quality_hourly
        SELECT city_id, ts, pm10, pm2_5, carbon_monoxide, nitrogen_dioxide,
               sulphur_dioxide, ozone, us_aqi, scraped_at
        FROM df_air_tmp""")
    con.unregister("df_air_tmp")
    return len(df)


def insert_fire_hotspot_rows(con, rows, run_id=None):
    """Dedup berdasarkan event_datetime + latitude + longitude."""
    df, stats = clean_fire(rows)
    if run_id:
        log_dq(con, run_id, "nasa-firms", None, stats)
    if df.empty:
        return 0
    df["scraped_at"] = _scraped_now()
    con.register("df_fire_tmp", df)
    con.execute("""
        DELETE FROM raw_fire_hotspot WHERE (event_datetime, latitude, longitude) IN
        (SELECT event_datetime, latitude, longitude FROM df_fire_tmp)""")
    con.execute("""
        INSERT INTO raw_fire_hotspot
        SELECT event_datetime, latitude, longitude, brightness_k, frp_mw, confidence,
               confidence_score, satellite, daynight, nearest_city_id, distance_km,
               is_within_radius, scraped_at
        FROM df_fire_tmp""")
    con.unregister("df_fire_tmp")
    return len(df)


def log_scrape(con, run_id, source, city_id, city_name, start_time, end_time,
               n_records, status, error_message=None):
    duration = (end_time - start_time).total_seconds()
    con.execute("INSERT INTO scrape_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, source, city_id, city_name, start_time, end_time,
                 duration, n_records, status, error_message])


# ============================================================
# LAPIS 2: MART BINTANG
# ============================================================
MART_TABLES = ["dim_city", "dim_date", "fact_weather_daily", "fact_air_quality_daily",
               "fact_hotspot_event", "fact_fire_daily"]

# tabel dari versi sebelumnya yang sudah digantikan
LEGACY_MART_TABLES = ["daily_joined", "fire_hotspot", "city"]


def mart_ddl(p):
    """DDL mart. `p` = prefix skema, mis. 'datmin_lake.datmin.' atau 'mart.'.
    Catatan: DuckLake tidak mendukung PRIMARY/FOREIGN KEY; kunci dicatat sebagai komentar."""
    return [
        f"""CREATE TABLE IF NOT EXISTS {p}dim_city (          -- PK city_id
            city_id INTEGER, city_name VARCHAR, province VARCHAR, region VARCHAR,
            latitude DOUBLE, longitude DOUBLE, bmkg_adm4 VARCHAR, bmkg_kelurahan VARCHAR)""",
        f"""CREATE TABLE IF NOT EXISTS {p}dim_date (          -- PK date
            date DATE, year INTEGER, month INTEGER, year_month VARCHAR,
            day INTEGER, day_name VARCHAR, is_weekend BOOLEAN)""",
        f"""CREATE TABLE IF NOT EXISTS {p}fact_weather_daily (   -- grain: city_id x date
            city_id INTEGER, date DATE, n_obs INTEGER,
            avg_temp DOUBLE, max_temp DOUBLE, min_temp DOUBLE, total_precipitation DOUBLE,
            avg_humidity DOUBLE, avg_wind_speed DOUBLE, avg_cloud_cover DOUBLE)""",
        f"""CREATE TABLE IF NOT EXISTS {p}fact_air_quality_daily (  -- grain: city_id x date
            city_id INTEGER, date DATE, n_obs INTEGER,
            avg_pm10 DOUBLE, avg_pm25 DOUBLE, avg_co DOUBLE, avg_no2 DOUBLE, avg_so2 DOUBLE,
            avg_ozone DOUBLE, avg_us_aqi DOUBLE, max_us_aqi DOUBLE, aqi_category VARCHAR)""",
        f"""CREATE TABLE IF NOT EXISTS {p}fact_hotspot_event (  -- grain: 1 deteksi satelit
            event_datetime TIMESTAMP, event_date DATE, latitude DOUBLE, longitude DOUBLE,
            brightness_k DOUBLE, frp_mw DOUBLE, confidence VARCHAR, confidence_score DOUBLE,
            daynight VARCHAR, city_id INTEGER, distance_km DOUBLE, is_within_radius BOOLEAN)""",
        f"""CREATE TABLE IF NOT EXISTS {p}fact_fire_daily (   -- grain: city_id x date (dalam radius)
            city_id INTEGER, date DATE, n_hotspot BIGINT, total_frp_mw DOUBLE,
            max_confidence_score DOUBLE, min_distance_km DOUBLE)""",
    ]


def mart_load_sql(p):
    """Urutan pengisian mart dari 3 bebek (weather_db, air_db, fire_db) + DataFrame city_df."""
    return [
        (f"{p}dim_city", f"""
            SELECT city_id, city_name, province, region, latitude, longitude, bmkg_adm4, bmkg_kelurahan
            FROM city_df"""),
        (f"{p}fact_weather_daily", """
            SELECT city_id, CAST(ts AS DATE) AS date, COUNT(*) AS n_obs,
                   AVG(temperature_c), MAX(temperature_c), MIN(temperature_c), SUM(precipitation_mm),
                   AVG(humidity_pct), AVG(wind_speed_kmh), AVG(cloud_cover_pct)
            FROM weather_db.raw_weather_bmkg_hourly GROUP BY city_id, CAST(ts AS DATE)"""),
        (f"{p}fact_air_quality_daily", """
            SELECT city_id, CAST(ts AS DATE) AS date, COUNT(*) AS n_obs,
                   AVG(pm10), AVG(pm2_5), AVG(carbon_monoxide), AVG(nitrogen_dioxide),
                   AVG(sulphur_dioxide), AVG(ozone), AVG(us_aqi), MAX(us_aqi),
                   CASE WHEN AVG(us_aqi) IS NULL THEN NULL
                        WHEN AVG(us_aqi) <= 50  THEN 'Baik'
                        WHEN AVG(us_aqi) <= 100 THEN 'Sedang'
                        WHEN AVG(us_aqi) <= 150 THEN 'Tidak sehat (sensitif)'
                        WHEN AVG(us_aqi) <= 200 THEN 'Tidak sehat'
                        WHEN AVG(us_aqi) <= 300 THEN 'Sangat tidak sehat'
                        ELSE 'Berbahaya' END
            FROM air_db.raw_air_quality_hourly GROUP BY city_id, CAST(ts AS DATE)"""),
        # event_date memakai WIB (UTC+7) agar sejajar dengan cuaca/udara yang berjam WIB
        (f"{p}fact_hotspot_event", """
            SELECT event_datetime, CAST(event_datetime + INTERVAL 7 HOUR AS DATE),
                   latitude, longitude, brightness_k, frp_mw, confidence, confidence_score,
                   daynight, nearest_city_id, distance_km, is_within_radius
            FROM fire_db.raw_fire_hotspot"""),
        (f"{p}fact_fire_daily", f"""
            SELECT city_id, event_date, COUNT(*), SUM(frp_mw), MAX(confidence_score), MIN(distance_km)
            FROM {p}fact_hotspot_event WHERE is_within_radius
            GROUP BY city_id, event_date"""),
        (f"{p}dim_date", f"""
            WITH b AS (SELECT
                LEAST((SELECT MIN(date) FROM {p}fact_weather_daily),
                      (SELECT MIN(date) FROM {p}fact_air_quality_daily),
                      (SELECT MIN(event_date) FROM {p}fact_hotspot_event)) AS lo,
                GREATEST((SELECT MAX(date) FROM {p}fact_weather_daily),
                         (SELECT MAX(date) FROM {p}fact_air_quality_daily),
                         (SELECT MAX(event_date) FROM {p}fact_hotspot_event)) AS hi)
            SELECT CAST(t.d AS DATE), year(t.d), month(t.d), strftime(t.d, '%Y-%m'), day(t.d),
                   ['Senin','Selasa','Rabu','Kamis','Jumat','Sabtu','Minggu'][isodow(t.d)],
                   isodow(t.d) >= 6
            FROM b, generate_series(b.lo, b.hi, INTERVAL 1 DAY) AS t(d)"""),
    ]


def view_daily_environment_sql(p):
    """
    View analisis lintas-fakta (tidak menyimpan salinan data).
    n_hotspot = NULL bila tanggal di luar rentang liputan data hotspot
    (supaya 'tidak ada data' tidak dikira 'nol kejadian').
    """
    return f"""
    CREATE OR REPLACE VIEW main.v_daily_environment AS
    WITH cov AS (SELECT MIN(event_date) AS lo, MAX(event_date) AS hi FROM {p}fact_hotspot_event)
    SELECT a.city_id, c.city_name, c.province, c.region, a.date,
           w.avg_temp, w.max_temp, w.min_temp, w.total_precipitation,
           w.avg_humidity, w.avg_wind_speed, w.avg_cloud_cover,
           a.avg_pm10, a.avg_pm25, a.avg_co, a.avg_no2, a.avg_so2, a.avg_ozone,
           a.avg_us_aqi, a.max_us_aqi, a.aqi_category,
           CASE WHEN f.n_hotspot IS NOT NULL THEN f.n_hotspot
                WHEN a.date BETWEEN cov.lo AND cov.hi THEN 0 END AS n_hotspot,
           f.total_frp_mw, f.max_confidence_score, f.min_distance_km
    FROM {p}fact_air_quality_daily a
    JOIN {p}dim_city c ON c.city_id = a.city_id
    LEFT JOIN {p}fact_weather_daily w ON w.city_id = a.city_id AND w.date = a.date
    LEFT JOIN {p}fact_fire_daily f ON f.city_id = a.city_id AND f.date = a.date
    CROSS JOIN cov"""


def attach_sources(con):
    """ATTACH 3 bebek sebagai weather_db, air_db, fire_db."""
    if is_motherduck():
        con.execute(f"SET motherduck_token='{MOTHERDUCK_TOKEN}'")
        con.execute("INSTALL motherduck; LOAD motherduck;")
    for source, alias in ATTACH_ALIAS.items():
        ro = "" if is_motherduck() else " (READ_ONLY)"
        con.execute(f"ATTACH '{source_uri(source)}' AS {alias}{ro}")


def refresh_mart(con, p, cities=None):
    """Membuat (bila belum ada) dan MENGISI ULANG mart dari 3 bebek. Return jml baris fact_air_quality_daily."""
    con.register("city_df", pd.DataFrame(cities or CITIES))
    for ddl in mart_ddl(p):
        con.execute(ddl)
    for target, select_sql in mart_load_sql(p):
        con.execute(f"DELETE FROM {target}")
        con.execute(f"INSERT INTO {target} {select_sql}")
    con.unregister("city_df")
    return con.execute(f"SELECT COUNT(*) FROM {p}fact_air_quality_daily").fetchone()[0]


# ---------------- DuckLake ----------------

def get_ducklake_connection():
    """Buka/buat DuckLake: katalog data/datmin_lake.ducklake + parquet di data/datmin_lake_files/."""
    catalog = _resolve_path(DUCKLAKE_CATALOG_PATH)
    data_path = _resolve_path(DUCKLAKE_DATA_PATH)
    os.makedirs(os.path.dirname(catalog), exist_ok=True)
    os.makedirs(data_path, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL ducklake")
    con.execute("LOAD ducklake")
    con.execute(f"ATTACH 'ducklake:{catalog}' AS {DUCKLAKE_ALIAS} (DATA_PATH '{data_path}')")
    return con


LAKE_PREFIX = f"{DUCKLAKE_ALIAS}.datmin."


def init_ducklake_schema(con):
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {DUCKLAKE_ALIAS}.datmin")
    for t in LEGACY_MART_TABLES:   # bersihkan tabel versi lama (duplikatif)
        con.execute(f"DROP TABLE IF EXISTS {LAKE_PREFIX}{t}")
    for ddl in mart_ddl(LAKE_PREFIX):
        con.execute(ddl)


def sync_to_ducklake(lake_con):
    """Isi ulang mart di DuckLake dari 3 bebek. Return jumlah baris fact_air_quality_daily."""
    attach_sources(lake_con)
    return refresh_mart(lake_con, LAKE_PREFIX)


_MART_CONNECTIONS = []   # koneksi mart yang sedang terbuka (agar bisa ditutup sebelum menulis)


def close_mart_connections():
    """Tutup semua koneksi mart. WAJIB dipanggil sebelum pipeline menulis ke file .duckdb,
    karena DuckDB tidak mengizinkan file yang sama dibuka dua kali dalam satu proses."""
    while _MART_CONNECTIONS:
        try:
            _MART_CONNECTIONS.pop().close()
        except Exception:  # noqa: BLE001
            pass


def get_mart_connection():
    """
    Koneksi untuk DASHBOARD. Return (con, prefix, label).
    1) Pakai DuckLake bila sudah ada & terisi.
    2) Jika tidak (ekstensi/katalog belum ada), bangun mart sementara di memori dari 3 bebek
       dengan SQL yang SAMA, lalu LEPAS (DETACH) ketiga bebek agar file tidak terkunci.
    View `v_daily_environment` selalu tersedia pada koneksi yang dikembalikan.
    """
    close_mart_connections()   # maksimal satu koneksi mart terbuka
    catalog = _resolve_path(DUCKLAKE_CATALOG_PATH)
    if os.path.exists(catalog):
        con = None
        try:
            con = get_ducklake_connection()
            con.execute(f"SELECT 1 FROM {LAKE_PREFIX}fact_air_quality_daily LIMIT 1")
            con.execute(view_daily_environment_sql(LAKE_PREFIX))
            _MART_CONNECTIONS.append(con)
            return con, LAKE_PREFIX, "DuckLake"
        except Exception:  # noqa: BLE001
            if con is not None:
                try:
                    con.close()
                except Exception:  # noqa: BLE001
                    pass
    con = duckdb.connect()
    attach_sources(con)
    con.execute("CREATE SCHEMA mart")
    refresh_mart(con, "mart.")
    con.execute(view_daily_environment_sql("mart."))
    for alias in ATTACH_ALIAS.values():     # mart sudah terisi di memori -> file sumber dilepas
        con.execute(f"DETACH {alias}")
    _MART_CONNECTIONS.append(con)
    return con, "mart.", "memori (DuckLake belum tersedia)"


# ---------------- MotherDuck ----------------

def push_to_motherduck():
    """Upload 3 bebek lokal ke MotherDuck sebagai 3 database terpisah."""
    if not MOTHERDUCK_TOKEN:
        raise RuntimeError("MOTHERDUCK_TOKEN belum di-set (env var).")
    con = duckdb.connect("md:")
    done = []
    for source in SOURCES:
        local = _resolve_path(LOCAL_PATHS[source])
        if os.path.exists(local):
            name = MOTHERDUCK_DBS[source]
            con.execute(f"CREATE OR REPLACE DATABASE {name} FROM '{local}'")
            done.append(name)
    con.close()
    return done
