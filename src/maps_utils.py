import geopandas as gpd
import folium
import json
import os
import ee
import pandas as pd
import requests
from pathlib import Path
from src.config import GOOGLE_CLOUD_PROJECT

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
    ee.Initialize(project=GOOGLE_CLOUD_PROJECT)

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

def export_sentinel_as_png(
    aoi_path: str,
    end_t1: str,
    end_t2: str,
    output_dir: str,
    lookback_days: int = 365,
    max_pixels: int = 1e8
):
    """
    Exporta imágenes Sentinel como PNG estáticos para evitar que expiren.
    Retorna las rutas locales a los archivos PNG.
    """
    ee.Initialize(project=GOOGLE_CLOUD_PROJECT)

    aoi = gpd.read_file(aoi_path)
    minx, miny, maxx, maxy = aoi.total_bounds
    geom = ee.Geometry.BBox(minx, miny, maxx, maxy)
    
    # Calcular dimensiones aproximadas basadas en el AOI
    # Asumiendo ~10m por pixel de Sentinel
    width_km = (maxx - minx) * 111  # aprox km
    height_km = (maxy - miny) * 111
    dimensions = min(int(width_km * 100), int(height_km * 100), 2048)  # max 2048px

    col_id = "COPERNICUS/S2_SR_HARMONIZED"
    vis = {"min": 0, "max": 3000, "bands": ["B4", "B3", "B2"], "gamma": 1.1}
    sel = ["B4", "B3", "B2"]

    def export_image(end, filename):
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

        image = collection.mosaic().clip(geom)
        
        # Obtener URL del thumbnail
        url = image.getThumbURL({
            'region': geom,
            'dimensions': dimensions,
            'format': 'png',
            **vis
        })
        
        # Descargar PNG
        output_path = os.path.join(output_dir, filename)
        response = requests.get(url, timeout=300)
        response.raise_for_status()
        
        with open(output_path, 'wb') as f:
            f.write(response.content)
        
        print(f"✅ Imagen exportada: {output_path}")
        return output_path

    png_t1 = export_image(end_t1, f"sentinel_{end_t1}_t1.png")
    png_t2 = export_image(end_t2, f"sentinel_{end_t2}_t2.png")
    
    # Retornar bounds para usar en folium
    return {
        "t1_path": png_t1,
        "t2_path": png_t2,
        "bounds": [[miny, minx], [maxy, maxx]]
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
    
    # Capas Sentinel RGB - usar PNG local si está disponible, sino tiles dinámicos
    if png_images:
        # Usar imágenes PNG locales
        folium.raster_layers.ImageOverlay(
            image=png_images["t1_path"],
            bounds=png_images["bounds"],
            name=f"Sentinel-2 {previous_month_str} {year}",
            opacity=1.0,
            overlay=True,
            show=True
        ).add_to(m)

        folium.raster_layers.ImageOverlay(
            image=png_images["t2_path"],
            bounds=png_images["bounds"],
            name=f"Sentinel-2 {month_str} {year}",
            opacity=1.0,
            overlay=True,
            show=False
        ).add_to(m)
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

    # Capas de expansión urbana
    normal_path = os.path.join(intersections_dir, "new_urban_intersections.geojson")
    if os.path.exists(normal_path):
        gdf_norm = sanitize_gdf(gpd.read_file(normal_path).to_crs(epsg=4326))
        folium.GeoJson(json.loads(gdf_norm.to_json()), name="Expansión del área construida",
                       style_function=lambda x: {"color": "orange", "weight": 1.5, "fillOpacity": 0.5}).add_to(m)

    strict_path = os.path.join(intersections_dir, "new_urban_strict_intersections.geojson")
    if os.path.exists(strict_path):
        gdf_strict = sanitize_gdf(gpd.read_file(strict_path).to_crs(epsg=4326))
        folium.GeoJson(json.loads(gdf_strict.to_json()), name="Expansión estricta del área construida",
                       style_function=lambda x: {"color": "purple", "weight": 1.5, "fillOpacity": 0.5}).add_to(m)
        
    # Capas base (SAC, Reserva, EEP)
    folium.GeoJson(json.loads(gdf_sac.to_json()), name="Conflictos Socioambientales",
                   style_function=lambda x: {"color": "#E31A1C", "weight": 1}, show=False).add_to(m)
    folium.GeoJson(json.loads(gdf_res.to_json()), name="Cerros Orientales",
                   style_function=lambda x: {"color": "#073013", "weight": 1}, show=False).add_to(m)
    folium.GeoJson(json.loads(gdf_eep.to_json()), name="Estructura Ecológica Principal",
                   style_function=lambda x: {"color": "#388900", "weight": 1}, show=False).add_to(m)


    # Control de capas y guardado
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(output_path)
    
def generate_maps(aoi_path, bounds_prev, bounds_curr, dirs, month_str, previous_month_str, year, sac, reserva, eep):
    """Genera mosaicos Sentinel y mapa interactivo usando PNG estáticos"""
    # Exportar imágenes Sentinel como PNG
    png_images = export_sentinel_as_png(
        aoi_path=aoi_path,
        end_t1=bounds_prev.strftime("%Y-%m-%d"),
        end_t2=bounds_curr.strftime("%Y-%m-%d"),
        output_dir=dirs["maps"],
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
