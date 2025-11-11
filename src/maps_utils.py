import geopandas as gpd
import folium
import json
import os
import pandas as pd


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


def plot_expansion_interactive(intersections_dir, sac_path, reserva_path, eep_path, output_path, aoi_path=None, tiles_before=None, tiles_current=None):
    
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
    
    # Capas Sentinel RGB (ajustadas con límites dinámicos)
    if tiles_before and os.path.exists(tiles_before):
        folium.raster_layers.ImageOverlay(
            name="Sentinel Antes",
            bounds=bounds,
            image=tiles_before,
            opacity=0.85
        ).add_to(m)

    if tiles_current and os.path.exists(tiles_current):
        folium.raster_layers.ImageOverlay(
            name="Sentinel Después",
            bounds=bounds,
            image=tiles_current,
            opacity=0.85
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
    folium.GeoJson(json.loads(gdf_sac.to_json()), name="SAC",
                   style_function=lambda x: {"color": "#E31A1C", "weight": 1}, show=False).add_to(m)
    folium.GeoJson(json.loads(gdf_res.to_json()), name="Reserva",
                   style_function=lambda x: {"color": "#1F78B4", "weight": 1}, show=False).add_to(m)
    folium.GeoJson(json.loads(gdf_eep.to_json()), name="EEP",
                   style_function=lambda x: {"color": "#33A02C", "weight": 1}, show=False).add_to(m)


    # Control de capas y guardado
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(output_path)