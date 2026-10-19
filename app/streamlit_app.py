"""
Dashboard: Cuaca, Kualitas Udara & Hotspot 15 Kota Besar Indonesia.
Membaca MART bintang (DuckLake) dan 3 database sumber ("bebek").
Jalankan:  streamlit run app/streamlit_app.py
"""
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from datmin.config import CITIES, DATMIN_BACKEND, FIRE_MAX_DISTANCE_KM, PAST_DAYS
from datmin.db import (
    MART_TABLES,
    SOURCES,
    close_mart_connections,
    get_connection,
    get_mart_connection,
    is_motherduck,
    source_exists,
    source_uri,
)
from datmin.diagrams import ARCH_DOT, DUCK_DOT, ERD_DOT
from datmin.pipeline import run_pipeline

st.set_page_config(page_title="Lingkungan 15 Kota Besar Indonesia", page_icon="🌏", layout="wide")

# ---------- Palet warna konsisten ----------
AQI_ORDER = ["Baik", "Sedang", "Tidak sehat (sensitif)", "Tidak sehat", "Sangat tidak sehat", "Berbahaya"]
AQI_COLORS = {"Baik": "#43A047", "Sedang": "#FDD835", "Tidak sehat (sensitif)": "#FB8C00",
              "Tidak sehat": "#E53935", "Sangat tidak sehat": "#8E24AA", "Berbahaya": "#6D1B1B"}
SRC_COLORS = {"bmkg-cuaca": "#1E88E5", "open-meteo-air-quality": "#43A047", "nasa-firms": "#E53935"}
CITY_SEQ = px.colors.qualitative.Safe + px.colors.qualitative.Set2
PLOT_FONT = dict(family="Inter, Segoe UI, sans-serif", size=13)
WHO_PM25 = 15  # pedoman harian WHO 2021 (µg/m³)

SOURCE_LABEL = {"weather": "weather.duckdb (BMKG)", "air_quality": "air_quality.duckdb (Open-Meteo)",
                "fire_hotspot": "fire_hotspot.duckdb (NASA FIRMS)"}
RAW_TABLE = {"weather": "raw_weather_bmkg_hourly", "air_quality": "raw_air_quality_hourly", "fire_hotspot": "raw_fire_hotspot"}
SOURCE_TABLES = {s: [RAW_TABLE[s], "scrape_log", "dq_log"] for s in SOURCES}

WEATHER_VARS = {"avg_temp": "Suhu rata-rata (°C)", "max_temp": "Suhu maksimum (°C)",
                "total_precipitation": "Total curah hujan (mm)", "avg_humidity": "Kelembapan rata-rata (%)",
                "avg_wind_speed": "Kecepatan angin rata-rata (km/j)", "avg_cloud_cover": "Tutupan awan rata-rata (%)"}
AIR_VARS = {"avg_pm10": "PM10 (µg/m³)", "avg_pm25": "PM2.5 (µg/m³)", "avg_co": "CO (µg/m³)",
            "avg_no2": "NO₂ (µg/m³)", "avg_so2": "SO₂ (µg/m³)", "avg_ozone": "Ozon (µg/m³)", "avg_us_aqi": "US AQI"}


# ---------- Akses data (di-cache; otomatis segar saat file bebek berubah / tiap 5 menit) ----------
def data_version():
    if is_motherduck():
        return int(time.time() // 300)
    return tuple(round(os.path.getmtime(source_uri(s)), 1) for s in SOURCES if os.path.exists(source_uri(s)))


@st.cache_resource(show_spinner="Menyiapkan mart...")   # dibuat ulang hanya saat file bebek berubah
def _mart_handle(version):
    return get_mart_connection()


def query_mart(sql, params=None):
    con, prefix, _ = _mart_handle(data_version())
    cur = con.cursor()
    try:
        return cur.execute(sql.replace("{m}", prefix), params or []).df()
    finally:
        cur.close()


def release_mart():
    """Tutup koneksi mart + kosongkan cache SEBELUM pipeline menulis ke file .duckdb."""
    close_mart_connections()
    st.cache_resource.clear()
    st.cache_data.clear()


def mart_label():
    return _mart_handle(data_version())[2]


@st.cache_data(ttl=300, show_spinner=False)
def _query_source(source, sql, version):
    con = get_connection(source, read_only=True)
    try:
        return con.execute(sql).df()
    finally:
        con.close()


def query_source(source, sql):
    return _query_source(source, sql, data_version())


def table_count(source, table):
    try:
        return int(query_source(source, f"SELECT COUNT(*) AS n FROM {table}")["n"].iloc[0])
    except Exception:  # noqa: BLE001
        return 0


def last_update():
    stamps = []
    for s, col in (("weather", "scraped_at"), ("air_quality", "scraped_at"), ("fire_hotspot", "scraped_at")):
        try:
            v = query_source(s, f"SELECT MAX({col}) AS m FROM {RAW_TABLE[s]}")["m"].iloc[0]
            if pd.notna(v):
                stamps.append(v)
        except Exception:  # noqa: BLE001
            pass
    return max(stamps) if stamps else None


def style(fig, title=None, height=420):
    fig.update_layout(title=dict(text=title, x=0.01, y=0.97, yanchor="top") if title else None, font=PLOT_FONT, height=height,
                      margin=dict(l=10, r=20, t=95 if title else 50, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0),
                      plot_bgcolor="white", paper_bgcolor="white")
    fig.update_xaxes(showgrid=True, gridcolor="#EEF1F5", zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor="#EEF1F5", zeroline=False)
    return fig


def circle_latlon(lat, lon, radius_km, n=60):
    lats, lons = [], []
    for i in range(n + 1):
        brg = math.radians(360 * i / n)
        lats.append(lat + (radius_km / 111.0) * math.cos(brg))
        lons.append(lon + (radius_km / (111.0 * math.cos(math.radians(lat)))) * math.sin(brg))
    return lats, lons


def aqi_cat_type(df, col="aqi_category"):
    df[col] = pd.Categorical(df[col], categories=AQI_ORDER, ordered=True)
    return df


# ---------- Guard: database belum ada ----------
if not all(source_exists(s) for s in SOURCES):
    st.title("Lingkungan 15 Kota Besar Indonesia")
    st.warning("Database per sumber belum lengkap (weather / air_quality / fire_hotspot). Jalankan scraping pertama kali.")
    n0 = st.slider("Jumlah kota untuk scraping awal", 1, len(CITIES), min(5, len(CITIES)))
    d0 = st.slider("Jumlah hari historis per kota", 7, 92, 30)
    if st.button("Jalankan Scraping Pertama Kali", type="primary"):
        release_mart()
        with st.spinner("Mengambil data dari BMKG, Open-Meteo, dan NASA FIRMS..."):
            res = run_pipeline(cities=CITIES[:n0], past_days=d0)
        st.success(f"Selesai! {res['total_records']} baris dalam {res['elapsed_sec']:.1f} detik.")
        st.rerun()
    st.stop()

# ---------- Sidebar ----------
st.sidebar.title("🌏 Lingkungan 15 Kota")
page = st.sidebar.radio("Menu", [
    "Ringkasan", "Peta Hotspot", "Analisis Korelasi", "Arsitektur & Skema",
    "Kualitas Data (Wrangling)", "Data Warehouse Explorer", "Skala & Update Otomatis"])
st.sidebar.divider()
lu = last_update()
st.sidebar.caption(f"Backend: **{DATMIN_BACKEND}**")
st.sidebar.caption(f"Mart: **{mart_label()}**")
st.sidebar.caption(f"Data terakhir diambil: **{lu:%d %b %Y %H:%M} UTC**" if lu is not None else "Data terakhir diambil: -")
if st.sidebar.button("🔄 Muat ulang data"):
    release_mart()
    st.rerun()


# =====================================================================
# 1. RINGKASAN
# =====================================================================
if page == "Ringkasan":
    st.title("Kualitas Udara, Cuaca & Titik Panas di 15 Kota Besar Indonesia")
    st.caption("Tiga sumber terbuka (BMKG · Open-Meteo · NASA FIRMS) → 3 database sumber → mart bintang di DuckLake → dashboard ini.")

    env = query_mart("SELECT * FROM main.v_daily_environment")
    env["date"] = pd.to_datetime(env["date"])
    env = aqi_cat_type(env)

    dmin, dmax = env["date"].min().date(), env["date"].max().date()
    snap = st.slider("Tanggal yang ditampilkan di peta & peringkat", dmin, dmax, dmax, format="DD MMM YYYY")
    day = env[env["date"].dt.date == snap].dropna(subset=["avg_us_aqi"])

    worst = day.sort_values("avg_us_aqi", ascending=False).head(1)
    best = day.sort_values("avg_us_aqi").head(1)
    fire_total = int(query_mart("SELECT COALESCE(SUM(n_hotspot),0) AS n FROM {m}fact_fire_daily")["n"].iloc[0])
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Kota dipantau", f"{env['city_id'].nunique()}")
    k2.metric("Rata-rata PM2.5", f"{day['avg_pm25'].mean():.1f} µg/m³" if len(day) else "-",
              help=f"Pedoman harian WHO: {WHO_PM25} µg/m³")
    k3.metric("Udara terburuk", worst["city_name"].iloc[0] if len(worst) else "-",
              f"AQI {worst['avg_us_aqi'].iloc[0]:.0f}" if len(worst) else None, delta_color="inverse", delta_arrow="off")
    k4.metric("Udara terbaik", best["city_name"].iloc[0] if len(best) else "-",
              f"AQI {best['avg_us_aqi'].iloc[0]:.0f}" if len(best) else None, delta_color="normal", delta_arrow="off")
    k5.metric("Hotspot dalam radius kota", f"{fire_total:,}")

    # --- peta AQI + peringkat ---
    cities = query_mart("SELECT city_id, city_name, latitude, longitude FROM {m}dim_city")
    day = day.merge(cities, on=["city_id", "city_name"])
    c_map, c_rank = st.columns([3, 2])
    with c_map:
        fig = go.Figure()
        for cat in AQI_ORDER:
            sub = day[day["aqi_category"] == cat]
            if sub.empty:
                continue
            fig.add_trace(go.Scattermap(
                lat=sub["latitude"], lon=sub["longitude"], mode="markers+text", name=cat,
                text=sub["city_name"], textposition="top right", textfont=dict(size=11),
                marker=dict(size=22, color=AQI_COLORS[cat], opacity=0.9),
                customdata=np.stack([sub["avg_us_aqi"].round(0), sub["avg_pm25"].round(1)], axis=-1),
                hovertemplate="<b>%{text}</b><br>US AQI: %{customdata[0]}<br>PM2.5: %{customdata[1]} µg/m³<extra></extra>"))
        fig.update_layout(map=dict(style="open-street-map", center=dict(lat=-2.5, lon=117.5), zoom=2.9),
                          height=470, margin=dict(l=0, r=0, t=40, b=0), font=PLOT_FONT,
                          title=dict(text=f"Peta kategori kualitas udara · {snap:%d %b %Y}", x=0.01),
                          legend=dict(title="Kategori US AQI", orientation="h", y=-0.02))
        st.plotly_chart(fig, width="stretch")
    with c_rank:
        r = day.sort_values("avg_us_aqi")
        fig = px.bar(r, x="avg_us_aqi", y="city_name", orientation="h", color="aqi_category",
                     color_discrete_map=AQI_COLORS, category_orders={"aqi_category": AQI_ORDER},
                     labels={"avg_us_aqi": "US AQI (rata-rata harian)", "city_name": "Kota", "aqi_category": "Kategori"},
                     text=r["avg_us_aqi"].round(0))
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_xaxes(range=[0, float(r["avg_us_aqi"].max()) * 1.18])
        st.plotly_chart(style(fig, "Peringkat kualitas udara (AQI rendah = baik)", 470), width="stretch")

    # --- tren PM2.5 ---
    st.subheader("Tren PM2.5 harian")
    top5 = (env.groupby("city_name")["avg_pm25"].mean().sort_values(ascending=False).head(4).index.tolist())
    sel = st.multiselect("Pilih kota", sorted(env["city_name"].unique()), default=top5)
    t = env[env["city_name"].isin(sel)]
    fig = px.line(t, x="date", y="avg_pm25", color="city_name", color_discrete_sequence=CITY_SEQ,
                  labels={"date": "Tanggal", "avg_pm25": "PM2.5 rata-rata (µg/m³)", "city_name": "Kota"})
    fig.add_hline(y=WHO_PM25, line_dash="dash", line_color="#546E7A",
                  annotation_text=f"Pedoman WHO ({WHO_PM25} µg/m³)", annotation_position="top left")
    st.plotly_chart(style(fig, "PM2.5 rata-rata harian per kota", 400), width="stretch")

    # --- perbandingan wilayah & hotspot ---
    c1, c2 = st.columns(2)
    with c1:
        reg = env.groupby("region", as_index=False).agg(pm25=("avg_pm25", "mean"), kota=("city_id", "nunique"))
        reg = reg.sort_values("pm25", ascending=False)
        fig = px.bar(reg, x="region", y="pm25", color="region", color_discrete_sequence=CITY_SEQ, text=reg["pm25"].round(1),
                     labels={"region": "Wilayah", "pm25": "PM2.5 rata-rata (µg/m³)"})
        fig.update_layout(showlegend=False)
        st.plotly_chart(style(fig, "Rata-rata PM2.5 per wilayah", 380), width="stretch")
    with c2:
        fc = query_mart("""SELECT c.city_name, SUM(f.n_hotspot) AS n_hotspot, SUM(f.total_frp_mw) AS frp
                           FROM {m}fact_fire_daily f JOIN {m}dim_city c USING(city_id)
                           GROUP BY 1 ORDER BY 2 DESC""")
        fig = px.bar(fc, x="city_name", y="n_hotspot", color="frp", color_continuous_scale="YlOrRd",
                     labels={"city_name": "Kota", "n_hotspot": "Jumlah titik panas", "frp": "Total FRP (MW)"})
        st.plotly_chart(style(fig, f"Titik panas dalam radius {FIRE_MAX_DISTANCE_KM} km (warna = intensitas FRP)", 380), width="stretch")

    with st.expander("Tentang proyek ini"):
        st.markdown(f"""
        **Tujuan:** membangun data warehouse mini yang menggabungkan cuaca, kualitas udara, dan titik panas
        untuk {len(CITIES)} kota besar Indonesia, lalu menampilkannya agar hubungan antar-faktor mudah dilihat.
        **Catatan:** US AQI mengikuti skala EPA; PM2.5 dibandingkan dengan pedoman harian WHO ({WHO_PM25} µg/m³).
        Hotspot adalah deteksi satelit, bukan konfirmasi kebakaran. Korelasi bersifat deskriptif, bukan sebab-akibat.""")


# =====================================================================
# 2. PETA HOTSPOT
# =====================================================================
elif page == "Peta Hotspot":
    st.title("Peta Hotspot (NASA FIRMS) di 15 Kota Besar Indonesia")
    st.caption(f"Titik panas satelit VIIRS dipetakan ke kota terdekat (haversine). Lingkaran = radius {FIRE_MAX_DISTANCE_KM} km kota.")

    fire = query_mart("""SELECT e.*, c.city_name FROM {m}fact_hotspot_event e LEFT JOIN {m}dim_city c USING(city_id)""")
    cities_df = query_mart("SELECT * FROM {m}dim_city ORDER BY city_id")
    if fire.empty:
        st.info("Belum ada data titik panas. Jalankan scraping terlebih dahulu.")
        st.stop()
    fire["event_date"] = pd.to_datetime(fire["event_date"]).dt.date

    f1, f2, f3, f4 = st.columns(4)
    d_min, d_max = fire["event_date"].min(), fire["event_date"].max()
    if d_min == d_max:
        f1.caption(f"Tanggal data: **{d_min}**")
        date_range = (d_min, d_max)
    else:
        date_range = f1.slider("Rentang tanggal (WIB)", d_min, d_max, (d_min, d_max))
    only_radius = f2.checkbox(f"Hanya dalam radius {FIRE_MAX_DISTANCE_KM} km kota", value=True)
    min_conf = f3.select_slider("Keyakinan minimum", options=[0, 30, 60, 90], value=0,
                                format_func=lambda v: {0: "semua", 30: "low+", 60: "nominal+", 90: "high"}[v])
    city_sel = f4.multiselect("Fokus kota (kosong = semua)", cities_df["city_name"].tolist())
    show_circle = st.checkbox("Tampilkan lingkaran radius kota", value=True)

    m = fire[(fire["event_date"] >= date_range[0]) & (fire["event_date"] <= date_range[1])]
    if only_radius:
        m = m[m["is_within_radius"]]
    m = m[m["confidence_score"].fillna(0) >= min_conf]
    if city_sel:
        m = m[m["city_name"].isin(city_sel)]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Titik panas ditampilkan", f"{len(m):,}")
    k2.metric("Total FRP (MW)", f"{m['frp_mw'].sum():,.0f}")
    k3.metric("Kota terdampak", f"{m['city_name'].nunique()}")
    k4.metric("Dalam radius kota", f"{int(m['is_within_radius'].sum()):,}")

    MAX_PTS = 6000
    mp = m.nlargest(MAX_PTS, "frp_mw") if len(m) > MAX_PTS else m
    if len(m) > MAX_PTS:
        st.caption(f"Peta menampilkan {MAX_PTS:,} titik dengan FRP tertinggi dari {len(m):,}.")

    fig = go.Figure()
    if show_circle:
        for _, c in (cities_df[cities_df["city_name"].isin(city_sel)] if city_sel else cities_df).iterrows():
            la, lo = circle_latlon(c["latitude"], c["longitude"], FIRE_MAX_DISTANCE_KM)
            fig.add_trace(go.Scattermap(lat=la, lon=lo, mode="lines", line=dict(width=1, color="#4b6cb7"),
                                        hoverinfo="skip", showlegend=False))
    if not mp.empty:
        fig.add_trace(go.Scattermap(
            lat=mp["latitude"], lon=mp["longitude"], mode="markers", name="Hotspot",
            marker=dict(size=np.clip(mp["frp_mw"].fillna(1).to_numpy() ** 0.5 * 2.5 + 5, 6, 22),
                        color=mp["frp_mw"], colorscale="YlOrRd", cmin=0,
                        cmax=float(mp["frp_mw"].quantile(0.95) or 1), opacity=0.75,
                        colorbar=dict(title="FRP (MW)", len=0.6, thickness=14)),
            customdata=np.stack([mp["city_name"].fillna("-"), mp["distance_km"].round(1), mp["frp_mw"],
                                 mp["confidence"], mp["event_datetime"].astype(str)], axis=-1),
            hovertemplate="<b>Hotspot</b><br>Waktu (UTC): %{customdata[4]}<br>Kota terdekat: %{customdata[0]}"
                          "<br>Jarak: %{customdata[1]} km<br>FRP: %{customdata[2]} MW"
                          "<br>Keyakinan: %{customdata[3]}<extra></extra>"))
    fig.add_trace(go.Scattermap(lat=cities_df["latitude"], lon=cities_df["longitude"], mode="markers+text",
                                marker=dict(size=11, color="#1f3b73"), text=cities_df["city_name"],
                                textposition="top right", textfont=dict(size=12, color="#1f3b73"), name="Kota",
                                hovertemplate="<b>%{text}</b><extra></extra>"))
    fig.update_layout(map=dict(style="open-street-map", center=dict(lat=-2.5, lon=117.5), zoom=3.3), height=640,
                      margin=dict(l=0, r=0, t=0, b=0), font=PLOT_FONT, legend=dict(orientation="h", y=1.02))
    st.plotly_chart(fig, width="stretch")

    st.subheader("Ringkasan per kota terdekat")
    summary = (m.groupby("city_name").agg(n_hotspot=("frp_mw", "size"), total_frp_mw=("frp_mw", "sum"),
                                          jarak_min_km=("distance_km", "min"), jarak_rata2_km=("distance_km", "mean"))
               .round(1).sort_values("n_hotspot", ascending=False).reset_index().rename(columns={"city_name": "kota"}))
    ca, cb = st.columns(2)
    ca.dataframe(summary, width="stretch", hide_index=True)
    if not summary.empty:
        fig = px.bar(summary, x="kota", y="n_hotspot", color="total_frp_mw", color_continuous_scale="YlOrRd",
                     labels={"kota": "Kota", "n_hotspot": "Jumlah titik panas", "total_frp_mw": "Total FRP (MW)"})
        cb.plotly_chart(style(fig, "Titik panas per kota", 360), width="stretch")
    st.caption("Hotspot = deteksi satelit (bukan konfirmasi kebakaran). Titik di luar radius tetap disimpan, tetapi tidak dihitung ke kota.")


# =====================================================================
# 3. ANALISIS KORELASI
# =====================================================================
elif page == "Analisis Korelasi":
    st.title("Analisis Korelasi: Cuaca vs Kualitas Udara")
    df = query_mart("SELECT * FROM main.v_daily_environment")
    if df.empty:
        st.info("Data tidak ditemukan, jalankan scraping terlebih dahulu.")
        st.stop()
    df["date"] = pd.to_datetime(df["date"])

    both = df.dropna(subset=["avg_temp"])
    st.caption(f"Cuaca (BMKG) hanya berupa prakiraan ±3 hari per run, sehingga tanggal yang beririsan dengan "
               f"kualitas udara: **{both['date'].nunique()} hari** ({len(both)} baris kota-hari).")

    sel_cities = st.multiselect("Filter kota (kosongkan = semua)", sorted(df["city_name"].unique()))
    dff = both[both["city_name"].isin(sel_cities)] if sel_cities else both

    c1, c2 = st.columns(2)
    x_col = c1.selectbox("Variabel cuaca (X)", list(WEATHER_VARS), format_func=WEATHER_VARS.get)
    y_col = c2.selectbox("Variabel kualitas udara (Y)", list(AIR_VARS), index=1, format_func=AIR_VARS.get)
    plot_df = dff.dropna(subset=[x_col, y_col])

    if len(plot_df) < 3:
        st.warning("Data terlalu sedikit untuk dianalisis pada filter ini.")
    else:
        r = np.corrcoef(plot_df[x_col], plot_df[y_col])[0, 1]
        strength = "kuat" if abs(r) >= 0.6 else "sedang" if abs(r) >= 0.3 else "lemah"
        m1, m2, m3 = st.columns(3)
        m1.metric("Korelasi Pearson (r)", f"{r:.3f}")
        m2.metric("Kekuatan hubungan", f"{strength} ({'positif' if r > 0 else 'negatif'})")
        m3.metric("Jumlah pengamatan", f"{len(plot_df):,}")

        fig = px.scatter(plot_df, x=x_col, y=y_col, color="region", color_discrete_sequence=CITY_SEQ,
                         hover_name="city_name", hover_data={"date": "|%d %b %Y"}, opacity=0.75,
                         labels={x_col: WEATHER_VARS[x_col], y_col: AIR_VARS[y_col], "region": "Wilayah"})
        z = np.polyfit(plot_df[x_col], plot_df[y_col], 1)
        xs = np.linspace(plot_df[x_col].min(), plot_df[x_col].max(), 50)
        fig.add_trace(go.Scatter(x=xs, y=np.polyval(z, xs), mode="lines", name="Garis tren linear",
                                 line=dict(color="#1B2430", dash="dash")))
        st.plotly_chart(style(fig, f"{WEATHER_VARS[x_col]} vs {AIR_VARS[y_col]}", 460), width="stretch")
        st.caption("Korelasi bersifat deskriptif, bukan sebab-akibat. Warna titik = wilayah.")

        st.subheader("Matriks korelasi (cuaca × kualitas udara)")
        cm = pd.DataFrame(index=list(WEATHER_VARS), columns=list(AIR_VARS), dtype=float)
        for wv in WEATHER_VARS:
            for av in AIR_VARS:
                sub = dff.dropna(subset=[wv, av])
                cm.loc[wv, av] = np.corrcoef(sub[wv], sub[av])[0, 1] if len(sub) >= 3 else np.nan
        fig2 = px.imshow(cm.astype(float), x=[AIR_VARS[c] for c in cm.columns], y=[WEATHER_VARS[i] for i in cm.index],
                         color_continuous_scale="RdBu_r", zmin=-1, zmax=1, text_auto=".2f", aspect="auto",
                         labels=dict(color="r"))
        fig2.update_xaxes(title="Variabel kualitas udara (Open-Meteo)")
        fig2.update_yaxes(title="Variabel cuaca (BMKG)")
        st.plotly_chart(style(fig2, "Merah = hubungan positif · Biru = negatif", 440), width="stretch")

    st.divider()
    st.subheader("Hari dengan titik panas dalam radius kota")
    fire_days = df[df["n_hotspot"].fillna(0) > 0]
    if sel_cities:
        fire_days = fire_days[fire_days["city_name"].isin(sel_cities)]
    if fire_days.empty:
        st.caption("Tidak ada titik panas (dalam radius kota) pada filter saat ini.")
    else:
        fig = px.bar(fire_days, x="date", y="n_hotspot", color="city_name", color_discrete_sequence=CITY_SEQ,
                     labels={"date": "Tanggal", "n_hotspot": "Jumlah titik panas", "city_name": "Kota"})
        st.plotly_chart(style(fig, "Titik panas harian dalam radius kota", 360), width="stretch")
        st.dataframe(fire_days[["city_name", "date", "n_hotspot", "total_frp_mw", "avg_pm25", "aqi_category"]]
                     .sort_values("date", ascending=False).rename(columns={
                         "city_name": "Kota", "date": "Tanggal", "n_hotspot": "Titik panas",
                         "total_frp_mw": "Total FRP (MW)", "avg_pm25": "PM2.5", "aqi_category": "Kategori AQI"}),
                     width="stretch", hide_index=True)
    st.caption("Catatan: n_hotspot kosong (NULL) di luar rentang liputan data hotspot, bukan berarti nol kejadian.")


# =====================================================================
# 4. ARSITEKTUR & SKEMA
# =====================================================================
elif page == "Arsitektur & Skema":
    st.title("Arsitektur & Skema Data Warehouse")
    st.caption("Tiap sumber punya satu 'bebek' (DuckDB). Ketiganya digabung menjadi mart bintang di DuckLake tanpa tabel duplikat.")

    t1, t2, t3, t4 = st.tabs(["Aliran data", "Isi tiap bebek", "Skema mart (ERD)", "Minim duplikasi"])
    with t1:
        st.graphviz_chart(ARCH_DOT, width="stretch")
    with t2:
        st.table(pd.DataFrame([
            ["weather.duckdb", "BMKG (api.bmkg.go.id)", "Prakiraan cuaca per 3 jam per kota", "raw_weather_bmkg_hourly"],
            ["air_quality.duckdb", "Open-Meteo Air Quality API", "Polutan & US AQI per jam", "raw_air_quality_hourly"],
            ["fire_hotspot.duckdb", "NASA FIRMS (VIIRS)", "Deteksi titik panas satelit", "raw_fire_hotspot"],
        ], columns=["Bebek", "Sumber", "Menyimpan", "Tabel raw"]))
        st.graphviz_chart(DUCK_DOT, width="stretch")
    with t3:
        st.graphviz_chart(ERD_DOT, width="stretch")
        st.table(pd.DataFrame([
            ["dim_city", "dimensi", "1 baris per kota", "city_id"],
            ["dim_date", "dimensi", "1 baris per tanggal", "date"],
            ["fact_weather_daily", "fakta", "kota × tanggal", "city_id, date"],
            ["fact_air_quality_daily", "fakta", "kota × tanggal", "city_id, date"],
            ["fact_hotspot_event", "fakta", "1 deteksi satelit", "event_datetime, lat, lon"],
            ["fact_fire_daily", "fakta (agregat)", "kota × tanggal (dalam radius)", "city_id, date"],
        ], columns=["Tabel mart", "Jenis", "Grain (1 baris = ...)", "Kunci"]))
    with t4:
        st.markdown("""
        - **Dimensi tunggal**: `dim_city` & `dim_date` hanya ada **sekali** di mart (sebelumnya `dim_city` disalin ke tiap bebek).
        - **Tidak ada nama kota di tabel fakta**: fakta hanya menyimpan `city_id`; nama diambil lewat join.
        - **Tidak ada tabel gabungan fisik**: analisis lintas-sumber memakai *view* `v_daily_environment`
          di atas tabel fakta, sehingga tidak ada salinan data kedua.
        - **Agregasi hanya di mart**: bebek menyimpan data mentah; rata-rata/total harian dihitung satu kali di mart.
        - **Idempotent**: kunci alami (kota+waktu, atau waktu+koordinat) mencegah baris ganda saat pipeline diulang.
        """)


# =====================================================================
# 5. KUALITAS DATA
# =====================================================================
elif page == "Kualitas Data (Wrangling)":
    st.title("Kualitas Data & Data Wrangling")
    st.caption("Setiap batch dibersihkan sebelum masuk database; statistiknya dicatat di tabel `dq_log` pada tiap bebek.")

    dq = pd.concat([query_source(s, "SELECT * FROM dq_log") for s in SOURCES], ignore_index=True)
    st.subheader("Aturan wrangling yang diterapkan")
    st.table(pd.DataFrame([
        ["1. Standarisasi tipe", "Timestamp → datetime, angka → numerik (gagal parse = NULL)", "Semua sumber"],
        ["2. Validasi rentang", "Nilai mustahil → NULL (mis. kelembapan > 100%, PM2.5 < 0, FRP > 5000 MW)", "Semua sumber"],
        ["3. Validasi kunci & wilayah", "Baris tanpa kota/waktu/koordinat atau di luar Indonesia → dibuang", "Semua sumber"],
        ["4. Deduplikasi", "Kunci alami: (kota, waktu) atau (waktu, lat, lon)", "Semua sumber"],
        ["5. Pelengkapan", "Celah pendek diisi interpolasi linear per kota (cuaca ≤ 6 jam, udara ≤ 3 jam)", "BMKG, Open-Meteo"],
        ["6. Normalisasi kategori", "confidence FIRMS l/n/h/angka → skor 0–100", "NASA FIRMS"],
        ["7. Penyelarasan zona waktu", "Tanggal hotspot (UTC) dikonversi ke WIB (+7) di mart", "NASA FIRMS"],
        ["8. Liputan data", "n_hotspot = NULL di luar rentang data hotspot (bukan 0 palsu)", "Mart"],
    ], columns=["Langkah", "Aturan", "Berlaku untuk"]))

    if dq.empty:
        st.info("Belum ada catatan dq_log. Jalankan pipeline terbaru agar statistik terisi.")
    else:
        tot = dq.groupby("source", as_index=False)[["rows_in", "rows_out", "duplicates_dropped", "invalid_dropped",
                                                    "out_of_range_nulled", "values_imputed"]].sum()
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Baris masuk (semua run)", f"{int(tot['rows_in'].sum()):,}")
        k2.metric("Baris lolos", f"{int(tot['rows_out'].sum()):,}")
        k3.metric("Duplikat dibuang", f"{int(tot['duplicates_dropped'].sum()):,}")
        k4.metric("Nilai diperbaiki (NULL/imputasi)", f"{int(tot['out_of_range_nulled'].sum() + tot['values_imputed'].sum()):,}")
        long = tot.melt(id_vars="source", value_vars=["duplicates_dropped", "invalid_dropped", "out_of_range_nulled", "values_imputed"],
                        var_name="aksi", value_name="jumlah")
        long["aksi"] = long["aksi"].map({"duplicates_dropped": "Duplikat dibuang", "invalid_dropped": "Baris tidak valid dibuang",
                                         "out_of_range_nulled": "Di luar rentang → NULL", "values_imputed": "Nilai diimputasi"})
        fig = px.bar(long, x="source", y="jumlah", color="aksi", barmode="group", color_discrete_sequence=px.colors.qualitative.Set2,
                     labels={"source": "Sumber", "jumlah": "Jumlah", "aksi": "Tindakan wrangling"})
        st.plotly_chart(style(fig, "Tindakan wrangling per sumber (semua run)", 380), width="stretch")
        st.caption("Jika semua batang bernilai 0, artinya data API sudah bersih pada run tersebut; pemeriksaan tetap dijalankan di setiap batch.")

    st.subheader("Kelengkapan data (persentase sel terisi)")
    rows = []
    for s in SOURCES:
        t = RAW_TABLE[s]
        cols = query_source(s, f"DESCRIBE {t}")["column_name"].tolist()
        agg = ", ".join(f"COUNT({c}) AS \"{c}\"" for c in cols)
        cnt = query_source(s, f"SELECT COUNT(*) AS _n, {agg} FROM {t}").iloc[0]
        n = max(int(cnt["_n"]), 1)
        rows += [{"tabel": t, "kolom": c, "terisi_pct": 100 * int(cnt[c]) / n} for c in cols if c != "scraped_at"]
    comp = pd.DataFrame(rows)
    fig = px.bar(comp, x="terisi_pct", y="kolom", color="tabel", orientation="h", facet_row="tabel",
                 color_discrete_sequence=["#1E88E5", "#43A047", "#E53935"], range_x=[0, 100],
                 labels={"terisi_pct": "Terisi (%)", "kolom": ""}, height=900)
    fig.update_yaxes(matches=None, showticklabels=True)
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
    fig.update_layout(showlegend=False, font=PLOT_FONT, plot_bgcolor="white")
    st.plotly_chart(fig, width="stretch")


# =====================================================================
# 6. EXPLORER
# =====================================================================
elif page == "Data Warehouse Explorer":
    st.title("Data Warehouse Explorer")
    st.caption("Pilih bebek sumber atau mart, lihat tabelnya, atau jalankan SQL sendiri.")
    src = st.radio("Database", list(SOURCE_LABEL) + ["mart", "view analisis"],
                   format_func=lambda k: SOURCE_LABEL.get(k, "mart bintang (DuckLake)" if k == "mart" else "v_daily_environment (view lintas fakta)"),
                   horizontal=True)
    if src in SOURCES:
        tables = SOURCE_TABLES[src]
        for col, t in zip(st.columns(len(tables)), tables):
            col.metric(t, f"{table_count(src, t):,}")
        runner = lambda sql: query_source(src, sql)  # noqa: E731
        default_sql = f"SELECT * FROM {tables[0]} LIMIT 20"
    elif src == "mart":
        tables = ["{m}" + t for t in MART_TABLES]
        runner = query_mart
        default_sql = "SELECT * FROM {m}fact_air_quality_daily ORDER BY date DESC LIMIT 20"
        st.caption("Tulis `{m}` sebagai awalan skema mart, mis. `SELECT * FROM {m}dim_city`.")
    else:
        tables = ["main.v_daily_environment"]
        runner = query_mart
        default_sql = "SELECT * FROM main.v_daily_environment ORDER BY date DESC LIMIT 20"

    tab1, tab2 = st.tabs(["Lihat tabel", "SQL Playground"])
    with tab1:
        selected = st.selectbox("Tabel/view", tables)
        limit = st.slider("Jumlah baris", 10, 1000, 100)
        try:
            st.dataframe(runner(f"SELECT * FROM {selected} LIMIT {int(limit)}"), width="stretch")
        except Exception as e:  # noqa: BLE001
            st.error(f"Tidak bisa membaca {selected}: {e}")
    with tab2:
        sql = st.text_area("Query SQL (dijalankan langsung di DuckDB)", value=default_sql, height=120)
        if st.button("Jalankan Query", type="primary"):
            try:
                t0 = time.time()
                res = runner(sql)
                st.success(f"{len(res):,} baris dalam {(time.time() - t0) * 1000:.1f} ms")
                st.dataframe(res, width="stretch")
            except Exception as e:  # noqa: BLE001
                st.error(f"Query gagal: {e}")


# =====================================================================
# 7. SKALA & UPDATE OTOMATIS
# =====================================================================
elif page == "Skala & Update Otomatis":
    st.title("Skala Data, Performa Scraping & Update Otomatis")

    log = pd.concat([query_source(s, "SELECT * FROM scrape_log") for s in SOURCES], ignore_index=True)
    log["start_time"] = pd.to_datetime(log["start_time"])

    def total_rows():
        return sum(table_count(s, RAW_TABLE[s]) for s in SOURCES)

    n_rows = total_rows()
    dur = log["duration_sec"].sum() if not log.empty else 0
    n_runs = log["run_id"].nunique() if not log.empty else 0
    ok = (log["status"] == "success").mean() * 100 if not log.empty else 0
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Baris tersimpan", f"{n_rows:,}")
    c2.metric("Run tercatat", f"{n_runs}")
    c3.metric("Tingkat sukses scraping", f"{ok:.0f}%")
    c4.metric("Throughput rata-rata", f"{(n_rows / dur if dur else 0):,.0f} baris/dtk")
    c5.metric("Kota × sumber", f"{len(CITIES)} × {len(SOURCES)}")

    cc1, cc2 = st.columns(2)
    vol = pd.DataFrame({"Sumber": [SOURCE_LABEL[s].split(" ")[0] for s in SOURCES],
                        "Baris": [table_count(s, RAW_TABLE[s]) for s in SOURCES]})
    fig = px.bar(vol, x="Sumber", y="Baris", color="Sumber", text="Baris", color_discrete_sequence=["#1E88E5", "#43A047", "#E53935"],
                 labels={"Baris": "Jumlah baris"})
    fig.update_traces(texttemplate="%{text:,}")
    fig.update_layout(showlegend=False)
    cc1.plotly_chart(style(fig, "Volume data per bebek", 360), width="stretch")
    if not log.empty:
        per_run = log.groupby(["run_id", "source"], as_index=False).agg(baris=("n_records", "sum"), waktu=("start_time", "min"))
        fig = px.bar(per_run.sort_values("waktu"), x="waktu", y="baris", color="source", color_discrete_map=SRC_COLORS,
                     labels={"waktu": "Waktu run", "baris": "Baris diambil", "source": "Sumber"})
        cc2.plotly_chart(style(fig, "Riwayat run otomatis/manual (baris diambil per run)", 360), width="stretch")

        st.subheader("Durasi scraping per kota")
        d = log[log["city_name"] != "(seluruh Indonesia)"].groupby(["city_name", "source"], as_index=False)["duration_sec"].mean()
        fig = px.bar(d, x="city_name", y="duration_sec", color="source", barmode="group", color_discrete_map=SRC_COLORS,
                     labels={"city_name": "Kota", "duration_sec": "Durasi rata-rata (detik)", "source": "Sumber"})
        st.plotly_chart(style(fig, None, 360), width="stretch")
        with st.expander("Log lengkap scraping"):
            st.dataframe(log.sort_values("start_time", ascending=False), width="stretch", hide_index=True)

    st.divider()
    st.subheader("Mekanisme update otomatis")
    st.markdown("""
    | Cara | Perintah / berkas | Cocok untuk |
    |---|---|---|
    | **Scheduler Python** | `python -m datmin.scheduler --every 180` | laptop/server yang menyala |
    | **GitHub Actions** | `.github/workflows/update-data.yml` (cron tiap 6 jam) | otomatis di cloud, tanpa menyalakan laptop |
    | **Task Scheduler / cron** | memanggil `python -m datmin.pipeline` | penjadwalan bawaan OS |

    Setiap run: scrape → wrangling → upsert (idempotent) → refresh mart DuckLake → (opsional) push MotherDuck.
    Dashboard otomatis memuat data baru (cache 5 menit atau tombol **Muat ulang data**).
    """)

    st.subheader("Tambah skala data (scraping manual)")
    ca, cb = st.columns(2)
    n_cities = ca.slider("Jumlah kota", 1, len(CITIES), len(CITIES))
    n_days = cb.slider("Jumlah hari historis udara per kota", 7, 92, PAST_DAYS)
    if st.button("Jalankan Scraping Sekarang", type="primary"):
        release_mart()          # lepas kunci file .duckdb sebelum menulis
        before = total_rows()
        with st.spinner(f"Scraping {n_cities} kota × {n_days} hari..."):
            res = run_pipeline(cities=CITIES[:n_cities], past_days=n_days)
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success(f"Selesai dalam {res['elapsed_sec']:.1f} dtk. Baris: {before:,} → {total_rows():,}.")
