import geopandas as gpd
import folium
import json
import os
import ee
import pandas as pd
import requests
from pathlib import Path
from src.config import GOOGLE_CLOUD_PROJECT, CLOUD_THRESHOLD

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
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", CLOUD_THRESHOLD))
                .select(sel)
            )

            # Tomar la mediana del período (reduce nubes y ruido)
            image = collection.median().clip(geom)
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
            )

            image = collection.median().clip(geom)
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
    
    # Filtrar tiles que contienen expansión urbana
    if intersections_dir:
        expansion_geoms = []
        # Buscar archivo principal de construcciones nuevas (new_urban.geojson)
        import glob
        
        # El archivo new_urban.geojson está en el directorio new_constructions/, no en intersections/
        # intersections_dir = outputs/2025_04/intersections
        # Necesitamos buscar en outputs/2025_04/new_constructions/new_urban.geojson
        output_root = os.path.dirname(intersections_dir)  # outputs/2025_04
        new_constructions_dir = os.path.join(output_root, "new_constructions")
        new_urban_path = os.path.join(new_constructions_dir, "new_urban.geojson")
        
        if os.path.exists(new_urban_path):
            print(f">> Usando {os.path.basename(new_urban_path)} para filtrar tiles...")
            gdf_normal = gpd.read_file(new_urban_path).to_crs(epsg=4326)
            expansion_geoms.append(gdf_normal.unary_union)
        else:
            # Fallback: buscar archivos de intersecciones
            normal_path = glob.glob(os.path.join(intersections_dir, "new_urban_*_intersections.geojson"))
            normal_path = normal_path[0] if normal_path else None
            
            if normal_path and os.path.exists(normal_path):
                gdf_normal = gpd.read_file(normal_path).to_crs(epsg=4326)
                expansion_geoms.append(gdf_normal.unary_union)
        
        if expansion_geoms:
            # Combinar todas las geometrías de expansión
            expansion_union = unary_union(expansion_geoms)
            
            # Filtrar tiles que intersectan con la expansión
            tiles_with_indices = []
            for idx, tile_bbox in enumerate(all_tiles):
                tile_geom = box(tile_bbox[0], tile_bbox[1], tile_bbox[2], tile_bbox[3])
                if tile_geom.intersects(expansion_union):
                    tiles_with_indices.append((idx, tile_bbox))
            
            print(f"📐 AOI dividido en {len(all_tiles)} tiles ({n_tiles}x{n_tiles})")
            print(f"✂️ Optimizado: solo {len(tiles_with_indices)} tiles contienen expansión urbana")
        else:
            print("⚠️ No hay geometrías de expansión, descargando todos los tiles")
            tiles_with_indices = list(enumerate(all_tiles))
    else:
        tiles_with_indices = list(enumerate(all_tiles))
        print(f"📐 AOI dividido en {len(all_tiles)} tiles ({n_tiles}x{n_tiles})")

    col_id = "COPERNICUS/S2_SR_HARMONIZED"
    vis = {"min": 0, "max": 3000, "bands": ["B4", "B3", "B2"], "gamma": 1.1}
    sel = ["B4", "B3", "B2"]

    # Limpiar y crear carpetas para cada periodo (elimina tiles viejos)
    import glob
    t1_folder = os.path.join(output_dir, f"sentinel_{end_t1}_t1")
    t2_folder = os.path.join(output_dir, f"sentinel_{end_t2}_t2")
    
    # Crear carpetas si no existen
    os.makedirs(t1_folder, exist_ok=True)
    os.makedirs(t2_folder, exist_ok=True)
    
    # Eliminar solo archivos PNG antiguos (evita problemas con OneDrive)
    for folder in [t1_folder, t2_folder]:
        old_tiles = glob.glob(os.path.join(folder, "*.png"))
        if old_tiles:
            print(f"🗑️  Limpiando {len(old_tiles)} tiles antiguos en {os.path.basename(folder)}")
            for old_tile in old_tiles:
                try:
                    os.remove(old_tile)
                except Exception as e:
                    print(f"⚠️  No se pudo eliminar {os.path.basename(old_tile)}: {e}")

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
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", CLOUD_THRESHOLD))
            .select(sel)
        )

        image = collection.median().clip(geom)
        
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

def plot_expansion_interactive(intersections_dir, sac_path, reserva_path, eep_path, output_path, month_str, previous_month_str, year, aoi_path=None, tiles_before=None, tiles_current=None, png_images=None, construcciones_path=None):
    
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
            # Usar ruta ABSOLUTA (Folium las embebe como base64 por defecto, pero las necesita para leerlas)
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
        t2_group = folium.FeatureGroup(name=f"Sentinel-2 {month_str} {year}", show=True)
        
        for tile in png_images["t2_tiles"]:
            # Usar ruta ABSOLUTA
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

    # === Capa de expansión urbana (construcciones nuevas detectadas) ===
    if construcciones_path and os.path.exists(construcciones_path):
        gdf_construcciones = sanitize_gdf(gpd.read_file(construcciones_path).to_crs(epsg=4326))
        folium.GeoJson(
            json.loads(gdf_construcciones.to_json()), 
            name="Expansión del área construida",
            style_function=lambda x: {
                "color": "#FF6B00",      # Naranja
                "weight": 1.5,
                "fillColor": "#FF6B00",  # Naranja
                "fillOpacity": 0.4       # Semitransparente
            },
            show=True  # Visible por defecto
        ).add_to(m)
    
    # === Capa de construcciones existentes (catastro) ===
    # Usar GPKG disuelto para visualización rápida y completa
    from src.config import CONSTRUCCIONES_GPKG_DISSOLVE
    from src.aux_utils import download_gcs_to_temp
    
    # Descargar desde GCS si es necesario
    construcciones_path = CONSTRUCCIONES_GPKG_DISSOLVE
    if str(CONSTRUCCIONES_GPKG_DISSOLVE).startswith("gs://"):
        print(f"\n📥 Descargando construcciones desde GCS...")
        try:
            construcciones_path = download_gcs_to_temp(CONSTRUCCIONES_GPKG_DISSOLVE)
            print(f"   Descargado a: {construcciones_path}")
        except Exception as e:
            print(f"   ⚠️ Error descargando desde GCS: {e}")
            construcciones_path = None
    
    if construcciones_path and os.path.exists(construcciones_path):
        print(f"\n📊 Cargando construcciones existentes (disueltas)...")
        print(f"   Archivo: {Path(construcciones_path).name}")
        
        try:
            gdf_const_dissolve = gpd.read_file(construcciones_path)
            
            # Reproyectar si es necesario
            if gdf_const_dissolve.crs != 'EPSG:4326':
                gdf_const_dissolve = gdf_const_dissolve.to_crs('EPSG:4326')
            
            # Filtrar al área del mapa (bbox del AOI)
            if hasattr(gdf_aoi, 'total_bounds'):
                bounds = gdf_aoi.total_bounds
                gdf_const_dissolve = gdf_const_dissolve.cx[bounds[0]:bounds[2], bounds[1]:bounds[3]]
            
            gdf_const_dissolve = sanitize_gdf(gdf_const_dissolve)
            print(f"   Geometrías: {len(gdf_const_dissolve)}")
            
            folium.GeoJson(
                json.loads(gdf_const_dissolve.to_json()),
                name="Construcciones existentes (Catastro)",
                style_function=lambda x: {
                    "color": "#999999",      # Gris medio borde
                    "weight": 1,
                    "fillColor": "#DDDDDD",  # Gris claro relleno
                    "fillOpacity": 0.3       # Semitransparente
                },
                show=False  # Oculto por defecto
            ).add_to(m)
            print(f"   ✅ Capa de construcciones añadida al mapa")
            
        except Exception as e:
            print(f"   ⚠️ Error cargando GPKG disuelto: {e}")
    else:
        print(f"\n⚠️ No se encontró GPKG disuelto: {CONSTRUCCIONES_GPKG_DISSOLVE}")
        
        # Fallback: buscar archivo generado por el pipeline
        output_root = os.path.dirname(intersections_dir) if intersections_dir else None
        if output_root:
            construcciones_path = os.path.join(output_root, "new_constructions", "construcciones_existentes.geojson")
            if os.path.exists(construcciones_path):
                print(f"   Usando archivo del pipeline como fallback...")
                gdf_const = sanitize_gdf(gpd.read_file(construcciones_path).to_crs(epsg=4326))
                n_total = len(gdf_const)
                
                # Muestreo si hay demasiados
                if n_total > 50000:
                    gdf_const = gdf_const.sample(n=50000, random_state=42)
                    print(f"   Muestra: 50,000 de {n_total:,}")
                
                folium.GeoJson(
                    json.loads(gdf_const.to_json()),
                    name="Construcciones existentes (Catastro)",
                    style_function=lambda x: {
                        "color": "#CCCCCC",
                        "weight": 0.5,
                        "fillColor": "#DDDDDD",
                        "fillOpacity": 0.2
                    },
                    show=False
                ).add_to(m)
        
    # === Capas ambientales y de protección ===
    folium.GeoJson(
        json.loads(gdf_sac.to_json()), 
        name="Conflictos Socioambientales",
        style_function=lambda x: {
            "color": "#E31A1C",    # Rojo
            "weight": 1,
            "fillColor": "#E31A1C",
            "fillOpacity": 0.2
        }, 
        show=False
    ).add_to(m)
    
    folium.GeoJson(
        json.loads(gdf_res.to_json()), 
        name="Cerros Orientales",
        style_function=lambda x: {
            "color": "#073013",    # Verde oscuro
            "weight": 1,
            "fillColor": "#073013",
            "fillOpacity": 0.3
        }, 
        show=False
    ).add_to(m)
    
    folium.GeoJson(
        json.loads(gdf_eep.to_json()), 
        name="Estructura Ecológica Principal",
        style_function=lambda x: {
            "color": "#388900",    # Verde
            "weight": 1,
            "fillColor": "#388900",
            "fillOpacity": 0.2
        }, 
        show=False
    ).add_to(m)

    # Control de capas
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(output_path)
    
    # Post-procesamiento: convertir rutas absolutas embebidas a rutas relativas
    if png_images and "t1_tiles" in png_images:
        output_dir = os.path.dirname(output_path)
        
        # Leer el HTML generado
        with open(output_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # Reemplazar rutas absolutas por relativas
        for tile in png_images["t1_tiles"] + png_images["t2_tiles"]:
            abs_path = tile["path"]
            rel_path = os.path.relpath(abs_path, output_dir).replace("\\", "/")
            
            # Folium puede embedder como base64 o como ruta, buscar ambos patrones
            # Patrón 1: url("file:///C:/ruta/absoluta/tile.png")
            html_content = html_content.replace(f'file:///{abs_path.replace(chr(92), "/")}', rel_path)
            html_content = html_content.replace(abs_path.replace("\\", "/"), rel_path)
            html_content = html_content.replace(abs_path, rel_path)
        
        # Guardar el HTML modificado
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
    
def generate_maps(aoi_path, bounds_prev, bounds_curr, dirs, month_str, previous_month_str, year, mes, sac, reserva, eep, construcciones_path=None):
    """Genera mosaicos Sentinel y mapa interactivo usando PNG estáticos (optimizado)"""
    # Exportar imágenes Sentinel como PNG (solo tiles con expansión urbana)
    png_images = export_sentinel_as_png(
        aoi_path=aoi_path,
        end_t1=bounds_prev.strftime("%Y-%m-%d"),
        end_t2=bounds_curr.strftime("%Y-%m-%d"),
        output_dir=dirs["maps"],
        intersections_dir=dirs["intersections"],
        lookback_days=365
    )

    map_html = os.path.join(dirs["maps"], f"map_expansion_{year}_{mes:02d}.html")
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
        png_images=png_images,
        construcciones_path=construcciones_path
    )
    return map_html
