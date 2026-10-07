"""
Penyimpanan & query (SQL via DuckDB).

Arsitektur: 1 database per SUMBER, lalu digabung di DuckLake.

    BMKG        -> weather.duckdb        (lokal)  | md:datmin_weather
    Open-Meteo  -> air_quality.duckdb    (lokal)  | md:datmin_air_quality
    NASA FIRMS  -> fire_hotspot.duckdb   (lokal)  | md:datmin_fire_hotspot
                         \\  |  //
                   DuckLake (daily_joined, hotspot, city)

Backend dipilih lewat env DATMIN_BACKEND (local | motherduck).
"""

import os

import duckdb
import pandas as pd

from .config import (
    WEATHER_DB_PATH,
    AIR_QUALITY_DB_PATH,
    FIRE_HOTSPOT_DB_PATH,
    DUCKLAKE_CATALOG_PATH,
    DUCKLAKE_DATA_PATH,
    DUCKLAKE_ALIAS,
    MOTHERDUCK_TOKEN,
    MOTHERDUCK_DBS,
    DATMIN_BACKEND,
)

SOURCES = ("weather", "air_quality", "fire_hotspot")

LOCAL_PATHS = {
    "weather": WEATHER_DB_PATH,
    "air_quality": AIR_QUALITY_DB_PATH,
    "fire_hotspot": FIRE_HOTSPOT_DB_PATH,
}

_CITY_DDL = """
CREATE TABLE IF NOT EXISTS dim_city (
    city_id INTEGER PRIMARY KEY,
    city_name VARCHAR,
    province VARCHAR,
    latitude DOUBLE,
    longitude DOUBLE
);
"""

_SCRAPE_LOG_DDL = """
CREATE TABLE IF NOT EXISTS scrape_log (
    run_id VARCHAR,
    source VARCHAR,
    city_id INTEGER,
    city_name VARCHAR,
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    duration_sec DOUBLE,
    n_records INTEGER,
    status VARCHAR,
    error_message VARCHAR
);
"""

SCHEMAS = {
    "weather": _CITY_DDL + """
CREATE TABLE IF NOT EXISTS raw_weather_bmkg_hourly (
    city_id INTEGER,
    ts TIMESTAMP,
    temperature_c DOUBLE,
    humidity_pct DOUBLE,
    precipitation_mm DOUBLE,
    wind_speed_kmh DOUBLE,
    wind_dir_deg DOUBLE,
    cloud_cover_pct DOUBLE,
    weather_code INTEGER,
    weather_desc VARCHAR,
    weather_desc_en VARCHAR,
    visibility_m DOUBLE,
    analysis_date VARCHAR,
    scraped_at TIMESTAMP
);
""" + _SCRAPE_LOG_DDL,
    "air_quality": _CITY_DDL + """
CREATE TABLE IF NOT EXISTS raw_air_quality_hourly (
    city_id INTEGER,
    ts TIMESTAMP,
    pm10 DOUBLE,
    pm2_5 DOUBLE,
    carbon_monoxide DOUBLE,
    nitrogen_dioxide DOUBLE,
    sulphur_dioxide DOUBLE,
    ozone DOUBLE,
    us_aqi DOUBLE,
    scraped_at TIMESTAMP
);
""" + _SCRAPE_LOG_DDL,
    "fire_hotspot": _CITY_DDL + """
CREATE TABLE IF NOT EXISTS raw_fire_hotspot (
    event_datetime TIMESTAMP,
    latitude DOUBLE,
    longitude DOUBLE,
    brightness_k DOUBLE,
    frp_mw DOUBLE,
    confidence VARCHAR,
    confidence_score DOUBLE,
    satellite VARCHAR,
    daynight VARCHAR,
    nearest_city_id INTEGER,
    nearest_city_name VARCHAR,
    distance_km DOUBLE,
    is_within_radius BOOLEAN,
    scraped_at TIMESTAMP
);
""" + _SCRAPE_LOG_DDL,
}

VIEWS = {
    "weather": """
CREATE OR REPLACE VIEW fact_weather_daily AS
SELECT city_id, CAST(ts AS DATE) AS date,
       AVG(temperature_c) AS avg_temp, MAX(temperature_c) AS max_temp,
       MIN(temperature_c) AS min_temp, SUM(precipitation_mm) AS total_precipitation,
       AVG(humidity_pct) AS avg_humidity, AVG(wind_speed_kmh) AS avg_wind_speed,
       AVG(cloud_cover_pct) AS avg_cloud_cover
FROM raw_weather_bmkg_hourly
GROUP BY city_id, CAST(ts AS DATE)
""",
    "air_quality": """
CREATE OR REPLACE VIEW fact_air_quality_daily AS
SELECT city_id, CAST(ts AS DATE) AS date,
       AVG(pm10) AS avg_pm10, AVG(pm2_5) AS avg_pm25,
       AVG(carbon_monoxide) AS avg_co, AVG(nitrogen_dioxide) AS avg_no2,
       AVG(sulphur_dioxide) AS avg_so2, AVG(ozone) AS avg_ozone,
       AVG(us_aqi) AS avg_us_aqi
FROM raw_air_quality_hourly
GROUP BY city_id, CAST(ts AS DATE)
""",
    "fire_hotspot": """
CREATE OR REPLACE VIEW fact_fire_daily AS
SELECT nearest_city_id AS city_id, CAST(event_datetime AS DATE) AS date,
       COUNT(*) AS n_hotspot, SUM(frp_mw) AS total_frp_mw,
       MAX(confidence_score) AS max_confidence_score,
       MIN(distance_km) AS min_distance_km
FROM raw_fire_hotspot
WHERE is_within_radius
GROUP BY nearest_city_id, CAST(event_datetime AS DATE)
""",
}


# ============================================================
# PATH & KONEKSI
# ============================================================

def _resolve_path(path):
    """Path relatif -> relatif terhadap root project."""
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
    """URI untuk duckdb.connect()/ATTACH sesuai backend."""
    _check_source(source)
    if is_motherduck():
        return f"md:{MOTHERDUCK_DBS[source]}"
    return _resolve_path(LOCAL_PATHS[source])


def get_connection(source="weather", read_only=False):
    """Koneksi ke database SATU sumber (weather | air_quality | fire_hotspot)."""
    _check_source(source)
    if is_motherduck():
        if not MOTHERDUCK_TOKEN:
            raise RuntimeError("MOTHERDUCK_TOKEN belum di-set (env var).")
        # database dibuat dulu bila belum ada
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
    """Buat tabel + view harian untuk satu sumber."""
    _check_source(source)
    con.execute(SCHEMAS[source])
    con.execute(VIEWS[source])


def get_analytics_connection():
    """
    Koneksi in-memory READ-ONLY yang meng-ATTACH ketiga database sumber
    (weather_db, air_db, fire_db) dan membuat view lintas-sumber
    `fact_daily_joined`. Dipakai dashboard supaya tiap sumber tetap
    terpisah di penyimpanan, tapi bisa di-join saat dianalisis.
    """
    con = duckdb.connect()
    if is_motherduck():
        con.execute(f"SET motherduck_token='{MOTHERDUCK_TOKEN}'")
        con.execute("INSTALL motherduck; LOAD motherduck;")
    for alias, source in (("weather_db", "weather"), ("air_db", "air_quality"), ("fire_db", "fire_hotspot")):
        ro = "" if is_motherduck() else " (READ_ONLY)"
        con.execute(f"ATTACH '{source_uri(source)}' AS {alias}{ro}")
    con.execute(JOINED_VIEW_SQL)
    return con


JOINED_SELECT = """
SELECT
    w.city_id, c.city_name, c.province, w.date,
    w.avg_temp, w.max_temp, w.min_temp, w.total_precipitation,
    w.avg_humidity, w.avg_wind_speed, w.avg_cloud_cover,
    a.avg_pm10, a.avg_pm25, a.avg_co, a.avg_no2, a.avg_so2,
    a.avg_ozone, a.avg_us_aqi,
    COALESCE(f.n_hotspot, 0) AS n_hotspot,
    f.total_frp_mw, f.max_confidence_score, f.min_distance_km
FROM weather_db.fact_weather_daily w
JOIN weather_db.dim_city c ON c.city_id = w.city_id
LEFT JOIN air_db.fact_air_quality_daily a
       ON a.city_id = w.city_id AND a.date = w.date
LEFT JOIN fire_db.fact_fire_daily f
       ON f.city_id = w.city_id AND f.date = w.date
"""

JOINED_VIEW_SQL = "CREATE OR REPLACE TEMP VIEW fact_daily_joined AS " + JOINED_SELECT


# ============================================================
# DUCKLAKE  (gabungan 3 sumber)
# ============================================================

def get_ducklake_connection():
    """
    Membuka (atau membuat) DuckLake lokal:
      katalog metadata : data/datmin_lake.ducklake
      file parquet     : data/datmin_lake_files/
    Butuh extension `ducklake` (di-download otomatis saat pertama kali,
    perlu internet).
    """
    catalog = _resolve_path(DUCKLAKE_CATALOG_PATH)
    data_path = _resolve_path(DUCKLAKE_DATA_PATH)
    os.makedirs(os.path.dirname(catalog), exist_ok=True)
    os.makedirs(data_path, exist_ok=True)

    con = duckdb.connect()
    con.execute("INSTALL ducklake")
    con.execute("LOAD ducklake")
    con.execute(
        f"ATTACH 'ducklake:{catalog}' AS {DUCKLAKE_ALIAS} "
        f"(DATA_PATH '{data_path}')"
    )
    return con


def init_ducklake_schema(con):
    """Tabel analitik di DuckLake (schema `datmin`)."""
    a = DUCKLAKE_ALIAS
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {a}.datmin")
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {a}.datmin.city (
            city_id INTEGER, city_name VARCHAR, province VARCHAR,
            latitude DOUBLE, longitude DOUBLE
        )""")
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {a}.datmin.daily_joined (
            city_id INTEGER, city_name VARCHAR, province VARCHAR, date DATE,
            avg_temp DOUBLE, max_temp DOUBLE, min_temp DOUBLE,
            total_precipitation DOUBLE, avg_humidity DOUBLE,
            avg_wind_speed DOUBLE, avg_cloud_cover DOUBLE,
            avg_pm10 DOUBLE, avg_pm25 DOUBLE, avg_co DOUBLE, avg_no2 DOUBLE,
            avg_so2 DOUBLE, avg_ozone DOUBLE, avg_us_aqi DOUBLE,
            n_hotspot BIGINT, total_frp_mw DOUBLE,
            max_confidence_score DOUBLE, min_distance_km DOUBLE
        )""")
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {a}.datmin.fire_hotspot (
            event_datetime TIMESTAMP, latitude DOUBLE, longitude DOUBLE,
            brightness_k DOUBLE, frp_mw DOUBLE, confidence VARCHAR,
            confidence_score DOUBLE, satellite VARCHAR, daynight VARCHAR,
            nearest_city_id INTEGER, nearest_city_name VARCHAR,
            distance_km DOUBLE, is_within_radius BOOLEAN
        )""")


def sync_to_ducklake(lake_con):
    """
    Membaca 3 database sumber (READ_ONLY), menggabungkannya, lalu
    me-refresh tabel DuckLake: city, daily_joined, fire_hotspot.
    Return: jumlah baris daily_joined.
    """
    a = DUCKLAKE_ALIAS
    for alias, source in (("weather_db", "weather"), ("air_db", "air_quality"), ("fire_db", "fire_hotspot")):
        ro = "" if is_motherduck() else " (READ_ONLY)"
        lake_con.execute(f"ATTACH '{source_uri(source)}' AS {alias}{ro}")

    lake_con.execute(f"DELETE FROM {a}.datmin.city")
    lake_con.execute(f"INSERT INTO {a}.datmin.city SELECT * FROM weather_db.dim_city")

    lake_con.execute(f"DELETE FROM {a}.datmin.daily_joined")
    lake_con.execute(f"INSERT INTO {a}.datmin.daily_joined {JOINED_SELECT}")

    lake_con.execute(f"DELETE FROM {a}.datmin.fire_hotspot")
    lake_con.execute(f"""
        INSERT INTO {a}.datmin.fire_hotspot
        SELECT event_datetime, latitude, longitude, brightness_k, frp_mw,
               confidence, confidence_score, satellite, daynight,
               nearest_city_id, nearest_city_name, distance_km, is_within_radius
        FROM fire_db.raw_fire_hotspot""")

    return lake_con.execute(f"SELECT COUNT(*) FROM {a}.datmin.daily_joined").fetchone()[0]


# ============================================================
# MOTHERDUCK  (unggah 3 database lokal ke cloud)
# ============================================================

def push_to_motherduck():
    """
    Meng-upload 3 file .duckdb lokal ke MotherDuck sebagai 3 database
    terpisah (datmin_weather, datmin_air_quality, datmin_fire_hotspot).
    """
    if not MOTHERDUCK_TOKEN:
        raise RuntimeError("MOTHERDUCK_TOKEN belum di-set (env var).")
    con = duckdb.connect("md:")
    done = []
    for source in SOURCES:
        local = _resolve_path(LOCAL_PATHS[source])
        if not os.path.exists(local):
            continue
        name = MOTHERDUCK_DBS[source]
        con.execute(f"CREATE OR REPLACE DATABASE {name} FROM '{local}'")
        done.append(name)
    con.close()
    return done


# ============================================================
# DIM CITY
# ============================================================

def upsert_cities(con, cities):
    """Memasukkan atau memperbarui data kota."""

    for city in cities:
        con.execute(
            """
            INSERT INTO dim_city (
                city_id,
                city_name,
                province,
                latitude,
                longitude
            )
            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT (city_id)
            DO UPDATE SET
                city_name = excluded.city_name,
                province = excluded.province,
                latitude = excluded.latitude,
                longitude = excluded.longitude
            """,
            [
                city["city_id"],
                city["city_name"],
                city["province"],
                city["latitude"],
                city["longitude"],
            ],
        )


# ============================================================
# HAPUS DATA LAMA BERDASARKAN RENTANG
# ============================================================

def _delete_existing_range(
    con,
    table,
    city_id,
    min_ts,
    max_ts,
):
    """
    Menghapus data lama pada rentang waktu yang sama
    sebelum data baru dimasukkan.
    """

    con.execute(
        f"""
        DELETE FROM {table}
        WHERE city_id = ?
          AND ts BETWEEN ? AND ?
        """,
        [
            city_id,
            min_ts,
            max_ts,
        ],
    )


# ============================================================
# INSERT WEATHER
# ============================================================

def insert_weather_rows(con, rows):
    """Memasukkan data cuaca BMKG."""

    if not rows:
        return 0

    df = pd.DataFrame(rows)

    df["ts"] = pd.to_datetime(
        df["ts"]
    )

    df["scraped_at"] = (
        pd.Timestamp.utcnow()
        .tz_localize(None)
    )

    city_id = int(
        df["city_id"].iloc[0]
    )

    _delete_existing_range(
        con,
        "raw_weather_bmkg_hourly",
        city_id,
        df["ts"].min(),
        df["ts"].max(),
    )

    con.register(
        "df_weather_tmp",
        df,
    )

    con.execute(
        """
        INSERT INTO raw_weather_bmkg_hourly
        SELECT
            city_id,
            ts,
            temperature_c,
            humidity_pct,
            precipitation_mm,
            wind_speed_kmh,
            wind_dir_deg,
            cloud_cover_pct,
            weather_code,
            weather_desc,
            weather_desc_en,
            visibility_m,
            analysis_date,
            scraped_at
        FROM df_weather_tmp
        """
    )

    con.unregister(
        "df_weather_tmp"
    )

    return len(df)


# ============================================================
# INSERT AIR QUALITY
# ============================================================

def insert_air_quality_rows(con, rows):
    """Memasukkan data kualitas udara Open-Meteo."""

    if not rows:
        return 0

    df = pd.DataFrame(rows)

    df["ts"] = pd.to_datetime(
        df["ts"]
    )

    df["scraped_at"] = (
        pd.Timestamp.utcnow()
        .tz_localize(None)
    )

    city_id = int(
        df["city_id"].iloc[0]
    )

    _delete_existing_range(
        con,
        "raw_air_quality_hourly",
        city_id,
        df["ts"].min(),
        df["ts"].max(),
    )

    con.register(
        "df_air_tmp",
        df,
    )

    con.execute(
        """
        INSERT INTO raw_air_quality_hourly
        SELECT
            city_id,
            ts,
            pm10,
            pm2_5,
            carbon_monoxide,
            nitrogen_dioxide,
            sulphur_dioxide,
            ozone,
            us_aqi,
            scraped_at
        FROM df_air_tmp
        """
    )

    con.unregister(
        "df_air_tmp"
    )

    return len(df)


# ============================================================
# INSERT FIRE HOTSPOT
# ============================================================

def insert_fire_hotspot_rows(con, rows):
    """
    Memasukkan titik panas NASA FIRMS.

    Dedup berdasarkan:
    event_datetime + latitude + longitude.
    """

    if not rows:
        return 0

    df = pd.DataFrame(rows)

    df = df.dropna(
        subset=["event_datetime"]
    )

    if df.empty:
        return 0

    df["scraped_at"] = (
        pd.Timestamp.utcnow()
        .tz_localize(None)
    )

    con.register(
        "df_fire_tmp",
        df,
    )

    con.execute(
        """
        DELETE FROM raw_fire_hotspot
        WHERE (event_datetime, latitude, longitude) IN (
            SELECT
                event_datetime,
                latitude,
                longitude
            FROM df_fire_tmp
        )
        """
    )

    con.execute(
        """
        INSERT INTO raw_fire_hotspot
        SELECT
            event_datetime,
            latitude,
            longitude,
            brightness_k,
            frp_mw,
            confidence,
            confidence_score,
            satellite,
            daynight,
            nearest_city_id,
            nearest_city_name,
            distance_km,
            is_within_radius,
            scraped_at
        FROM df_fire_tmp
        """
    )

    con.unregister(
        "df_fire_tmp"
    )

    return len(df)


# ============================================================
# LOG SCRAPING
# ============================================================

def log_scrape(
    con,
    run_id,
    source,
    city_id,
    city_name,
    start_time,
    end_time,
    n_records,
    status,
    error_message=None,
):
    """Menyimpan log proses scraping."""

    duration = (
        end_time - start_time
    ).total_seconds()

    con.execute(
        """
        INSERT INTO scrape_log
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run_id,
            source,
            city_id,
            city_name,
            start_time,
            end_time,
            duration,
            n_records,
            status,
            error_message,
        ],
    )