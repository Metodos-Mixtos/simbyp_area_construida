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
    
    """Generar mapa interactivo de expansión urbana - OPTIMIZADO para PNG."""
    
    # Si hay imágenes PNG, usar HTML personalizado en vez de Folium
    if png_images and "t1_tiles" in png_images and len(png_images["t1_tiles"]) > 0:
        create_custom_leaflet_map(
            intersections_dir=intersections_dir,
            sac_path=sac_path,
            reserva_path=reserva_path,
            eep_path=eep_path,
            output_path=output_path,
            month_str=month_str,
            previous_month_str=previous_month_str,
            year=year,
            aoi_path=aoi_path,
            png_images=png_images,
            construcciones_path=construcciones_path
        )
        return

    # Fallback: usar Folium (para casos sin PNG)
    # Leer y limpiar capas base
    gdf_sac = sanitize_gdf(gpd.read_file(sac_path).to_crs(epsg=4326))
    gdf_res = sanitize_gdf(gpd.read_file(reserva_path).to_crs(epsg=4326))
    gdf_eep = sanitize_gdf(gpd.read_file(eep_path).to_crs(epsg=4326))

    # Filtrar SAC
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
    
    gdf_aoi = gpd.read_file(aoi_path).to_crs(epsg=4326)
    
    if tiles_before and tiles_current:
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

    if construcciones_path and os.path.exists(construcciones_path):
        gdf_construcciones = sanitize_gdf(gpd.read_file(construcciones_path).to_crs(epsg=4326))
        folium.GeoJson(
            json.loads(gdf_construcciones.to_json()), 
            name="Expansión del área construida",
            style_function=lambda x: {
                "color": "#FF6B00",
                "weight": 1.5,
                "fillColor": "#FF6B00",
                "fillOpacity": 0.4
            },
            show=True
        ).add_to(m)
    
    folium.GeoJson(
        json.loads(gdf_sac.to_json()), 
        name="Conflictos Socioambientales",
        style_function=lambda x: {"color": "#E31A1C", "weight": 1, "fillColor": "#E31A1C", "fillOpacity": 0.2}, 
        show=False
    ).add_to(m)
    
    folium.GeoJson(
        json.loads(gdf_res.to_json()), 
        name="Cerros Orientales",
        style_function=lambda x: {"color": "#073013", "weight": 1, "fillColor": "#073013", "fillOpacity": 0.3}, 
        show=False
    ).add_to(m)
    
    folium.GeoJson(
        json.loads(gdf_eep.to_json()), 
        name="Estructura Ecológica Principal",
        style_function=lambda x: {"color": "#388900", "weight": 1, "fillColor": "#388900", "fillOpacity": 0.2}, 
        show=False
    ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.save(output_path)
    print(f"   ✅ Mapa guardado: {output_path}")


def create_custom_leaflet_map(intersections_dir, sac_path, reserva_path, eep_path, output_path, month_str, previous_month_str, year, aoi_path, png_images, construcciones_path):
    """Crear mapa HTML personalizado con Leaflet para manejar PNG eficientemente."""
    
    # Leer capas GeoJSON
    gdf_sac = sanitize_gdf(gpd.read_file(sac_path).to_crs(epsg=4326))
    gdf_res = sanitize_gdf(gpd.read_file(reserva_path).to_crs(epsg=4326))
    gdf_eep = sanitize_gdf(gpd.read_file(eep_path).to_crs(epsg=4326))
    
    # Filtrar SAC
    sac_filtro = [
        "Expansión urbana y asentamientos ilegales",
        "Invasión de áreas protegidas",
        "Ocupación por habitante de calle y cambuches",
        "Zonas con riesgo de remoción en masa, flujos y receptaciones"
    ]
    if "sac" in gdf_sac.columns:
        gdf_sac = gdf_sac[gdf_sac["sac"].isin(sac_filtro)]
    
    # Construcciones nuevas
    construcciones_geojson = "{}"
    if construcciones_path and os.path.exists(construcciones_path):
        gdf_construcciones = sanitize_gdf(gpd.read_file(construcciones_path).to_crs(epsg=4326))
        construcciones_geojson = gdf_construcciones.to_json()
    
    # Generar rutas relativas para tiles PNG
    output_dir = os.path.dirname(output_path)
    t1_tiles_js = []
    t2_tiles_js = []
    
    for tile in png_images["t1_tiles"]:
        rel_path = os.path.relpath(tile["path"], output_dir).replace("\\", "/")
        b = tile["bounds"]
        t1_tiles_js.append(f"L.imageOverlay('{rel_path}', [[{b[0][0]}, {b[0][1]}], [{b[1][0]}, {b[1][1]}]])")
    
    for tile in png_images["t2_tiles"]:
        rel_path = os.path.relpath(tile["path"], output_dir).replace("\\", "/")
        b = tile["bounds"]
        t2_tiles_js.append(f"L.imageOverlay('{rel_path}', [[{b[0][0]}, {b[0][1]}], [{b[1][0]}, {b[1][1]}]])")
    
    # HTML template completo
    html_content = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Mapa de Expansión Urbana - {month_str} {year}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <style>
        body {{ margin: 0; padding: 0; }}
        #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}
    </style>
</head>
<body>
    <div id="map"></div>
    <script>
        var map = L.map('map').setView([4.65, -74.1], 11);
        
        // Mapa base
        var baseLayer = L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
            attribution: '© OpenStreetMap contributors © CARTO',
            maxZoom: 19
        }}).addTo(map);
        
        // Sentinel-2 periodo anterior (T1)
        var sentinel_t1 = L.layerGroup([
            {',\n            '.join(t1_tiles_js)}
        ]).addTo(map);
        
        // Sentinel-2 periodo actual (T2)
        var sentinel_t2 = L.layerGroup([
            {',\n            '.join(t2_tiles_js)}
        ]);
        
        // Expansión urbana (construcciones nuevas)
        var expansion_layer = L.geoJSON({construcciones_geojson}, {{
            style: {{
                color: '#FF6B00',
                weight: 1.5,
                fillColor: '#FF6B00',
                fillOpacity: 0.4
            }}
        }}).addTo(map);
        
        // SAC
        var sac_layer = L.geoJSON({gdf_sac.to_json()}, {{
            style: {{
                color: '#E31A1C',
                weight: 1,
                fillColor: '#E31A1C',
                fillOpacity: 0.2
            }}
        }});
        
        // Cerros Orientales
        var cerros_layer = L.geoJSON({gdf_res.to_json()}, {{
            style: {{
                color: '#073013',
                weight: 1,
                fillColor: '#073013',
                fillOpacity: 0.3
            }}
        }});
        
        // EEP
        var eep_layer = L.geoJSON({gdf_eep.to_json()}, {{
            style: {{
                color: '#388900',
                weight: 1,
                fillColor: '#388900',
                fillOpacity: 0.2
            }}
        }});
        
        // Control de capas
        var baseMaps = {{
            "CartoDB Positron": baseLayer
        }};
        
        var overlayMaps = {{
            "Sentinel-2 {previous_month_str} {year}": sentinel_t1,
            "Sentinel-2 {month_str} {year}": sentinel_t2,
            "Expansión del área construida": expansion_layer,
            "Conflictos Socioambientales": sac_layer,
            "Cerros Orientales": cerros_layer,
            "Estructura Ecológica Principal": eep_layer
        }};
        
        L.control.layers(baseMaps, overlayMaps, {{collapsed: false}}).addTo(map);
    </script>
</body>
</html>'''
    
    # Guardar HTML
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"   ✅ Mapa personalizado guardado: {output_path}")
    print(f"   ✅ {len(png_images['t1_tiles'])} tiles T1 + {len(png_images['t2_tiles'])} tiles T2")


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

