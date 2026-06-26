"""
Módulo de filtrado SAR para validación de expansión urbana

Este módulo integra datos Sentinel-1 SAR para filtrar falsos positivos
en la detección de expansión urbana de Dynamic World.

Workflow (análisis independiente, NO requiere mismas geometrías):
1. DW detecta expansión → Genera polígonos vectoriales
2. SAR analiza por TILES OPTIMIZADOS:
   - Divide el AOI en grid (6×6)
   - Identifica tiles donde DW detectó expansión
   - Descarga datos SAR SOLO para esos tiles (ahorro 70-95% unidades)
   - Clasifica pixel por pixel en cada tile (análisis raster)
   - Genera nuevos polígonos donde SAR detectó expansión
   - Combina polígonos de todos los tiles
3. Intersección geométrica: DW ∩ SAR
   - Combina resultados: solo áreas donde AMBOS detectaron expansión
   - Filtra falsos positivos de DW que SAR no confirmó

¿Cómo detecta SAR expansión urbana?
------------------------------------
SAR (Synthetic Aperture Radar) usa ondas que penetran nubes y funcionan 
día/noche. Detecta construcciones por su retrodispersión característica:

1. CLASIFICACIÓN URBANA (pixel por pixel):
   Un píxel se clasifica como URBANO si cumple TODOS estos criterios:
   
   ✓ VV > -12 dB (umbral mínimo)
     → Superficies rugosas con retrodispersión fuerte
     → Edificios, concreto, asfalto reflejan señal de vuelta al satélite
   
   AND VH > -18 dB (umbral mínimo)
     → Dispersión cruzada moderada de estructuras complejas
   
   AND Ratio VV-VH entre 1.0 y 9.5 dB (rango característico usado que diferencia urbano de vegetación/agua/suelo desnudo)
   
   Urbano = (VV > -12) AND (VH > -18) AND (1.0 < VV-VH < 9.5)
   
   ¿Por qué AND?
   - AND = Clasificación estricta, mejora la precisión y evita falsos positivos
   - Si solo cumple un criterio (ej: VV alto pero VH bajo) probablemente NO es urbano → Podría ser suelo desnudo o roca expuesta
   
   Por qué funciona:
   - Áreas urbanas: estructuras verticales → valores VV y VH altos
   - Vegetación: dispersión volumétrica → valores BAJOS (< umbrales)
   - Agua: reflexión especular → valores MUY BAJOS (< umbrales)
   - Suelo desnudo: valores intermedios (puede cumplir pero ratio lo filtra)

2. DETECCIÓN DE CAMBIOS:
   • t1 (90 días atrás): Clasificar como urbano → Máscara de urbano previo
   • t2 (30 días atrás): Clasificar como urbano → Máscara de urbano actual
   • Expansión = (urbano en t2) AND NOT (urbano en t1)
   
   Resultado: Solo píxeles que cambiaron de no-urbano → urbano

3. LIMPIEZA MORFOLÓGICA:
   • Erosión (3 iteraciones): Elimina píxeles aislados (ruido)
   • Dilatación (2 iteraciones): Rellena huecos dentro de construcciones
   • Filtro por área: Descarta clusters < 5 píxeles (50 m²)

4. VECTORIZACIÓN:
   • Convierte máscara binaria → polígonos georreferenciados
   • Cada cluster de píxeles contiguos → un polígono
   • Mantiene geometría precisa para intersección con DW

IMPORTANTE: SAR NO analiza geometrías DW individualmente. Hace análisis
independiente por tiles y luego se intersecta con DW. Esto optimiza el
uso de unidades de procesamiento gratuitas de Copernicus Dataspace.


Datos técnicos:
- Sensor: Sentinel-1
- Modo: IW
- Producto: GRD (Ground Range Detected)
- Corrección: RTC GAMMA0_TERRAIN (Radiometric Terrain Correction)
- Resolución: 10m
- Polarizaciones: VV (vertical-vertical), VH (vertical-horizontal)

API: Copernicus Dataspace

"""

import os
import numpy as np
import geopandas as gpd
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from shapely.geometry import shape, box, mapping
from scipy import ndimage
from skimage.measure import label, regionprops
import warnings
import time 

from sentinelhub import (
    SHConfig,
    SentinelHubRequest,
    DataCollection,
    MimeType,
    CRS,
    BBox,
    bbox_to_dimensions
)

# Definir colección para Copernicus Dataspace (CDSE)
# Esto usa el servicio GRATIS de Copernicus en lugar del comercial
# IMPORTANTE: No usar fallback a SENTINEL1_IW (sería servicio comercial)
SENTINEL1_CDSE = DataCollection.define(
    name="SENTINEL1_IW_CDSE",
    api_id="sentinel-1-grd",
    service_url="https://sh.dataspace.copernicus.eu",
    collection_type="sentinel-1-grd",
    sensor_type=None,
    bands=("VV", "VH"),
    is_timeless=False
)

# Evalscript para composición mediana 
EVALSCRIPT = """
//VERSION=3
function setup() {
    return {
        input: [{
            bands: ["VV", "VH", "dataMask"],
            units: "LINEAR_POWER"
        }],
        output: [
            {id: "VV", bands: 1, sampleType: "FLOAT32"},
            {id: "VH", bands: 1, sampleType: "FLOAT32"},
            {id: "MASK", bands: 1, sampleType: "UINT8"}
        ],
        mosaicking: "ORBIT"
    };
}

function evaluatePixel(samples) {
    var valid_vv = [];
    var valid_vh = [];
    
    for (var i = 0; i < samples.length; i++) {
        if (samples[i].dataMask === 1 && 
            samples[i].VV > 0 && 
            samples[i].VH > 0) {
            valid_vv.push(samples[i].VV);
            valid_vh.push(samples[i].VH);
        }
    }
    
    if (valid_vv.length === 0) {
        return {VV: [NaN], VH: [NaN], MASK: [0]};
    }
    
    valid_vv.sort(function(a, b) { return a - b; });
    valid_vh.sort(function(a, b) { return a - b; });
    
    var mid = Math.floor(valid_vv.length / 2);
    var vv_median = valid_vv.length % 2 !== 0 ? 
        valid_vv[mid] : (valid_vv[mid - 1] + valid_vv[mid]) / 2.0;
    var vh_median = valid_vh.length % 2 !== 0 ? 
        valid_vh[mid] : (valid_vh[mid - 1] + valid_vh[mid]) / 2.0;
    
    return {
        VV: [vv_median],
        VH: [vh_median],
        MASK: [1]
    };
}
"""


def initialize_sentinel_hub_config(client_id=None, client_secret=None):
    """
    Inicializa configuración de Sentinel Hub para Copernicus Dataspace
    
    Args:
        client_id: OAuth client ID (si None, usa variables de entorno)
        client_secret: OAuth client secret (si None, usa variables de entorno)
    
    Returns:
        SHConfig configurado
    """
    config = SHConfig()
    
    # Usar credenciales provistas o de variables de entorno
    config.sh_client_id = client_id or os.getenv("SENTINELHUB_CLIENT_ID")
    config.sh_client_secret = client_secret or os.getenv("SENTINELHUB_CLIENT_SECRET")
    
    # Configurar URLs para Copernicus Dataspace (no Sentinel Hub comercial)
    config.sh_base_url = "https://sh.dataspace.copernicus.eu"
    config.sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    
    # NO guardar config para evitar cachear credenciales viejas
    # config.save()
    
    print(f"✅ Sentinel Hub configurado para Copernicus Dataspace (usando .env)")
    
    return config


def download_sentinel1_data(bbox, size, time_interval, config):
    """
    Descarga datos Sentinel-1 RTC con corrección por terreno
    
    Args:
        bbox: BBox de Sentinel Hub
        size: tupla (width, height) en píxeles
        time_interval: tupla (fecha_inicio, fecha_fin) formato "YYYY-MM-DD"
        config: SHConfig configurado
    
    Returns:
        numpy array (height, width, 3) con:
        - Band 0: VV (dB)
        - Band 1: VH (dB)  
        - Band 2: Mask (0=invalid, 1=valid)
    """
    # Asegurar que config tiene las URLs correctas
    config.sh_base_url = "https://sh.dataspace.copernicus.eu"
    config.sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    config.save()
    
    request = SentinelHubRequest(
        evalscript=EVALSCRIPT,
        input_data=[
            SentinelHubRequest.input_data(
                data_collection=SENTINEL1_CDSE,
                time_interval=time_interval,
                other_args={
                    "dataFilter": {
                        "resolution": "HIGH",
                        "orbitDirection": "ASCENDING"
                    },
                    "processing": {
                        "backCoeff": "GAMMA0_TERRAIN",
                        "orthorectify": True,
                        "demInstance": "COPERNICUS_30"
                    }
                }
            )
        ],
        responses=[
            SentinelHubRequest.output_response('VV', MimeType.TIFF),
            SentinelHubRequest.output_response('VH', MimeType.TIFF),
            SentinelHubRequest.output_response('MASK', MimeType.TIFF)
        ],
        bbox=bbox,
        size=size,
        config=config
    )
    
    try:
        response = request.get_data()
        
        # Extraer datos: response = [{'VV.tif': array, 'VH.tif': array, 'MASK.tif': array}]
        data = response[0]
        vv_raw = data['VV.tif']
        vh_raw = data['VH.tif']
        mask_raw = data['MASK.tif']
        
        # Asegurar dimensiones correctas (altura, ancho)
        if len(vv_raw.shape) == 3:
            vv_raw = vv_raw.squeeze()
            vh_raw = vh_raw.squeeze()
            mask_raw = mask_raw.squeeze()
        
        # Convertir a dB (datos vienen en LINEAR_POWER)
        vv_db = 10 * np.log10(vv_raw + 1e-10)
        vh_db = 10 * np.log10(vh_raw + 1e-10)
        
        # Limpiar valores infinitos/inválidos
        vv_db[~np.isfinite(vv_db)] = np.nan
        vh_db[~np.isfinite(vh_db)] = np.nan
        
        # Combinar en array 3D (height, width, 3)
        combined = np.stack([vv_db, vh_db, mask_raw], axis=-1).astype(np.float32)
        
        return combined
    except Exception as e:
        print(f"   ❌ Error al descargar SAR: {str(e)}")
        raise


def classify_urban_sar(data, params):
    """
    Clasifica áreas urbanas usando thresholds multi-criterio SAR
    
    Args:
        data: numpy array (height, width, 3) con VV, VH, mask
        params: dict con umbrales:
            - vv_threshold: umbral VV en dB
            - vh_threshold: umbral VH en dB
            - use_ratio: True/False para usar ratio VV/VH
            - vv_vh_ratio_min: ratio mínimo
            - vv_vh_ratio_max: ratio máximo
            - erosion_size: iteraciones de erosión
            - dilation_size: iteraciones de dilatación
    
    Returns:
        binary mask (height, width) donde 1=urbano, 0=no urbano
    """
    vv = data[:, :, 0]
    vh = data[:, :, 1]
    valid_mask = data[:, :, 2]
    
    # Enmascarar píxeles inválidos
    vv = np.where(valid_mask == 1, vv, np.nan)
    vh = np.where(valid_mask == 1, vh, np.nan)
    
    # Criterio 1: VV alto (superficies rugosas/urbanas tienen mayor backscatter)
    urban_vv = vv > params['vv_threshold']
    
    # Criterio 2: VH moderado
    urban_vh = vh > params['vh_threshold']
    
    # Combinar criterios básicos
    urban_mask = urban_vv & urban_vh
    
    # Criterio 3 (opcional): Ratio VV/VH
    # Áreas urbanas tienen ratio característico
    if params.get('use_ratio', True):
        ratio = vv - vh  # En dB, resta = ratio logarítmico
        urban_ratio = (ratio > params['vv_vh_ratio_min']) & (ratio < params['vv_vh_ratio_max'])
        urban_mask = urban_mask & urban_ratio
    
    # Limpieza morfológica
    # Erosión: elimina píxeles aislados
    urban_mask = ndimage.binary_erosion(urban_mask, iterations=params['erosion_size'])
    # Dilatación: rellena huecos pequeños
    urban_mask = ndimage.binary_dilation(urban_mask, iterations=params['dilation_size'])
    
    return urban_mask.astype(np.uint8)


def filter_dw_polygons_with_sar(
    dw_geojson_path,
    output_dir,
    last_day_prev,
    last_day_curr,
    sar_params,
    config,
    lookback_t1_days=30,  # MODIFICADO: Ahora 30 días (antes era 90)
    lookback_t2_days=30
):
    """
    Filtra polígonos de Dynamic World usando validación SAR con intersección geométrica
    
    IMPORTANTE: Hace intersección geométrica real (DW ∩ SAR), no solo verificación.
    Esto significa que solo mantiene las áreas donde AMBOS detectaron expansión.
    
    Nueva lógica temporal (alineada con DW):
    - t2: período actual con lookback de 30 días (construcción reciente)
    - t1: termina 90 días antes del INICIO de t2, con lookback de 30 días
    - Delta (gap): 90 días de separación entre el FIN de t1 y el INICIO de t2
    
    Ejemplo con last_day_curr = 2025-11-30:
    - t2: 2025-11-01 a 2025-11-30 (30 días)
    - t1: 2025-07-04 a 2025-08-03 (30 días)
    - Gap: 2025-08-03 a 2025-11-01 (90 días sin observación)
    
    Esto permite capturar el proceso progresivo de construcción:
    suelo desnudo → limpieza → bases → construcción vertical
    
    Workflow:
    1. Lee polígonos de expansión DW
    2. Descarga datos SAR t1 y t2 para el área (composición MEDIANA)
    3. Clasifica áreas urbanas con SAR en ambos periodos
    4. Detecta expansión SAR (urbano en t2 AND NOT urbano en t1)
    5. Calcula intersección geométrica: DW ∩ SAR
    6. Retorna SOLO las geometrías donde ambos detectaron expansión
    
    Resultado: Áreas validadas por AMBOS métodos (DW Y SAR)
    
    
    Args:
        dw_geojson_path: ruta al GeoJSON con polígonos DW
        output_dir: directorio para guardar resultados
        last_day_prev: fecha fin t1 (datetime)
        last_day_curr: fecha fin t2 (datetime)
        sar_params: dict con parámetros SAR
        config: SHConfig configurado
        lookback_t1_days: días lookback para t1 (default: 30)
        lookback_t2_days: días lookback para t2 (default: 30)
    
    Returns:
        Ruta al GeoJSON filtrado
    """
    print("\n" + "="*70)
    print("🛰️ INICIANDO FILTRADO SAR")
    print("="*70)
    
    # Cargar polígonos DW
    if not os.path.exists(dw_geojson_path):
        print(f"⚠️ No se encontró archivo DW: {dw_geojson_path}")
        return None
    
    gdf_dw = gpd.read_file(dw_geojson_path).to_crs("EPSG:4326")
    
    if len(gdf_dw) == 0:
        print("⚠️ No hay polígonos DW para filtrar")
        return None
    
    print(f"📊 Polígonos DW a validar: {len(gdf_dw)}")
    
    # Nueva lógica temporal (alineada con DW):
    # t2: período actual (30 días lookback desde last_day_curr)
    # t1: 90 días antes del INICIO de t2 (30 días lookback)
    # Delta: 90 días de gap entre el FIN de t1 y el INICIO de t2
    
    t2_end = last_day_curr
    start_t2 = t2_end - timedelta(days=lookback_t2_days)
    
    # t1 termina 90 días antes del INICIO de t2 (no del fin de t2)
    t1_end = start_t2 - timedelta(days=90)
    start_t1 = t1_end - timedelta(days=lookback_t1_days)
    
    print(f"\n📅 Configuración temporal SAR (alineada con DW):")
    print(f"   t1: {start_t1.strftime('%Y-%m-%d')} a {t1_end.strftime('%Y-%m-%d')} ({lookback_t1_days} días lookback)")
    print(f"   t2: {start_t2.strftime('%Y-%m-%d')} a {t2_end.strftime('%Y-%m-%d')} ({lookback_t2_days} días lookback)")
    print(f"   Delta (gap): 90 días entre fin de t1 ({t1_end.strftime('%Y-%m-%d')}) e inicio de t2 ({start_t2.strftime('%Y-%m-%d')})")
    print(f"   Composición: MEDIANA (reduce speckle noise)")
    
    # OPTIMIZACIÓN CRÍTICA: Procesamiento por tiles con expansión DW
    # Para AOIs extensos, descargar SAR para toda el área consume muchas unidades.
    # Estrategia: Solo procesar tiles donde DW detectó expansión (ahorro 70-95%)
    
    from shapely.ops import unary_union
    
    # Obtener bounds y dividir en tiles
    minx, miny, maxx, maxy = gdf_dw.total_bounds
    
    # Dividir en grid (ajustable según tamaño del AOI)
    area_km2 = ((maxx - minx) * 111) * ((maxy - miny) * 111)  # Aproximación
    
    if area_km2 > 400:
        n_tiles = 12  # Grid 12x12 = 144 tiles (antes 6x6 = 36)
    elif area_km2 > 100:
        n_tiles = 8   # Grid 8x8 = 64 tiles (antes 4x4 = 16)
    else:
        n_tiles = 4   # Grid 4x4 = 16 tiles (antes 2x2 = 4)
    
    tile_width = (maxx - minx) / n_tiles
    tile_height = (maxy - miny) / n_tiles
    
    # Crear geometría unión de todos los polígonos DW
    dw_union = gdf_dw.unary_union
    
    # Identificar tiles que intersectan con polígonos DW
    tiles_to_process = []
    for i in range(n_tiles):
        for j in range(n_tiles):
            tile_minx = minx + i * tile_width
            tile_miny = miny + j * tile_height
            tile_maxx = tile_minx + tile_width
            tile_maxy = tile_miny + tile_height
            
            tile_geom = box(tile_minx, tile_miny, tile_maxx, tile_maxy)
            
            # Solo procesar si el tile intersecta con expansión DW
            if tile_geom.intersects(dw_union):
                tiles_to_process.append({
                    'index': (i, j),
                    'bounds': (tile_minx, tile_miny, tile_maxx, tile_maxy),
                    'bbox': BBox((tile_minx, tile_miny, tile_maxx, tile_maxy), crs=CRS.WGS84)
                })
    
    total_tiles = n_tiles * n_tiles
    n_tiles_to_process = len(tiles_to_process)
    savings_pct = ((total_tiles - n_tiles_to_process) / total_tiles * 100)
    
    print(f"\n📦 Optimización de procesamiento SAR:")
    print(f"   Área total: ~{area_km2:.0f} km²")
    print(f"   Grid: {n_tiles}x{n_tiles} = {total_tiles} tiles")
    print(f"   Tiles con expansión DW: {n_tiles_to_process}")
    print(f"   💰 Ahorro de unidades: {savings_pct:.1f}%")
    
    # Procesar cada tile con expansión
    all_sar_polygons = []
    
    print(f"\n📡 Descargando y procesando SAR por tiles...")
    
    for idx, tile_info in enumerate(tiles_to_process, 1):
        tile_idx = tile_info['index']
        tile_bbox = tile_info['bbox']
        
        #  DELAY para respetar límite de 300 PU/minuto
        if idx > 1:  # No esperar en el primer tile
            time.sleep(7)  # Pausa de 7 segundos entre tiles
        
        try:
            print(f"\n   🔄 Tile {idx}/{n_tiles_to_process} (posición {tile_idx[0]},{tile_idx[1]})...")
            
            # Calcular tamaño del tile
            tile_size = bbox_to_dimensions(tile_bbox, resolution=10)
            print(f"      Tamaño: {tile_size[0]}x{tile_size[1]} píxeles")
            
            # Descargar datos SAR para este tile
            time_interval_t1 = (start_t1.strftime("%Y-%m-%d"), t1_end.strftime("%Y-%m-%d"))
            time_interval_t2 = (start_t2.strftime("%Y-%m-%d"), t2_end.strftime("%Y-%m-%d"))
            
            print(f"      Descargando t1...")
            data_t1 = download_sentinel1_data(tile_bbox, tile_size, time_interval_t1, config)
            
            print(f"      Descargando t2...")
            data_t2 = download_sentinel1_data(tile_bbox, tile_size, time_interval_t2, config)
            
            # Clasificar áreas urbanas para este tile
            print(f"      Clasificando...")
            urban_t1 = classify_urban_sar(data_t1, sar_params)
            urban_t2 = classify_urban_sar(data_t2, sar_params)
            
            # Detectar expansión SAR en este tile
            expansion_sar = (urban_t2 == 1) & (urban_t1 == 0)
            
            # Filtrar por área mínima
            labeled_expansion = label(expansion_sar, connectivity=2)
            regions = regionprops(labeled_expansion)
            
            filtered_expansion = np.zeros_like(labeled_expansion)
            for region in regions:
                if region.area >= sar_params['min_cluster_pixels']:
                    filtered_expansion[labeled_expansion == region.label] = region.label
            
            expansion_mask = (filtered_expansion > 0).astype(np.uint8)
            expansion_pixels = np.sum(expansion_mask)
            
            if expansion_pixels > 0:
                # Vectorizar expansion_mask del tile
                from rasterio import Affine, features
                
                tile_bounds = tile_info['bounds']
                tile_minx, tile_miny, tile_maxx, tile_maxy = tile_bounds
                
                pixel_width = (tile_maxx - tile_minx) / tile_size[0]
                pixel_height = (tile_maxy - tile_miny) / tile_size[1]
                
                transform = Affine(
                    pixel_width, 0, tile_minx,
                    0, -pixel_height, tile_maxy
                )
                
                shapes_gen = features.shapes(
                    expansion_mask,
                    mask=expansion_mask > 0,
                    transform=transform
                )
                
                tile_polygons = []
                for geom, value in shapes_gen:
                    if value > 0:
                        tile_polygons.append(shape(geom))
                
                all_sar_polygons.extend(tile_polygons)
                
                expansion_ha = (expansion_pixels * 100) / 10000
                print(f"      ✅ Expansión detectada: {expansion_ha:.2f} ha ({len(tile_polygons)} polígonos)")
            else:
                print(f"      ⏭️ Sin expansión en este tile")
                
        except Exception as e:
            print(f"      ❌ Error en tile {tile_idx}: {e}")
            print(f"      ⏭️ Continuando con siguiente tile...")
            continue
    
    # Verificar si se detectó expansión SAR en algún tile
    if len(all_sar_polygons) == 0:
        print("\n⚠️ SAR no detectó expansión en ningún tile - Rechazando todos los polígonos DW")
        empty_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        output_path = dw_geojson_path.replace(".geojson", "_sar_filtered.geojson")
        empty_gdf.to_file(output_path, driver="GeoJSON")
        return output_path
    
    print(f"\n✅ Procesamiento por tiles completado")
    print(f"   Total de polígonos SAR: {len(all_sar_polygons)}")
    
    # Continuar con intersección DW ∩ SAR (código existente)
    sar_polygons = all_sar_polygons
    
    gdf_sar = gpd.GeoDataFrame(geometry=sar_polygons, crs="EPSG:4326")
    
    # Intersección geométrica real DW ∩ SAR
    # Solo mantiene las áreas donde AMBOS (DW Y SAR) detectaron expansión
    print(f"\n🔍 Validando polígonos DW contra SAR (intersección geométrica)...")
    
    validated_geometries = []
    total_dw_area_ha = 0
    total_validated_area_ha = 0
    
    for idx, row in gdf_dw.iterrows():
        poly_dw = row.geometry
        dw_area_m2 = poly_dw.area  # Área en grados, pero solo para comparación relativa
        
        # Calcular intersección geométrica con todos los polígonos SAR
        intersection_result = None
        for poly_sar in gdf_sar.geometry:
            if poly_dw.intersects(poly_sar):
                # Intersección geométrica: solo la parte común
                intersection = poly_dw.intersection(poly_sar)
                if not intersection.is_empty:
                    if intersection_result is None:
                        intersection_result = intersection
                    else:
                        # Si intersecta con múltiples polígonos SAR, combinar
                        intersection_result = intersection_result.union(intersection)
        
        # Solo agregar si hay intersección válida Y es un polígono (no línea o punto)
        if intersection_result is not None and not intersection_result.is_empty:
            # Filtrar solo geometrías tipo Polygon o MultiPolygon (no LineString ni Point)
            if intersection_result.geom_type in ['Polygon', 'MultiPolygon']:
                validated_geometries.append(intersection_result)
            elif intersection_result.geom_type == 'GeometryCollection':
                # Extraer solo polígonos de la colección
                from shapely.geometry import Polygon, MultiPolygon
                polys = [geom for geom in intersection_result.geoms if isinstance(geom, (Polygon, MultiPolygon))]
                if polys:
                    if len(polys) == 1:
                        validated_geometries.append(polys[0])
                    else:
                        from shapely.ops import unary_union
                        validated_geometries.append(unary_union(polys))
    
    # Crear GeoDataFrame filtrado
    if len(validated_geometries) > 0:
        gdf_filtered = gpd.GeoDataFrame(geometry=validated_geometries, crs="EPSG:4326")
    else:
        gdf_filtered = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    
    # Guardar resultado
    output_path = dw_geojson_path.replace(".geojson", "_sar_filtered.geojson")
    gdf_filtered.to_file(output_path, driver="GeoJSON")
    
    # Estadísticas - calcular áreas en hectáreas
    # Proyectar a sistema métrico para cálculo preciso de área
    gdf_dw_projected = gdf_dw.to_crs("EPSG:3116")  # Colombia Bogotá zone
    gdf_filtered_projected = gdf_filtered.to_crs("EPSG:3116")
    
    original_area_ha = gdf_dw_projected.geometry.area.sum() / 10000  # m² a ha
    filtered_area_ha = gdf_filtered_projected.geometry.area.sum() / 10000 if len(gdf_filtered) > 0 else 0
    rejected_area_ha = original_area_ha - filtered_area_ha
    rejection_rate_area = (rejected_area_ha / original_area_ha * 100) if original_area_ha > 0 else 0
    
    original_count = len(gdf_dw)
    filtered_count = len(gdf_filtered)
    
    print(f"\n📊 RESULTADOS DEL FILTRADO (Intersección geométrica DW ∩ SAR):")
    print(f"   📍 Polígonos DW originales: {original_count}")
    print(f"   📍 Geometrías validadas: {filtered_count}")
    print(f"   📏 Área DW original: {original_area_ha:.2f} ha")
    print(f"   ✅ Área validada (DW ∩ SAR): {filtered_area_ha:.2f} ha")
    print(f"   ❌ Área rechazada: {rejected_area_ha:.2f} ha ({rejection_rate_area:.1f}%)")
    print(f"\n💾 Guardado: {output_path}")
    print("="*70 + "\n")
    
    return output_path


def apply_sar_filter_to_intersections(
    intersections_dir,
    sar_filtered_path,
    anio,
    mes
):
    """
    Aplica el filtro SAR a los archivos de intersecciones
    
    Genera nuevos archivos *_sar_filtered.geojson para:
    - intersections
    - no_intersections
    
    Args:
        intersections_dir: directorio con archivos de intersecciones DW
        sar_filtered_path: ruta al GeoJSON filtrado por SAR
        anio: año del análisis
        mes: mes del análisis
    
    Returns:
        dict con rutas a archivos filtrados
    """
    if not sar_filtered_path or not os.path.exists(sar_filtered_path):
        print("⚠️ No hay archivo SAR filtrado, saltando este paso")
        return None
    
    gdf_sar_filtered = gpd.read_file(sar_filtered_path).to_crs("EPSG:4326")
    
    if len(gdf_sar_filtered) == 0:
        print("⚠️ SAR filtró todos los polígonos - generando archivos vacíos")
        
        # Archivos vacíos
        empty_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        
        inter_path = os.path.join(intersections_dir, f"new_urban_{anio}_{mes:02d}_intersections_sar_filtered.geojson")
        no_inter_path = os.path.join(intersections_dir, f"new_urban_{anio}_{mes:02d}_no_intersections_sar_filtered.geojson")
        
        empty_gdf.to_file(inter_path, driver="GeoJSON")
        empty_gdf.to_file(no_inter_path, driver="GeoJSON")
        
        return {
            "intersections": inter_path,
            "no_intersections": no_inter_path
        }
    
    # Leer archivos DW originales
    inter_path_original = os.path.join(intersections_dir, f"new_urban_{anio}_{mes:02d}_intersections.geojson")
    no_inter_path_original = os.path.join(intersections_dir, f"new_urban_{anio}_{mes:02d}_no_intersections.geojson")
    
    result = {}
    
    # Filtrar intersections con intersección geométrica real (overlay)
    if os.path.exists(inter_path_original):
        gdf_inter = gpd.read_file(inter_path_original).to_crs("EPSG:4326")
        
        # Asegurar que solo tenemos Polygons/MultiPolygons antes de overlay
        gdf_sar_filtered_poly = gdf_sar_filtered[gdf_sar_filtered.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])].copy()
        
        if len(gdf_sar_filtered_poly) == 0:
            print("   ⚠️ No hay geometrías poligonales válidas en SAR, generando archivo vacío")
            gdf_inter_filtered = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        else:
            # Usar overlay con intersection para obtener SOLO la geometría común DW ∩ SAR
            gdf_inter_filtered = gpd.overlay(gdf_inter, gdf_sar_filtered_poly, how="intersection", keep_geom_type=False)
            
            # Filtrar solo Polygons/MultiPolygons del resultado
            if len(gdf_inter_filtered) > 0:
                gdf_inter_filtered = gdf_inter_filtered[gdf_inter_filtered.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])].copy()
        
        inter_path_filtered = inter_path_original.replace(".geojson", "_sar_filtered.geojson")
        gdf_inter_filtered.to_file(inter_path_filtered, driver="GeoJSON")
        result["intersections"] = inter_path_filtered
        
        print(f"   ✅ Intersections: {len(gdf_inter)} polígonos → {len(gdf_inter_filtered)} geometrías validadas")
    
    # Filtrar no_intersections con intersección geométrica real (overlay)
    if os.path.exists(no_inter_path_original):
        gdf_no_inter = gpd.read_file(no_inter_path_original).to_crs("EPSG:4326")
        
        # Asegurar que solo tenemos Polygons/MultiPolygons antes de overlay
        gdf_sar_filtered_poly = gdf_sar_filtered[gdf_sar_filtered.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])].copy()
        
        if len(gdf_sar_filtered_poly) == 0:
            print("   ⚠️ No hay geometrías poligonales válidas en SAR, generando archivo vacío")
            gdf_no_inter_filtered = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        else:
            # Usar overlay con intersection para obtener SOLO la geometría común DW ∩ SAR
            gdf_no_inter_filtered = gpd.overlay(gdf_no_inter, gdf_sar_filtered_poly, how="intersection", keep_geom_type=False)
            
            # Filtrar solo Polygons/MultiPolygons del resultado
            if len(gdf_no_inter_filtered) > 0:
                gdf_no_inter_filtered = gdf_no_inter_filtered[gdf_no_inter_filtered.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])].copy()
        
        no_inter_path_filtered = no_inter_path_original.replace(".geojson", "_sar_filtered.geojson")
        gdf_no_inter_filtered.to_file(no_inter_path_filtered, driver="GeoJSON")
        result["no_intersections"] = no_inter_path_filtered
        
        print(f"   ✅ No intersections: {len(gdf_no_inter)} polígonos → {len(gdf_no_inter_filtered)} geometrías validadas")
    
    return result


def export_sar_visualization(
    aoi_geojson_path,
    output_dir,
    date_end,
    lookback_days=30,
    sar_params=None,
    config=None,
    export_geotiff=True,
    export_png=True
):
    """
    Exporta visualización completa de datos SAR para el AOI.
    
    IMPORTANTE: Para AOIs grandes, esta función puede consumir muchas unidades
    de procesamiento. Solo usar para validación visual ocasional.
    
    Esta función descarga la imagen SAR completa del AOI (sin división en tiles)
    y la exporta para inspección visual:
    - GeoTIFF con bandas VV, VH, máscara urbana
    - PNG de visualización RGB falso color
    
    Args:
        aoi_geojson_path: Ruta al GeoJSON del AOI
        output_dir: Directorio para guardar resultados
        date_end: Fecha final del período (datetime)
        lookback_days: Días hacia atrás desde date_end (default: 30)
        sar_params: Dict con parámetros SAR (usa default de config.py si None)
        config: SHConfig configurado (usa default si None)
        export_geotiff: Si True, exporta GeoTIFF multiband
        export_png: Si True, exporta PNG de visualización
    
    Returns:
        Dict con rutas a los archivos exportados
    """
    from datetime import timedelta
    import rasterio
    from rasterio.transform import from_bounds
    import matplotlib.pyplot as plt
    
    print("\n" + "="*70)
    print("🛰️ EXPORTACIÓN DE VISUALIZACIÓN SAR")
    print("="*70)
    print("⚠️ ADVERTENCIA: Esta función descarga el AOI completo")
    print("   Puede consumir muchas unidades de procesamiento para AOIs grandes")
    print("="*70)
    
    # Cargar AOI
    gdf_aoi = gpd.read_file(aoi_geojson_path).to_crs("EPSG:4326")
    minx, miny, maxx, maxy = gdf_aoi.total_bounds
    bbox = BBox((minx, miny, maxx, maxy), crs=CRS.WGS84)
    
    # Calcular tamaño a 10m resolución
    size = bbox_to_dimensions(bbox, resolution=10)
    area_km2 = ((maxx - minx) * 111) * ((maxy - miny) * 111)
    
    print(f"\n📍 AOI:")
    print(f"   Área: ~{area_km2:.1f} km²")
    print(f"   Dimensiones: {size[0]}x{size[1]} píxeles ({size[0]*size[1]/1e6:.1f}M píxeles)")
    print(f"   Resolución: 10m/píxel")
    
    # Usar parámetros por defecto si no se proporcionan
    if sar_params is None:
        from src.config import SAR_PARAMS
        sar_params = SAR_PARAMS
    
    if config is None:
        from src.config import SENTINELHUB_CLIENT_ID, SENTINELHUB_CLIENT_SECRET
        config = initialize_sentinel_hub_config(
            client_id=SENTINELHUB_CLIENT_ID,
            client_secret=SENTINELHUB_CLIENT_SECRET
        )
    
    # Calcular período
    date_start = date_end - timedelta(days=lookback_days)
    time_interval = (date_start.strftime("%Y-%m-%d"), date_end.strftime("%Y-%m-%d"))
    
    print(f"\n📅 Período:")
    print(f"   {date_start.strftime('%Y-%m-%d')} a {date_end.strftime('%Y-%m-%d')} ({lookback_days} días)")
    print(f"   Composición: MEDIANA")
    
    # Descargar datos SAR
    print(f"\n📡 Descargando datos SAR...")
    print(f"   Esto puede tardar varios minutos para AOIs grandes...")
    
    try:
        data = download_sentinel1_data(bbox, size, time_interval, config)
    except Exception as e:
        print(f"\n❌ Error al descargar datos SAR: {e}")
        return None
    
    vv = data[:, :, 0]
    vh = data[:, :, 1]
    mask = data[:, :, 2]
    
    print(f"   ✅ Descarga completada")
    
    # Clasificar áreas urbanas
    print(f"\n🏙️ Clasificando áreas urbanas...")
    urban_mask = classify_urban_sar(data, sar_params)
    
    urban_pixels = np.sum(urban_mask)
    urban_ha = (urban_pixels * 100) / 10000
    urban_pct = (urban_pixels / (size[0] * size[1])) * 100
    
    print(f"   Área urbana detectada: {urban_ha:.2f} ha ({urban_pct:.1f}% del AOI)")
    
    # Preparar directorio de salida
    os.makedirs(output_dir, exist_ok=True)
    
    results = {}
    
    # 1. Exportar GeoTIFF multiband
    if export_geotiff:
        print(f"\n💾 Exportando GeoTIFF...")
        
        geotiff_path = os.path.join(output_dir, f"sar_visualization_{date_end.strftime('%Y%m%d')}.tif")
        
        # Crear transform
        transform = from_bounds(minx, miny, maxx, maxy, size[0], size[1])
        
        # Exportar con 4 bandas: VV, VH, Mask, Urban
        with rasterio.open(
            geotiff_path,
            'w',
            driver='GTiff',
            height=size[1],
            width=size[0],
            count=4,
            dtype=rasterio.float32,
            crs='EPSG:4326',
            transform=transform,
            compress='lzw'
        ) as dst:
            dst.write(vv.astype(rasterio.float32), 1)
            dst.write(vh.astype(rasterio.float32), 2)
            dst.write(mask.astype(rasterio.float32), 3)
            dst.write(urban_mask.astype(rasterio.float32), 4)
            
            dst.set_band_description(1, 'VV_dB')
            dst.set_band_description(2, 'VH_dB')
            dst.set_band_description(3, 'Valid_Mask')
            dst.set_band_description(4, 'Urban_Mask')
        
        print(f"   ✅ GeoTIFF guardado: {geotiff_path}")
        results['geotiff'] = geotiff_path
    
    # 2. Exportar PNG de visualización
    if export_png:
        print(f"\n🎨 Generando visualización PNG...")
        
        fig, axes = plt.subplots(2, 2, figsize=(16, 14))
        fig.suptitle(f'Sentinel-1 SAR - {date_start.strftime("%Y-%m-%d")} a {date_end.strftime("%Y-%m-%d")}',
                     fontsize=16, fontweight='bold')
        
        # VV
        ax = axes[0, 0]
        im1 = ax.imshow(vv, cmap='gray', vmin=-25, vmax=0)
        ax.set_title('VV Polarization (dB)', fontsize=12, fontweight='bold')
        ax.axis('off')
        plt.colorbar(im1, ax=ax, fraction=0.046, pad=0.04)
        
        # VH
        ax = axes[0, 1]
        im2 = ax.imshow(vh, cmap='gray', vmin=-30, vmax=-5)
        ax.set_title('VH Polarization (dB)', fontsize=12, fontweight='bold')
        ax.axis('off')
        plt.colorbar(im2, ax=ax, fraction=0.046, pad=0.04)
        
        # Ratio VV/VH
        ax = axes[1, 0]
        ratio = vv - vh
        im3 = ax.imshow(ratio, cmap='RdYlGn', vmin=0, vmax=12)
        ax.set_title('VV/VH Ratio (dB)', fontsize=12, fontweight='bold')
        ax.axis('off')
        plt.colorbar(im3, ax=ax, fraction=0.046, pad=0.04)
        
        # Máscara urbana
        ax = axes[1, 1]
        im4 = ax.imshow(urban_mask, cmap='Reds', vmin=0, vmax=1)
        ax.set_title(f'Urban Classification ({urban_ha:.1f} ha)', fontsize=12, fontweight='bold')
        ax.axis('off')
        plt.colorbar(im4, ax=ax, fraction=0.046, pad=0.04, ticks=[0, 1])
        
        plt.tight_layout()
        
        png_path = os.path.join(output_dir, f"sar_visualization_{date_end.strftime('%Y%m%d')}.png")
        plt.savefig(png_path, dpi=150, bbox_inches='tight')
        plt.close()
        
        print(f"   ✅ PNG guardado: {png_path}")
        results['png'] = png_path
    
    print(f"\n✅ Exportación completada")
    print(f"="*70)
    
    return results

