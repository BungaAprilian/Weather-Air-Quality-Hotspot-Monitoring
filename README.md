# Indonesia Environmental Data Warehouse

Data warehouse mini yang mengumpulkan **cuaca**, **kualitas udara**, dan **titik panas kebakaran**
untuk **15 kota besar Indonesia**, lalu menampilkannya di dashboard Streamlit (termasuk peta hotspot).

## Sumber data

| Domain | Sumber | "Bebek" (database) |
|---|---|---|
| Cuaca (prakiraan per 3 jam) | [BMKG](https://data.bmkg.go.id/prakiraan-cuaca/) | `weather.duckdb` |
| Kualitas udara (per jam) | [Open-Meteo](https://open-meteo.com/en/docs/air-quality-api) | `air_quality.duckdb` |
| Titik panas (satelit VIIRS) | [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) | `fire_hotspot.duckdb` |

## Arsitektur

```
BMKG / Open-Meteo / NASA FIRMS
        | scraping (Python requests) + WRANGLING (wrangle.py)
        v
weather.duckdb   air_quality.duckdb   fire_hotspot.duckdb      <- 1 bebek per sumber (raw, scrape_log, dq_log)
        \________________|________________/
                         v   ATTACH + SQL
        DuckLake: mart bintang (dim_city, dim_date, fact_weather_daily,
                  fact_air_quality_daily, fact_hotspot_event, fact_fire_daily)
                         |  (+ view v_daily_environment)
                         v
                 Streamlit dashboard         (opsional: MotherDuck = salinan cloud 3 bebek)
```

**Minim duplikasi:** dimensi hanya ada sekali (di mart); tabel fakta hanya menyimpan `city_id`;
tidak ada tabel gabungan fisik (analisis lintas-sumber memakai *view*); agregasi harian hanya dihitung di mart.

## Data wrangling (`datmin/wrangle.py`)

Standarisasi tipe -> validasi rentang (nilai mustahil jadi NULL) -> validasi kunci & wilayah ->
deduplikasi kunci alami -> imputasi celah pendek (interpolasi) -> normalisasi `confidence` FIRMS ->
penyelarasan zona waktu (hotspot UTC -> WIB di mart). Statistik tiap batch dicatat di tabel `dq_log`.

## Update otomatis

| Cara | Perintah |
|---|---|
| Scheduler Python | `python -m datmin.scheduler --every 180` |
| GitHub Actions (cron 6 jam) | `.github/workflows/update-data.yml` (isi secret `FIRMS_MAP_KEY`, opsional `MOTHERDUCK_TOKEN`) |
| Task Scheduler / cron | jadwalkan `python -m datmin.pipeline` |

## Menjalankan

```bash
python -m venv .venv
.venv\Scripts\Activate.ps1            # Windows PowerShell (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt

$env:FIRMS_MAP_KEY="map-key-anda"     # gratis di NASA FIRMS (untuk data hotspot baru)
python -m datmin.pipeline             # scrape + wrangling + refresh mart DuckLake
python -m datmin.pipeline --push-motherduck   # + upload ke MotherDuck (butuh MOTHERDUCK_TOKEN)
python -m datmin.audit                # audit kualitas data yang sudah tersimpan -> dq_log

streamlit run app/streamlit_app.py    # tutup dengan Ctrl + C
```

Database lama (`datmin.duckdb` satu file) bisa dipecah dengan `python -m datmin.migrate_legacy`.
Bebek versi lama otomatis di-upgrade (dim_city & view harian dihapus) saat pipeline dijalankan.

## Halaman dashboard

Ringkasan (peta & peringkat AQI, tren PM2.5) · Peta Hotspot · Analisis Korelasi · Arsitektur & Skema ·
Kualitas Data (Wrangling) · Data Warehouse Explorer (+SQL) · Skala & Update Otomatis.

## Batasan

- BMKG hanya menyediakan **prakiraan** ±3 hari (tanpa arsip publik); histori cuaca terbentuk dari akumulasi run.
- Hotspot = deteksi satelit, bukan konfirmasi kebakaran; FIRMS NRT hanya memuat beberapa hari terakhir.
- Korelasi bersifat deskriptif, bukan sebab-akibat. Zona waktu: cuaca BMKG waktu lokal, udara WIB, hotspot UTC (dikonversi ke WIB).

## Atribusi & lisensi

Data: **BMKG**, **Open-Meteo**, **NASA FIRMS / LANCE, EOSDIS**. Kode: lisensi MIT (lihat `LICENSE`).
