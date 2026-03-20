import os
import json
import ee
import pandas as pd
from pathlib import Path

from src.aux_utils import export_image, make_relative_path
from src.config import GCS_OUTPUT_BUCKET, GCS_OUTPUT_PREFIX
from reporte.render_report import render

def get_dw_mosaic_1year(end_date, geometry, bands=None, window_days=60):
    """Mosaico de Dynamic World con ventana temporal robusta a nubes.
    
    Args:
        end_date: Fecha final del período (último día del mes)
        geometry: Geometría del área de interés
        bands: Lista de bandas o 'all' para todas. Si None, solo 'built'
        window_days: Número de días retroactivos para el mosaico (default: 60)
        
    Nota: Usa .median() en lugar de .mosaic() para mejor robustez con nubes.
          Ventana de 60 días = ~18 imágenes en zonas nubladas, suficiente para coherencia.
    """
    end = ee.Date(end_date.strftime("%Y-%m-%d"))
    start = end.advance(-window_days, "day")
    
    collection = (
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterDate(start, end)
        .filterBounds(geometry)
    )
    
    # Debug: mostrar cantidad de imágenes (solo para banda built, no para all)
    if bands != "all":
        count = collection.size().getInfo()
        period_label = f"{window_days}-día ventana" if window_days != 60 else "mes-a-mes (60d)"
        print(f"   🌍 Imágenes Dynamic World ({period_label} hasta {end_date.strftime('%Y-%m-%d')}): {count}")
    
    if bands == "all":
        all_bands = ["water", "trees", "grass", "flooded_vegetation", "crops", 
                     "shrub_and_scrub", "built", "bare", "snow_and_ice"]
        collection = collection.select(all_bands)
    elif bands is not None:
        collection = collection.select(bands)
    else:
        collection = collection.select("built")
    
    # Usar .median() en lugar de .mosaic() para mejor robustez a nubes
    # .median() promedia todos los píxeles válidos en el período
    return collection.median().clip(geometry)

def get_sentinel2_quality_mask(end_date, geometry, cloud_threshold=30):
    """Genera máscara de calidad de Sentinel-2 basada en nubes y clasificación de escena.
    
    Args:
        end_date: Fecha final del período (último día del mes)
        geometry: Geometría del área de interés
        cloud_threshold: Umbral de probabilidad de nubes (0-100, default: 30)
    
    Returns:
        ee.Image: Máscara binaria (1=válido, 0=no válido) en resolución 10m
        
    Criterios de exclusión:
        - MSK_CLDPRB >= cloud_threshold (nubes)
        - SCL en [3, 8, 9, 10, 11] (sombras, nubes, cirrus, nieve)
        
    Nota: Usa solo 30 días del mes para verificar calidad temporal específica.
    """
    end = ee.Date(end_date.strftime("%Y-%m-%d"))
    # Usar solo el mes específico (30 días)
    start = ee.Date(end_date.replace(day=1).strftime("%Y-%m-%d"))
    
    # Colección Sentinel-2 L2A (con corrección atmosférica)
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(start, end)
        .filterBounds(geometry)
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 80))
    )
    
    # Verificar cantidad de imágenes
    count = collection.size().getInfo()
    print(f"   📷 Imágenes Sentinel-2 encontradas (hasta {end_date.strftime('%Y-%m-%d')}): {count}")
    
    if count == 0:
        print(f"   ⚠️ ADVERTENCIA: No hay imágenes Sentinel-2 en el período. Todos los píxeles serán válidos.")
        # Retornar máscara con todo válido si no hay datos
        return ee.Image.constant(1).clip(geometry).rename('quality_mask')
    
    # 1. Máscara de nubes: MSK_CLDPRB < cloud_threshold (usar mediana)
    cld_prob_median = collection.select('MSK_CLDPRB').median()
    cloud_mask = cld_prob_median.lt(cloud_threshold).unmask(0)
    
    # 2. Máscara de clasificación: SCL no en [3, 8, 9, 10, 11] (usar moda)
    scl_mode = collection.select('SCL').mode()
    
    # Crear máscara: TRUE si NO está en clases excluidas
    excluded_classes = [3, 8, 9, 10, 11]
    scl_mask = scl_mode.eq(excluded_classes[0]).Not()
    for cls in excluded_classes[1:]:
        scl_mask = scl_mask.And(scl_mode.eq(cls).Not())
    
    # Aplicar unmask para que píxeles sin datos sean inválidos
    scl_mask = scl_mask.unmask(0)
    
    # 3. Combinar máscaras
    quality_mask = cloud_mask.And(scl_mask)
    
    # 4. Resamplear a 10m (resolución de Dynamic World)
    quality_mask_10m = quality_mask.resample('bilinear').reproject(
        crs='EPSG:4326',
        scale=10
    )
    
    return quality_mask_10m.clip(geometry).rename('quality_mask')

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
    """Genera y exporta los mosaicos de Dynamic World con filtros de calidad S2.
    
    Metodología CORREGIDA:
        1. T1 = DW median (60 días hasta fin de mes anterior)
        2. T2 = DW median (60 días hasta fin de mes actual)
        3. Ambos períodos son temporally coherent (meses consecutivos)
        4. Detectar expansión: T1<0.2 AND T2>threshold
        5. Filtrar por píxeles válidos en ambos períodos
    
    Nota: Cambio de .mosaic() a .median() para robustez a nubes.
          Ventana de 60 días (~18 imágenes) en lugar de 30 días (~12 imágenes).
    """
    print("🛰️  Generando máscaras de calidad Sentinel-2...")
    quality_mask_t1 = get_sentinel2_quality_mask(last_day_prev, geometry, cloud_threshold=30)
    quality_mask_t2 = get_sentinel2_quality_mask(last_day_curr, geometry, cloud_threshold=30)
    
    # PASO 1: Identificar píxeles válidos (calidad en ambos períodos)
    valid_pixels = quality_mask_t1.And(quality_mask_t2)
    print("✅ Máscara de píxeles válidos generada")
    
    # Exportar máscara de píxeles válidos
    valid_pixels_path = os.path.join(output_dir, "valid_pixels.tif")
    export_image(valid_pixels, geometry, valid_pixels_path)
    print(f"💾 Máscara de píxeles válidos exportada: valid_pixels.tif")
    
    print("🌍 Obteniendo mosaicos Dynamic World (mes-a-mes con 60-día ventana)...")
    print(f"   ⮕️ T1 (fin mes anterior): 60 días hasta {last_day_prev.strftime('%Y-%m-%d')}")
    before = get_dw_mosaic_1year(last_day_prev, geometry, window_days=60)
    print(f"   ⮕️ T2 (fin mes actual):    60 días hasta {last_day_curr.strftime('%Y-%m-%d')}")
    current = get_dw_mosaic_1year(last_day_curr, geometry, window_days=60)
    print("✅ Mosaicos DW obtenidos")
    
    # Exportar todas las bandas DW del período actual
    print("📥 Exportando todas las bandas de Dynamic World...")
    current_all_bands = get_dw_mosaic_1year(last_day_curr, geometry, bands="all", window_days=60)
    dw_all_bands_path = os.path.join(output_dir, "dw_current_all_bands.tif")
    export_image(current_all_bands, geometry, dw_all_bands_path)
    paths = {"dw_all_bands": dw_all_bands_path, "valid_pixels": valid_pixels_path}

    print("📥 Generando rasters de expansión con filtros S2...")
    configs = [("new_urban", 0.5), ("new_urban_strict", 0.7)]

    for label, threshold in configs:
        path = os.path.join(output_dir, f"{label}.tif")
        paths[label] = path
        
        # PASO 2: Detectar expansión solo en píxeles válidos
        expansion = before.lt(0.2).And(current.gt(threshold))
        result = expansion.And(valid_pixels).rename(label)
        
        export_image(result, geometry, path)
        print(f"✅ Generado: {label}.tif")
    
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
    
    # Convertir columnas numéricas a float para evitar TypeError
    if 'interseccion_ha' in df.columns:
        df['interseccion_ha'] = pd.to_numeric(df['interseccion_ha'], errors='coerce').fillna(0)
    if 'no_interseccion_ha' in df.columns:
        df['no_interseccion_ha'] = pd.to_numeric(df['no_interseccion_ha'], errors='coerce').fillna(0)
    if 'total_ha' in df.columns:
        df['total_ha'] = pd.to_numeric(df['total_ha'], errors='coerce').fillna(0)
    
    if os.path.exists(strict_path):
        df_strict = pd.read_csv(strict_path)[["NOMBRE", "interseccion_ha"]].rename(columns={"interseccion_ha": "interseccion_ha_strict"})
        df_strict['interseccion_ha_strict'] = pd.to_numeric(df_strict['interseccion_ha_strict'], errors='coerce').fillna(0)
        df = df.merge(df_strict, on="NOMBRE", how="left").fillna(0)
    else:
        df["interseccion_ha_strict"] = 0

    # Manejar caso de DataFrame vacío o sin datos
    has_expansion = len(df) > 0 and df['interseccion_ha'].sum() > 0
    
    if has_expansion:
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
    else:
        top_upls = []

    base_dir = Path(output_dir)
    fecha_rango = f"{month}_{year}"
    map_iframe_url = f"https://storage.googleapis.com/{GCS_OUTPUT_BUCKET}/{GCS_OUTPUT_PREFIX}/{year}_{mes_num:02d}/maps/map_expansion.html"
    data = {
        "TITULO": f"Expansión urbana {month.capitalize()} {year}",
        "FECHA_REPORTE": f"{month.capitalize()} {year}",
        "HEADER_IMG1": gcs_to_base64_data_uri(header_img1_path),
        "HEADER_IMG2": gcs_to_base64_data_uri(header_img2_path),
        "FOOTER_IMG": gcs_to_base64_data_uri(footer_img_path),
        "MAP_IFRAME_URL": map_iframe_url,
        "TOP_UPLS": top_upls,
        "HAS_EXPANSION": has_expansion,
        "month": month,
        "year": year,
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
