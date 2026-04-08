import os
import json
import ee
import pandas as pd
from pathlib import Path
import shutil

from src.aux_utils import export_image, make_relative_path
from src.config import GCS_OUTPUT_BUCKET, GCS_OUTPUT_PREFIX
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

def get_sentinel2_ndbi_mask(end_date, geometry, ndbi_threshold=0.3, lookback_days=60):
    """
    Calcula la máscara NDBI de Sentinel-2 para validar expansión urbana.
    
    NDBI = (SWIR - NIR) / (SWIR + NIR) = (B11 - B8) / (B11 + B8)
    
    Args:
        end_date: Fecha final del período
        geometry: Geometría del área de interés
        ndbi_threshold: Umbral NDBI (default: 0.3). Valores >= threshold indican superficie construida
        lookback_days: Días hacia atrás para buscar imágenes (default: 60 = 2 meses, coherente con alertas mensuales)
        
    Returns:
        dict con 'confirmed' (NDBI>=threshold) y 'has_data' (máscara de píxeles con datos NDBI válidos)
        
    Nota: Ventana de 60 días mantiene especificidad temporal para alertas mensuales.
          Retorna tanto la máscara confirmada como la máscara de datos válidos para separar
          construcciones confirmadas de aquellas sin datos por nubes.
    """
    end = ee.Date(end_date.strftime("%Y-%m-%d"))
    start = end.advance(-lookback_days, "day")
    
    # Colección Sentinel-2 L2A
    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(start, end)
        .filterBounds(geometry)
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 50))
        .select(['B8', 'B11', 'MSK_CLDPRB'])
    )
    
    count = collection.size().getInfo()
    print(f"   📷 Imágenes Sentinel-2 para NDBI ({lookback_days} días hasta {end_date.strftime('%Y-%m-%d')}): {count}")
    
    if count == 0:
        print(f"   ⚠️ No hay imágenes Sentinel-2. Todas las expansiones serán no confirmadas.")
        # Retornar máscaras vacías
        empty_mask = ee.Image.constant(0).clip(geometry)
        return {
            'confirmed': empty_mask.rename('confirmed'),
            'has_data': empty_mask.rename('has_data')
        }
    
    # Función para calcular NDBI y filtrar nubes
    def calculate_ndbi(image):
        # Máscara de nubes (probabilidad < 30%)
        cloud_mask = image.select('MSK_CLDPRB').lt(30)
        
        # Calcular NDBI = (B11 - B8) / (B11 + B8)
        nir = image.select('B8').float()
        swir = image.select('B11').float()
        
        ndbi = swir.subtract(nir).divide(swir.add(nir)).rename('ndbi')
        
        # Aplicar máscara de nubes
        return ndbi.updateMask(cloud_mask)
    
    # Calcular NDBI para todas las imágenes y obtener mediana
    ndbi_collection = collection.map(calculate_ndbi)
    ndbi_median = ndbi_collection.median()
    
    # Máscara 1: Píxeles con NDBI >= threshold (confirmados)
    confirmed_mask = ndbi_median.gte(ndbi_threshold).rename('confirmed')
    
    # Máscara 2: Píxeles donde NDBI tiene datos válidos (no NoData)
    has_data_mask = ndbi_median.mask().rename('has_data')
    
    print(f"   ✅ Máscaras NDBI calculadas (umbral: {ndbi_threshold})")
    
    return {
        'confirmed': confirmed_mask.clip(geometry),
        'has_data': has_data_mask.clip(geometry)
    }
    
    return ndbi_mask.clip(geometry)

def cleanup_month_outputs(dirs):
    """
    Elimina archivos específicos del período para garantizar datos frescos.
    Busca y elimina archivos que coincidan con patrones comunes de expansión.
    Preserva archivos _original.tif para validación.
    """
    patterns = [
        "new_urban_confirmed*.tif",      # new_urban_confirmed.tif, etc.
        "new_urban_unconfirmed*.tif",    # new_urban_unconfirmed.tif, etc.
        "new_urban_strict_confirmed*.tif",
        "new_urban_strict_unconfirmed*.tif",
        "area_*.tif",               # area_construida.tif, etc.
        "area_*.csv",               # area stats
        "dw_*.tif",                 # dw_current_all_bands.tif, etc.
        "expansion_dw*.csv",        # expansion stats
        "resumen_expansion*.csv",   # resumen stats
        "*_intersections.geojson",  # intersections
        "*_no_intersections.geojson", # no intersections
    ]
    
    # Buscar en directorios específicos
    cleanup_dirs = ["dw", "intersections", "stats"]
    
    deleted_count = 0
    for dir_key in cleanup_dirs:
        if dir_key not in dirs:
            continue
        dir_path = dirs[dir_key]
        if not os.path.exists(dir_path):
            continue
        
        for pattern in patterns:
            matching_files = list(Path(dir_path).glob(pattern))
            for file in matching_files:
                try:
                    if file.is_file():
                        file.unlink()
                        deleted_count += 1
                    elif file.is_dir():
                        shutil.rmtree(file)
                        deleted_count += 1
                except Exception as e:
                    print(f"   ⚠️ No se pudo eliminar {file}: {e}")
    
    if deleted_count > 0:
        print(f"🗑️  Limpiados {deleted_count} archivo(s) del período anterior")


def prepare_folders(base_path, anio, mes):
    """Crea los directorios de salida organizados por componente"""
    output_base = os.path.join(base_path, "urban_sprawl", "outputs")
    output_dir = os.path.join(output_base, f"{anio}_{mes:02d}")
    os.makedirs(output_dir, exist_ok=True)

    dirs = {k: os.path.join(output_dir, k) for k in ["dw", "sentinel", "intersections", "maps", "stats", "reportes"]}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    return dirs


def process_dynamic_world(geometry, output_dir, last_day_prev, last_day_curr, apply_ndbi_validation=True):
    """
    Genera y exporta los mosaicos de Dynamic World con validación NDBI dual.
    
    Args:
        geometry: Geometría del área de interés
        output_dir: Directorio de salida
        last_day_prev: Último día del mes anterior (T1)
        last_day_curr: Último día del mes actual (T2)
        apply_ndbi_validation: Si True, aplica filtro NDBI a las expansiones (default: True)
        
    Returns:
        dict: Rutas de los archivos generados (solo confirmadas para reportar)
        
    Metodología dual (especificidad temporal + no perder info):
        1. Detectar expansión: DW_T1 < 0.2 AND DW_T2 > threshold
        2. Si NDBI validation activo:
           - CONFIRMADAS: expansión AND NDBI >= 0.3 (reportar en HTML)
           - NO CONFIRMADAS: expansión NOT confirmada (NDBI < 0.3 O NoData) (guardar para análisis)
        3. Solo las confirmadas se procesan para reportes públicos
    """
    print("🌍 Obteniendo mosaicos Dynamic World...")
    before = get_dw_mosaic_1year(last_day_prev, geometry)
    current = get_dw_mosaic_1year(last_day_curr, geometry)

    # Obtener máscaras NDBI del período actual (T2)
    if apply_ndbi_validation:
        print("🔍 Calculando máscaras NDBI para validación...")
        # Usar 60 días para especificidad temporal (alertas mensuales)
        ndbi_masks = get_sentinel2_ndbi_mask(last_day_curr, geometry, ndbi_threshold=0.3, lookback_days=60)
        confirmed_mask = ndbi_masks['confirmed']
        has_data_mask = ndbi_masks['has_data']
        print("✅ Máscaras NDBI listas")
    else:
        print("⏭️ Validación NDBI desactivada")
        confirmed_mask = None
        has_data_mask = None

    configs = [("new_urban", 0.5), ("new_urban_strict", 0.7)]
    paths = {}

    for label, threshold in configs:
        # Detectar expansión con Dynamic World
        expansion = before.lt(0.2).And(current.gt(threshold))
        
        # === GUARDAR ORIGINAL (sin filtro NDBI) para validación ===
        path_original = os.path.join(output_dir, f"{label}_original.tif")
        if apply_ndbi_validation and not os.path.exists(path_original):
            result_orig = expansion.rename(f"{label}_orig")
            export_image(result_orig, geometry, path_original)
            print(f"   📌 {label}_original: Expansión DW sin filtro NDBI (referencia para validación)")
        
        if apply_ndbi_validation:
            # CONFIRMADAS: expansión + NDBI >= 0.3
            expansion_confirmed = expansion.And(confirmed_mask)
            
            # NO CONFIRMADAS: expansión que NO confirma (NDBI < 0.3 O NoData)
            # = Expansión DW - Confirmadas
            expansion_unconfirmed = expansion.And(confirmed_mask.Not())
            
            # Exportar CONFIRMADAS (para reporte)
            path_confirmed = os.path.join(output_dir, f"{label}_confirmed.tif")
            if not os.path.exists(path_confirmed):
                result = expansion_confirmed.rename(label)
                export_image(result, geometry, path_confirmed)
                print(f"   ✓ {label}_confirmed: Expansión NDBI-validada (>= 0.3)")
            else:
                print(f"   ⏭️ {label}_confirmed ya existe")
            
            # Exportar NO CONFIRMADAS (para análisis interno)
            path_unconfirmed = os.path.join(output_dir, f"{label}_unconfirmed.tif")
            if not os.path.exists(path_unconfirmed):
                result_unc = expansion_unconfirmed.rename(f"{label}_unc")
                export_image(result_unc, geometry, path_unconfirmed)
                print(f"   💾 {label}_unconfirmed: Expansión no validada (NDBI < 0.3 o NoData)")
            else:
                print(f"   ⏭️ {label}_unconfirmed ya existe")
            
            # Solo retornar las confirmadas para el pipeline principal
            paths[label] = path_confirmed
        else:
            # Sin validación NDBI, exportar todo
            path = os.path.join(output_dir, f"{label}.tif")
            paths[label] = path
            if not os.path.exists(path):
                result = expansion.rename(label)
                export_image(result, geometry, path)
                print(f"   ✓ {label}: Expansión detectada sin validación NDBI")
            else:
                print(f"   ⏭️ {label} ya existe")
    
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
    # Verificar si existe el CSV, si no, crear uno vacío
    if not os.path.exists(df_path):
        print(f"⚠️ {df_path} no existe, creando CSV vacío")
        df = pd.DataFrame(columns=["NOMBRE", "interseccion_ha", "no_interseccion_ha", "total_ha"])
    else:
        df = pd.read_csv(df_path)
    
    if os.path.exists(strict_path):
        df_strict = pd.read_csv(strict_path)[["NOMBRE", "interseccion_ha"]].rename(columns={"interseccion_ha": "interseccion_ha_strict"})
        df = df.merge(df_strict, on="NOMBRE", how="left").fillna(0)
    else:
        df["interseccion_ha_strict"] = 0

    # Manejar caso de DataFrame vacío (sin expansión)
    if len(df) == 0:
        print("⚠️ Sin datos de expansión: generando reporte vacío")
        top_upls = []
    else:
        # Convertir a numérico por si acaso
        df["interseccion_ha"] = pd.to_numeric(df["interseccion_ha"], errors='coerce').fillna(0)
        
        # Filtrar PRIMERO: Solo UPLs donde el valor REDONDEADO a 4 decimales de intersection_ha > 0
        # (Asegura que solo aparezcan UPLs con expansión VISIBLE en la tabla: > 0.0000 ha)
        df_filtered = df[df["interseccion_ha"].round(4) > 0]
        
        # Luego ORDENAR: Top 5 UPLs con mayor expansión en áreas restrictivas
        # (Asegura que la tabla siempre muestre solo UPLs con REAL expansión en restricciones)
        if len(df_filtered) > 0:
            df_top = df_filtered.nlargest(5, "interseccion_ha")
        else:
            df_top = pd.DataFrame()
        
        top_upls = [
            {
                "UPL": r["NOMBRE"],
                "INTER_HA": round(r["interseccion_ha"], 4),
                "INTER_HA_STRICT": round(r["interseccion_ha_strict"], 4),
                "TOTAL_HA": round(r["total_ha"], 4)
            }
            for _, r in df_top.iterrows()
        ]

    base_dir = Path(output_dir)
    fecha_rango = f"{month}_{year}"
    map_iframe_url = f"https://storage.googleapis.com/{GCS_OUTPUT_BUCKET}/{GCS_OUTPUT_PREFIX}/{year}_{mes_num:02d}/maps/map_expansion.html"
    
    # Determinar si hay expansión
    has_expansion = len(top_upls) > 0
    
    data = {
        "TITULO": "Reporte de expansión urbana en Bogotá",
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
