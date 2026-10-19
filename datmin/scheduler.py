"""
UPDATE OTOMATIS BERKESINAMBUNGAN.

Menjalankan pipeline berulang tiap N menit di proses yang sama
(cocok untuk server/laptop yang menyala). Alternatif tanpa proses
yang terus hidup: GitHub Actions (.github/workflows/update-data.yml)
atau Windows Task Scheduler / cron yang memanggil `python -m datmin.pipeline`.

    python -m datmin.scheduler --every 180          # tiap 3 jam, tanpa batas
    python -m datmin.scheduler --every 60 --runs 3  # 3 kali lalu berhenti
Hentikan dengan Ctrl + C.
"""
import argparse
import time
import traceback
from datetime import datetime

from .pipeline import run_pipeline


def main():
    p = argparse.ArgumentParser(description="Jalankan pipeline berkala")
    p.add_argument("--every", type=int, default=180, help="Interval antar run (menit)")
    p.add_argument("--runs", type=int, default=0, help="Jumlah run (0 = tanpa batas)")
    p.add_argument("--push-motherduck", action="store_true")
    a = p.parse_args()

    n = 0
    while True:
        n += 1
        print(f"\n===== RUN #{n} | {datetime.now():%Y-%m-%d %H:%M:%S} =====")
        try:
            run_pipeline(push_md=a.push_motherduck)
        except Exception:  # noqa: BLE001  -> satu run gagal tidak menghentikan jadwal
            traceback.print_exc()
        if a.runs and n >= a.runs:
            break
        print(f"Tidur {a.every} menit... (Ctrl+C untuk berhenti)")
        try:
            time.sleep(a.every * 60)
        except KeyboardInterrupt:
            print("Dihentikan.")
            break


if __name__ == "__main__":
    main()
