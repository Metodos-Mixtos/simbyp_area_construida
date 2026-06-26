import os
import json
import ee
import pandas as pd
from pathlib import Path

from src.aux_utils import export_image, make_relative_path
from src.config import GCS_OUTPUT_BUCKET, GCS_OUTPUT_PREFIX, URB_PROB, DW_LOOKBACK_T1_DAYS, DW_LOOKBACK_T2_DAYS
from reporte.render_report import render

def get_dw_composite(end_date, geometry, lookback_days=30):
    """
    Composición mediana de Dynamic World (built) usando lookback configurable.
    
    Args:
        end_date: Fecha final del período
        geometry: Geometría del AOI
        lookback_days: Días hacia atrás desde end_date (default: 30)
    
    Returns:
        Imagen compuesta mediana de la banda 'built'
    """
    end = ee.Date(end_date.strftime("%Y-%m-%d"))
    start = end.advance(-lookback_days, "day")
    collection = (
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterDate(start, end)
        .filterBounds(geometry)
        .select("built")
    )
    return collection.median().clip(geometry)

def prepare_folders(base_path, anio, mes):
    """Crea los directorios de salida organizados por componente"""
    output_base = os.path.join(base_path, "urban_sprawl", "outputs")
    output_dir = os.path.join(output_base, f"{anio}_{mes:02d}")
    os.makedirs(output_dir, exist_ok=True)

    dirs = {k: os.path.join(output_dir, k) for k in ["dw", "sentinel", "intersections", "maps", "stats", "reportes"]}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    return dirs


def process_dynamic_world(geometry, output_dir, last_day_prev, last_day_curr, anio, mes):
    """
    Genera y exporta la detección de expansión urbana usando Dynamic World.
    
    Nueva lógica temporal para capturar construcciones progresivas:
    - t2: último día del mes actual con lookback configurable (construcción reciente)
    - t1: 90 días antes del INICIO de t2 con lookback configurable (verificar ausencia previa)
    - Delta: 90 días de gap entre el FIN de t1 y el INICIO de t2
    
    Los valores de lookback se configuran en config.py:
    - DW_LOOKBACK_T2_DAYS (default: 30 días)
    - DW_LOOKBACK_T1_DAYS (default: 90 días)
    
    Esto permite detectar el proceso: suelo desnudo → limpieza → construcción
    """
    from datetime import timedelta
    
    # t2: período actual (lookback configurable)
    t2_end = last_day_curr
    start_t2 = t2_end - timedelta(days=DW_LOOKBACK_T2_DAYS)
    current = get_dw_composite(t2_end, geometry, lookback_days=DW_LOOKBACK_T2_DAYS)
    
    # t1: 90 días antes del INICIO de t2 (lookback configurable)
    t1_end = start_t2 - timedelta(days=90)
    before = get_dw_composite(t1_end, geometry, lookback_days=DW_LOOKBACK_T1_DAYS)
    
    print(f"\n📅 Períodos DW:")
    print(f"   t1: {(t1_end - timedelta(days=DW_LOOKBACK_T1_DAYS)).strftime('%Y-%m-%d')} a {t1_end.strftime('%Y-%m-%d')} ({DW_LOOKBACK_T1_DAYS} días lookback)")
    print(f"   t2: {start_t2.strftime('%Y-%m-%d')} a {t2_end.strftime('%Y-%m-%d')} ({DW_LOOKBACK_T2_DAYS} días lookback)")
    print(f"   Delta (gap): 90 días entre fin de t1 ({t1_end.strftime('%Y-%m-%d')}) e inicio de t2 ({start_t2.strftime('%Y-%m-%d')})")
    print(f"   Composición: MEDIANA (reducción de ruido/nubes)")

    label = f"new_urban_{anio}_{mes:02d}"
    path = os.path.join(output_dir, f"{label}.tif")
    
    if not os.path.exists(path):
        result = before.lt(0.2).And(current.gt(URB_PROB)).rename(label)
        export_image(result, geometry, path)
        print(f"✅ Generado {label} con umbral URB_PROB={URB_PROB}")
    else:
        print(f"⏭ {label} ya existente: {path}")
    
    return path


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

def build_report(df_path, map_html, header_img1_path, header_img2_path, footer_img_path, output_dir, month, year, mes_num):
    """Genera reporte final en JSON y HTML"""
    df = pd.read_csv(df_path)
    
    # Convertir columnas numéricas
    numeric_columns = ['interseccion_ha', 'no_interseccion_ha', 'total_ha']
    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    
    # Verificar si hay datos válidos
    if len(df) == 0 or df['interseccion_ha'].sum() == 0:
        print("⚠️ No hay datos de expansión para generar reporte (posiblemente SAR rechazó todos los polígonos)")
        top_upls = []
    else:
        df_top = df.nlargest(5, "interseccion_ha")
        top_upls = [
            {
                "UPL": r["NOMBRE"],
                "INTER_HA": round(r["interseccion_ha"], 2),
                "TOTAL_HA": round(r["total_ha"], 2)
            }
            for _, r in df_top.iterrows()
        ]

    base_dir = Path(output_dir)
    fecha_rango = f"{month}_{year}"
    map_iframe_url = f"https://storage.googleapis.com/{GCS_OUTPUT_BUCKET}/{GCS_OUTPUT_PREFIX}/{year}_{mes_num:02d}/maps/map_expansion_{year}_{mes_num:02d}.html"
    
    # Nombres de archivos con año y mes para trazabilidad
    tif_filename = f"new_urban_{year}_{mes_num:02d}.tif"
    inter_geojson_filename = f"new_urban_{year}_{mes_num:02d}_intersections.geojson"
    no_inter_geojson_filename = f"new_urban_{year}_{mes_num:02d}_no_intersections.geojson"
    csv_filename = f"resumen_expansion_upl_ha_{year}_{mes_num:02d}.csv"
    
    data = {
        "TITULO": "Reporte de expansión urbana en Bogotá",
        "FECHA_REPORTE": f"{month.capitalize()} {year}",
        "HEADER_IMG1": gcs_to_base64_data_uri(header_img1_path),
        "HEADER_IMG2": gcs_to_base64_data_uri(header_img2_path),
        "FOOTER_IMG": gcs_to_base64_data_uri(footer_img_path),
        "MAP_IFRAME_URL": map_iframe_url,
        "TOP_UPLS": top_upls,
        "month": month,
        "year": year,
        "mes_num": f"{mes_num:02d}",
        "URB_PROB": URB_PROB,
        "URB_PROB_PERCENT": int(URB_PROB * 100),
        "FUENTE": "Dynamic World, Google Earth Engine",
        "TIF_FILENAME": tif_filename,
        "INTER_GEOJSON_FILENAME": inter_geojson_filename,
        "NO_INTER_GEOJSON_FILENAME": no_inter_geojson_filename,
        "CSV_FILENAME": csv_filename
    }


    json_path = os.path.join(output_dir, f"urban_sprawl_reporte_{year}_{month}.json")
    html_path = os.path.join(output_dir, f"urban_sprawl_reporte_{year}_{month}.html")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Use absolute path to template based on this file's location
    template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reporte", "report_template.html")
    render(Path(template_path), Path(json_path), Path(html_path))
    print(f"✅ Reporte generado: {html_path}")


def build_no_expansion_report(header_img1_path, header_img2_path, footer_img_path, output_dir, month, year, mes_num):
    """Genera reporte HTML simple cuando no se detectó expansión urbana"""
    
    # Crear JSON con información básica
    report_data = {
        "mes": month,
        "anio": year,
        "mes_num": f"{mes_num:02d}",
        "expansion_detectada": False,
        "mensaje": "No se detectó expansión urbana significativa en este período"
    }
    
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, f"urban_sprawl_reporte_{year}_{month}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    
    # Generar HTML simple
    html_content = f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Reporte de expansión urbana en Bogotá</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin:0; padding:0; background:white; color:#1a1a1a; font-family: Arial, sans-serif; font-size: 14px; line-height: 1.6; }}
    header.banner {{ background:#e3351f; width:100%; margin:0; padding:1.5rem 0; display:flex; justify-content:space-between; align-items:center; box-sizing:border-box; }}
    header.banner img {{ height:70px; margin:0 2rem; }}
    footer.banner {{ background:#e3351f; width:100%; margin:0; padding:1.5rem 0; text-align:center; display:block; box-sizing:border-box; position:relative; left:0; right:0; }}
    footer.banner img {{ height:70px; }}
    .wrap {{max-width: 1000px; margin: 0 auto; padding: 2rem; padding-bottom:0;}}
    h1 {{font-size: 22pt; font-weight: bold; text-align: center;}}
    .note {{font-size: 11pt; color: #555; text-align: center;}}
    .message-box {{ 
      background: #fff5f5; 
      border: 2px solid #c53030; 
      border-radius: 8px; 
      padding: 3rem 2rem; 
      margin: 3rem 0; 
      text-align: center;
    }}
    .message-box h2 {{
      color: #8F0000;
      font-size: 20pt;
      margin-bottom: 1rem;
    }}
    .message-box p {{
      font-size: 14pt;
      color: #555;
      line-height: 1.8;
    }}
    section {{ margin-bottom:3rem; }}
  </style>
</head>
<body>
  <header class="banner">
    <img src="{gcs_to_base64_data_uri(header_img1_path)}" alt="Aquí sí pasa Bogotá">
    <img src="{gcs_to_base64_data_uri(header_img2_path)}" alt="Bogotá">
  </header>
  <div class="wrap">
    <h1>Reporte mensual de expansión urbana en Bogotá</h1>
    <div class="note">{month.capitalize()} {year}</div> 

    <div class="message-box">
      <p><strong>Para el mes analizado no se detectó expansión urbana.</strong></p>
      <p>Durante el periodo de {month.capitalize()} {year}, no se identificaron cambios significativos en las coberturas que indiquen procesos de expansión urbana en el área de estudio según los criterios metodológicos establecidos.</p>
    </div>

    <section>
      <h2 style="color: #8F0000;">Metodología</h2>
      <p style="text-align: justify;">
        Para la detección de expansión urbana se utilizó el enfoque de detección de cambio de coberturas usando bandas de probabilidad, 
        basado en el conjunto de datos Dynamic World de Google Earth Engine. Este conjunto de datos proporciona, para cada píxel y fecha, 
        las probabilidades de pertenecer a diferentes clases de cobertura, entre ellas la clase <strong>área construida</strong>.
      </p>
      <p style="text-align: justify;">
        Se aplica un umbral probabilístico de <strong>{URB_PROB * 100:.0f}%</strong> para identificar las zonas que pasaron de ser no construidas a construidas. 
        Cuando no se detectan píxeles que cumplan estos criterios en el período analizado, significa que no hubo cambios 
        significativos en las coberturas urbanas del área de estudio.
      </p>
      <p style="font-size: 11pt; color: #555;">
        <strong>Fuente:</strong> Dynamic World, Google Earth Engine
      </p>
    </section>
  </div>
  
  <footer class="banner">
    <img src="{gcs_to_base64_data_uri(footer_img_path)}" alt="Secretaría de Planeación">
  </footer>
</body>
</html>
"""
    
    html_path = os.path.join(output_dir, f"urban_sprawl_reporte_{year}_{month}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"✅ Reporte sin expansión generado: {html_path}")
