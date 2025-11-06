import os
import pandas as pd
import geopandas as gpd
import folium
import json
import ee

from shapely.geometry import box


def top_clusters(gdf_path, top_n=10):
    """
    Retorna los N clusters más grandes por área.
    """
    gdf = gpd.read_file(gdf_path).to_crs("EPSG:9377")
    resumen = gdf.dissolve(by="cluster_id", as_index=False)
    resumen["area_ha"] = resumen.geometry.area / 10000
    resumen = resumen.sort_values("area_ha", ascending=False).head(top_n)
    return resumen


def get_cluster_bboxes(gdf):
    """
    Retorna los bounding boxes de cada cluster.
    """
    bboxes = []
    for _, row in gdf.iterrows():
        minx, miny, maxx, maxy = row.geometry.bounds
        bboxes.append({
            "cluster_id": row.cluster_id,
            "geometry": box(minx, miny, maxx, maxy),
            "area_ha": row.area_ha
        })
    return gpd.GeoDataFrame(bboxes, crs=gdf.crs)

def plot_expansion_interactive(
    intersections_dir: str,
    sac_path: str,
    reserva_path: str,
    eep_path: str,
    aoi_path: str,
    output_path: str,
    annio: int,
    mes: int,
    tiles_before=None,
    tiles_after=None
):
    """
    Mapa interactivo con:
    - CartoDB Positron como basemap
    - Sentinel T1 (mes anterior) y T2 (mes de referencia) como overlays
    - Capas vectoriales y de intersección como overlays
    """

    MONTHS_ES = {
        1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
        5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
        9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
    }

    mes_anterior = mes - 1 if mes > 1 else 12
    annio_anterior = annio if mes > 1 else annio - 1

    nombre_mes_anterior = MONTHS_ES[mes_anterior].capitalize()
    nombre_mes_actual = MONTHS_ES[mes].capitalize()

    def sanitize_gdf(gdf):
        for c in gdf.columns:
            if pd.api.types.is_datetime64_any_dtype(gdf[c]):
                gdf[c] = gdf[c].astype(str)
        return gdf

    # === AOI y centro ===
    aoi = gpd.read_file(aoi_path).to_crs(epsg=4326)
    centroid = aoi[aoi["NOMBRE"]=="Salitre"].centroid
    lat, lon = centroid.y, centroid.x

    # === Crear mapa con CartoDB Positron como base ===
    m = folium.Map(location=[lat, lon], zoom_start=11, tiles="CartoDB positron")

    # === Sentinel como overlays ===
    if tiles_before:
        folium.TileLayer(
            tiles=tiles_before,
            name=f"Sentinel {nombre_mes_anterior} {annio_anterior} (T1)",
            attr="Sentinel-2 EE Median Before",
            overlay=True,
            show=False
        ).add_to(m)

    if tiles_after:
        folium.TileLayer(
            tiles=tiles_after,
            name=f"Sentinel {nombre_mes_actual} {annio} (T2)",
            attr="Sentinel-2 EE Median After",
            overlay=True,
            show=False
        ).add_to(m)

    # === AOI ===
    folium.GeoJson(
        json.loads(aoi.to_json()),
        name="Área de estudio",
        style_function=lambda x: {"color": "black", "weight": 1.2, "fillOpacity": 0},
        show=True
    ).add_to(m)

    # === Capas de expansión ===
    inter_path = os.path.join(intersections_dir, "new_urban_intersections.geojson")
    no_path = os.path.join(intersections_dir, "new_urban_no_intersections.geojson")

    if os.path.exists(inter_path):
        gdf_inter = sanitize_gdf(gpd.read_file(inter_path).to_crs(epsg=4326))
        folium.GeoJson(
            json.loads(gdf_inter.to_json()),
            name="Nueva área construida con restricciones",
            style_function=lambda x: {"color": "red", "weight": 1.2, "fillOpacity": 0.5},
            show=True
        ).add_to(m)

    if os.path.exists(no_path):
        gdf_no = sanitize_gdf(gpd.read_file(no_path).to_crs(epsg=4326))
        folium.GeoJson(
            json.loads(gdf_no.to_json()),
            name="Nueva área construida sin restricciones",
            style_function=lambda x: {"color": "green", "weight": 1.2, "fillOpacity": 0.4},
            show=True
        ).add_to(m)

    # === Capas adicionales ===
    for path, name, color in [
        (sac_path, "Situaciones Ambientalmente Conflictivas (2019)", "orange"),
        (reserva_path, "Reserva Cerros Orientales", "purple"),
        (eep_path, "Estructura Ecológica Principal", "blue")
    ]:
        if os.path.exists(path):
            gdf = sanitize_gdf(gpd.read_file(path).to_crs(epsg=4326))
            if not gdf.empty:
                folium.GeoJson(
                    json.loads(gdf.to_json()),
                    name=name,
                    style_function=lambda x, c=color: {"color": c, "weight": 1, "fillOpacity": 0.25},
                    show=False
                ).add_to(m)

    # === Control de capas ===
    folium.LayerControl(collapsed=False).add_to(m)

    # === Guardar ===
    m.save(output_path)
    print(f"✅ Mapa guardado en: {output_path}")

def get_sentinel_tiles_from_ee(aoi_path: str, start_before: str, end_before: str,
                               start_after: str, end_after: str, cloudy=30):
    """
    Obtiene los URLs de tiles de Sentinel-2 (Before / After) desde Google Earth Engine.
    Si no hay imágenes disponibles, devuelve None en esa capa.
    """

    ee.Initialize(project="bosques-bogota-416214")
    aoi = gpd.read_file(aoi_path)
    geom = ee.Geometry.Polygon(aoi.geometry.unary_union.exterior.coords[:])
    vis_params = {"min": 0, "max": 3000, "gamma": 1.1}

    def get_tile_url(start, end, label):
        col = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(geom)
            .filterDate(start, end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloudy))
            .select(["B4", "B3", "B2"])
        )
        count = col.size().getInfo()
        print(f"🛰️ {label}: {count} imágenes disponibles ({start} → {end})")

        if count == 0:
            print(f"⚠️ {label}: sin imágenes válidas, se omite esta capa.")
            return None

        img = col.median().clip(geom)
        tile = img.getMapId(vis_params)
        return tile["tile_fetcher"].url_format

    return {
        "before": get_tile_url(start_before, end_before, "Antes"),
        "after": get_tile_url(start_after, end_after, "Después"),
    }