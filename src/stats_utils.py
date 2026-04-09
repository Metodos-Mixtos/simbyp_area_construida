import geopandas as gpd
import pandas as pd
import numpy as np
import rasterio
import os
import shutil
from shapely.geometry import shape
from rasterio.features import shapes
from pathlib import Path
from src.aux_utils import download_gcs_to_temp, TEMP_DATA_DIR

# ============================================================================
# Bandas de Dynamic World v1 (orden estándar de GOOGLE/DYNAMICWORLD/V1)
# ============================================================================
# Fuente: https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_DYNAMICWORLD_V1
DW_BANDS = [
    'water',             # Probabilidad de agua
    'trees',             # Probabilidad de árboles
    'grass',             # Probabilidad de césped/pasto
    'flooded_vegetation',# Probabilidad de vegetación inundada
    'crops',             # Probabilidad de cultivos
    'shrub_and_scrub',   # Probabilidad de arbustos y matorrales
    'built',             # Probabilidad de área construida
    'bare',              # Probabilidad de suelo desnudo
    'snow_and_ice'       # Probabilidad de nieve y hielo
]
# Total: 9 bandas de probabilidad [0,1]
# Máxima entropía teórica: log2(9) ≈ 3.17 bits

def create_intersections(new_urban_tif, sac_path, reserva_path, eep_path, output_dir):
    """
    Convierte un raster binario de expansión urbana a polígonos,
    genera intersecciones con SAC, Reserva y EEP,
    y maneja el caso en que no haya geometrías válidas.
    """
    base_name = Path(new_urban_tif).stem

    with rasterio.open(new_urban_tif) as src:
        data = src.read(1)
        mask = data > 0
        results = list(shapes(data, mask=mask, transform=src.transform))

    # Validar si hay geometrías
    if not results:
        print(f"⚠️ No se encontraron píxeles positivos en {base_name}. Se omite intersección.")
        empty_path = os.path.join(output_dir, f"{base_name}_intersections.geojson")
        gpd.GeoDataFrame(geometry=[], crs=src.crs).to_file(empty_path)
        return

    features = []
    for geom, value in results:
        if geom and geom.get("type") in ("Polygon", "MultiPolygon"):
            features.append({"geometry": shape(geom), "value": value})

    if not features:
        print(f"⚠️ Ninguna geometría válida encontrada en {base_name}.")
        gpd.GeoDataFrame(geometry=[], crs=src.crs).to_file(
            os.path.join(output_dir, f"{base_name}_intersections.geojson")
        )
        return

    gdf_newurban = gpd.GeoDataFrame(features, geometry="geometry", crs=src.crs)

    sac_local = download_gcs_to_temp(sac_path)
    res_local = download_gcs_to_temp(reserva_path)
    eep_local = download_gcs_to_temp(eep_path)

    gdf_sac = gpd.read_file(sac_local).to_crs(gdf_newurban.crs)
    gdf_res = gpd.read_file(res_local).to_crs(gdf_newurban.crs)
    gdf_eep = gpd.read_file(eep_local).to_crs(gdf_newurban.crs)

    # Clean up temp files
    for p, lp in [(sac_path, sac_local), (reserva_path, res_local), (eep_path, eep_local)]:
        if str(p).startswith("gs://"):
            temp_dir = os.path.dirname(lp)
            # Only delete if it's a subdirectory within TEMP_DATA_DIR, not TEMP_DATA_DIR itself
            if temp_dir != str(TEMP_DATA_DIR) and os.path.isdir(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except PermissionError as e:
                    print(f"⚠️ No se pudo eliminar el directorio temporal {temp_dir}: {e}. Continuando...")
            elif os.path.isfile(lp):
                try:
                    os.unlink(lp)
                except (PermissionError, OSError) as e:
                    print(f"⚠️ No se pudo eliminar el archivo temporal {lp}: {e}. Continuando...")

    gdf_inter = pd.concat([
        gpd.overlay(gdf_newurban, gdf_sac, how="intersection"),
        gpd.overlay(gdf_newurban, gdf_res, how="intersection"),
        gpd.overlay(gdf_newurban, gdf_eep, how="intersection")
    ], ignore_index=True)

    gdf_inter.to_file(os.path.join(output_dir, f"{base_name}_intersections.geojson"))

    if len(gdf_inter) == 0:
        print(f"⚠️ No hubo intersecciones para {base_name}.")
        gdf_newurban.to_file(os.path.join(output_dir, f"{base_name}_no_intersections.geojson"))
        return

    no_inter = gpd.overlay(gdf_newurban, gdf_inter, how="difference")
    no_inter.to_file(os.path.join(output_dir, f"{base_name}_no_intersections.geojson"))
    print(f"✅ Intersecciones generadas correctamente para {base_name}.")


def calculate_expansion_areas(input_dir, output_dir, upl_path, prefix="", file_suffix="new_urban"):

    crs = "EPSG:9377"
    path_no = os.path.join(input_dir, f"{file_suffix}_no_intersections.geojson")
    path_inter = os.path.join(input_dir, f"{file_suffix}_intersections.geojson")

    # Verificar si los archivos existen (pueden no existir si no hubo expansión)
    if not os.path.exists(path_no) or not os.path.exists(path_inter):
        print(f"⏭️ Omitiendo cálculo para {file_suffix}: archivos de intersección no existen (sin expansión detectada)")
        return

    gdf_no = gpd.read_file(path_no).to_crs(crs)
    gdf_inter = gpd.read_file(path_inter).to_crs(crs)

    upl_local = download_gcs_to_temp(upl_path)
    gdf_upl = gpd.read_file(upl_local).to_crs(crs)
    print(f"Debug: UPL columns: {list(gdf_upl.columns)}")
    if "NOMBRE" not in gdf_upl.columns:
        raise ValueError(f"La columna 'NOMBRE' no existe en el archivo UPL. Columnas disponibles: {list(gdf_upl.columns)}")

    # Clean up temp file
    if str(upl_path).startswith("gs://"):
        temp_dir = os.path.dirname(upl_local)
        # Only delete if it's a subdirectory within TEMP_DATA_DIR, not TEMP_DATA_DIR itself
        if temp_dir != str(TEMP_DATA_DIR) and os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except PermissionError as e:
                print(f"⚠️ No se pudo eliminar el directorio temporal {temp_dir}: {e}. Continuando...")
        elif os.path.isfile(upl_local):
            try:
                os.unlink(upl_local)
            except (PermissionError, OSError) as e:
                print(f"⚠️ No se pudo eliminar el archivo temporal {upl_local}: {e}. Continuando...")

    inter_upl = gpd.overlay(gdf_upl, gdf_inter, how="intersection")
    nointer_upl = gpd.overlay(gdf_upl, gdf_no, how="intersection")

    inter_upl["area_ha"] = inter_upl.geometry.area / 10000
    nointer_upl["area_ha"] = nointer_upl.geometry.area / 10000

    resumen = (
        inter_upl.groupby("NOMBRE")["area_ha"].sum().reset_index().rename(columns={"area_ha": "interseccion_ha"})
        .merge(nointer_upl.groupby("NOMBRE")["area_ha"].sum().reset_index().rename(columns={"area_ha": "no_interseccion_ha"}),
               on="NOMBRE", how="outer").fillna(0)
    )
    resumen["total_ha"] = resumen["interseccion_ha"] + resumen["no_interseccion_ha"]

    out_csv = os.path.join(output_dir, f"resumen_expansion_upl_ha_{prefix.strip('_')}.csv") if prefix else os.path.join(output_dir, "resumen_expansion_upl_ha.csv")
    resumen.to_csv(out_csv, index=False)
    print(f"✅ Guardado: {out_csv}")
    return resumen, None


# ============================================================================
# ENTROPÍA - Validación de confianza de clasificación (Shannon 1948)
# ============================================================================

def calculate_shannon_entropy(probabilities):
    """
    Calcula la entropía de Shannon para un conjunto de probabilidades.
    
    Fórmula: H = -Σ(p_i * log2(p_i))
    
    Args:
        probabilities (array-like): Vector de probabilidades [0,1]
        
    Returns:
        float: Entropía Shannon (bits). Rango [0, log2(n)] donde n = número de clases
               0 = máxima confianza (una clase domina)
               log2(n) = mínima confianza (todas equiprobables)
    
    Ejemplo:
        >>> probs = [0.9, 0.05, 0.025, 0.025]  # Una clase domina
        >>> calculate_shannon_entropy(probs)
        0.469  # Baja entropía = confianza alta
        
        >>> probs = [0.25, 0.25, 0.25, 0.25]   # Todas iguales
        >>> calculate_shannon_entropy(probs)
        2.0    # Alta entropía = confianza baja
    """
    probs = np.asarray(probabilities)
    # Evitar log(0)
    probs = probs[probs > 0]
    
    if len(probs) == 0:
        return 0.0
    
    return -np.sum(probs * np.log2(probs))


def apply_entropy_filter_to_raster(input_raster, dw_raster_all_bands, 
                                     output_raster, entropy_threshold=2.0,
                                     dw_bands=None):
    """
    Filtra un raster binario de expansión urbana usando entropía Shannon.
    
    Solo mantiene píxeles donde la entropía de las probabilidades de cobertura
    es MENOR al umbral (indica confianza alta en la clasificación).
    
    Args:
        input_raster (str): Ruta al GeoTIFF binario (new_urban.tif)
        dw_raster_all_bands (str): Ruta al GeoTIFF DW con todas las bandas
        output_raster (str): Ruta para guardar resultado filtrado
        entropy_threshold (float): Umbral máximo de entropía 
                                  Default=2.0 (rango típico: 1.5-2.5)
        dw_bands (list): Nombres de las 10 bandas DW en orden
                        Default: orden estándar de GOOGLE/DYNAMICWORLD/V1
    
    Returns:
        dict: Estadísticas del filtrado
              {
                  'total_pixels': int,
                  'high_confidence_pixels': int,
                  'filtered_out_pixels': int,
                  'retention_rate': float (0-1),
                  'avg_entropy_kept': float
              }
    
    Bandas de Dynamic World (en defecto):
        Se usan las 9 bandas estándar definidas en DW_BANDS:
        water, trees, grass, flooded_vegetation, crops,
        shrub_and_scrub, built, bare, snow_and_ice
    """
    if dw_bands is None:
        dw_bands = DW_BANDS
    
    print(f"\n🔍 Aplicando validación por entropía (umbral={entropy_threshold})...")
    
    with rasterio.open(input_raster) as src_input:
        profile = src_input.profile
        input_data = src_input.read(1)
    
    with rasterio.open(dw_raster_all_bands) as src_dw:
        dw_data = src_dw.read()  # Lee todas las bandas [9, height, width]
    
    height, width = input_data.shape
    output_data = np.zeros((height, width), dtype=np.uint8)
    
    entropies_kept = []
    high_conf_count = 0
    
    # Procesar cada píxel
    for y in range(height):
        for x in range(width):
            if input_data[y, x] > 0:  # Solo píxeles que son "nueva área construida"
                # Extraer probabilidades de todas las bandas DW
                probs = dw_data[:, y, x].astype(float)
                
                # Normalizar si no suma 1 (por seguridad)
                prob_sum = probs.sum()
                if prob_sum > 0:
                    probs = probs / prob_sum
                
                # Calcular entropía
                entropy = calculate_shannon_entropy(probs)
                
                # Mantener solo si entropía < umbral (confianza alta)
                if entropy < entropy_threshold:
                    output_data[y, x] = 1
                    high_conf_count += 1
                    entropies_kept.append(entropy)
    
    # Guardar resultado
    profile.update(dtype=rasterio.uint8, count=1)
    with rasterio.open(output_raster, 'w', **profile) as dst:
        dst.write(output_data, 1)
    
    # Estadísticas
    total_pixels = (input_data > 0).sum()
    filtered_out = total_pixels - high_conf_count
    retention_rate = high_conf_count / total_pixels if total_pixels > 0 else 0
    avg_entropy = np.mean(entropies_kept) if entropies_kept else np.nan
    
    stats = {
        'total_pixels': int(total_pixels),
        'high_confidence_pixels': int(high_conf_count),
        'filtered_out_pixels': int(filtered_out),
        'retention_rate': float(retention_rate),
        'avg_entropy_kept': float(avg_entropy)
    }
    
    print(f"✅ Entropía validada:")
    print(f"   - Píxeles originales: {stats['total_pixels']}")
    print(f"   - Píxeles confiables (H < {entropy_threshold}): {stats['high_confidence_pixels']}")
    print(f"   - Descartados (confianza baja): {stats['filtered_out_pixels']}")
    print(f"   - Tasa retención: {stats['retention_rate']*100:.1f}%")
    print(f"   - Entropía promedio (conservados): {stats['avg_entropy_kept']:.3f}")
    print(f"   - Guardado: {output_raster}")
    
    return stats


def get_entropy_calibration_stats(dw_raster_all_bands, input_raster, 
                                   entropy_percentiles=[10, 25, 50, 75, 90]):
    """
    Proporciona estadísticas para calibrar el umbral de entropía.
    
    IMPORTANTE: Ejecutar esto primero para entender tu distribución de entropías
    antes de fijar un umbral definitivo.
    
    Args:
        dw_raster_all_bands (str): Ruta a GeoTIFF DW con todas 10 bandas
        input_raster (str): Ruta a GeoTIFF binario de nueva área construida
        entropy_percentiles (list): Percentiles a calcular
        
    Returns:
        dict: Estadísticas detalladas para calibración
        
    Ejemplo de uso:
        >>> stats = get_entropy_calibration_stats(
        ...     "dw_2025_12.tif", 
        ...     "new_urban.tif"
        ... )
        >>> # Mirar stats['percentiles'] para elegir umbral
        >>> # Si P50=1.8, sugiero umbral=2.0
    """
    print("\n📊 Calculando distribución de entropía para calibración...")
    
    with rasterio.open(input_raster) as src:
        input_data = src.read(1)
    
    with rasterio.open(dw_raster_all_bands) as src:
        dw_data = src.read()
    
    height, width = input_data.shape
    entropies = []
    
    for y in range(height):
        for x in range(width):
            if input_data[y, x] > 0:
                probs = dw_data[:, y, x].astype(float)
                prob_sum = probs.sum()
                if prob_sum > 0:
                    probs = probs / prob_sum
                entropy = calculate_shannon_entropy(probs)
                entropies.append(entropy)
    
    if not entropies:
        print("⚠️ No se encontraron píxeles para calibrar")
        return {}
    
    entropies = np.array(entropies)
    percentile_values = np.percentile(entropies, entropy_percentiles)
    
    stats = {
        'total_pixels': len(entropies),
        'min_entropy': float(np.min(entropies)),
        'max_entropy': float(np.max(entropies)),
        'mean_entropy': float(np.mean(entropies)),
        'std_entropy': float(np.std(entropies)),
        'percentiles': {f'P{p}': float(v) for p, v in zip(entropy_percentiles, percentile_values)}
    }
    
    print(f"\n📊 Estadísticas de entropía ({len(entropies)} píxeles):")
    print(f"   - Rango: [{stats['min_entropy']:.3f}, {stats['max_entropy']:.3f}]")
    print(f"   - Media: {stats['mean_entropy']:.3f} ± {stats['std_entropy']:.3f}")
    print(f"   - Percentiles:")
    for p, v in stats['percentiles'].items():
        print(f"      {p}: {v:.3f}")
    print("\n💡 Recomendaciones para umbral:")
    print(f"   - Conservador (retiene ~75%): {stats['percentiles']['P25']:.2f}")
    print(f"   - Moderado (retiene ~50%): {stats['percentiles']['P50']:.2f}")
    print(f"   - Menos restrictivo (retiene ~25%): {stats['percentiles']['P75']:.2f}")
    
    return stats


# ============================================================================
# ENTROPÍA para Earth Engine - Procesamiento en la nube
# ============================================================================

def calculate_entropy_ee(dw_image, bands=None):
    """
    Calcula la entropía de Shannon para cada píxel de Dynamic World usando Earth Engine.
    
    Esta función trabaja con imágenes ee.Image (procesamiento remoto en Google Earth Engine),
    a diferencia de apply_entropy_filter_to_raster que trabaja con archivos locales.
    
    Fórmula: H = -Σ(p_i * log2(p_i))
    
    Args:
        dw_image (ee.Image): Imagen de Dynamic World con bandas de probabilidad
        bands (list): Nombres de bandas a usar. Si None, usa las 9 bandas estándar de DW_BANDS
    
    Returns:
        ee.Image: Imagen con banda 'entropy' agregada
                  Valores: 0 (confianza máxima) a ~3.17 (confianza mínima para 9 clases)
    
    Interpretación:
        - H < 1.0: Clasificación muy confiable (una clase domina fuertemente)
        - H 1.0-2.0: Clasificación moderadamente confiable
        - H > 2.0: Clasificación poco confiable (múltiples clases equiprobables)
    
    Ejemplo de uso:
        >>> dw = ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1") \\
        ...     .filterDate('2025-01-01', '2025-12-31') \\
        ...     .filterBounds(geometry) \\
        ...     .mosaic()
        >>> dw_with_entropy = calculate_entropy_ee(dw)
        >>> entropy_band = dw_with_entropy.select('entropy')
    """
    import ee
    
    if bands is None:
        # Usar bandas estándar definidas al inicio del módulo
        bands = DW_BANDS
    
    # DEBUG: Imprimir bandas disponibles
    available_bands = dw_image.bandNames().getInfo()
    print(f"🔍 DEBUG: Bandas disponibles en imagen DW: {available_bands}")
    print(f"🔍 DEBUG: Bandas a usar para entropía: {bands}")
    
    # Seleccionar solo las bandas de probabilidad
    probs_image = dw_image.select(bands)
    
    # Evitar log(0) reemplazando 0 con un valor muy pequeño
    safe_probs = probs_image.where(probs_image.lte(0), 1e-10)
    
    # Calcular log2(p) = log(p) / log(2)
    log2_probs = safe_probs.log().divide(ee.Number(2).log())
    
    # Calcular p * log2(p) para cada banda
    p_log_p = safe_probs.multiply(log2_probs)
    
    # Sumar sobre todas las bandas: Σ(p * log2(p))
    sum_p_log_p = p_log_p.reduce(ee.Reducer.sum())
    
    # Aplicar signo negativo: H = -Σ(p * log2(p))
    entropy = sum_p_log_p.multiply(-1).rename('entropy')
    
    # Agregar banda de entropía a la imagen original
    return dw_image.addBands(entropy)
    
    entropies = np.array(entropies)
    
    stats = {
        'total_pixels': len(entropies),
        'mean': float(np.mean(entropies)),
        'median': float(np.median(entropies)),
        'std': float(np.std(entropies)),
        'min': float(np.min(entropies)),
        'max': float(np.max(entropies)),
        'percentiles': {}
    }
    
    for p in entropy_percentiles:
        stats['percentiles'][f'P{p}'] = float(np.percentile(entropies, p))
    
    print(f"📈 Distribución de Entropía:")
    print(f"   - Mínimo: {stats['min']:.3f}")
    print(f"   - Máximo: {stats['max']:.3f}")
    print(f"   - Media: {stats['mean']:.3f}")
    print(f"   - Mediana: {stats['median']:.3f}")
    print(f"   - Desv. Est.: {stats['std']:.3f}")
    print(f"\n   📍 Percentiles (úsalos para calibrar):")
    for p, val in stats['percentiles'].items():
        print(f"      {p}: {val:.3f}")
    
    print(f"\n💡 SUGERENCIA DE UMBRAL:")
    print(f"   - Conservador (retener 75%): umbral ≈ {stats['percentiles']['P75']:.2f}")
    print(f"   - Moderado (retener 50%): umbral ≈ {stats['percentiles']['P50']:.2f}")
    print(f"   - Agresivo (retener 25%): umbral ≈ {stats['percentiles']['P25']:.2f}")
    
    return stats