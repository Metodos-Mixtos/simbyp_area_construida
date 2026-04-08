import geopandas as gpd
import folium
import json
import os
import ee
import pandas as pd
import requests
from pathlib import Path
from src.config import GOOGLE_CLOUD_PROJECT

def _ensure_ee_initialized():
    """
    Asegurar que Earth Engine esté inicializado con credenciales de servicio.
    Si ya está inicializado, no hacer nada.
    Si no, intentar inicializar con credenciales de servicio.
    Esto es crítico para funciones que usan Earth Engine como generar mapas.
    """
    try:
        # Intentar hacer una pequeña operación con ee para verificar autenticación
        ee.Date("2020-01-01").format().getInfo()
        # Si llegamos aquí, ya está autenticado
        return
    except Exception as auth_error:
        # No está autenticado o la autenticación falló, intentar inicializar
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        
        if credentials_path and os.path.exists(credentials_path):
            try:
                from google.oauth2 import service_account
                
                with open(credentials_path) as f:
                    credentials_dict = json.load(f)
                
                credentials = service_account.Credentials.from_service_account_info(
                    credentials_dict,
                    scopes=[
                        'https://www.googleapis.com/auth/cloud-platform',
                        'https://www.googleapis.com/auth/earthengine'
                    ]
                )
                
                # Reset and initialize ee with credentials
                ee.Reset()
                ee.Initialize(
                    credentials=credentials,
                    project=GOOGLE_CLOUD_PROJECT,
                    opt_url='https://earthengine-highvolume.googleapis.com'
                )
                print("🔐 Earth Engine reautenticado con credenciales de servicio (maps_utils)")
                return
            except Exception as e:
                raise RuntimeError(
                    f"No se pudo autenticar Earth Engine en maps_utils.\n"
                    f"Error inicial: {auth_error}\n"
                    f"Error de credenciales: {e}\n"
                    f"Credenciales: {credentials_path}"
                )
        else:
            raise RuntimeError(
                f"No se pudo autenticar Earth Engine.\n"
                f"Error: {auth_error}\n"
                f"GOOGLE_APPLICATION_CREDENTIALS no configurado correctamente: {credentials_path}"
            )

def sanitize_gdf(gdf):
    """Sanitizar GeoDataFrame para evitar problemas al exportar a GeoJSON."""
    for col in gdf.columns:
        # Detectar columnas con objetos complejos o de fecha
        if gdf[col].dtype == "datetime64[ns]" or gdf[col].apply(lambda v: isinstance(v, pd.Timestamp)).any():
            gdf[col] = gdf[col].astype(str)
        elif gdf[col].dtype == "object":
            # Reemplazar cualquier valor problemático con su representación textual
            gdf[col] = gdf[col].apply(lambda v: str(v) if not isinstance(v, (int, float, str)) else v)
    return gdf

def get_tiles_from_ee(
    aoi_path: str,
    end_t1: str,
    end_t2: str,
    dataset: str = "SENTINEL",
    lookback_days: int = 365
):
    """
    Devuelve URLs de tiles (T1 y T2) desde Google Earth Engine para Sentinel o Dynamic World.
    Ambos usan lookback_days para tomar la imagen más reciente antes de cada fecha final.
    """
    _ensure_ee_initialized()

    aoi = gpd.read_file(aoi_path)
    minx, miny, maxx, maxy = aoi.total_bounds
    geom = ee.Geometry.BBox(minx, miny, maxx, maxy)

    if dataset == "SENTINEL":
        col_id = "COPERNICUS/S2_SR_HARMONIZED"
        vis = {"min": 0, "max": 3000, "bands": ["B4", "B3", "B2"], "gamma": 1.1}
        sel = ["B4", "B3", "B2"]

        def get_tile_url(end):
            end_ee = ee.Date(end)
            start_ee = end_ee.advance(-lookback_days, "day")

            collection = (
                ee.ImageCollection(col_id)
                .filterDate(start_ee, end_ee)
                .filterBounds(geom)
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
                .select(sel)
                .sort("system:time_start", False)
                .sort("system:index")
            )

            # Tomar el mosaico más limpio del período
            image = collection.mosaic().clip(geom)
            return image.getMapId(vis)["tile_fetcher"].url_format

    elif dataset == "DW":
        col_id = "GOOGLE/DYNAMICWORLD/V1"
        vis = {
            "min": 0,
            "max": 8,
            "palette": [
                "#419BDF", "#397D49", "#88B053", "#7A87C6",
                "#E49635", "#DFC35A", "#C4281B", "#A59B8F", "#B39FE1"
            ]
        }
        sel = ["label"]

        def get_tile_url(end):
            end_ee = ee.Date(end)
            start_ee = end_ee.advance(-lookback_days, "day")

            collection = (
                ee.ImageCollection(col_id)
                .filterDate(start_ee, end_ee)
                .filterBounds(geom)
                .select(sel)
                .sort("system:time_start", False)
                .sort("system:index")
            )

            image = collection.mosaic().clip(geom)
            return image.getMapId(vis)["tile_fetcher"].url_format

    else:
        raise ValueError("dataset debe ser 'SENTINEL' o 'DW'")

    return {
        "t1": get_tile_url(end_t1),
        "t2": get_tile_url(end_t2)
    }

def create_grid(minx, miny, maxx, maxy, n_tiles=4):
    """
    Divide el AOI en una cuadrícula de n_tiles x n_tiles.
    Retorna lista de bboxes [(minx, miny, maxx, maxy), ...]
    """
    width = (maxx - minx) / n_tiles
    height = (maxy - miny) / n_tiles
    
    tiles = []
    for i in range(n_tiles):
        for j in range(n_tiles):
            tile_minx = minx + i * width
            tile_miny = miny + j * height
            tile_maxx = tile_minx + width
            tile_maxy = tile_miny + height
            tiles.append((tile_minx, tile_miny, tile_maxx, tile_maxy))
    
    return tiles

def export_sentinel_as_png(
    aoi_path: str,
    end_t1: str,
    end_t2: str,
    output_dir: str,
    intersections_dir: str = None,
    lookback_days: int = 365,
    n_tiles: int = 6
):
    """
    Exporta imágenes Sentinel-2 como mosaico de PNGs a 10m/píxel.
    Divide el AOI en tiles para evitar límite de Earth Engine.
    OPTIMIZADO: Solo descarga tiles que contienen áreas de expansión urbana.
    
    Args:
        n_tiles: Número de divisiones por eje (total = n_tiles x n_tiles tiles)
                 Por defecto 6 (36 tiles totales) para AOIs grandes
        intersections_dir: Directorio con geometrías de expansión urbana (para filtrar tiles)
    
    Retorna rutas a carpetas con los tiles organizados por periodo.
    """
    from src.aux_utils import download_gcs_to_temp
    from shapely.geometry import box
    from shapely.ops import unary_union
    
    _ensure_ee_initialized()

    # Descargar AOI de GCS si es necesario
    local_aoi_path = download_gcs_to_temp(aoi_path)
    aoi = gpd.read_file(local_aoi_path)
    minx, miny, maxx, maxy = aoi.total_bounds
    
    # Crear cuadrícula de tiles completa
    all_tiles = create_grid(minx, miny, maxx, maxy, n_tiles=n_tiles)
    
    # Preparar carpetas de salida
    t1_folder = os.path.join(output_dir, f"sentinel_{end_t1}_t1")
    t2_folder = os.path.join(output_dir, f"sentinel_{end_t2}_t2")
    os.makedirs(t1_folder, exist_ok=True)
    os.makedirs(t2_folder, exist_ok=True)
    
    # Filtrar tiles que contienen expansión urbana
    tiles_with_indices = []
    
    if intersections_dir:
        expansion_geoms = []
        # Buscar intersecciones confirmadas (con filtro NDBI)
        normal_path = os.path.join(intersections_dir, "new_urban_confirmed_intersections.geojson")
        strict_path = os.path.join(intersections_dir, "new_urban_strict_confirmed_intersections.geojson")
        
        if os.path.exists(normal_path):
            gdf_normal = gpd.read_file(normal_path).to_crs(epsg=4326)
            expansion_geoms.append(gdf_normal.unary_union)
        
        if os.path.exists(strict_path):
            gdf_strict = gpd.read_file(strict_path).to_crs(epsg=4326)
            expansion_geoms.append(gdf_strict.unary_union)
        
        if expansion_geoms:
            # Combinar todas las geometrías de expansión
            expansion_union = unary_union(expansion_geoms)
            
            # Filtrar tiles que intersectan con la expansión
            for idx, tile_bbox in enumerate(all_tiles):
                tile_geom = box(tile_bbox[0], tile_bbox[1], tile_bbox[2], tile_bbox[3])
                if tile_geom.intersects(expansion_union):
                    tiles_with_indices.append((idx, tile_bbox))
            
            print(f"📐 AOI dividido en {len(all_tiles)} tiles ({n_tiles}x{n_tiles})")
            print(f"✂️ Optimizado: solo {len(tiles_with_indices)} tiles contienen expansión urbana")
        else:
            # Sin expansión: no descargar tiles
            print("⚠️ Sin expansión detectada: omitiendo descarga de tiles")
            return {
                "t1_tiles": [],
                "t2_tiles": [],
                "bounds": [[miny, minx], [maxy, maxx]]
            }
    
    # Eliminar solo archivos PNG antiguos (evita problemas con OneDrive)
    import glob
    for folder in [t1_folder, t2_folder]:
        old_tiles = glob.glob(os.path.join(folder, "*.png"))
        if old_tiles:
            print(f"🗑️  Limpiando {len(old_tiles)} tiles antiguos en {os.path.basename(folder)}")
            for old_tile in old_tiles:
                try:
                    os.remove(old_tile)
                except Exception as e:
                    print(f"⚠️  No se pudo eliminar {os.path.basename(old_tile)}: {e}")
    
    # Si no hay tiles con expansión, retornar sin descargar
    if not tiles_with_indices:
        print("⚠️ Sin áreas de expansión en los tiles: omitiendo descarga de Sentinel-2")
        return {
            "t1_tiles": [],
            "t2_tiles": [],
            "bounds": [[miny, minx], [maxy, maxx]]
        }

    col_id = "COPERNICUS/S2_SR_HARMONIZED"
    vis = {"min": 0, "max": 3000, "bands": ["B4", "B3", "B2"], "gamma": 1.1}
    sel = ["B4", "B3", "B2"]

    def download_tile_png(end_date, tile_bbox, tile_index, output_folder):
        """Descarga un tile individual de Sentinel a 10m/píxel."""
        tile_minx, tile_miny, tile_maxx, tile_maxy = tile_bbox
        geom = ee.Geometry.BBox(tile_minx, tile_miny, tile_maxx, tile_maxy)
        
        end_ee = ee.Date(end_date)
        start_ee = end_ee.advance(-lookback_days, "day")

        collection = (
            ee.ImageCollection(col_id)
            .filterDate(start_ee, end_ee)
            .filterBounds(geom)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
            .select(sel)
            .sort("system:time_start", False)
            .sort("system:index")
        )

        image = collection.mosaic().clip(geom)
        
        # Obtener URL con escala de 10m/píxel
        url = image.getThumbURL({
            'region': geom,
            'scale': 10,
            'format': 'png',
            **vis
        })
        
        # Descargar PNG
        filename = f"tile_{tile_index:02d}.png"
        output_path = os.path.join(output_folder, filename)
        response = requests.get(url, timeout=300)
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(response.content)
        
        return output_path, (tile_miny, tile_minx, tile_maxy, tile_maxx)  # bounds para folium

    # Descargar solo los tiles filtrados para t1
    print(f"📥 Descargando {len(tiles_with_indices)} tiles para periodo T1 ({end_t1})...")
    t1_tiles = []
    for i, (tile_idx, tile_bbox) in enumerate(tiles_with_indices):
        path, bounds = download_tile_png(end_t1, tile_bbox, tile_idx, t1_folder)
        t1_tiles.append({"path": path, "bounds": [[bounds[0], bounds[1]], [bounds[2], bounds[3]]]})
        print(f"  ✅ Tile {i+1}/{len(tiles_with_indices)} descargado")
    
    # Descargar solo los tiles filtrados para t2  
    print(f"📥 Descargando {len(tiles_with_indices)} tiles para periodo T2 ({end_t2})...")
    t2_tiles = []
    for i, (tile_idx, tile_bbox) in enumerate(tiles_with_indices):
        path, bounds = download_tile_png(end_t2, tile_bbox, tile_idx, t2_folder)
        t2_tiles.append({"path": path, "bounds": [[bounds[0], bounds[1]], [bounds[2], bounds[3]]]})
        print(f"  ✅ Tile {i+1}/{len(tiles_with_indices)} descargado")
    
    print(f"✅ Mosaico optimizado: {len(tiles_with_indices)} tiles por periodo a 10m/píxel")
    
    # Retornar información de todos los tiles
    return {
        "t1_tiles": t1_tiles,
        "t2_tiles": t2_tiles,
        "bounds": [[miny, minx], [maxy, maxx]]  # bounds completos del AOI
    }

def plot_expansion_interactive(intersections_dir, sac_path, reserva_path, eep_path, output_path, month_str, previous_month_str, year, aoi_path=None, tiles_before=None, tiles_current=None, png_images=None):
    
    """Generar mapa interactivo de expansión urbana con folium."""

    # Leer y limpiar capas base
    gdf_sac = sanitize_gdf(gpd.read_file(sac_path).to_crs(epsg=4326))
    gdf_res = sanitize_gdf(gpd.read_file(reserva_path).to_crs(epsg=4326))
    gdf_eep = sanitize_gdf(gpd.read_file(eep_path).to_crs(epsg=4326))

    # Filtrar SAC (solo categorías relevantes)
    sac_filtro = [
        "Expansión urbana y asentamientos ilegales",
        "Invasión de áreas protegidas",
        "Ocupación por habitante de calle y cambuches",
        "Zonas con riesgo de remoción en masa, flujos y receptaciones"
    ]
    if "sac" in gdf_sac.columns:
        gdf_sac = gdf_sac[gdf_sac["sac"].isin(sac_filtro)]

    # Crear mapa base
    m = folium.Map(location=[4.65, -74.1], zoom_start=11, tiles="cartodb positron")
    
    #  Definir límites del mapa según las capas disponibles
    gdf_aoi = gpd.read_file(aoi_path).to_crs(epsg=4326)
    minx, miny, maxx, maxy = gdf_aoi.total_bounds
    bounds = [[miny, minx], [maxy, maxx]]
    
    # Capas Sentinel RGB - usar mosaico de tiles PNG si está disponible
    if png_images and "t1_tiles" in png_images:
        # Usar rutas ABSOLUTAS para que Folium pueda encontrar los archivos
        # Luego haremos post-procesamiento del HTML para convertirlas a relativas
        output_dir = os.path.dirname(output_path)
        
        # === PERIODO T1 (mes anterior) - Añadir todos los tiles ===
        t1_group = folium.FeatureGroup(name=f"Sentinel-2 {previous_month_str} {year}", show=True)
        
        for tile in png_images["t1_tiles"]:
            folium.raster_layers.ImageOverlay(
                image=tile["path"],
                bounds=tile["bounds"],
                opacity=1.0,
                interactive=False,
                cross_origin=False,
                zindex=1
            ).add_to(t1_group)
        
        t1_group.add_to(m)
        
        # === PERIODO T2 (mes actual) - Añadir todos los tiles ===
        t2_group = folium.FeatureGroup(name=f"Sentinel-2 {month_str} {year}", show=False)
        
        for tile in png_images["t2_tiles"]:
            folium.raster_layers.ImageOverlay(
                image=tile["path"],
                bounds=tile["bounds"],
                opacity=1.0,
                interactive=False,
                cross_origin=False,
                zindex=1
            ).add_to(t2_group)
        
        t2_group.add_to(m)
        
    elif tiles_before and tiles_current:
        # Usar tiles dinámicos de EE
        folium.TileLayer(
            tiles=tiles_before,
            name=f"Sentinel-2 {previous_month_str} {year}",
            attr="Sentinel-2 EE Mosaic",
            overlay=True,
            show=True
        ).add_to(m)

        folium.TileLayer(
            tiles=tiles_current,
            name=f"Sentinel-2 {month_str} {year}",
            attr="Sentinel-2 EE Mosaic",
            overlay=True,
            show=False
        ).add_to(m)

    # Capas de expansión urbana (confirmadas con filtro NDBI)
    normal_path = os.path.join(intersections_dir, "new_urban_confirmed_intersections.geojson")
    if os.path.exists(normal_path):
        gdf_norm = sanitize_gdf(gpd.read_file(normal_path).to_crs(epsg=4326))
        folium.GeoJson(json.loads(gdf_norm.to_json()), name="Expansión del área construida",
                       style_function=lambda x: {"color": "orange", "weight": 1.5, "fillOpacity": 0.05}).add_to(m)

    strict_path = os.path.join(intersections_dir, "new_urban_strict_confirmed_intersections.geojson")
    if os.path.exists(strict_path):
        gdf_strict = sanitize_gdf(gpd.read_file(strict_path).to_crs(epsg=4326))
        folium.GeoJson(json.loads(gdf_strict.to_json()), name="Expansión estricta del área construida",
                       style_function=lambda x: {"color": "purple", "weight": 1.5, "fillOpacity": 0.05}).add_to(m)
        
    # Capas base (SAC, Reserva, EEP)
    folium.GeoJson(json.loads(gdf_sac.to_json()), name="Conflictos Socioambientales",
                   style_function=lambda x: {"color": "#E31A1C", "weight": 1}, show=False).add_to(m)
    folium.GeoJson(json.loads(gdf_res.to_json()), name="Cerros Orientales",
                   style_function=lambda x: {"color": "#073013", "weight": 1}, show=False).add_to(m)
    folium.GeoJson(json.loads(gdf_eep.to_json()), name="Estructura Ecológica Principal",
                   style_function=lambda x: {"color": "#388900", "weight": 1}, show=False).add_to(m)

    # Control de capas
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(output_path)
    
def generate_maps(aoi_path, bounds_prev, bounds_curr, dirs, month_str, previous_month_str, year, sac, reserva, eep):
    """Genera mosaicos Sentinel y mapa interactivo usando PNG estáticos (optimizado)"""
    # Exportar imágenes Sentinel como PNG (solo tiles con expansión urbana)
    png_images = export_sentinel_as_png(
        aoi_path=aoi_path,
        end_t1=bounds_prev.strftime("%Y-%m-%d"),
        end_t2=bounds_curr.strftime("%Y-%m-%d"),
        output_dir=dirs["maps"],
        intersections_dir=dirs["intersections"],  # Pasar directorio para filtrar tiles
        lookback_days=365
    )

    map_html = os.path.join(dirs["maps"], f"map_expansion.html")
    plot_expansion_interactive(
        intersections_dir=dirs["intersections"],
        sac_path=sac,
        reserva_path=reserva,
        eep_path=eep,
        output_path=map_html,
        aoi_path=aoi_path, 
        month_str=month_str, 
        previous_month_str=previous_month_str,
        year=year,
        png_images=png_images
    )
    return map_html
