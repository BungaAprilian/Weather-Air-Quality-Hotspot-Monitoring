"""Diagram (format Graphviz DOT) dipakai bersama oleh dashboard dan materi presentasi."""

C_SRC = "#E3F2FD"   # sumber data
C_DUCK = "#FFF3CD"  # bebek (raw)
C_MART = "#E8F5E9"  # mart (DuckLake)
C_APP = "#F3E5F5"   # dashboard

ARCH_DOT = f"""
digraph G {{
  rankdir=LR; nodesep=0.35; ranksep=0.7; fontname="Helvetica";
  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, margin="0.15,0.08"];
  edge [fontname="Helvetica", fontsize=9, color="#546E7A"];

  subgraph cluster_src {{ label="1. SUMBER DATA (REST API)"; style=dashed; color="#90A4AE"; fontsize=11;
    bmkg [label="BMKG\\nprakiraan cuaca\\n(per 3 jam)", fillcolor="{C_SRC}"];
    om   [label="Open-Meteo\\nkualitas udara\\n(per jam)", fillcolor="{C_SRC}"];
    firms[label="NASA FIRMS\\ntitik panas VIIRS\\n(CSV)", fillcolor="{C_SRC}"];
  }}
  etl [label="Python scraper\\n+ WRANGLING\\n(validasi, dedup,\\nimputasi)", fillcolor="#FFE0B2", shape=box];
  subgraph cluster_duck {{ label="2. BEBEK per sumber (DuckDB - raw)"; style=dashed; color="#FBC02D"; fontsize=11;
    d1 [label="weather.duckdb\\nraw_weather_bmkg_hourly", fillcolor="{C_DUCK}"];
    d2 [label="air_quality.duckdb\\nraw_air_quality_hourly", fillcolor="{C_DUCK}"];
    d3 [label="fire_hotspot.duckdb\\nraw_fire_hotspot", fillcolor="{C_DUCK}"];
  }}
  subgraph cluster_mart {{ label="3. MART bintang (DuckLake)"; style=dashed; color="#66BB6A"; fontsize=11;
    lake [label="datmin_lake\\n2 dimensi + 4 fakta", fillcolor="{C_MART}"];
  }}
  md  [label="MotherDuck\\n(cloud, opsional)", fillcolor="#ECEFF1"];
  app [label="Dashboard\\nStreamlit", fillcolor="{C_APP}"];
  sch [label="Scheduler / GitHub Actions\\n(update otomatis)", shape=note, fillcolor="#FFFFFF"];

  bmkg -> etl; om -> etl; firms -> etl;
  etl -> d1 [label="cuaca"]; etl -> d2 [label="udara"]; etl -> d3 [label="hotspot"];
  d1 -> lake; d2 -> lake; d3 -> lake [label="ATTACH + SQL"];
  d1 -> md [style=dashed]; d2 -> md [style=dashed]; d3 -> md [style=dashed, label="push"];
  lake -> app [label="query"];
  sch -> etl [style=dotted, label="tiap 6 jam"];
}}
"""

def _table(name, color, rows):
    body = "".join(
        f'<TR><TD ALIGN="LEFT" PORT="{r[0]}">{r[1]}</TD></TR>' for r in rows)
    return (f'"{name}" [shape=plaintext, label=<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="3">'
            f'<TR><TD BGCOLOR="{color}"><B>{name}</B></TD></TR>{body}</TABLE>>];')


ERD_DOT = "digraph ERD {\n  rankdir=LR; nodesep=0.5; ranksep=1.1; fontname=Helvetica;\n" \
    '  edge [color="#546E7A", arrowhead=crow, arrowtail=none, fontsize=9, fontname=Helvetica];\n' + "\n  ".join([
    _table("dim_city", "#A5D6A7", [("a", "<B>PK</B> city_id"), ("b", "city_name"), ("c", "province"), ("d", "region"),
                                    ("e", "latitude, longitude"), ("f", "bmkg_adm4, bmkg_kelurahan")]),
    _table("dim_date", "#A5D6A7", [("a", "<B>PK</B> date"), ("b", "year, month, year_month"), ("c", "day, day_name"), ("d", "is_weekend")]),
    _table("fact_weather_daily", "#90CAF9", [("a", "<B>FK</B> city_id"), ("b", "<B>FK</B> date"), ("c", "avg/max/min_temp"),
                                              ("d", "total_precipitation"), ("e", "avg_humidity, avg_wind_speed"), ("f", "avg_cloud_cover, n_obs")]),
    _table("fact_air_quality_daily", "#90CAF9", [("a", "<B>FK</B> city_id"), ("b", "<B>FK</B> date"), ("c", "avg_pm10, avg_pm25"),
                                                  ("d", "avg_co/no2/so2/ozone"), ("e", "avg_us_aqi, max_us_aqi"), ("f", "aqi_category, n_obs")]),
    _table("fact_fire_daily", "#90CAF9", [("a", "<B>FK</B> city_id"), ("b", "<B>FK</B> date"), ("c", "n_hotspot, total_frp_mw"),
                                           ("d", "max_confidence_score"), ("e", "min_distance_km")]),
    _table("fact_hotspot_event", "#FFCC80", [("a", "event_datetime (UTC), event_date (WIB)"), ("b", "latitude, longitude"),
                                              ("c", "brightness_k, frp_mw"), ("d", "confidence, confidence_score"),
                                              ("e", "<B>FK</B> city_id, distance_km"), ("f", "is_within_radius")]),
]) + """
  dim_city -> fact_weather_daily [label="1 : N"];
  dim_city -> fact_air_quality_daily [label="1 : N"];
  dim_city -> fact_fire_daily [label="1 : N"];
  dim_city -> fact_hotspot_event [label="1 : N"];
  dim_date -> fact_weather_daily [label="1 : N"];
  dim_date -> fact_air_quality_daily [label="1 : N"];
  dim_date -> fact_fire_daily [label="1 : N"];
  fact_hotspot_event -> fact_fire_daily [label="agregasi harian\\n(dalam radius)", style=dashed, arrowhead=normal];
}
"""

# Skema isi tiap bebek (raw)
DUCK_DOT = f"""
digraph D {{
  rankdir=TB; nodesep=0.35; fontname=Helvetica;
  {{rank=same; "raw_weather_bmkg_hourly"; "raw_air_quality_hourly"}}
  {{rank=same; "raw_fire_hotspot"; "scrape_log + dq_log"}}
  "raw_weather_bmkg_hourly" -> "raw_fire_hotspot" [style=invis];
  "raw_air_quality_hourly" -> "scrape_log + dq_log" [style=invis];
  node [shape=plaintext, fontname=Helvetica, fontsize=10];
  {_table("raw_weather_bmkg_hourly", "#FFE082", [("a","<B>city_id</B>, <B>ts</B> (kunci alami)"),("b","temperature_c, humidity_pct"),("c","precipitation_mm, wind_speed_kmh"),("d","wind_dir_deg, cloud_cover_pct"),("e","weather_code, weather_desc(_en)"),("f","visibility_m, analysis_date, scraped_at")])}
  {_table("raw_air_quality_hourly", "#FFE082", [("a","<B>city_id</B>, <B>ts</B> (kunci alami)"),("b","pm10, pm2_5"),("c","carbon_monoxide, nitrogen_dioxide"),("d","sulphur_dioxide, ozone"),("e","us_aqi, scraped_at")])}
  {_table("raw_fire_hotspot", "#FFE082", [("a","<B>event_datetime, latitude, longitude</B> (kunci alami)"),("b","brightness_k, frp_mw"),("c","confidence, confidence_score"),("d","satellite, daynight"),("e","nearest_city_id, distance_km"),("f","is_within_radius, scraped_at")])}
  {_table("scrape_log + dq_log", "#FFF8E1", [("a","ada di SETIAP bebek"),("b","scrape_log: run_id, source, status, durasi, n_records"),("c","dq_log: rows_in/out, duplikat, invalid,"),("d","out_of_range, values_imputed")])}
}}
"""
