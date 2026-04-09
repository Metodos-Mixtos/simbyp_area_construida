import os
import json
import ee
import pandas as pd
from pathlib import Path

from src.aux_utils import export_image, make_relative_path
from src.config import GCS_OUTPUT_BUCKET, GCS_OUTPUT_PREFIX, ENTROPY_THRESHOLD
from src.stats_utils import calculate_entropy_ee
from reporte.render_report import render

def get_dw_mosaic_1year(end_date, geometry, include_entropy=False):
    """
    Mosaico de Dynamic World (built) de los últimos 365 días hasta end_date.
    
    Args:
        end_date: Fecha final del periodo
        geometry: Geometría ee.Geometry para filtrar y recortar
        include_entropy: Si True, calcula y agrega banda de entropía Shannon
    
    Returns:
        ee.Image: Imagen con banda 'built' y opcionalmente 'entropy'
    """
    end = ee.Date(end_date.strftime("%Y-%m-%d"))
    start = end.advance(-365, "day")
    
    if include_entropy:
        # Cargar todas las bandas de probabilidad para calcular entropía
        collection = (
            ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
            .filterDate(start, end)
            .filterBounds(geometry)
            .sort("system:time_start", False)
            .sort("system:index")
        )
        mosaic = collection.mosaic().clip(geometry)
        # Calcular entropía y agregar como banda
        mosaic_with_entropy = calculate_entropy_ee(mosaic)
        # Retornar solo 'built' y 'entropy'
        return mosaic_with_entropy.select(['built', 'entropy'])
    else:
        # Comportamiento original: solo banda 'built'
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
    """
    Genera y exporta los mosaicos de Dynamic World con validación por entropía.
    
    Esta rama SIEMPRE calcula entropía para validar la expansión urbana.
    Exporta:
    - Rasters binarios de expansión (new_urban.tif, new_urban_strict.tif)
    - Bandas de entropía para t1 y t2
    - Versiones filtradas por entropía (new_urban_entropy_filtered.tif, etc.)
    """
    # Cargar mosaicos con cálculo de entropía
    before = get_dw_mosaic_1year(last_day_prev, geometry, include_entropy=True)
    current = get_dw_mosaic_1year(last_day_curr, geometry, include_entropy=True)

    configs = [("new_urban", 0.5), ("new_urban_strict", 0.7)]
    paths = {}

    for label, threshold in configs:
        path = os.path.join(output_dir, f"{label}.tif")
        paths[label] = path
        if not os.path.exists(path):
            result = before.select('built').lt(0.2).And(current.select('built').gt(threshold)).rename(label)
            export_image(result, geometry, path)
        else:
            print(f"⏭{label} ya existente: {path}")
    
    # Exportar bandas de entropía
    entropy_t1_path = os.path.join(output_dir, "entropy_t1.tif")
    entropy_t2_path = os.path.join(output_dir, "entropy_t2.tif")
    
    if not os.path.exists(entropy_t1_path):
        print(f"🔍 Exportando entropía T1 (umbral={ENTROPY_THRESHOLD})...")
        export_image(before.select('entropy'), geometry, entropy_t1_path)
    else:
        print(f"⏭Entropía T1 ya existente: {entropy_t1_path}")
    
    if not os.path.exists(entropy_t2_path):
        print(f"🔍 Exportando entropía T2 (umbral={ENTROPY_THRESHOLD})...")
        export_image(current.select('entropy'), geometry, entropy_t2_path)
    else:
        print(f"⏭Entropía T2 ya existente: {entropy_t2_path}")
    
    paths['entropy_t1'] = entropy_t1_path
    paths['entropy_t2'] = entropy_t2_path
    
    # Generar versiones filtradas por entropía
    for label, threshold in configs:
            filtered_label = f"{label}_entropy_filtered"
            filtered_path = os.path.join(output_dir, f"{filtered_label}.tif")
            paths[filtered_label] = filtered_path
            
            if not os.path.exists(filtered_path):
                # Criterio: expansión urbana Y entropía < umbral (confianza alta)
                original_mask = before.select('built').lt(0.2).And(current.select('built').gt(threshold))
                low_entropy_mask = current.select('entropy').lt(ENTROPY_THRESHOLD)
                filtered_result = original_mask.And(low_entropy_mask).rename(filtered_label)
                
                print(f"🔍 Exportando {filtered_label} (H < {ENTROPY_THRESHOLD})...")
                export_image(filtered_result, geometry, filtered_path)
            else:
                print(f"⏭{filtered_label} ya existente: {filtered_path}")
    
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
    """
    Genera reporte final en JSON y HTML.
    
    Esta rama SIEMPRE usa datos validados por entropía como datos principales.
    """
    import rasterio
    
    # Usar CSVs validados por entropía (si existen, sino usa originales)
    stats_dir = os.path.join(os.path.dirname(output_dir), "stats")
    
    # Verificar si existen CSVs validados por entropía
    entropy_csv = os.path.join(stats_dir, "resumen_expansion_upl_ha_entropy.csv")
    entropy_strict_csv = os.path.join(stats_dir, "resumen_expansion_upl_ha_entropy_strict.csv")
    
    if os.path.exists(entropy_csv):
        df_path = entropy_csv
        print(f"📊 Usando datos validados por entropía: {entropy_csv}")
    
    if os.path.exists(entropy_strict_csv):
        strict_path = entropy_strict_csv
        print(f"📊 Usando datos estrictos validados: {entropy_strict_csv}")
    
    # Cargar datos (originales o validados según disponibilidad)
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
    map_iframe_url = f"https://storage.googleapis.com/{GCS_OUTPUT_BUCKET}/{GCS_OUTPUT_PREFIX}/{year}_{mes_num:02d}/maps/map_expansion.html"
    
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
        "FUENTE": "Dynamic World, Google Earth Engine"
    }
    
    # Agregar estadísticas de entropía
    # Calcular estadísticas de píxeles desde los rasters
    dw_dir = os.path.join(os.path.dirname(output_dir), "dw")
    
    def count_pixels(tif_path):
        """Cuenta píxeles positivos en un raster binario"""
        if not os.path.exists(tif_path):
            return 0
        try:
            with rasterio.open(tif_path) as src:
                data = src.read(1)
                return int((data > 0).sum())
        except Exception as e:
            print(f"⚠️ Error contando píxeles en {tif_path}: {e}")
            return 0
    
    # Contar píxeles para expansión normal
    normal_orig = count_pixels(os.path.join(dw_dir, "new_urban.tif"))
    normal_filtered = count_pixels(os.path.join(dw_dir, "new_urban_entropy_filtered.tif"))
    
    # Contar píxeles para expansión estricta
    strict_orig = count_pixels(os.path.join(dw_dir, "new_urban_strict.tif"))
    strict_filtered = count_pixels(os.path.join(dw_dir, "new_urban_strict_entropy_filtered.tif"))
    
    # Calcular estadísticas
    normal_retention = (normal_filtered / normal_orig * 100) if normal_orig > 0 else 0
    strict_retention = (strict_filtered / strict_orig * 100) if strict_orig > 0 else 0
    
    data["ENTROPY_ENABLED"] = True
    data["ENTROPY_THRESHOLD"] = ENTROPY_THRESHOLD
    data["ENTROPY_STATS"] = {
        "normal": {
            "original_pixels": f"{normal_orig:,}",
            "validated_pixels": f"{normal_filtered:,}",
            "filtered_out": f"{normal_orig - normal_filtered:,}",
            "retention_rate": f"{normal_retention:.1f}",
            "avg_entropy": "N/A"  # Se puede calcular después si es necesario
        },
        "strict": {
            "original_pixels": f"{strict_orig:,}",
            "validated_pixels": f"{strict_filtered:,}",
            "filtered_out": f"{strict_orig - strict_filtered:,}",
            "retention_rate": f"{strict_retention:.1f}",
            "avg_entropy": "N/A"
        }
    }

    json_path = os.path.join(output_dir, "urban_sprawl_reporte.json")
    html_path = os.path.join(output_dir, f"urban_sprawl_reporte_{year}_{month}.html")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Use absolute path to template based on this file's location
    template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reporte", "report_template.html")
    render(Path(template_path), Path(json_path), Path(html_path))
    print(f"✅ Reporte generado: {html_path}")
