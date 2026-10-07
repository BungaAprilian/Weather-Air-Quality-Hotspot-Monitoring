"""
Memecah database lama `data/datmin.duckdb` (satu file berisi semua sumber)
menjadi 3 database terpisah per sumber:

    weather.duckdb  <- raw_weather_bmkg_hourly + scrape_log (bmkg-cuaca)
    air_quality.duckdb <- raw_air_quality_hourly + scrape_log (open-meteo)
    fire_hotspot.duckdb <- raw_fire_hotspot + scrape_log (nasa-firms)

Aman dijalankan ulang (isi tabel di-reset dulu). File lama TIDAK diubah.

    python -m datmin.migrate_legacy
"""
import duckdb

from .db import SOURCES, _resolve_path, get_connection, init_schema

LEGACY = "data/datmin.duckdb"

PLAN = {
    "weather": [("raw_weather_bmkg_hourly", None)],
    "air_quality": [("raw_air_quality_hourly", None)],
    "fire_hotspot": [("raw_fire_hotspot", None)],
}
LOG_SOURCE = {"weather": "bmkg-cuaca", "air_quality": "open-meteo-air-quality", "fire_hotspot": "nasa-firms"}


def main():
    legacy = _resolve_path(LEGACY)
    for source in SOURCES:
        con = get_connection(source)
        init_schema(con, source)
        con.execute(f"ATTACH '{legacy}' AS legacy (READ_ONLY)")
        con.execute("DELETE FROM dim_city")
        con.execute("INSERT INTO dim_city SELECT * FROM legacy.dim_city")
        con.execute("DELETE FROM scrape_log")
        con.execute("INSERT INTO scrape_log SELECT * FROM legacy.scrape_log WHERE source = ?", [LOG_SOURCE[source]])
        for table, _ in PLAN[source]:
            con.execute(f"DELETE FROM {table}")
            con.execute(f"INSERT INTO {table} SELECT * FROM legacy.{table}")
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"{source:<13} {table:<26} {n:>7} baris")
        con.execute("DETACH legacy")
        con.close()


if __name__ == "__main__":
    main()
