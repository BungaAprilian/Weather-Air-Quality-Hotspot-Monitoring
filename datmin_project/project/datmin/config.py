"""
Konfigurasi utama project.

Ubah PAST_DAYS dan/atau jumlah kota di CITIES untuk melakukan
UJI SKALA: seberapa banyak data yang bisa di-scraping, disimpan
di DuckDB, ditampilkan di Streamlit, dan berapa lama prosesnya.

Tiga sumber data dipadukan pada 15 kota besar Indonesia yang sama:
1. BMKG        -> cuaca (prakiraan-cuaca, per kelurahan/adm4)
2. Open-Meteo  -> kualitas udara (air-quality API, per titik koordinat)
3. NASA FIRMS  -> titik panas, dipetakan ke kota terdekat
"""
import os

# -----------------------------------------------------------------
# Daftar kota (dipakai sebagai titik koordinat untuk Open-Meteo &
# untuk pencocokan kota-terdekat pada data titik panas).
#
# "bmkg_adm4" adalah kode wilayah administrasi tingkat IV (kelurahan/desa)
# sesuai Kepmendagri No 300.2.2-2138 Tahun 2025, dipakai untuk query
# endpoint resmi BMKG: https://api.bmkg.go.id/publik/prakiraan-cuaca?adm4=...
# BMKG tidak menyediakan endpoint per "kota" secara langsung (hanya per
# kelurahan/desa), sehingga untuk tiap kota dipilih SATU kelurahan yang
# berada di kecamatan pertama pada kota tersebut (representatif, karena
# resolusi model cuaca BMKG pada level ini sudah cukup seragam untuk
# satu kota). Kode ini dihasilkan dari dataset resmi kode wilayah
# (lihat datmin/resolve_bmkg_adm4.py untuk cara reproduksi/verifikasi).
# -----------------------------------------------------------------
CITIES = [
    {"city_id": 1,  "city_name": "Jakarta",     "province": "DKI Jakarta",        "latitude": -6.2088, "longitude": 106.8456, "bmkg_adm4": "31.71.01.1001", "bmkg_kelurahan": "Gambir"},
    {"city_id": 2,  "city_name": "Bandung",     "province": "Jawa Barat",         "latitude": -6.9175, "longitude": 107.6191, "bmkg_adm4": "32.73.01.1001", "bmkg_kelurahan": "Sukarasa"},
    {"city_id": 3,  "city_name": "Surabaya",    "province": "Jawa Timur",         "latitude": -7.2575, "longitude": 112.7521, "bmkg_adm4": "35.78.01.1001", "bmkg_kelurahan": "Karang Pilang"},
    {"city_id": 4,  "city_name": "Semarang",    "province": "Jawa Tengah",        "latitude": -6.9932, "longitude": 110.4203, "bmkg_adm4": "33.74.01.1001", "bmkg_kelurahan": "Miroto"},
    {"city_id": 5,  "city_name": "Yogyakarta",  "province": "DI Yogyakarta",      "latitude": -7.7956, "longitude": 110.3695, "bmkg_adm4": "34.71.01.1001", "bmkg_kelurahan": "Kricak"},
    {"city_id": 6,  "city_name": "Medan",       "province": "Sumatera Utara",     "latitude": 3.5952,  "longitude": 98.6722,  "bmkg_adm4": "12.71.01.1001", "bmkg_kelurahan": "Pasar Baru"},
    {"city_id": 7,  "city_name": "Palembang",   "province": "Sumatera Selatan",   "latitude": -2.9761, "longitude": 104.7754, "bmkg_adm4": "16.71.01.1001", "bmkg_kelurahan": "Tiga-puluh-lima Ilir"},
    {"city_id": 8,  "city_name": "Makassar",    "province": "Sulawesi Selatan",   "latitude": -5.1477, "longitude": 119.4327, "bmkg_adm4": "73.71.01.1001", "bmkg_kelurahan": "Bontorannu"},
    {"city_id": 9,  "city_name": "Denpasar",    "province": "Bali",               "latitude": -8.6705, "longitude": 115.2126, "bmkg_adm4": "51.71.01.1001", "bmkg_kelurahan": "Serangan"},
    {"city_id": 10, "city_name": "Balikpapan",  "province": "Kalimantan Timur",   "latitude": -1.2379, "longitude": 116.8529, "bmkg_adm4": "64.71.01.1001", "bmkg_kelurahan": "Manggar"},
    {"city_id": 11, "city_name": "Manado",      "province": "Sulawesi Utara",     "latitude": 1.4748,  "longitude": 124.8421, "bmkg_adm4": "71.71.01.1001", "bmkg_kelurahan": "Molas"},
    {"city_id": 12, "city_name": "Pontianak",   "province": "Kalimantan Barat",   "latitude": -0.0263, "longitude": 109.3425, "bmkg_adm4": "61.71.01.1002", "bmkg_kelurahan": "Benuamelayu Laut"},
    {"city_id": 13, "city_name": "Banda Aceh",  "province": "Aceh",               "latitude": 5.5483,  "longitude": 95.3238,  "bmkg_adm4": "11.71.01.2001", "bmkg_kelurahan": "Kampung Baru"},
    {"city_id": 14, "city_name": "Jayapura",    "province": "Papua",              "latitude": -2.5333, "longitude": 140.7181, "bmkg_adm4": "91.71.01.1001", "bmkg_kelurahan": "Gurabesi"},
    {"city_id": 15, "city_name": "Malang",      "province": "Jawa Timur",         "latitude": -7.9666, "longitude": 112.6326, "bmkg_adm4": "35.73.01.1001", "bmkg_kelurahan": "Balearjosari"},
]

# Berapa hari ke belakang data historis kualitas udara (Open-Meteo) yang
# diambil per kota. Semakin besar -> semakin banyak baris data per kota
# (dipakai untuk uji skala). Open-Meteo mendukung past_days hingga 92.
#
# CATATAN: parameter ini HANYA berlaku untuk sumber kualitas udara
# (Open-Meteo). Sumber cuaca (BMKG) tidak menyediakan arsip historis
# publik, hanya prakiraan 3 hari ke depan yang diperbarui tiap kali
# pipeline dijalankan -> volumenya bertambah lewat akumulasi run
# (lihat README bagian "Jenis Sumber & Pola Pertumbuhan Data").
PAST_DAYS = 60

# -----------------------------------------------------------------
# Penyimpanan: 1 file DuckDB per SUMBER + 1 DuckLake gabungan
# -----------------------------------------------------------------
WEATHER_DB_PATH = "data/weather.duckdb"          # BMKG
AIR_QUALITY_DB_PATH = "data/air_quality.duckdb"  # Open-Meteo
FIRE_HOTSPOT_DB_PATH = "data/fire_hotspot.duckdb"  # NASA FIRMS

# DuckLake: katalog metadata (file DuckDB) + folder file parquet.
# Path relatif -> aman dipindah antar komputer (jangan pakai path absolut).
DUCKLAKE_CATALOG_PATH = "data/datmin_lake.ducklake"
DUCKLAKE_DATA_PATH = "data/datmin_lake_files/"
DUCKLAKE_ALIAS = "datmin_lake"

# MotherDuck (DuckDB cloud). Token diambil dari env var, JANGAN di-hardcode:
#   export MOTHERDUCK_TOKEN="..."   (buat di app.motherduck.com -> Settings)
# DATMIN_BACKEND=local (default) -> baca/tulis file .duckdb lokal
# DATMIN_BACKEND=motherduck      -> baca/tulis database di MotherDuck
MOTHERDUCK_TOKEN = os.environ.get("MOTHERDUCK_TOKEN", "")
DATMIN_BACKEND = os.environ.get("DATMIN_BACKEND", "local").lower()
MOTHERDUCK_DBS = {
    "weather": "datmin_weather",
    "air_quality": "datmin_air_quality",
    "fire_hotspot": "datmin_fire_hotspot",
}

# Pengaturan HTTP
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 2

# Sumber 1: BMKG - prakiraan cuaca
# Wajib mencantumkan BMKG sebagai sumber data (syarat penggunaan data
# terbuka BMKG): https://data.bmkg.go.id/prakiraan-cuaca/
BMKG_WEATHER_API_URL = "https://api.bmkg.go.id/publik/prakiraan-cuaca"

# Sumber 2: Open-Meteo - kualitas udara
AIR_QUALITY_API_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# Jumlah hari ke depan (forecast) yang ikut diambil, disamakan dengan
# horizon prakiraan BMKG (3 hari) supaya `fact_daily_joined` (cuaca x
# kualitas udara, di-join per city+date) punya overlap tanggal yang
# cukup lebar untuk dianalisis/ditampilkan, bukan cuma 1 hari.
AIR_QUALITY_FORECAST_DAYS = 3

AIR_QUALITY_HOURLY_VARS = [
    "pm10",
    "pm2_5",
    "carbon_monoxide",
    "nitrogen_dioxide",
    "sulphur_dioxide",
    "ozone",
    "us_aqi",
]

# Sumber 3: NASA FIRMS - titik panas,
#   1) Buka https://firms.modaps.eosdis.nasa.gov/api/map_key/
#   2) Isi email dan MAP_KEY langsung dikirim ke email.
#   3) Isi env var FIRMS_MAP_KEY sebelum menjalankan pipeline, mis.:
#        export FIRMS_MAP_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
#
# Data ini sangat relevan untuk konteks Indonesia 
# KEJADIAN diskrit (deteksi titik panas
# satelit), bukan pengukuran per-jam yang halus, sehingga perlu
# dipadukan ke 15 kota lewat pencocokan JARAK TERDEKAT (haversine) -
# pola yang sama seperti pemaduan data kejadian pada umumnya.
FIRMS_MAP_KEY = os.environ.get("FIRMS_MAP_KEY", "")

# Sumber satelit VIIRS (resolusi ~375m, lebih detail dari MODIS ~1km),
# data Near-Real-Time (NRT), tersedia lewat FIRMS Area API.
FIRMS_SOURCE = "VIIRS_SNPP_NRT"

# Bounding box (west, south, east, north) yang mencakup seluruh wilayah
# Indonesia (Sabang s.d. Merauke), dipakai untuk sekali query per run
# (bukan per kota) hasilnya lalu dicocokkan ke kota
# terdekat di Python.
FIRMS_BBOX = (95.0, -11.0, 141.5, 6.5)

# Rentang hari ke belakang yang diambil tiap kali pipeline dijalankan
# (maks 10 untuk API area/csv). Sama seperti BMKG, FIRMS tidak dipakai
# untuk historis dalam (past_days) - datanya bersifat "terkini", volume
# bertambah lewat akumulasi tiap run.
FIRMS_DAY_RANGE = 1

# Radius maksimum (km) agar sebuah titik panas dianggap "terkait" dengan
# salah satu dari 15 kota; di luar radius ini tetap disimpan di
# raw_fire_hotspot (nearest_city_id tetap diisi) tapi ditandai
# is_within_radius = false agar tidak menyesatkan analisis korelasi.
# Dibuat lebih kecil dari radius gempa karena titik api adalah kejadian
# yang jauh lebih lokal.
FIRE_MAX_DISTANCE_KM = 100
