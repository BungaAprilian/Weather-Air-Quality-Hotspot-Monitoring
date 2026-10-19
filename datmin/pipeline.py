"""
Pipeline: SCRAPING (Python/requests) -> WRANGLING (wrangle.py) -> 3 DuckDB terpisah per sumber
          -> MART bintang di DuckLake -> (opsional) MotherDuck.

    python -m datmin.pipeline                        # scrape + sync DuckLake
    python -m datmin.pipeline --no-lake              # scrape saja
    python -m datmin.pipeline --push-motherduck      # + upload ke MotherDuck
    python -m datmin.pipeline --sync-only            # tanpa scraping
"""

import argparse
import time
import uuid
from datetime import datetime, timezone

from .config import CITIES, PAST_DAYS
from .db import (
    get_connection,
    get_ducklake_connection,
    init_ducklake_schema,
    init_schema,
    insert_air_quality_rows,
    insert_fire_hotspot_rows,
    insert_weather_rows,
    log_scrape,
    push_to_motherduck,
    sync_to_ducklake,
)
from .scrape_air_quality import fetch_air_quality
from .scrape_fire import fetch_fire_hotspots
from .scrape_weather import fetch_weather


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _scrape_per_city(con, run_id, source_name, tag, cities, fetch, insert, verbose):
    """Loop per kota untuk sumber per-kota. Log masuk ke DB sumber itu sendiri."""
    total = 0
    for city in cities:
        start = _now()
        try:
            rows = fetch(city)
            n = insert(con, rows, run_id)
            end = _now()
            log_scrape(con, run_id, source_name, city["city_id"], city["city_name"], start, end, n, "success" if n else "empty")
            total += n
            if verbose:
                print(f"[{tag}] {city['city_name']:<12} {n:>6} baris ({(end-start).total_seconds():.2f}s)")
        except Exception as e:  # noqa: BLE001
            end = _now()
            log_scrape(con, run_id, source_name, city["city_id"], city["city_name"], start, end, 0, "failed", str(e))
            if verbose:
                print(f"[{tag}] {city['city_name']:<12} GAGAL - {e}")
    return total


def sync_lake(verbose=True):
    """Refresh DuckLake dari 3 DB sumber. Return jumlah baris / None bila gagal."""
    try:
        lake = get_ducklake_connection()
        init_ducklake_schema(lake)
        n = sync_to_ducklake(lake)
        lake.close()
        if verbose:
            print(f"[ducklake] mart diperbarui ({n} baris fact_air_quality_daily)")
        return n
    except Exception as e:  # noqa: BLE001
        print(f"[ducklake] DILEWATI - {e}")
        return None


def run_pipeline(cities=None, past_days=None, verbose=True, sync_ducklake=True, push_md=False):
    cities = cities or CITIES
    past_days = past_days or PAST_DAYS

    weather_con = get_connection("weather")
    air_con = get_connection("air_quality")
    fire_con = get_connection("fire_hotspot")

    for con, src in ((weather_con, "weather"), (air_con, "air_quality"), (fire_con, "fire_hotspot")):
        init_schema(con, src)   # buat tabel / upgrade skema lama

    run_id = str(uuid.uuid4())[:8]
    t0 = time.time()
    total = 0

    # 1. BMKG -> weather.duckdb
    total += _scrape_per_city(
        weather_con, run_id, "bmkg-cuaca", "cuaca", cities,
        lambda c: fetch_weather(c, past_days), insert_weather_rows, verbose,
    )

    # 2. Open-Meteo -> air_quality.duckdb
    total += _scrape_per_city(
        air_con, run_id, "open-meteo-air-quality", "air  ", cities,
        lambda c: fetch_air_quality(c, past_days), insert_air_quality_rows, verbose,
    )

    # 3. NASA FIRMS -> fire_hotspot.duckdb (1 request untuk seluruh Indonesia)
    start = _now()
    try:
        n = insert_fire_hotspot_rows(fire_con, fetch_fire_hotspots(cities), run_id)
        end = _now()
        log_scrape(fire_con, run_id, "nasa-firms", None, "(seluruh Indonesia)", start, end, n, "success" if n else "empty")
        total += n
        if verbose:
            print(f"[api   ] {n} titik panas tersimpan ({(end-start).total_seconds():.2f}s)")
    except Exception as e:  # noqa: BLE001
        end = _now()
        log_scrape(fire_con, run_id, "nasa-firms", None, "(seluruh Indonesia)", start, end, 0, "failed", str(e))
        if verbose:
            print(f"[hotspot] GAGAL - {e}")

    elapsed = time.time() - t0
    for con in (weather_con, air_con, fire_con):
        con.close()

    # 4. DuckLake & MotherDuck (setelah semua koneksi tulis ditutup)
    if sync_ducklake:
        sync_lake(verbose)
    if push_md:
        try:
            names = push_to_motherduck()
            if verbose:
                print(f"[motherduck] terunggah: {', '.join(names)}")
        except Exception as e:  # noqa: BLE001
            print(f"[motherduck] GAGAL - {e}")

    if verbose:
        rate = total / elapsed if elapsed > 0 else 0
        print(f"\nSelesai. Total {total} baris dalam {elapsed:.2f} detik ({rate:.1f} baris/detik)")

    return {"run_id": run_id, "total_records": total, "elapsed_sec": elapsed, "n_cities": len(cities)}


def main():
    p = argparse.ArgumentParser(description="Scraping BMKG, Open-Meteo, NASA FIRMS -> DuckDB per sumber + DuckLake")
    p.add_argument("--past-days", type=int, default=PAST_DAYS, help="Histori kualitas udara (hanya Open-Meteo)")
    p.add_argument("--cities", type=int, default=None, help="Batasi jumlah kota")
    p.add_argument("--no-lake", action="store_true", help="Lewati sinkronisasi DuckLake")
    p.add_argument("--push-motherduck", action="store_true", help="Upload 3 DB ke MotherDuck setelah selesai")
    p.add_argument("--sync-only", action="store_true", help="Tanpa scraping: hanya sync DuckLake (+ MotherDuck)")
    a = p.parse_args()

    if a.sync_only:
        sync_lake()
        if a.push_motherduck:
            print("[motherduck] terunggah:", ", ".join(push_to_motherduck()))
        return

    cities = CITIES[: a.cities] if a.cities else CITIES
    run_pipeline(cities=cities, past_days=a.past_days, sync_ducklake=not a.no_lake, push_md=a.push_motherduck)


if __name__ == "__main__":
    main()
