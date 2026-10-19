"""
Audit kualitas data yang SUDAH tersimpan di 3 bebek: menjalankan aturan wrangling
(wrangle.py) pada isi tabel raw dan mencatat hasilnya ke dq_log (run_id = AUDIT-xxxx).
Berguna untuk data hasil run lama yang belum punya catatan dq_log.

    python -m datmin.audit
"""
import uuid

from .db import get_connection, init_schema, log_dq
from .wrangle import clean_air_quality, clean_fire, clean_weather

JOBS = [
    ("weather", "raw_weather_bmkg_hourly", "bmkg-cuaca", clean_weather),
    ("air_quality", "raw_air_quality_hourly", "open-meteo-air-quality", clean_air_quality),
    ("fire_hotspot", "raw_fire_hotspot", "nasa-firms", clean_fire),
]


def main():
    run_id = "AUDIT-" + uuid.uuid4().hex[:6]
    for source, table, label, cleaner in JOBS:
        con = get_connection(source)
        init_schema(con, source)
        rows = con.execute(f"SELECT * FROM {table}").df().to_dict("records")
        _, stats = cleaner(rows)
        log_dq(con, run_id, label, None, stats)
        print(f"{label:<24} {stats}")
        con.close()


if __name__ == "__main__":
    main()
