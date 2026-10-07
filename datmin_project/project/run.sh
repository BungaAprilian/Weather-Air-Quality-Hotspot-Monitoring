#!/bin/bash
set -e

echo "=== 1/2: Pipeline: BMKG, Open-Meteo, NASA FIRMS -> 3 DuckDB -> DuckLake ==="
python -m datmin.pipeline

echo ""
echo "=== 2/2: Dashboard Streamlit ==="
streamlit run app/streamlit_app.py
