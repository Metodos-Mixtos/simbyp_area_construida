import os
import json
import ee
import pandas as pd
import numpy as np
import geopandas as gpd
import requests
from pathlib import Path
from datetime import timedelta, datetime
from scipy.ndimage import uniform_filter
from rasterio.features import geometry_mask, shapes as rasterio_shapes
from rasterio.transform import from_bounds
from shapely.geometry import shape

from sentinelhub import (
    SHConfig,
    DataCollection,
    SentinelHubRequest,
    BBox,
    CRS,
    MimeType,
    bbox_to_dimensions
)

from src.aux_utils import export_image, make_relative_path
from src.config import (
    GCS_OUTPUT_BUCKET, GCS_OUTPUT_PREFIX, URB_PROB,
    BUFFER_CONSTRUCCIONES_METROS, SENTINEL1_RESOLUTION,
    NDVI_THRESHOLD, CLOUD_THRESHOLD,
    CONSTRUCCIONES_GPKG_LOCAL, CONSTRUCCIONES_GPKG_DISSOLVE
)
from reporte.render_report import render

# ============================================
# FUNCIONES LEGACY ELIMINADAS
# ============================================
# Las funciones get_dw_composite() y process_dynamic_world() fueron eliminadas
# porque usaban la metodología antigua (Dynamic World + SAR filter).
# La nueva metodología usa: Sentinel-1 VV temporal + NDVI + Catastro local

# ============================================
# FUNCIONES DE PREPARACIÓN
# ============================================

def prepare_folders(base_path, anio, mes):
    """Crea los directorios de salida organizados por componente."""
    output_base = os.path.join(base_path, "urban_sprawl", "outputs")
    output_dir = os.path.join(output_base, f"{anio}_{mes:02d}")
    os.makedirs(output_dir, exist_ok=True)
    
    dirs = {k: os.path.join(output_dir, k) for k in ["new_constructions", "sentinel", "intersections", "maps", "stats", "reportes"]}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)
    return dirs


def download_catastro_construcciones(aoi_geometry, output_path=None, local_gpkg_path=None):
    """Descarga construcciones existentes desde servicio REST de Catastro Bogota o usa GPKG local.
    
    Args:
        aoi_geometry: ee.Geometry o GeoDataFrame con el área de interés
        output_path: Ruta donde guardar el resultado
        local_gpkg_path: Ruta opcional a GPKG local (si existe, se usa en vez del REST API)
    """
    
    # Convertir AOI a GeoDataFrame
    if isinstance(aoi_geometry, ee.Geometry):
        aoi_geojson = aoi_geometry.getInfo()
        feature = {"type": "Feature", "geometry": aoi_geojson, "properties": {}}
        aoi_gdf = gpd.GeoDataFrame.from_features([feature], crs='EPSG:4326')
    elif isinstance(aoi_geometry, gpd.GeoDataFrame):
        aoi_gdf = aoi_geometry
    else:
        raise ValueError("aoi_geometry debe ser ee.Geometry o GeoDataFrame")
    
    # Intentar usar GPKG local primero
    if local_gpkg_path and os.path.exists(local_gpkg_path):
        print(f"\n🏗️  Usando polígono DISUELTO (construcciones fusionadas): {Path(local_gpkg_path).name}")
        try:
            gdf = gpd.read_file(local_gpkg_path)
            print(f"   Cargadas {len(gdf):,} construcciones totales")
            
            # Asegurar que está en el CRS correcto
            if gdf.crs is None:
                print("   Asignando CRS EPSG:4686...")
                gdf.set_crs(epsg=4686, inplace=True)
            
            # IMPORTANTE: Convertir a EPSG:4326 ANTES de filtrar
            # (igual que en el notebook)
            if gdf.crs.to_epsg() != 4326:
                print(f"   Convirtiendo desde {gdf.crs.to_epsg()} a EPSG:4326...")
                gdf = gdf.to_crs(epsg=4326)
            
            # Filtrar por AOI usando mismo método del notebook (gdf.cx)
            # IMPORTANTE: Ambos GeoDataFrames deben estar en el MISMO CRS (4326)
            print(f"\nFiltrando construcciones al AOI...")
            
            # Asegurar que AOI esté en EPSG:4326 (mismo que construcciones)
            aoi_gdf_4326 = aoi_gdf.to_crs('EPSG:4326') if aoi_gdf.crs != 'EPSG:4326' else aoi_gdf
            aoi_bounds_4326 = aoi_gdf_4326.total_bounds
            
            # Filtrar usando spatial indexing con bounds en 4326
            # gdf YA está en EPSG:4326 (línea 88)
            gdf_filtered = gdf.cx[
                aoi_bounds_4326[0]:aoi_bounds_4326[2],
                aoi_bounds_4326[1]:aoi_bounds_4326[3]
            ]
            
            print(f"   Construcciones en AOI: {len(gdf_filtered):,}")
            
            # Reparar geometrías inválidas
            invalid_count = (~gdf_filtered.geometry.is_valid).sum()
            if invalid_count > 0:
                print(f"   Reparando {invalid_count:,} geometrias invalidas...")
                invalid_mask = ~gdf_filtered.geometry.is_valid
                gdf_filtered.loc[invalid_mask, 'geometry'] = gdf_filtered.loc[invalid_mask, 'geometry'].buffer(0)
            
            # gdf_filtered ya está en EPSG:4326 (no hace falta convertir)
            
            if output_path:
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                
                # Guardar archivo detallado (para la máscara SAR)
                gdf_filtered.to_file(output_path, driver='GeoJSON')
                print(f"   Guardadas: {output_path}")
            
            return gdf_filtered
            
        except Exception as e:
            print(f"   ⚠️ Error leyendo GPKG local: {e}")
            print(f"   Intentando con REST API de Catastro...")
    
    # Fallback: descargar desde REST API
    print("\n\ud83c\udfed  Descargando construcciones desde Catastro Bogota...")
    
    url_rest = "https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/catastro/construccion/MapServer/0/query"
    
    if aoi_gdf.crs != 'EPSG:4686':
        aoi_gdf_4686 = aoi_gdf.to_crs('EPSG:4686')
    else:
        aoi_gdf_4686 = aoi_gdf
    
    params = {
        "where": "1=1",
        "outFields": "*",
        "f": "geojson",
        "outSR": "4686"
    }
    
    try:
        response = requests.get(url_rest, params=params, timeout=300)
        response.raise_for_status()
        datos_json = response.json()
    except Exception as e:
        print(f"Error al conectar con el servidor: {e}")
        raise
    
    if "features" not in datos_json or len(datos_json["features"]) == 0:
        print("No se encontraron construcciones")
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4686')
    
    gdf = gpd.GeoDataFrame.from_features(datos_json["features"])
    gdf.set_crs(epsg=4686, inplace=True)
    print(f"   Descargadas {len(gdf):,} construcciones totales")
    
    print(f"\nFiltrando construcciones al AOI...")
    gdf_filtered = gpd.sjoin(gdf, aoi_gdf_4686, how='inner', predicate='intersects')
    
    if 'index_right' in gdf_filtered.columns:
        gdf_filtered = gdf_filtered.drop(columns=['index_right'])
    
    print(f"   Construcciones en AOI: {len(gdf_filtered):,}")
    
    invalid_count = (~gdf_filtered.geometry.is_valid).sum()
    if invalid_count > 0:
        print(f"   Reparando {invalid_count:,} geometrias invalidas...")
        invalid_mask = ~gdf_filtered.geometry.is_valid
        gdf_filtered.loc[invalid_mask, 'geometry'] = gdf_filtered.loc[invalid_mask, 'geometry'].buffer(0)
    
    gdf_filtered = gdf_filtered.to_crs('EPSG:4326')
    
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        gdf_filtered.to_file(output_path, driver='GeoJSON')
        print(f"   Guardadas: {output_path}")
    
    return gdf_filtered


def lee_filter(img, mask, window_size=5):
    """Filtro Lee para reduccion de speckle en imagenes SAR."""
    valid_mask = (mask == 1) & np.isfinite(img) & (img > 0)
    img_copy = img.copy()
    img_copy[~valid_mask] = np.nan
    
    img_mean = uniform_filter(np.nan_to_num(img_copy), size=window_size, mode='constant')
    img_sqr_mean = uniform_filter(np.nan_to_num(img_copy**2), size=window_size, mode='constant')
    img_variance = np.maximum(img_sqr_mean - img_mean**2, 0)
    
    img_std = np.sqrt(img_variance)
    mean_std = img_std.mean()
    img_mean = np.where(img_mean == 0, 1e-10, img_mean)
    
    cu = mean_std / (img_mean + 1e-10)
    weight_denominator = np.where(1 + cu**2 == 0, 1e-10, 1 + cu**2)
    weight = np.clip(1 - (cu**2) / weight_denominator, 0, 1)
    
    img_filtered = img_mean + weight * (img_copy - img_mean)
    img_filtered[~valid_mask] = np.nan
    
    return img_filtered


def create_polygon_mask(gdf, bbox, bbox_size):
    """Crea mascara binaria raster a partir de poligonos."""
    bounds = bbox.geometry.bounds
    transform = from_bounds(
        bounds[0], bounds[1], bounds[2], bounds[3],
        bbox_size[0], bbox_size[1]
    )
    
    mask = ~geometry_mask(
        gdf.geometry,
        out_shape=(bbox_size[1], bbox_size[0]),
        transform=transform,
        invert=False
    )
    
    return mask.astype(np.uint8)


def create_tiles(bbox, bbox_size, max_tile_size=2400):
    """
    Divide un bbox en tiles más pequeños para evitar límite de Sentinel Hub.
    
    Args:
        bbox: BBox original
        bbox_size: Tamaño en píxeles (width, height)
        max_tile_size: Tamaño máximo de tile (default: 2400, límite SH: 2500)
        
    Returns:
        Lista de tuplas (tile_bbox, tile_size, tile_offset)
    """
    width, height = bbox_size
    bounds = bbox.geometry.bounds  # (minx, miny, maxx, maxy)
    
    # Calcular número de tiles necesarios
    n_tiles_x = int(np.ceil(width / max_tile_size))
    n_tiles_y = int(np.ceil(height / max_tile_size))
    
    print(f"   Dividiendo en {n_tiles_x}x{n_tiles_y} = {n_tiles_x * n_tiles_y} tiles")
    
    tiles = []
    
    # Calcular tamaño de pixel en grados
    pixel_width = (bounds[2] - bounds[0]) / width
    pixel_height = (bounds[3] - bounds[1]) / height
    
    for i in range(n_tiles_y):
        for j in range(n_tiles_x):
            # Calcular offsets en píxeles
            x_start = j * max_tile_size
            y_start = i * max_tile_size
            
            # Calcular tamaño del tile (último puede ser más pequeño)
            tile_width = min(max_tile_size, width - x_start)
            tile_height = min(max_tile_size, height - y_start)
            
            # Calcular bounds del tile en coordenadas geográficas
            tile_minx = bounds[0] + x_start * pixel_width
            tile_maxx = bounds[0] + (x_start + tile_width) * pixel_width
            tile_miny = bounds[1] + y_start * pixel_height
            tile_maxy = bounds[1] + (y_start + tile_height) * pixel_height
            
            tile_bbox = BBox(bbox=[tile_minx, tile_miny, tile_maxx, tile_maxy], crs=bbox.crs)
            tile_size = (tile_width, tile_height)
            tile_offset = (x_start, y_start)
            
            tiles.append((tile_bbox, tile_size, tile_offset))
    
    return tiles


def download_sentinel1_vv(bbox, bbox_size, time_interval, sh_config, aoi_mask=None):
    """
    Descarga datos Sentinel-1 VV con correccion de terreno.
    Divide en tiles si el área es muy grande para Sentinel Hub.
    """
    width, height = bbox_size
    max_size = 2400  # Límite de Sentinel Hub: 2500, usamos 2400 por seguridad
    
    # Si el área es pequeña, descargar directamente
    if width <= max_size and height <= max_size:
        print(f"   Descargando Sentinel-1 VV {time_interval}...")
        return _download_sentinel1_single_tile(bbox, bbox_size, time_interval, sh_config, aoi_mask)
    
    # Si es muy grande, dividir en tiles
    print(f"\n   Área muy grande ({width}x{height} px), dividiendo en tiles...")
    tiles = create_tiles(bbox, bbox_size, max_tile_size=max_size)
    
    # Inicializar arrays de salida
    vv_full = np.full(bbox_size[::-1], np.nan, dtype=np.float32)  # (height, width)
    mask_full = np.zeros(bbox_size[::-1], dtype=np.uint8)
    
    # Procesar cada tile
    for idx, (tile_bbox, tile_size, tile_offset) in enumerate(tiles, 1):
        print(f"\n   Procesando tile {idx}/{len(tiles)}: {tile_size[0]}x{tile_size[1]} px...")
        
        # Extraer máscara del tile si existe
        x_off, y_off = tile_offset
        tile_w, tile_h = tile_size
        tile_mask = None
        if aoi_mask is not None:
            tile_mask = aoi_mask[y_off:y_off+tile_h, x_off:x_off+tile_w]
        
        try:
            # Descargar tile
            vv_tile, mask_tile = _download_sentinel1_single_tile(
                tile_bbox, tile_size, time_interval, sh_config, tile_mask
            )
            
            # Insertar en arrays completos
            vv_full[y_off:y_off+tile_h, x_off:x_off+tile_w] = vv_tile
            mask_full[y_off:y_off+tile_h, x_off:x_off+tile_w] = mask_tile
            
            print(f"      ✅ Tile {idx} completado")
            
        except Exception as e:
            print(f"      ⚠️ Error en tile {idx}: {e}")
            # Continuar con el siguiente tile
            continue
    
    print(f"\n   ✅ Todos los tiles procesados")
    print(f"      Píxeles válidos totales: {np.sum(mask_full):,}")
    print(f"      Rango VV: [{np.nanmin(vv_full):.2f}, {np.nanmax(vv_full):.2f}] dB")
    
    return vv_full, mask_full


def _download_sentinel1_single_tile(bbox, bbox_size, time_interval, sh_config, aoi_mask=None):
    """Descarga un tile individual de Sentinel-1 VV."""
    sentinel1_cdse = DataCollection.define(
        name='SENTINEL1_IW_CDSE',
        api_id='sentinel-1-grd',
        service_url=sh_config.sh_base_url,
        collection_type='sentinel-1-grd',
        sensor_type=None,
        bands=('VV', 'VH'),
        is_timeless=False
    )
    
    evalscript = """
    //VERSION=3
    function setup() {
        return {
            input: [{bands: ["VV"], orthorectify: true}],
            output: [{id: "VV", bands: 1, sampleType: "FLOAT32"}]
        };
    }
    function evaluatePixel(sample) {
        return {VV: [sample.VV]};
    }
    """
    
    request = SentinelHubRequest(
        evalscript=evalscript,
        input_data=[
            SentinelHubRequest.input_data(
                data_collection=sentinel1_cdse,
                time_interval=time_interval,
                mosaicking_order='mostRecent',
                other_args={
                    "dataFilter": {
                        "resolution": "HIGH",
                        "acquisitionMode": "IW",
                        "orbitDirection": "DESCENDING"
                    },
                    "processing": {
                        "backCoeff": "GAMMA0_TERRAIN",
                        "orthorectify": True,
                        "demInstance": "COPERNICUS_30"
                    }
                }
            )
        ],
        responses=[SentinelHubRequest.output_response('VV', MimeType.TIFF)],
        bbox=bbox,
        size=bbox_size,
        config=sh_config
    )
    
    response = request.get_data()
    
    if isinstance(response, list) and len(response) > 0:
        vv_linear = response[0]['VV.tif'] if isinstance(response[0], dict) else response[0]
    else:
        vv_linear = response['VV.tif']
    
    if vv_linear.ndim > 2:
        vv_linear = vv_linear.squeeze()
    
    mask = ((vv_linear > 0) & np.isfinite(vv_linear)).astype(np.uint8)
    
    if aoi_mask is not None:
        mask = mask & aoi_mask
        vv_linear = np.where(aoi_mask == 1, vv_linear, np.nan)
    
    # Aplicar filtro Lee
    vv_filtered = lee_filter(vv_linear, mask, window_size=5)
    
    # Convertir a dB
    vv_db = 10 * np.log10(vv_filtered + 1e-10)
    vv_db = np.where(mask == 1, vv_db, np.nan)
    
    return vv_db, mask


def extract_p99_polygons(vv_difference, mask_combined, bbox, bbox_size, resolution=10):
    """Extrae poligonos donde la diferencia temporal supera P99."""
    diferencias_validas = vv_difference[~np.isnan(vv_difference)]
    p99_threshold = np.percentile(diferencias_validas, 99)
    
    print(f"\nPercentil 99: {p99_threshold:.3f} dB")
    
    mask_p99 = np.zeros_like(vv_difference, dtype=np.uint8)
    mask_p99[mask_combined & (vv_difference > p99_threshold)] = 1
    
    n_pixels_p99 = np.sum(mask_p99)
    print(f"   Pixeles P99: {n_pixels_p99:,} ({(n_pixels_p99 * resolution**2) / 10000:.2f} ha)")
    
    bbox_coords = bbox.geometry.bounds
    transform = from_bounds(
        bbox_coords[0], bbox_coords[1], 
        bbox_coords[2], bbox_coords[3],
        bbox_size[0], bbox_size[1]
    )
    
    print(f"   Vectorizando...")
    shapes_gen = rasterio_shapes(
        mask_p99.astype(np.int16),
        mask=mask_p99.astype(bool),
        transform=transform
    )
    
    polygons = [shape(geom) for geom, value in shapes_gen if value == 1]
    print(f"   Poligonos: {len(polygons)}")
    
    if len(polygons) == 0:
        return None, p99_threshold
    
    gdf_p99 = gpd.GeoDataFrame({'geometry': polygons}, crs='EPSG:4326')
    gdf_p99['area_ha'] = gdf_p99.geometry.area * 111320 * 111320 / 10000
    gdf_p99['threshold'] = p99_threshold
    
    print(f"   Area total: {gdf_p99['area_ha'].sum():.2f} ha")
    
    return gdf_p99, p99_threshold


def filter_polygons_by_ndvi(gdf, fecha_inicio, fecha_fin, ndvi_threshold=0.1, tiles_info=None):
    """
    Filtra polígonos por NDVI usando Google Earth Engine.
    Procesa por tiles espaciales para evitar límite de payload (10MB).
    
    Args:
        gdf: GeoDataFrame con polígonos a filtrar
        fecha_inicio: Fecha inicio formato 'YYYY-MM-DD'
        fecha_fin: Fecha fin formato 'YYYY-MM-DD'
        ndvi_threshold: Umbral NDVI (default: 0.1)
        tiles_info: Lista de tuplas (tile_bbox, tile_size, tile_offset) para procesar por tiles
        
    Returns:
        GeoDataFrame filtrado (solo polígonos con NDVI < threshold)
    """
    print(f"\n📊 Filtrando por NDVI < {ndvi_threshold}...")
    print(f"   Periodo: {fecha_inicio} a {fecha_fin}")
    print(f"   Total polígonos P99: {len(gdf):,}")
    
    if len(gdf) == 0:
        return gdf
    
    # ============================================
    # MÉTODO ESCALABLE: NDVI por tiles espaciales
    # ============================================
    # Para áreas grandes con muchos polígonos (>1000), procesar por tiles
    # evita superar el límite de 10MB de Earth Engine
    
    # Resetear índice para evitar problemas
    gdf = gdf.reset_index(drop=True)
    
    # Si hay pocos polígonos, procesar directamente (método notebook)
    if len(gdf) < 500:
        print(f"   Pocos polígonos, procesando directamente...")
        return _filter_ndvi_simple(gdf, fecha_inicio, fecha_fin, ndvi_threshold)
    
    # Para muchos polígonos, usar tiles espaciales
    print(f"   Muchos polígonos, procesando por tiles espaciales...")
    
    # Si no se proveen tiles, crearlos dinámicamente
    if tiles_info is None:
        # Dividir en grid 3x3 = 9 tiles
        bounds = gdf.total_bounds
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        
        tiles_info = []
        for i in range(3):
            for j in range(3):
                tile_minx = bounds[0] + (width / 3) * j
                tile_maxx = bounds[0] + (width / 3) * (j + 1)
                tile_miny = bounds[1] + (height / 3) * i
                tile_maxy = bounds[1] + (height / 3) * (i + 1)
                
                tile_bbox = BBox(bbox=[tile_minx, tile_miny, tile_maxx, tile_maxy], crs=CRS.WGS84)
                tiles_info.append((tile_bbox, None, None))
        
        print(f"   Creados {len(tiles_info)} tiles espaciales")
    
    return _filter_ndvi_by_tiles_spatial(gdf, fecha_inicio, fecha_fin, ndvi_threshold, tiles_info)


def _filter_ndvi_simple(gdf, fecha_inicio, fecha_fin, ndvi_threshold):
    """Método simple para pocos polígonos (< 500) - replica notebook exactamente."""
    # Usar bounds en lugar de crear FeatureCollection grande
    roi_bounds = gdf.total_bounds
    roi = ee.Geometry.Rectangle([roi_bounds[0], roi_bounds[1], roi_bounds[2], roi_bounds[3]])
    
    print(f"\n   Cargando colección Sentinel-2...")
    
    # Filtrar imágenes por cobertura de nubes
    s2_collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
        .filterBounds(roi) \
        .filterDate(fecha_inicio, fecha_fin) \
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', CLOUD_THRESHOLD))
    
    try:
        n_images = s2_collection.size().getInfo()
        print(f"   ✅ Imágenes disponibles: {n_images} (umbral nubes < {CLOUD_THRESHOLD}%)")
    except Exception as e:
        print(f"   ⚠️ Error consultando imágenes: {e}")
        n_images = 0
    
    if s2_collection is None or n_images == 0:
        print(f"   ⚠️ Sin imágenes S2, ELIMINANDO todos los polígonos (sin validación NDVI)")
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
    
    # Calcular NDVI mediano
    def add_ndvi(image):
        return image.addBands(image.normalizedDifference(['B8', 'B4']).rename('NDVI'))
    
    ndvi_median = s2_collection.map(add_ndvi).select('NDVI').median()
    print(f"   Mosaico NDVI calculado")
    
    # Procesar en batches pequeños
    return _process_ndvi_batches(gdf, ndvi_median, ndvi_threshold, batch_size=200)


def _filter_ndvi_by_tiles_spatial(gdf, fecha_inicio, fecha_fin, ndvi_threshold, tiles_info):
    """Filtra NDVI procesando por tiles espaciales - escalable para áreas grandes."""
    print(f"   Procesando {len(tiles_info)} tiles espaciales...")
    
    filtered_gdfs = []
    
    for idx, (tile_bbox, _, _) in enumerate(tiles_info, 1):
        # Filtrar polígonos en este tile
        bounds = tile_bbox.geometry.bounds
        geom_bounds = gdf.geometry.bounds
        
        tile_mask = ~(
            (geom_bounds['maxx'] < bounds[0]) |
            (geom_bounds['minx'] > bounds[2]) |
            (geom_bounds['maxy'] < bounds[1]) |
            (geom_bounds['miny'] > bounds[3])
        )
        
        gdf_tile = gdf[tile_mask].copy()
        
        if len(gdf_tile) == 0:
            print(f"      Tile {idx}/{len(tiles_info)}: 0 polígonos - omitido")
            continue
        
        print(f"      Tile {idx}/{len(tiles_info)}: {len(gdf_tile)} polígonos")
        
        # Crear ROI del tile
        tile_roi = ee.Geometry.Rectangle([bounds[0], bounds[1], bounds[2], bounds[3]])
        
        # Filtrar imágenes por cobertura de nubes
        s2_collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
            .filterBounds(tile_roi) \
            .filterDate(fecha_inicio, fecha_fin) \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', CLOUD_THRESHOLD))
        
        try:
            n_images = s2_collection.size().getInfo()
        except:
            n_images = 0
        
        if s2_collection is None or n_images == 0:
            # Sin imágenes, ELIMINAR todos (no se puede validar con NDVI)
            print(f"         Sin imágenes S2, ELIMINANDO {len(gdf_tile)} polígonos (sin validación)")
            continue
        
        # Calcular NDVI para este tile
        def add_ndvi(image):
            return image.addBands(image.normalizedDifference(['B8', 'B4']).rename('NDVI'))
        
        ndvi_median = s2_collection.map(add_ndvi).select('NDVI').median()
        
        # Procesar polígonos del tile en batches
        gdf_filtered = _process_ndvi_batches(gdf_tile, ndvi_median, ndvi_threshold, batch_size=200)
        
        if len(gdf_filtered) > 0:
            filtered_gdfs.append(gdf_filtered)
            print(f"         Conservados: {len(gdf_filtered)} polígonos")
    
    if len(filtered_gdfs) == 0:
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
    
    gdf_final = pd.concat(filtered_gdfs, ignore_index=True)
    
    n_original = len(gdf)
    n_final = len(gdf_final)
    n_removed = n_original - n_final
    
    print(f"\n   ✅ Filtrado completado:")
    print(f"      Polígonos originales: {n_original:,}")
    print(f"      Polígonos con NDVI < {ndvi_threshold}: {n_final:,}")
    print(f"      Polígonos eliminados (vegetación): {n_removed:,} ({100*n_removed/n_original:.1f}%)")
    
    return gdf_final


def _process_ndvi_batches(gdf, ndvi_median, ndvi_threshold, batch_size=200):
    """Procesa polígonos en batches para extraer NDVI y filtrar."""
    n_batches = int(np.ceil(len(gdf) / batch_size))
    filtered_parts = []
    
    for batch_idx in range(n_batches):
        start_idx = batch_idx * batch_size
        end_idx = min((batch_idx + 1) * batch_size, len(gdf))
        gdf_batch = gdf.iloc[start_idx:end_idx].copy()
        
        try:
            # Convertir a FeatureCollection
            geojson_batch = json.loads(gdf_batch.to_json())
            features_batch = [
                ee.Feature(ee.Geometry(f['geometry']), f['properties'])
                for f in geojson_batch['features']
            ]
            ee_polygons_batch = ee.FeatureCollection(features_batch)
            
            # Calcular NDVI promedio por polígono
            def compute_ndvi_mean(feature):
                ndvi_mean = ndvi_median.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=feature.geometry(),
                    scale=10,
                    maxPixels=1e9
                ).get('NDVI')
                return feature.set('ndvi_mean', ndvi_mean)
            
            polygons_with_ndvi = ee_polygons_batch.map(compute_ndvi_mean)
            
            # Filtrar por umbral
            polygons_filtered = polygons_with_ndvi.filter(
                ee.Filter.lt('ndvi_mean', ndvi_threshold)
            )
            
            n_filtered = polygons_filtered.size().getInfo()
            
            if n_filtered > 0:
                geojson_filtered = polygons_filtered.getInfo()
                geometries = [shape(f['geometry']) for f in geojson_filtered['features']]
                properties = [f['properties'] for f in geojson_filtered['features']]
                gdf_filtered = gpd.GeoDataFrame(properties, geometry=geometries, crs='EPSG:4326')
                filtered_parts.append(gdf_filtered)
        except Exception as e:
            print(f"         ⚠️ Error en batch {batch_idx+1}: {e}, ELIMINANDO polígonos")
            # No agregar nada - eliminar estos polígonos por seguridad
    
    if len(filtered_parts) == 0:
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
    
    return pd.concat(filtered_parts, ignore_index=True)


def _extract_ndvi_per_polygon_batched(gdf, ee_polygons_fc, ndvi_median, ndvi_threshold, batch_size=1000):
    """
    Extrae NDVI promedio por polígono en batches y filtra por umbral.
    Replica el flujo del notebook: compute_ndvi_mean() + filtrado.
    
    Args:
        gdf: GeoDataFrame original con polígonos
        ee_polygons_fc: FeatureCollection de Earth Engine con los mismos polígonos
        ndvi_median: Imagen NDVI mediana ya calculada
        ndvi_threshold: Umbral para conservar (< threshold)
        batch_size: Número de polígonos por batch
    
    Returns:
        GeoDataFrame filtrado (NDVI < threshold)
    """
    print(f"\n   Extrayendo NDVI por polígono (batches de {batch_size})...")
    
    # Calcular NDVI promedio para cada polígono
    def compute_ndvi_mean(feature):
        ndvi_mean = ndvi_median.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=feature.geometry(),
            scale=10,
            maxPixels=1e9
        ).get('NDVI')
        return feature.set('ndvi_mean', ndvi_mean)
    
    polygons_with_ndvi = ee_polygons_fc.map(compute_ndvi_mean)
    
    # Filtrar por umbral (conservar solo NDVI < threshold, es decir, NO vegetación)
    polygons_filtered = polygons_with_ndvi.filter(
        ee.Filter.lt('ndvi_mean', ndvi_threshold)
    )
    
    # Obtener conteos
    try:
        n_original = ee_polygons_fc.size().getInfo()
        n_filtered = polygons_filtered.size().getInfo()
        n_removed = n_original - n_filtered
        
        print(f"\n   ✅ Filtrado NDVI completado:")
        print(f"      Polígonos originales: {n_original:,}")
        print(f"      Polígonos con NDVI < {ndvi_threshold}: {n_filtered:,}")
        print(f"      Polígonos eliminados (vegetación): {n_removed:,} ({100*n_removed/n_original:.1f}%)")
        
    except Exception as e:
        print(f"   ⚠️ No se pudieron obtener estadísticas: {e}")
        n_filtered = None
    
    # Obtener resultados filtrados
    if n_filtered is None or n_filtered == 0:
        # Si no sabemos cuántos hay o no hay ninguno, consultar de todas formas
        try:
            n_check = polygons_filtered.size().getInfo()
            if n_check == 0:
                return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
        except:
            return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
    
    # Descargar resultados en batches (Earth Engine tiene límite de payload)
    # Si hay muchos polígonos, procesar en lotes
    try:
        if n_filtered and n_filtered > batch_size:
            print(f"   Descargando en batches de {batch_size}...")
            gdf_parts = []
            
            for batch_start in range(0, n_filtered, batch_size):
                batch_end = min(batch_start + batch_size, n_filtered)
                batch_fc = ee.FeatureCollection(polygons_filtered.toList(batch_size, batch_start))
                
                geojson_batch = batch_fc.getInfo()
                geometries = [shape(f['geometry']) for f in geojson_batch['features']]
                properties = [f['properties'] for f in geojson_batch['features']]
                gdf_batch = gpd.GeoDataFrame(properties, geometry=geometries, crs='EPSG:4326')
                gdf_parts.append(gdf_batch)
                
                print(f"      Batch {batch_start}-{batch_end}: {len(gdf_batch)} polígonos")
            
            gdf_final = pd.concat(gdf_parts, ignore_index=True)
            return gdf_final
        else:
            # Pocos polígonos, descargar de una vez
            geojson_filtered = polygons_filtered.getInfo()
            geometries = [shape(f['geometry']) for f in geojson_filtered['features']]
            properties = [f['properties'] for f in geojson_filtered['features']]
            return gpd.GeoDataFrame(properties, geometry=geometries, crs='EPSG:4326')
            
    except Exception as e:
        print(f"   ⚠️ Error descargando resultados: {e}")
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')


def _filter_ndvi_by_tiles(gdf, fecha_inicio, fecha_fin, ndvi_threshold, tiles_info):
    """Filtra NDVI procesando por tiles espaciales con sub-batching automático."""
    print(f"   Procesando por {len(tiles_info)} tiles espaciales...")
    
    # Resetear índice para evitar problemas de alineación
    gdf = gdf.reset_index(drop=True)
    
    filtered_gdfs = []
    
    for idx, (tile_bbox, tile_size, tile_offset) in enumerate(tiles_info, 1):
        # Extraer bounds del tile
        bounds = tile_bbox.geometry.bounds  # (minx, miny, maxx, maxy)
        
        # Filtrar polígonos que intersectan este tile usando vectorización
        geom_bounds = gdf.geometry.bounds
        tile_mask = ~(
            (geom_bounds['maxx'] < bounds[0]) |  # Polígono completamente a la izquierda del tile
            (geom_bounds['minx'] > bounds[2]) |  # Polígono completamente a la derecha del tile
            (geom_bounds['maxy'] < bounds[1]) |  # Polígono completamente abajo del tile
            (geom_bounds['miny'] > bounds[3])    # Polígono completamente arriba del tile
        )
        
        gdf_tile = gdf[tile_mask].copy()
        
        if len(gdf_tile) == 0:
            print(f"      Tile {idx}/{len(tiles_info)}: 0 polígonos - omitido")
            continue
        
        # Si el tile tiene muchos polígonos, subdividir en sub-batches
        # Límite conservador: 5000 polígonos por sub-batch para evitar 10MB payload
        max_polygons_per_batch = 5000
        
        if len(gdf_tile) > max_polygons_per_batch:
            print(f"      Tile {idx}/{len(tiles_info)}: {len(gdf_tile)} polígonos (grande, subdividiendo)...")
            
            # Subdividir en sub-batches
            tile_filtered = []
            num_sub_batches = (len(gdf_tile) + max_polygons_per_batch - 1) // max_polygons_per_batch
            
            for sub_idx in range(num_sub_batches):
                start_idx = sub_idx * max_polygons_per_batch
                end_idx = min((sub_idx + 1) * max_polygons_per_batch, len(gdf_tile))
                gdf_sub_batch = gdf_tile.iloc[start_idx:end_idx].copy()
                
                # Filtrar NDVI para este sub-batch
                try:
                    gdf_sub_filtered = _process_ndvi_batch(gdf_sub_batch, fecha_inicio, fecha_fin, ndvi_threshold, bounds)
                    if len(gdf_sub_filtered) > 0:
                        tile_filtered.append(gdf_sub_filtered)
                    print(f"         Sub-batch {sub_idx+1}/{num_sub_batches}: {len(gdf_sub_batch)} → {len(gdf_sub_filtered)}")
                except Exception as e:
                    print(f"         ⚠️ Error en sub-batch {sub_idx+1}/{num_sub_batches}: {e}")
                    continue
            
            if tile_filtered:
                gdf_tile_final = pd.concat(tile_filtered, ignore_index=True)
                filtered_gdfs.append(gdf_tile_final)
                print(f"      Tile {idx}/{len(tiles_info)}: ✓ {len(gdf_tile_final)} conservados (de {len(gdf_tile)})")
            else:
                print(f"      Tile {idx}/{len(tiles_info)}: ✓ 0 conservados")
            
            continue
        
        print(f"      Tile {idx}/{len(tiles_info)}: {len(gdf_tile)} polígonos...", end=" ")
        
        try:
            gdf_filtered_tile = _process_ndvi_batch(gdf_tile, fecha_inicio, fecha_fin, ndvi_threshold, bounds)
            
            if len(gdf_filtered_tile) > 0:
                filtered_gdfs.append(gdf_filtered_tile)
                print(f"✓ {len(gdf_filtered_tile)} conservados")
            else:
                print("✓ 0 conservados")
            
        except Exception as e:
            print(f"⚠️ Error: {str(e)[:80]}")
            continue
    
    # Consolidar resultados
    if len(filtered_gdfs) == 0:
        print(f"\n   ⚠️ Ningún polígono cumple criterio NDVI < {ndvi_threshold}")
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
    
    gdf_final = pd.concat(filtered_gdfs, ignore_index=True)
    
    n_original = len(gdf)
    n_final = len(gdf_final)
    n_removed = n_original - n_final
    
    print(f"\n   ✅ Filtrado completado:")
    print(f"      Polígonos originales: {n_original:,}")
    print(f"      Polígonos con NDVI < {ndvi_threshold}: {n_final:,}")
    print(f"      Polígonos eliminados (vegetación): {n_removed:,} ({100*n_removed/n_original:.1f}%)")
    
    return gdf_final


def _process_ndvi_batch(gdf_batch, fecha_inicio, fecha_fin, ndvi_threshold, tile_bounds):
    """
    Procesa un batch de polígonos para filtrado NDVI.
    
    Args:
        gdf_batch: GeoDataFrame con polígonos a procesar
        fecha_inicio: Fecha inicio en formato YYYY-MM-DD
        fecha_fin: Fecha fin en formato YYYY-MM-DD
        ndvi_threshold: Umbral NDVI (conservar < threshold)
        tile_bounds: Tupla (minx, miny, maxx, maxy) del tile
    
    Returns:
        GeoDataFrame con polígonos filtrados (NDVI < threshold)
    """
    # Crear geometría del tile
    tile_roi = ee.Geometry.Rectangle([tile_bounds[0], tile_bounds[1], tile_bounds[2], tile_bounds[3]])
    
    # Obtener colección Sentinel-2 para este tile
    s2_collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
        .filterBounds(tile_roi) \
        .filterDate(fecha_inicio, fecha_fin) \
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', CLOUD_THRESHOLD))
    
    # Verificar disponibilidad de imágenes
    try:
        n_images = s2_collection.size().getInfo()
    except:
        # Si hay error consultando, devolver todos los polígonos (asumiendo sin vegetación)
        return gdf_batch.copy()
    
    if n_images == 0:
        # Sin imágenes disponibles, devolver todos los polígonos (asumiendo sin vegetación)
        return gdf_batch.copy()
    
    # Calcular NDVI mediano del tile
    def add_ndvi(image):
        return image.addBands(image.normalizedDifference(['B8', 'B4']).rename('NDVI'))
    
    ndvi_median = s2_collection.map(add_ndvi).select('NDVI').median()
    
    # Convertir polígonos a FeatureCollection
    geojson_batch = json.loads(gdf_batch.to_json())
    features_batch = [
        ee.Feature(ee.Geometry(f['geometry']), f['properties'])
        for f in geojson_batch['features']
    ]
    ee_polygons_batch = ee.FeatureCollection(features_batch)
    
    # Calcular NDVI promedio para cada polígono
    def compute_ndvi_mean(feature):
        ndvi_mean = ndvi_median.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=feature.geometry(),
            scale=10,
            maxPixels=1e9
        ).get('NDVI')
        return feature.set('ndvi_mean', ndvi_mean)
    
    polygons_with_ndvi = ee_polygons_batch.map(compute_ndvi_mean)
    
    # Filtrar por umbral NDVI (conservar solo < threshold, es decir, NO vegetación)
    polygons_filtered = polygons_with_ndvi.filter(
        ee.Filter.lt('ndvi_mean', ndvi_threshold)
    )
    
    # Obtener resultados
    n_filtered = polygons_filtered.size().getInfo()
    
    if n_filtered > 0:
        geojson_filtered = polygons_filtered.getInfo()
        geometries = [shape(f['geometry']) for f in geojson_filtered['features']]
        properties = [f['properties'] for f in geojson_filtered['features']]
        return gpd.GeoDataFrame(properties, geometry=geometries, crs='EPSG:4326')
    else:
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')


def _filter_ndvi_by_batches(gdf, fecha_inicio, fecha_fin, ndvi_threshold, batch_size=200):
    """Filtra NDVI procesando por lotes de polígonos (fallback menos eficiente)."""
    print(f"   ⚠️ Procesando sin tiles - usando lotes de {batch_size} polígonos...")
    
    # Obtener región de interés y calcular NDVI mediano
    roi = gdf.total_bounds  # [minx, miny, maxx, maxy]
    roi_geom = ee.Geometry.Rectangle([roi[0], roi[1], roi[2], roi[3]])
    
    s2_collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED') \
        .filterBounds(roi_geom) \
        .filterDate(fecha_inicio, fecha_fin) \
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', CLOUD_THRESHOLD))
    
    # Verificar si hay imágenes disponibles
    try:
        n_images = s2_collection.size().getInfo()
        print(f"   Imágenes S2: {n_images}")
    except:
        print(f"   ⚠️ Error consultando imágenes S2, omitiendo filtro NDVI")
        return gdf
    
    if n_images == 0:
        print(f"   ⚠️ Sin imágenes, omitiendo filtro NDVI")
        return gdf
    
    # Calcular NDVI mediano
    def add_ndvi(image):
        return image.addBands(image.normalizedDifference(['B8', 'B4']).rename('NDVI'))
    
    ndvi_median = s2_collection.map(add_ndvi).select('NDVI').median()
    
    # Procesar polígonos en lotes
    n_batches = int(np.ceil(len(gdf) / batch_size))
    print(f"   Procesando en {n_batches} lotes...")
    
    filtered_gdfs = []
    
    for batch_idx in range(n_batches):
        start_idx = batch_idx * batch_size
        end_idx = min((batch_idx + 1) * batch_size, len(gdf))
        
        gdf_batch = gdf.iloc[start_idx:end_idx].copy()
        
        print(f"      Lote {batch_idx + 1}/{n_batches}: {len(gdf_batch)} polígonos...", end=" ")
        
        try:
            # Convertir a FeatureCollection
            geojson_batch = json.loads(gdf_batch.to_json())
            features_batch = [
                ee.Feature(ee.Geometry(f['geometry']), f['properties'])
                for f in geojson_batch['features']
            ]
            ee_polygons_batch = ee.FeatureCollection(features_batch)
            
            # Calcular NDVI promedio para cada polígono
            def compute_ndvi_mean(feature):
                ndvi_mean = ndvi_median.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=feature.geometry(),
                    scale=10,
                    maxPixels=1e9
                ).get('NDVI')
                return feature.set('ndvi_mean', ndvi_mean)
            
            polygons_with_ndvi = ee_polygons_batch.map(compute_ndvi_mean)
            
            # Filtrar por umbral NDVI
            polygons_filtered = polygons_with_ndvi.filter(
                ee.Filter.lt('ndvi_mean', ndvi_threshold)
            )
            
            # Obtener resultados
            n_filtered_batch = polygons_filtered.size().getInfo()
            
            if n_filtered_batch > 0:
                geojson_filtered = polygons_filtered.getInfo()
                geometries = [shape(f['geometry']) for f in geojson_filtered['features']]
                properties = [f['properties'] for f in geojson_filtered['features']]
                gdf_filtered_batch = gpd.GeoDataFrame(properties, geometry=geometries, crs='EPSG:4326')
                filtered_gdfs.append(gdf_filtered_batch)
            
            print(f"✓ {n_filtered_batch} conservados")
            
        except Exception as e:
            print(f"⚠️ Error: {str(e)[:50]}")
            # En caso de error, conservar todos los polígonos del lote
            filtered_gdfs.append(gdf_batch)
            continue
    
    # Consolidar resultados
    if len(filtered_gdfs) == 0:
        return gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
    
    gdf_final = pd.concat(filtered_gdfs, ignore_index=True)
    
    n_original = len(gdf)
    n_final = len(gdf_final)
    n_removed = n_original - n_final
    
    print(f"\n   ✅ Filtrado completado:")
    print(f"      Polígonos originales: {n_original:,}")
    print(f"      Polígonos con NDVI < {ndvi_threshold}: {n_final:,}")
    print(f"      Polígonos eliminados (vegetación): {n_removed:,} ({100*n_removed/n_original:.1f}%)")
    
    return gdf_final


def initialize_sentinel_hub_config(client_id, client_secret):
    """Inicializa configuracion de Sentinel Hub."""
    config = SHConfig()
    config.sh_client_id = client_id
    config.sh_client_secret = client_secret
    config.sh_base_url = "https://sh.dataspace.copernicus.eu"
    config.sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
    return config


def process_new_constructions(geometry, output_dir, year, month, sh_config):
    """Pipeline completo de deteccion de construcciones nuevas."""
    print("\n" + "="*70)
    print("DETECCION DE CONSTRUCCIONES NUEVAS")
    print("   Metodologia: Sentinel-1 VV Temporal + NDVI")
    print("="*70)
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    construcciones_path = output_dir / "construcciones_existentes.geojson"
    
    # MÉTODO DE DETECCIÓN: Construcciones individuales vs disueltas
    # 
    # OPCIÓN 1: GPKG DISUELTO - Rápido pero conservador
    #   - Usa 1 polígono único (2.4M construcciones fusionadas)
    #   - Buffer 3m solo al perímetro exterior
    #   - Excluye TODO el interior + 3m borde
    #   - Ventaja: 1000x más rápido
    #   - Desventaja: NO detecta construcciones en huecos/patios internos
    #
    # OPCIÓN 2 (REPLICAR NOTEBOOK): CONSTRUCCIONES INDIVIDUALES - Lento pero preciso
    #   - Usa 2.4M construcciones individuales
    #   - Buffer 3m a CADA construcción
    #   - Conserva huecos entre construcciones para análisis
    #   - Ventaja: Detecta más construcciones nuevas (ej: en calles angostas)
    #   - Desventaja: 100-1000x más lento
    #
    # CONFIGURACIÓN: Polígono disuelto para producción
    # - 1 polígono fusionado (2.4M construcciones)
    # - 1000x más rápido que construcciones individuales
    # - Más conservador: NO detecta construcciones en espacios internos
    
    local_gpkg = CONSTRUCCIONES_GPKG_DISSOLVE  # Rápido (producción) - 1000x más veloz
    
    print(f"   ⚡ Usando polígono DISUELTO (rápido): {Path(local_gpkg).name}")
    
    gdf_construcciones = download_catastro_construcciones(
        geometry, 
        construcciones_path,
        local_gpkg_path=local_gpkg if os.path.exists(local_gpkg) else None
    )
    
    if len(gdf_construcciones) == 0:
        print("No hay construcciones en el AOI")
        return None
    
    if isinstance(geometry, ee.Geometry):
        aoi_geojson = geometry.getInfo()
        # Envolver la geometría en un feature completo
        feature = {"type": "Feature", "geometry": aoi_geojson, "properties": {}}
        aoi_gdf = gpd.GeoDataFrame.from_features([feature], crs='EPSG:4326')
    else:
        aoi_gdf = geometry if isinstance(geometry, gpd.GeoDataFrame) else gpd.read_file(geometry)
        if aoi_gdf.crs != 'EPSG:4326':
            aoi_gdf = aoi_gdf.to_crs('EPSG:4326')
    
    aoi_bounds = aoi_gdf.total_bounds.tolist()
    aoi_bbox = BBox(bbox=aoi_bounds, crs=CRS.WGS84)
    bbox_size = bbox_to_dimensions(aoi_bbox, resolution=SENTINEL1_RESOLUTION)
    
    print(f"\nConfig: {SENTINEL1_RESOLUTION}m, {bbox_size} px")
    
    print(f"\nCreando mascaras...")
    aoi_mask = create_polygon_mask(aoi_gdf, aoi_bbox, bbox_size)
    
    # ⚠️ NOTA: Buffer aplicado en grados (método del notebook original)
    # Esto es geográficamente impreciso pero necesario para replicar resultados
    buffer_grados = BUFFER_CONSTRUCCIONES_METROS / 111320
    print(f"   Buffer: {BUFFER_CONSTRUCCIONES_METROS}m ≈ {buffer_grados:.8f} grados")
    
    # EXPLICACIÓN: Polígono disuelto + buffer 3m
    # - El polígono disuelto YA contiene TODO el interior construido (2.4M construcciones fusionadas)
    # - Al aplicar buffer(+3m), expandimos hacia AFUERA del perímetro
    # - Resultado: Excluye TODO el interior + 3m de borde (equivalente al notebook, 1000x más rápido)
    gdf_construcciones_buffered = gdf_construcciones.copy()
    gdf_construcciones_buffered['geometry'] = gdf_construcciones.geometry.buffer(buffer_grados)
    
    construcciones_mask = create_polygon_mask(gdf_construcciones_buffered, aoi_bbox, bbox_size)
    analisis_mask = aoi_mask & (~construcciones_mask.astype(bool)).astype(np.uint8)
    
    print(f"   AOI: {np.sum(aoi_mask):,} px")
    print(f"   Construcciones (+{BUFFER_CONSTRUCCIONES_METROS}m): {np.sum(construcciones_mask):,} px")
    print(f"   Analizable: {np.sum(analisis_mask):,} px ({(np.sum(analisis_mask)*SENTINEL1_RESOLUTION**2)/1e6:.2f} km2)")
    
    # Calcular períodos de análisis (mes completo actual vs mes completo anterior)
    # Mes actual: del 1 al último día del mes
    curr_start = datetime(year, month, 1)
    if month == 12:
        last_day_curr = datetime(year, 12, 31)
    else:
        last_day_curr = datetime(year, month + 1, 1) - timedelta(days=1)
    
    # Mes anterior: del 1 al último día del mes anterior
    if month == 1:
        prev_start = datetime(year - 1, 12, 1)
        last_day_prev = datetime(year - 1, 12, 31)
    else:
        prev_start = datetime(year, month - 1, 1)
        last_day_prev = datetime(year, month, 1) - timedelta(days=1)
    
    curr_interval = (curr_start.strftime("%Y-%m-%d"), last_day_curr.strftime("%Y-%m-%d"))
    prev_interval = (prev_start.strftime("%Y-%m-%d"), last_day_prev.strftime("%Y-%m-%d"))
    
    print(f"\nPeriodos:")
    print(f"   Anterior: {prev_interval[0]} a {prev_interval[1]}")
    print(f"   Actual:   {curr_interval[0]} a {curr_interval[1]}")
    
    # Calcular tiles una vez para reutilizar en Sentinel-1 y NDVI
    width, height = bbox_size
    tiles_info = None
    if width > 2400 or height > 2400:
        tiles_info = create_tiles(aoi_bbox, bbox_size, max_tile_size=2400)
        print(f"\n📐 Tiles calculados: {len(tiles_info)} tiles para reutilizar")
    
    print(f"\nDescargando Sentinel-1...")
    vv_prev, mask_prev = download_sentinel1_vv(aoi_bbox, bbox_size, prev_interval, sh_config, analisis_mask)
    vv_curr, mask_curr = download_sentinel1_vv(aoi_bbox, bbox_size, curr_interval, sh_config, analisis_mask)
    
    print(f"\nDiferencia temporal...")
    vv_diferencia = vv_curr - vv_prev
    mask_combinada = (mask_prev == 1) & (mask_curr == 1) & np.isfinite(vv_diferencia)
    vv_diferencia = np.where(mask_combinada, vv_diferencia, np.nan)
    
    diferencias_validas = vv_diferencia[~np.isnan(vv_diferencia)]
    print(f"   Media: {np.mean(diferencias_validas):.2f} dB")
    print(f"   Rango: [{np.min(diferencias_validas):.2f}, {np.max(diferencias_validas):.2f}] dB")
    
    gdf_p99, p99_threshold = extract_p99_polygons(
        vv_diferencia, mask_combinada, aoi_bbox, bbox_size, SENTINEL1_RESOLUTION
    )
    
    if gdf_p99 is None or len(gdf_p99) == 0:
        print("No hay cambios P99")
        return None
    
    # Filtrar NDVI usando los mismos tiles para máxima eficiencia
    gdf_final = filter_polygons_by_ndvi(
        gdf_p99, curr_interval[0], curr_interval[1], NDVI_THRESHOLD, tiles_info=tiles_info
    )
    
    if len(gdf_final) == 0:
        print("Todos eliminados por NDVI")
        return None
    
    output_path = output_dir / "new_urban.geojson"
    gdf_final.to_file(output_path, driver='GeoJSON')
    
    print(f"\n" + "="*70)
    print(f"DETECCION COMPLETADA")
    print(f"="*70)
    print(f"{output_path}")
    print(f"Poligonos: {len(gdf_final)}, Area: {gdf_final['area_ha'].sum():.2f} ha")
    print(f"="*70)
    
    return str(output_path)





def build_report(df_path, map_html, header_img1_path, header_img2_path, footer_img_path, output_dir, month, year, mes_num):
    """Genera el reporte HTML usando el template correcto."""
    from reporte.render_report import render
    from pathlib import Path
    import tempfile
    from src.aux_utils import download_gcs_to_temp
    
    # Leer datos del CSV
    import pandas as pd
    df = pd.read_csv(df_path)
    
    # Obtener top 5 UPLs con mayor área en intersección
    top_upls = df.nlargest(5, 'interseccion_ha')[['NOMBRE', 'interseccion_ha', 'total_ha']].to_dict('records')
    
    # Descargar imágenes de GCS si es necesario y convertir a rutas relativas
    output_dir_path = Path(output_dir)
    
    def get_image_url(gcs_path, output_dir):
        """Convierte ruta GCS a URL pública o ruta relativa local."""
        if gcs_path and gcs_path.startswith('gs://'):
            # Convertir gs://bucket/path a URL pública de GCS
            # Formato: https://storage.googleapis.com/bucket/path
            # IMPORTANTE: Codificar espacios y caracteres especiales en la URL
            from urllib.parse import quote
            gcs_path_clean = gcs_path.replace('gs://', '')
            # Codificar cada parte de la ruta (no el separador /)
            parts = gcs_path_clean.split('/')
            encoded_parts = [quote(part, safe='') for part in parts]
            url = f"https://storage.googleapis.com/{'/'.join(encoded_parts)}"
            print(f"   ✅ URL imagen: {Path(gcs_path).name}")
            return url
        elif gcs_path and os.path.exists(gcs_path):
            # Archivo local - copiar al directorio del reporte
            try:
                filename = Path(gcs_path).name
                dest_path = output_dir_path / filename
                import shutil
                shutil.copy2(gcs_path, dest_path)
                print(f"   ✅ Imagen copiada: {filename}")
                return filename  # Ruta relativa
            except Exception as e:
                print(f"   ⚠️ Error copiando imagen {Path(gcs_path).name}: {e}")
                return ""
        return ""
    
    header1_rel = get_image_url(header_img1_path, output_dir)
    header2_rel = get_image_url(header_img2_path, output_dir)
    footer_rel = get_image_url(footer_img_path, output_dir)
    
    # Manejar caso cuando map_html es None (falló generación de mapa)
    map_rel = ""
    if map_html and os.path.exists(map_html):
        map_rel = os.path.relpath(map_html, output_dir)
    
    # Preparar datos para el template
    template_data = {
        "TITULO": f"Reporte de Expansión Urbana - {month} {year}",
        "FECHA_REPORTE": f"{month} {year}",
        "HEADER_IMG1": header1_rel,
        "HEADER_IMG2": header2_rel,
        "FOOTER_IMG": footer_rel,
        "TOP_UPLS": [{"UPL": row['NOMBRE'], "INTER_HA": f"{row['interseccion_ha']:.2f}", "TOTAL_HA": f"{row['total_ha']:.2f}"} for row in top_upls],
        "MAP_IFRAME_URL": map_rel,
        "FUENTE": "Fuente: Sentinel-1 y Sentinel-2 de Copernicus y Google Earth Engine.",
        "URB_PROB_PERCENT": "20",
        "TIF_FILENAME": f"new_urban_{year}_{mes_num:02d}.geojson",
        "INTER_GEOJSON_FILENAME": f"new_urban_{year}_{mes_num:02d}_intersections.geojson",
        "NO_INTER_GEOJSON_FILENAME": f"new_urban_{year}_{mes_num:02d}_no_intersections.geojson",
        "CSV_FILENAME": f"resumen_expansion_upl_ha_{year}_{mes_num:02d}.csv"
    }
    
    # Crear archivo temporal con los datos JSON
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as temp_data:
        json.dump(template_data, temp_data, ensure_ascii=False, indent=2)
        temp_data_path = temp_data.name
    
    try:
        # Renderizar usando el template
        template_path = Path(__file__).parent.parent / "reporte" / "report_template.html"
        output_path = Path(output_dir) / f"urban_sprawl_reporte_{year}_{mes_num:02d}.html"
        
        render(template_path, Path(temp_data_path), output_path)
        
        print(f"✅ Reporte generado: {output_path}")
        return str(output_path)
    finally:
        # Limpiar archivo temporal
        try:
            os.unlink(temp_data_path)
        except:
            pass


def build_no_expansion_report(header_img1_path, header_img2_path, footer_img_path, output_dir, month, year, mes_num, custom_message=None):
    """Genera reporte cuando no hay expansion."""
    html_path = os.path.join(output_dir, f"urban_sprawl_reporte_{year}_{month}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(f"<html><body><h1>Sin expansion detectada - {month}/{year}</h1></body></html>")
    return html_path
