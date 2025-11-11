import os
import json
import ee
import pandas as pd
from pathlib import Path

from src.aux_utils import export_image, make_relative_path
from reporte.render_report import render

def get_dw_mosaic_1year(end_date, geometry):
    """Mosaico de Dynamic World (built) de los últimos 365 días hasta end_date."""
    end = ee.Date(end_date.strftime("%Y-%m-%d"))
    start = end.advance(-365, "day")
    collection = (
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterDate(start, end)
        .filterBounds(geometry)
        .select("built")
        .sort("system:time_start", False)
        .sort("system:index")
    )
    return collection.mosaic().clip(geometry)

def prepare_folders(base_path, anio, mes):
    """Crea los directorios de salida organizados por componente"""
    output_base = os.path.join(base_path, "urban_sprawl", "outputs")
    output_dir = os.path.join(output_base, f"{anio}_{mes:02d}")
    os.makedirs(output_dir, exist_ok=True)

    dirs = {k: os.path.join(output_dir, k) for k in ["dw", "sentinel", "intersections", "maps", "stats", "reportes"]}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    return dirs


def process_dynamic_world(geometry, output_dir, last_day_prev, last_day_curr):
    """Genera y exporta los mosaicos de Dynamic World"""
    before = get_dw_mosaic_1year(last_day_prev, geometry)
    current = get_dw_mosaic_1year(last_day_curr, geometry)

    configs = [("new_urban", 0.5), ("new_urban_strict", 0.7)]
    paths = {}

    for label, threshold in configs:
        path = os.path.join(output_dir, f"{label}.tif")
        paths[label] = path
        if not os.path.exists(path):
            result = before.lt(0.2).And(current.gt(threshold)).rename(label)
            export_image(result, geometry, path)
        else:
            print(f"⏭{label} ya existente: {path}")
    return paths


def build_report(df_path, strict_path, map_html, logo_path, output_dir, month, year):
    """Genera reporte final en JSON y HTML"""
    df = pd.read_csv(df_path)
    if os.path.exists(strict_path):
        df_strict = pd.read_csv(strict_path)[["NOMBRE", "interseccion_ha"]].rename(columns={"interseccion_ha": "interseccion_ha_strict"})
        df = df.merge(df_strict, on="NOMBRE", how="left").fillna(0)
    else:
        df["interseccion_ha_strict"] = 0

    df_top = df.nlargest(5, "interseccion_ha")
    top_upls = [
        {
            "UPL": r["NOMBRE"],
            "INTER_HA": round(r["interseccion_ha"], 2),
            "INTER_HA_STRICT": round(r["interseccion_ha_strict"], 2),
            "TOTAL_HA": round(r["total_ha"], 2)
        }
        for _, r in df_top.iterrows()
    ]

    base_dir = Path(output_dir)
    data = {
        "TITULO": "Reporte de expansión urbana en Bogotá",
        "FECHA_REPORTE": f"{month.capitalize()} {year}",
        "LOGO": make_relative_path(logo_path, base_dir),
        "TOP_UPLS": top_upls,
        "MAPA_INTERACTIVO": make_relative_path(map_html, base_dir)
    }

    json_path = os.path.join(output_dir, "urban_sprawl_reporte.json")
    html_path = os.path.join(output_dir, f"urban_sprawl_reporte_{year}_{month}.html")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    render(Path("urban_sprawl/reporte/report_template.html"), Path(json_path), Path(html_path))
    print(f"✅ Reporte generado: {html_path}")
