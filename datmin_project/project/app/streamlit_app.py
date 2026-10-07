"""
Real-Time Environmental Monitoring Dashboard: Cuaca, Kualitas Udara, dan Hotspot Kota-Kota Besar di Indonesia

"""
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import duckdb
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

from datmin.config import CITIES, PAST_DAYS, FIRE_MAX_DISTANCE_KM, DATMIN_BACKEND
from datmin.db import (
    SOURCES,
    get_analytics_connection,
    get_connection,
    get_ducklake_connection,
    source_exists,
)
from datmin.pipeline import run_pipeline

st.set_page_config(page_title="Cuaca, Kualitas Udara, dan Hotspot Kota-Kota Besar di Indonesia", layout="wide")

SOURCE_LABEL = {
    "weather": "weather.duckdb (BMKG)",
    "air_quality": "air_quality.duckdb (Open-Meteo)",
    "fire_hotspot": "fire_hotspot.duckdb (NASA FIRMS)",
}

# Tabel/view per sumber. "joined" = view lintas-sumber (3 DB di-ATTACH).
TABLES = {
    "weather": ["dim_city", "raw_weather_bmkg_hourly", "scrape_log", "fact_weather_daily"],
    "air_quality": ["dim_city", "raw_air_quality_hourly", "scrape_log", "fact_air_quality_daily"],
    "fire_hotspot": ["dim_city", "raw_fire_hotspot", "scrape_log", "fact_fire_daily"],
}


def all_sources_exist():
    return all(source_exists(s) for s in SOURCES)


def query_source(source, sql, params=None):
    """Query ke SATU database sumber."""
    con = get_connection(source, read_only=True)
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def query_joined(sql, params=None):
    """Query lintas-sumber (fact_daily_joined)."""
    con = get_analytics_connection()
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def table_count(source, table):
    try:
        return int(query_source(source, f"SELECT COUNT(*) AS n FROM {table}")["n"].iloc[0])
    except Exception:
        return 0


def circle_latlon(lat, lon, radius_km, n=60):
    """Titik-titik lingkaran radius_km di sekitar (lat, lon) untuk digambar di peta."""
    lats, lons = [], []
    for i in range(n + 1):
        brg = math.radians(360 * i / n)
        dlat = (radius_km / 111.0) * math.cos(brg)
        dlon = (radius_km / (111.0 * math.cos(math.radians(lat)))) * math.sin(brg)
        lats.append(lat + dlat)
        lons.append(lon + dlon)
    return lats, lons


# SIDEBAR
st.sidebar.title("Data Warehouse")
st.sidebar.caption(f"Backend: **{DATMIN_BACKEND}**")
page = st.sidebar.radio(
    "Menu",
    [
        "Tentang Project",
        "Data Warehouse Explorer",
        "Peta Hotspot",
        "Analisis Korelasi",
        "Skala & Performa Scraping",
    ],
)

if not all_sources_exist():
    st.warning("Database per sumber belum lengkap (weather / air_quality / fire_hotspot). Jalankan scraping pertama kali.")
    n_city_init = st.slider("Jumlah kota untuk scraping awal", 1, len(CITIES), min(5, len(CITIES)))
    days_init = st.slider("Jumlah hari historis per kota", 7, 92, 30)
    if st.button("Jalankan Scraping Pertama Kali"):
        with st.spinner("Mengambil data dari BMKG, Open-Meteo, dan NASA FIRMS..."):
            result = run_pipeline(cities=CITIES[:n_city_init], past_days=days_init)
        st.success(f"Selesai! {result['total_records']} baris tersimpan dalam {result['elapsed_sec']:.1f} detik.")
        st.rerun()
    st.stop()


# PAGE: TENTANG PROJECT
if page == "Tentang Project":
    st.title("Real-Time Environmental Monitoring Dashboard: Cuaca, Kualitas Udara, dan Hotspot Kota-Kota Besar di Indonesia")

    st.markdown(
        """
### Topik & Tujuan
Project ini adalah data warehouse sederhana yang menggabungkan 3 sumber data (cuaca, kualitas udara, dan hotspot/ titik panas) dari kota-kota besar di Indonesia, 
lalu menyimpannya secara terstruktur untuk dianalisis apakah terdapat hubungan antara kondisi cuaca dan tingkat polusi udara di suatu kota, 
sekaligus melihat titik panas pada setiap kota sebagai konteks tambahan (musim kemarau, kabut asap, dsb).

Selain analisis korelasi, tujuan utama project ini adalah menguji seberapa besar skala data yang mampu diproses dalam satu pipeline penuh 
(scraping 3 API, menyimpan & agregasi DuckDB, Tampilan Dashboard Streamlit) 
mulai dari 1 kota dalam beberapa hari, sampai belasan kota dengan puluhan ribu baris data per jam.

### Sumber Data & Variabel
| # | Sumber | Endpoint | Variabel | Resolusi |
|---|---|---|---|---|
| 1 | BMKG (Cuaca) | `api.bmkg.go.id/publik/prakiraan-cuaca' | suhu (°C), kelembapan (%), curah hujan (mm), kecepatan & arah angin, tutupan awan (%), deskripsi cuaca | per 3 jam, prakiraan 3 hari ke depan |
| 2 | Open-Meteo (Kualitas Udara) | `air-quality-api.open-meteo.com/v1/air-quality` | PM10, PM2.5 (µg/m³), CO, NO2, SO2 (µg/m³), ozon (µg/m³), US AQI | per jam, histori hingga 92 hari |
| 3 | NASA FIRMS (Hotspot/ titik panas) | `firms.modaps.eosdis.nasa.gov/api/area` | koordinat titik panas, kecerahan, fire radiative power (FRP), tingkat keyakinan deteksi, dipetakan ke kota terdekat (haversine) | per kejadian (deteksi satelit near-real-time, seluruh Indonesia) |

Cakupan data saat ini: **{n_city} kota** (datmin/config.py), dengan histori
kualitas udara **{n_days} hari terakhir** per kota. Data cuaca (BMKG) tidak
memiliki arsip historis publik, sehingga bertambah melalui akumulasi setiap kali
pipeline dijalankan ulang, bukan melalui parameter historis.

### Arsitektur & Jenis Database
```
BMKG        -> weather.duckdb       --\
Open-Meteo  -> air_quality.duckdb   ----> DuckLake (daily_joined, hotspot, city)
NASA FIRMS  -> fire_hotspot.duckdb  --/        |
        (opsional) di-upload ke MotherDuck: datmin_weather, datmin_air_quality, datmin_fire_hotspot
                                               v
                                          Streamlit
```

- Pengambilan data dari internet dilakukan dengan Python (`requests`) ke REST API/feed BMKG,
  Open-Meteo, dan NASA FIRMS. Setiap sumber disimpan di **file DuckDB-nya sendiri**
  (`weather.duckdb`, `air_quality.duckdb`, `fire_hotspot.duckdb`), lengkap dengan `dim_city`,
  `scrape_log`, dan view agregat harian sumber itu.
- **DuckLake** menggabungkan ketiga sumber (join per kota + tanggal) menjadi tabel
  `daily_joined`, serta menyimpan salinan titik panas dan daftar kota.
- **MotherDuck** adalah versi cloud dari ketiga database sumber (dipilih dengan `DATMIN_BACKEND=motherduck`
  atau di-upload dengan `--push-motherduck`).
- Cuaca & kualitas udara di-upsert per rentang waktu (idempotent); titik panas di-dedup per waktu+koordinat.
        """.format(n_city=len(CITIES), n_days=PAST_DAYS)
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Baris cuaca (BMKG)", f"{table_count('weather', 'raw_weather_bmkg_hourly'):,}")
    c2.metric("Baris kualitas udara (Open-Meteo)", f"{table_count('air_quality', 'raw_air_quality_hourly'):,}")
    c3.metric("Hotspot/ Titik panas (NASA FIRMS)", f"{table_count('fire_hotspot', 'raw_fire_hotspot'):,}")
    c4.metric("Kota terdaftar", f"{table_count('weather', 'dim_city'):,}")
    c5.metric("Total proses scraping", f"{sum(table_count(s_, 'scrape_log') for s_ in SOURCES):,}")


# PAGE: DATA WAREHOUSE EXPLORER
elif page == "Data Warehouse Explorer":
    st.title("Data Warehouse Explorer")
    st.caption("Tiap sumber punya database sendiri. Pilih sumber, lalu jelajahi tabel atau jalankan SQL.")

    src = st.radio("Database sumber", list(SOURCE_LABEL) + ["joined (lintas sumber)", "ducklake"],
                   format_func=lambda k: SOURCE_LABEL.get(k, k), horizontal=True)

    if src in SOURCES:
        tables = TABLES[src]
        cols = st.columns(len(tables))
        for col, t in zip(cols, tables):
            col.metric(t, f"{table_count(src, t):,}")
        runner = lambda sql, params=None: query_source(src, sql, params)
        default_sql = f"SELECT * FROM {tables[1]} LIMIT 20"
    elif src.startswith("joined"):
        tables = ["fact_daily_joined"]
        st.caption("View `fact_daily_joined`: weather_db x air_db x fire_db di-ATTACH, di-join per kota + tanggal.")
        runner = query_joined
        default_sql = "SELECT * FROM fact_daily_joined ORDER BY date DESC LIMIT 20"
    else:
        tables = ["datmin.daily_joined", "datmin.fire_hotspot", "datmin.city"]
        st.caption("DuckLake `datmin_lake` (perlu extension `ducklake`; jalankan pipeline dulu agar terisi).")

        def runner(sql, params=None):
            con = get_ducklake_connection()
            try:
                return con.execute(sql.replace("datmin.", "datmin_lake.datmin."), params or []).df()
            finally:
                con.close()

        default_sql = "SELECT * FROM datmin.daily_joined ORDER BY date DESC LIMIT 20"

    st.divider()
    tab1, tab2 = st.tabs(["Lihat Tabel", "SQL Playground"])

    with tab1:
        selected = st.selectbox("Pilih tabel/view", tables)
        limit = st.slider("Jumlah baris ditampilkan", 10, 1000, 100)
        try:
            st.dataframe(runner(f"SELECT * FROM {selected} LIMIT {int(limit)}"), width="stretch")
        except Exception as e:  # noqa: BLE001
            st.error(f"Tidak bisa membaca {selected}: {e}")

    with tab2:
        sql = st.text_area("Tulis query SQL (dijalankan langsung di DuckDB)", value=default_sql, height=120)
        if st.button("Jalankan Query"):
            try:
                t0 = time.time()
                result = runner(sql)
                st.success(f"{len(result):,} baris dalam {(time.time()-t0)*1000:.1f} ms")
                st.dataframe(result, width="stretch")
            except Exception as e:  # noqa: BLE001
                st.error(f"Query gagal: {e}")


# PAGE: PETA HOTSPOT
elif page == "Peta Hotspot":
    st.title("Peta Hotspot (NASA FIRMS) di 15 Kota Besar Indonesia")
    st.caption(
        "Titik panas satelit VIIRS dari `fire_hotspot.duckdb`, dipetakan ke kota terdekat (haversine). "
        f"Lingkaran = radius {FIRE_MAX_DISTANCE_KM} km tempat titik panas dianggap terkait dengan kota."
    )

    fire = query_source("fire_hotspot", "SELECT * FROM raw_fire_hotspot")
    cities_df = query_source("fire_hotspot", "SELECT * FROM dim_city ORDER BY city_id")

    if fire.empty:
        st.info("Belum ada data titik panas. Jalankan scraping terlebih dahulu.")
        st.stop()

    fire["date"] = pd.to_datetime(fire["event_datetime"]).dt.date

    f1, f2, f3, f4 = st.columns([2, 2, 2, 2])
    d_min, d_max = fire["date"].min(), fire["date"].max()
    if d_min == d_max:
        f1.caption(f"Tanggal data: **{d_min}**")
        date_range = (d_min, d_max)
    else:
        date_range = f1.slider("Rentang tanggal", d_min, d_max, (d_min, d_max))
    only_radius = f2.checkbox(f"Hanya dalam radius {FIRE_MAX_DISTANCE_KM} km kota", value=True)
    min_conf = f3.select_slider("Keyakinan minimum", options=[0, 30, 60, 90], value=0,
                                format_func=lambda v: {0: "semua", 30: "low+", 60: "nominal+", 90: "high"}[v])
    city_sel = f4.multiselect("Fokus kota (kosong = semua)", cities_df["city_name"].tolist())

    show_circle = st.checkbox("Tampilkan lingkaran radius kota", value=True)

    m = fire[(fire["date"] >= date_range[0]) & (fire["date"] <= date_range[1])]
    if only_radius:
        m = m[m["is_within_radius"]]
    m = m[m["confidence_score"].fillna(0) >= min_conf]
    if city_sel:
        m = m[m["nearest_city_name"].isin(city_sel)]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Titik panas ditampilkan", f"{len(m):,}")
    k2.metric("Total FRP (MW)", f"{m['frp_mw'].sum():,.0f}")
    k3.metric("Kota terdampak", f"{m['nearest_city_name'].nunique()}")
    k4.metric("Dalam radius kota", f"{int(m['is_within_radius'].sum()):,}")

    MAX_PTS = 6000
    mp = m.nlargest(MAX_PTS, "frp_mw") if len(m) > MAX_PTS else m
    if len(m) > MAX_PTS:
        st.caption(f"Peta menampilkan {MAX_PTS:,} titik dengan FRP tertinggi dari {len(m):,}.")

    fig = go.Figure()

    if show_circle:
        sel_cities = cities_df[cities_df["city_name"].isin(city_sel)] if city_sel else cities_df
        for _, c in sel_cities.iterrows():
            la, lo = circle_latlon(c["latitude"], c["longitude"], FIRE_MAX_DISTANCE_KM)
            fig.add_trace(go.Scattermap(lat=la, lon=lo, mode="lines", line=dict(width=1, color="#4b6cb7"),
                                        hoverinfo="skip", showlegend=False))

    if not mp.empty:
        fig.add_trace(go.Scattermap(
            lat=mp["latitude"], lon=mp["longitude"], mode="markers",
            marker=dict(size=np.clip(mp["frp_mw"].fillna(1).to_numpy() ** 0.5 * 2.5 + 5, 6, 22),
                        color=mp["frp_mw"], colorscale="YlOrRd", cmin=0,
                        cmax=float(mp["frp_mw"].quantile(0.95) or 1), opacity=0.75,
                        colorbar=dict(title="FRP (MW)")),
            customdata=np.stack([mp["nearest_city_name"], mp["distance_km"].round(1), mp["frp_mw"],
                                 mp["confidence"], mp["event_datetime"].astype(str)], axis=-1),
            hovertemplate="<b>Hotspot</b><br>Waktu (UTC): %{customdata[4]}<br>Kota terdekat: %{customdata[0]}"
                          "<br>Jarak: %{customdata[1]} km<br>FRP: %{customdata[2]} MW"
                          "<br>Keyakinan: %{customdata[3]}<extra></extra>",
            name="Hotspot",
        ))

    fig.add_trace(go.Scattermap(
        lat=cities_df["latitude"], lon=cities_df["longitude"], mode="markers+text",
        marker=dict(size=11, color="#1f3b73"), text=cities_df["city_name"], textposition="top right",
        textfont=dict(size=12, color="#1f3b73"), name="Kota",
        hovertemplate="<b>%{text}</b><extra></extra>",
    ))

    fig.update_layout(
        map=dict(style="open-street-map", center=dict(lat=-2.5, lon=118), zoom=3.6),
        height=640, margin=dict(l=0, r=0, t=0, b=0),
        legend=dict(orientation="h", y=1.02),
    )
    st.plotly_chart(fig, width="stretch")

    st.subheader("Ringkasan per kota terdekat")
    summary = (
        m.groupby("nearest_city_name")
        .agg(n_hotspot=("frp_mw", "size"), total_frp_mw=("frp_mw", "sum"),
             jarak_min_km=("distance_km", "min"), jarak_rata2_km=("distance_km", "mean"))
        .round(1).sort_values("n_hotspot", ascending=False).reset_index()
        .rename(columns={"nearest_city_name": "kota"})
    )
    c_a, c_b = st.columns([1, 1])
    c_a.dataframe(summary, width="stretch", hide_index=True)
    if not summary.empty:
        c_b.plotly_chart(px.bar(summary, x="kota", y="n_hotspot", labels={"n_hotspot": "Jumlah titik panas"}),
                         width="stretch")
    st.caption(
        "Data hotspot = deteksi satelit (bukan konfirmasi kebakaran). Titik di luar radius tetap disimpan "
        "tetapi tidak dihitung ke kota (mis. Jayapura: banyak deteksi di pedalaman Papua yang jauh dari kota)."
    )


# PAGE: ANALISIS KORELASI
elif page == "Analisis Korelasi":
    st.title("Analisis Korelasi Cuaca vs Kualitas Udara")

    df = query_joined("SELECT * FROM fact_daily_joined")
    if df.empty:
        st.info("Data tidak ditemukan, jalankan scraping terlebih dahulu.")
        st.stop()

    df["date"] = pd.to_datetime(df["date"])

    city_options = sorted(df["city_name"].unique().tolist())
    selected_cities = st.multiselect("Filter kota (kosongkan = semua kota)", city_options, default=[])
    df_f = df[df["city_name"].isin(selected_cities)] if selected_cities else df

    date_min, date_max = df_f["date"].min(), df_f["date"].max()
    if date_min.date() == date_max.date():
        st.caption(f"Hanya ada 1 tanggal yang overlap antara cuaca & kualitas udara saat ini: **{date_min.date()}**.")
        date_range = (date_min.date(), date_max.date())
    else:
        date_range = st.slider("Rentang tanggal", min_value=date_min.date(), max_value=date_max.date(),
                                value=(date_min.date(), date_max.date()))
    df_f = df_f[(df_f["date"].dt.date >= date_range[0]) & (df_f["date"].dt.date <= date_range[1])]

    weather_vars = {
        "avg_temp": "Suhu rata-rata (°C)",
        "max_temp": "Suhu maksimum (°C)",
        "total_precipitation": "Total curah hujan (mm)",
        "avg_humidity": "Kelembapan rata-rata (%)",
        "avg_wind_speed": "Kecepatan angin rata-rata (km/j)",
        "avg_cloud_cover": "Tutupan awan rata-rata (%)",
    }
    air_vars = {
        "avg_pm10": "PM10 (µg/m³)",
        "avg_pm25": "PM2.5 (µg/m³)",
        "avg_co": "CO (µg/m³)",
        "avg_no2": "NO2 (µg/m³)",
        "avg_so2": "SO2 (µg/m³)",
        "avg_ozone": "Ozon (µg/m³)",
        "avg_us_aqi": "US AQI",
    }

    c1, c2 = st.columns(2)
    x_col = c1.selectbox("Variabel cuaca (X)", list(weather_vars.keys()), format_func=lambda k: weather_vars[k])
    y_col = c2.selectbox("Variabel kualitas udara (Y)", list(air_vars.keys()), format_func=lambda k: air_vars[k])

    plot_df = df_f.dropna(subset=[x_col, y_col])
    if len(plot_df) < 2:
        st.warning("Data terlalu sedikit untuk dianalisis pada filter ini.")
    else:
        r = np.corrcoef(plot_df[x_col], plot_df[y_col])[0, 1]
        st.metric("Koefisien korelasi Pearson (r)", f"{r:.3f}")

        fig = px.scatter(
            plot_df, x=x_col, y=y_col, color="city_name",
            hover_data=["date"], opacity=0.7,
            labels={x_col: weather_vars[x_col], y_col: air_vars[y_col]},
            title=f"{weather_vars[x_col]} vs {air_vars[y_col]}",
        )
        z = np.polyfit(plot_df[x_col], plot_df[y_col], 1)
        x_line = np.linspace(plot_df[x_col].min(), plot_df[x_col].max(), 50)
        fig.add_trace(go.Scatter(x=x_line, y=np.polyval(z, x_line), mode="lines",
                                  name="Garis tren (linear)", line=dict(color="black", dash="dash")))
        st.plotly_chart(fig, width="stretch")

        st.caption("Catatan: korelasi bersifat deskriptif/ bukan sebab-akibat.")

        st.subheader("Matriks Korelasi (Cuaca x Kualitas Udara)")
        corr_matrix = pd.DataFrame(index=list(weather_vars.keys()), columns=list(air_vars.keys()), dtype=float)
        for wv in weather_vars:
            for av in air_vars:
                sub = df_f.dropna(subset=[wv, av])
                corr_matrix.loc[wv, av] = np.corrcoef(sub[wv], sub[av])[0, 1] if len(sub) >= 2 else np.nan

        fig2 = px.imshow(
            corr_matrix.astype(float),
            x=[air_vars[c] for c in corr_matrix.columns],
            y=[weather_vars[r_] for r_ in corr_matrix.index],
            color_continuous_scale="RdBu_r", zmin=-1, zmax=1, text_auto=".2f",
            aspect="auto",
        )
        st.plotly_chart(fig2, width="stretch")

    st.divider()
    st.subheader("Hotspot/ Titik Panas")
    fire_days = df_f[df_f["n_hotspot"] > 0]
    if fire_days.empty:
        st.caption("Tidak ada titik panas (dalam radius kota) pada filter kota/tanggal saat ini.")
    else:
        st.caption(
            "Hari dengan deteksi titik panas dalam radius kota terpilih, "
            "hasil pencocokan jarak-terdekat dari NASA FIRMS (satelit VIIRS)."
        )
        st.dataframe(
            fire_days[["city_name", "date", "n_hotspot", "total_frp_mw", "max_confidence_score", "min_distance_km"]]
            .sort_values("date", ascending=False),
            width="stretch",
        )


# PAGE: SKALA & PERFORMA SCRAPING
elif page == "Skala & Performa Scraping":
    st.title("Skala & Performa Scraping")
    st.caption("Bagian ini menunjukkan seberapa besar data yang sudah diproses, dan memungkinkan menambah skala data secara langsung.")

    log_df = pd.concat([query_source(s_, "SELECT * FROM scrape_log") for s_ in SOURCES], ignore_index=True)
    log_df = log_df.sort_values("start_time", ascending=False)

    def total_raw_rows():
        return (
            table_count("weather", "raw_weather_bmkg_hourly")
            + table_count("air_quality", "raw_air_quality_hourly")
            + table_count("fire_hotspot", "raw_fire_hotspot")
        )

    total_rows = total_raw_rows()
    total_duration = log_df["duration_sec"].sum() if not log_df.empty else 0
    throughput = total_rows / total_duration if total_duration > 0 else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total baris tersimpan", f"{total_rows:,}")
    c2.metric("Total waktu scraping (semua run)", f"{total_duration:,.1f} s")
    c3.metric("Throughput rata-rata", f"{throughput:,.1f} baris/detik")
    c4.metric("Jumlah proses (run) tercatat", f"{len(log_df):,}")

    if not log_df.empty:
        st.subheader("Durasi Scraping per Kota & Sumber")
        fig = px.bar(log_df, x="city_name", y="duration_sec", color="source", barmode="group",
                     labels={"duration_sec": "Durasi (detik)", "city_name": "Kota"})
        st.plotly_chart(fig, width="stretch")

        st.subheader("Log Lengkap Proses Scraping")
        st.dataframe(log_df, width="stretch")

    st.divider()
    st.subheader("Tambah Skala Data (Jalankan Scraping Baru)")
    st.caption("Menambah kota dan/atau rentang hari akan menambah volume data di warehouse")

    col_a, col_b = st.columns(2)
    n_cities = col_a.slider("Jumlah kota", 1, len(CITIES), min(5, len(CITIES)))
    n_days = col_b.slider("Jumlah hari historis per kota", 7, 92, PAST_DAYS)

    if st.button("Jalankan Scraping Sekarang"):
        before = total_rows
        progress = st.empty()
        with st.spinner(f"Scraping {n_cities} kota x {n_days} hari..."):
            result = run_pipeline(cities=CITIES[:n_cities], past_days=n_days)
        after_rows = total_raw_rows()
        st.success(
            f"Selesai dalam {result['elapsed_sec']:.1f} detik. "
            f"Baris sebelum: {before:,} -> sesudah: {after_rows:,} "
            f"(throughput {result['total_records']/result['elapsed_sec']:.1f} baris/detik)."
        )
        st.rerun()
