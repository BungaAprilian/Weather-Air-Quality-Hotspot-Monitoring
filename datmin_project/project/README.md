# Datmin: Data Warehouse Cuaca, Kualitas Udara & Titik Panas Kebakaran (DuckDB + Streamlit)

Project data warehouse mini yang menguji seberapa besar skala data yang
bisa diproses dari scraping **3 sumber API/feed berbeda**, menyimpan &
agregasi di DuckDB, dan menampilkan di Streamlit.

---

## 1. Tujuan, dan Topik

**Topik:** hubungan antara cuaca (suhu, curah hujan, kelembapan, angin,
tutupan awan), kualitas udara (PM10, PM2.5, CO, NO2, SO2, ozon, AQI), dan
titik panas kebakaran hutan/lahan (karhutla) di kota-kota besar Indonesia.

**Tujuan:**
1. Membangun pipeline data end-to-end: *scraping* 3 sumber (Python/HTTP) →
   *storage + transform* (DuckDB/SQL) → *presentation* (Streamlit).
2. Memadukan **3 sumber data heterogen** (2 sumber pengukuran per-jam +
   1 sumber kejadian diskrit) pada 15 kota besar yang sama, termasuk
   teknik pemaduan yang berbeda-beda: join per kota+tanggal (cuaca x
   kualitas udara) dan pencocokan jarak-terdekat/haversine (titik panas
   kebakaran x kota).
3. Mengukur skalabilitas: berapa banyak data (jumlah kota × jumlah hari ×
   variabel per jam) yang bisa diambil, disimpan, dan ditampilkan, serta
   berapa lama waktu prosesnya (throughput baris/detik) — semua ini tercatat
   otomatis di tabel `scrape_log` dan bisa dilihat di dashboard.
4. Sebagai studi kasus tambahan: melihat apakah ada korelasi antara
   variabel cuaca dan tingkat polusi udara (mis. apakah hari dengan curah
   hujan tinggi punya PM2.5 lebih rendah), serta melihat kejadian
   kebakaran hutan/lahan yang paling dekat dengan tiap kota sebagai
   konteks tambahan (relevan untuk musim kemarau & kabut asap di
   Indonesia).

Topik ini dipilih sebagai alternatif dari contoh "curah hujan vs pertanian",
dengan pola analisis yang sama (dataset lingkungan yang mungkin
berkorelasi) tapi sumber data berbeda, lebih mudah diskalakan (banyak
kota, resolusi per jam), dan memadukan lebih dari 2 sumber sekaligus.

---

## 2. Sumber Data & Variabel

Tiga sumber data dipadukan pada 15 kota besar Indonesia yang sama. Dua
sumber pertama gratis tanpa API key sama sekali; sumber ketiga gratis
tapi perlu token akses (MAP_KEY) yang didaftarkan sendiri secara instan:

| # | Domain | Sumber | Endpoint | Resolusi | Variabel |
|---|---|---|---|---|---|
| 1 | Cuaca | **BMKG** (resmi) | `https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4=<kode>` | per 3 jam, prakiraan 3 hari ke depan | `t` (suhu °C), `hu` (kelembapan %), `tp` (curah hujan mm), `ws`/`wd_deg` (kecepatan/arah angin), `tcc` (tutupan awan %), `weather_desc` (deskripsi cuaca) |
| 2 | Kualitas udara | **Open-Meteo** | `https://air-quality-api.open-meteo.com/v1/air-quality` | per jam, histori hingga 92 hari | `pm10`, `pm2_5` (µg/m³), `carbon_monoxide`, `nitrogen_dioxide`, `sulphur_dioxide` (µg/m³), `ozone` (µg/m³), `us_aqi` (indeks) |
| 3 | Titik panas kebakaran | **NASA FIRMS** (bukan BMKG/Open-Meteo) | `https://firms.modaps.eosdis.nasa.gov/api/area/csv/<MAP_KEY>/VIIRS_SNPP_NRT/<bbox>/<hari>` | per kejadian (deteksi satelit VIIRS, near-real-time) | koordinat titik panas, `bright_ti4` (kecerahan K), `frp` (*fire radiative power*, MW), `confidence` (keyakinan deteksi: l/n/h), dipetakan ke kota terdekat (haversine) |

**Cakupan default:** 15 kota besar Indonesia (Jakarta, Bandung, Surabaya,
Semarang, Yogyakarta, Medan, Palembang, Makassar, Denpasar, Balikpapan,
Manado, Pontianak, Banda Aceh, Jayapura, Malang) — bisa diperluas/dikurangi
di `datmin/config.py`.

**Cara memadukan tiap sumber ke 15 kota yang sama:**
- **Kualitas udara (Open-Meteo):** query langsung per titik koordinat
  (lat/lon) tiap kota — sama seperti sebelumnya.
- **Cuaca (BMKG):** BMKG hanya punya endpoint per kelurahan/desa (kode
  wilayah tingkat IV / `adm4`), bukan per kota. Untuk tiap kota dipilih
  **satu kelurahan representatif** (kelurahan pertama pada kecamatan
  pertama di kota tersebut), diambil dari dataset resmi kode wilayah
  (Kepmendagri No 300.2.2-2138/2025). Kode ini sudah diisi di
  `datmin/config.py` (field `bmkg_adm4`); cara mereproduksi/memverifikasi
  ada di `datmin/resolve_bmkg_adm4.py`.
- **Titik panas kebakaran (NASA FIRMS):** satu kali query per run dengan
  *bounding box* seluruh Indonesia (bukan per kota, supaya hemat kuota
  API), lalu tiap titik panas yang terdeteksi dicocokkan ke kota terdekat
  memakai jarak **haversine** (`datmin/geo_utils.py`), dan ditandai
  `is_within_radius` bila jaraknya ≤ `FIRE_MAX_DISTANCE_KM` (default
  100 km) supaya titik panas yang jauh dari semua kota (mis. di tengah
  hutan Kalimantan yang jauh dari 15 kota ini) tidak menyesatkan analisis.

**Kenapa NASA FIRMS, bukan BMKG lagi untuk sumber ke-3?**
Supaya benar-benar 3 *provider* berbeda (bukan 2 dataset dari provider
yang sama), sekaligus data ini relevan untuk konteks Indonesia (karhutla
& kabut asap yang berdampak langsung ke kualitas udara). NASA FIRMS
memerlukan **MAP_KEY** gratis (didaftarkan sendiri, instan, cukup email,
tanpa proses approval) — lihat bagian [Cara Menjalankan](#5-cara-menjalankan).

**Perbedaan penting pola histori antar-sumber:**
- Kualitas udara (Open-Meteo) punya arsip historis publik → volume data
  bisa diperbesar langsung lewat parameter `past_days` (7–92 hari).
- Cuaca (BMKG) **tidak** punya arsip historis publik, hanya prakiraan 3
  hari ke depan → `past_days` diabaikan untuk sumber ini; volume datanya
  bertambah lewat **akumulasi tiap kali pipeline dijalankan ulang**
  (setiap run menambah snapshot prakiraan baru).
- Titik panas kebakaran (NASA FIRMS) juga berupa feed "terkini" (bukan
  arsip dalam) → volume bertambah lewat akumulasi run, dengan dedup
  otomatis berdasarkan waktu+koordinat kejadian (bukan berdasarkan
  rentang tanggal seperti data per jam).

---

## 3. Arsitektur & Jenis Database

```
BMKG        -> data/weather.duckdb       --\
Open-Meteo  -> data/air_quality.duckdb   ----> DuckLake: data/datmin_lake.ducklake
NASA FIRMS  -> data/fire_hotspot.duckdb  --/    (tabel datmin.daily_joined, fire_hotspot, city)
      \__ (opsional) upload ke MotherDuck: datmin_weather, datmin_air_quality, datmin_fire_hotspot
                                   |
                               Streamlit
```

**Satu database per sumber** (tiap file punya `dim_city`, `scrape_log`, tabel raw, dan view harian sendiri):

| File DuckDB | Sumber | Tabel raw | View harian |
|---|---|---|---|
| `weather.duckdb` | BMKG | `raw_weather_bmkg_hourly` | `fact_weather_daily` |
| `air_quality.duckdb` | Open-Meteo | `raw_air_quality_hourly` | `fact_air_quality_daily` |
| `fire_hotspot.duckdb` | NASA FIRMS | `raw_fire_hotspot` | `fact_fire_daily` |

**DuckLake** (`data/datmin_lake.ducklake` + folder `data/datmin_lake_files/`) menggabungkan
ketiganya: `datmin.daily_joined` (cuaca LEFT JOIN kualitas udara LEFT JOIN titik panas, per kota+tanggal),
`datmin.fire_hotspot`, dan `datmin.city`. Di-refresh setiap pipeline selesai (atau `--sync-only`).

**MotherDuck** = versi cloud dari ketiga database sumber.
- Upload: `export MOTHERDUCK_TOKEN=...` lalu `python -m datmin.pipeline --push-motherduck`
- Jalankan seluruh pipeline/dashboard langsung di cloud: `export DATMIN_BACKEND=motherduck`

**Pembagian peran:** scraping = Python (`requests`); DuckDB = penyimpanan + SQL; Streamlit hanya membaca
(dashboard meng-ATTACH ketiga database untuk membuat view lintas-sumber `fact_daily_joined`).

---

## 4. Struktur Folder

```
datmin_project/
├── README.md
├── requirements.txt
├── run.sh
├── datmin/
│   ├── config.py               # kota, endpoint, path DB per sumber, DuckLake, MotherDuck
│   ├── db.py                   # schema per sumber, koneksi (lokal/MotherDuck), DuckLake, push MotherDuck
│   ├── pipeline.py             # scraping 3 sumber -> 3 DuckDB -> DuckLake (-> MotherDuck)
│   ├── migrate_legacy.py       # memecah datmin.duckdb lama menjadi 3 DB per sumber
│   ├── scrape_weather.py / scrape_air_quality.py / scrape_fire.py
│   └── http_utils.py, geo_utils.py, resolve_bmkg_adm4.py
├── app/streamlit_app.py        # dashboard (5 halaman, termasuk Peta Hotspot)
└── data/
    ├── weather.duckdb, air_quality.duckdb, fire_hotspot.duckdb
    ├── datmin.duckdb           # database lama (cadangan, tidak dipakai lagi)
    └── datmin_lake.ducklake    # dibuat otomatis oleh pipeline (+ datmin_lake_files/)
```

---

## 5. Cara Menjalankan

```bash
# 1. Install dependency
pip install -r requirements.txt

# 2. Daftar MAP_KEY gratis untuk NASA FIRMS (sekali saja, instan):
#    https://firms.modaps.eosdis.nasa.gov/api/map_key/ -> isi email -> MAP_KEY dikirim ke email
export FIRMS_MAP_KEY="isi-dengan-map-key-kamu"

# 3. (Sekali saja, bila masih punya data lama di data/datmin.duckdb)
python -m datmin.migrate_legacy               # pecah jadi 3 database per sumber

# 4. Scraping dari terminal
python -m datmin.pipeline                     # 15 kota, 60 hari, lalu sync DuckLake
python -m datmin.pipeline --push-motherduck   # + upload ke MotherDuck (butuh MOTHERDUCK_TOKEN)
python -m datmin.pipeline --sync-only         # hanya refresh DuckLake

# 5. Jalankan dashboard
streamlit run app/streamlit_app.py

# Atau langsung keduanya:
bash run.sh
```

Jika `data/datmin.duckdb` belum ada, dashboard akan menampilkan tombol
untuk menjalankan scraping pertama kali langsung dari UI. Jika
`FIRMS_MAP_KEY` belum diisi, scraping cuaca & kualitas udara tetap
berjalan normal; hanya bagian titik panas kebakaran yang akan tercatat
"failed" di `scrape_log` dengan pesan yang jelas.

> Wajib mencantumkan **BMKG** sebagai sumber data cuaca, **Open-Meteo**
> untuk kualitas udara, dan **NASA FIRMS/LANCE, EOSDIS** untuk titik
> panas kebakaran (syarat penggunaan data terbuka masing-masing).

---

## 6. Cara Uji Skala

Buka menu **"Skala & Performa Scraping"** di dashboard, lalu:
1. Atur jumlah kota (1–15) dan jumlah hari histori kualitas udara (7–92)
   dengan slider (khusus BMKG & NASA FIRMS, jumlah baris per run relatif
   tetap karena sifatnya prakiraan/deteksi terkini, bukan histori —
   jalankan pipeline beberapa kali untuk melihat akumulasinya).
2. Klik **"Jalankan Scraping Sekarang"**.
3. Dashboard menampilkan jumlah baris sebelum/sesudah, total durasi, dan
   throughput (baris/detik) — ini yang membuktikan seberapa besar skala
   data yang mampu diproses pipeline ini dari 3 sumber sekaligus,
   sekaligus menunjukkan sifat dinamis dari data warehouse (volume data
   terus bertambah).

---

## 7. Halaman Dashboard

| Halaman | Isi |
|---|---|
| Tentang Project | Penjelasan topik, tujuan, 3 sumber data, arsitektur |
| Data Warehouse Explorer | Pilih database (weather / air_quality / fire_hotspot / joined / DuckLake), lihat tabel + SQL Playground |
| Peta Hotspot | Peta interaktif titik panas NASA FIRMS + 15 kota + lingkaran radius; filter tanggal, keyakinan, kota |
| Analisis Korelasi | Scatter plot + garis tren + matriks korelasi cuaca vs kualitas udara, plus tabel konteks titik panas kebakaran per kota/hari |
| Skala & Performa Scraping | Statistik volume data (3 sumber), log gabungan dari 3 database tiap proses scraping, tombol tambah data |

---

## 8. Catatan Teknis

- Endpoint BMKG & Open-Meteo gratis dan tidak butuh API key. NASA FIRMS
  gratis tapi butuh `MAP_KEY` (lihat bagian 5); jangan hardcode MAP_KEY
  langsung di `config.py` kalau repo bersifat publik — gunakan env var
  `FIRMS_MAP_KEY`. Tetap hormati rate-limit wajar di ketiganya (jeda
  antar request sudah ditangani lewat retry otomatis di `http_utils.py`).
- Extension `ducklake` dan `motherduck` di-download otomatis oleh DuckDB saat pertama dipakai (perlu internet).
- DuckDB hanya mendukung satu koneksi *read-write* aktif pada satu waktu;
  dashboard membuka koneksi *read-only* singkat untuk tiap query agar tidak
  bentrok dengan proses scraping.
- Korelasi yang ditampilkan bersifat **deskriptif/statistik**, bukan bukti
  hubungan sebab-akibat — berlaku juga untuk asosiasi titik panas
  kebakaran dengan kota (hanya kedekatan jarak, bukan hubungan kausal
  dengan cuaca atau kualitas udara).
- Struktur respons JSON BMKG (`prakiraan-cuaca`) diparsing secara
  defensif di `scrape_weather.py` karena ada dua bentuk struktur yang
  beredar di dokumentasi/implementasi komunitas (dengan atau tanpa
  pembungkus `"data": [...]`); bila BMKG mengubah struktur responsnya di
  kemudian hari, sesuaikan fungsi `_flatten_cuaca`.
- Kolom `confidence` NASA FIRMS berbeda bentuk antar sensor: VIIRS
  memberi huruf (l/n/h = low/nominal/high), MODIS memberi angka 0-100;
  `scrape_fire.py` menormalkan keduanya jadi `confidence_score` numerik
  supaya bisa diagregasi.
