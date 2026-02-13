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


def gcs_to_base64_data_uri(gcs_path):
    """Convierte una imagen de GCS a data URI en Base64 para embeber en HTML"""
    import base64
    from google.cloud import storage
    
    if gcs_path.startswith("gs://"):
        # Descargar imagen desde GCS
        path_without_prefix = gcs_path[5:]
        parts = path_without_prefix.split('/', 1)
        bucket_name = parts[0]
        blob_path = parts[1] if len(parts) > 1 else ''
        
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        
        # Descargar como bytes
        image_bytes = blob.download_as_bytes()
        
        # Convertir a Base64
        base64_encoded = base64.b64encode(image_bytes).decode('utf-8')
        
        # Determinar el tipo MIME basado en la extensión
        if blob_path.lower().endswith('.png'):
            mime_type = 'image/png'
        elif blob_path.lower().endswith(('.jpg', '.jpeg')):
            mime_type = 'image/jpeg'
        else:
            mime_type = 'image/png'  # Default
        
        # Retornar data URI
        return f"data:{mime_type};base64,{base64_encoded}"
    
    return gcs_path

def build_report(df_path, strict_path, map_html, header_img1_path, header_img2_path, footer_img_path, output_dir, month, year, mes_num):
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
    fecha_rango = f"{month}_{year}"
    data = {
        "TITULO": "Reporte de expansión urbana en Bogotá",
        "FECHA_REPORTE": f"{month.capitalize()} {year}",
        "HEADER_IMG1": gcs_to_base64_data_uri(header_img1_path),
        "HEADER_IMG2": gcs_to_base64_data_uri(header_img2_path),
        "FOOTER_IMG": gcs_to_base64_data_uri(footer_img_path),
        "TOP_UPLS": top_upls,
        "month": month,
        "year": year,
        "mes_num": f"{mes_num:02d}",
        "mes_num": f"{mes_num:02d}",
        "FUENTE": "Dynamic World, Google Earth Engine"
    }


    json_path = os.path.join(output_dir, "urban_sprawl_reporte.json")
    html_path = os.path.join(output_dir, f"urban_sprawl_reporte_{year}_{month}.html")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Use absolute path to template based on this file's location
    template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reporte", "report_template.html")
    render(Path(template_path), Path(json_path), Path(html_path))
    print(f"✅ Reporte generado: {html_path}")
