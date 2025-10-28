import ee
import geemap
import geopandas as gpd
import pandas as pd
import rasterio
import os
import matplotlib.pyplot as plt
import calendar
from shapely.geometry import Polygon, MultiPolygon
from rasterio.features import shapes
from datetime import date

def authenticate_gee(project='bosques-bogota-416214'):
    try:
        ee.Initialize(project=project)
    except Exception:
        print("🔐 Autenticando por primera vez...")
        ee.Authenticate()
        ee.Initialize(project=project)

def load_geometry(path):
    """
    Carga una o varias geometrías desde un shapefile o GeoJSON y
    devuelve una geometría Earth Engine combinada (unión de todas).
    """
    gdf = gpd.read_file(path)

    # Verificar cuántas geometrías hay
    if len(gdf) == 0:
        raise ValueError("El archivo de geometría está vacío.")
    
    # Unir todas las geometrías en una sola
    geom_union = gdf.unary_union

    # Convertir a geometría de EE
    if isinstance(geom_union, Polygon):
        coords = list(geom_union.exterior.coords)
        geometry = ee.Geometry.Polygon(coords)
    elif isinstance(geom_union, MultiPolygon):
        polygons = [ee.Geometry.Polygon(list(poly.exterior.coords)) for poly in geom_union.geoms]
        geometry = ee.Geometry.MultiPolygon(polygons)
    else:
        raise ValueError("La geometría no es Polygon ni MultiPolygon.")

    return geometry

def get_monthly_periods(month: int, year: int):
    """
    Genera automáticamente las fechas de comparación mensual
    (mes anterior vs mes actual) en formato ISO (YYYY-MM-DD).

    Ejemplo:
    >>> get_monthly_periods(10, 2025)
    (('2025-09-01', '2025-09-30'), ('2025-10-01', '2025-10-31'))
    """
    if month == 1:
        prev_month = 12
        prev_year = year - 1
    else:
        prev_month = month - 1
        prev_year = year

    # Primer y último día de cada mes
    first_day_prev = date(prev_year, prev_month, 1)
    last_day_prev = date(prev_year, prev_month, calendar.monthrange(prev_year, prev_month)[1])
    first_day_curr = date(year, month, 1)
    last_day_curr = date(year, month, calendar.monthrange(year, month)[1])

    periodo_antes = (first_day_prev.isoformat(), last_day_prev.isoformat())
    periodo_despues = (first_day_curr.isoformat(), last_day_curr.isoformat())

    return periodo_antes, periodo_despues

def get_dw_median(year, geometry):
    start = ee.Date(f"{year}-01-01")
    end = start.advance(1, 'year')
    dw = ee.ImageCollection('GOOGLE/DYNAMICWORLD/V1') \
        .filterDate(start, end) \
        .filterBounds(geometry) \
        .select('built')
    return dw.median().clip(geometry)

def get_dw_median_period(start_date, end_date, geometry):
    """
    Calcula la mediana de probabilidad de la clase 'built' (Dynamic World)
    para un periodo de fechas específico. Si el periodo no tiene imágenes,
    devuelve una imagen vacía con valores 0 para evitar errores posteriores.
    """
    start = ee.Date(start_date)
    end = ee.Date(end_date)

    dw = (
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterDate(start, end)
        .filterBounds(geometry)
        .select("built")
    )

    count = dw.size()
    # Si hay imágenes, calcula la mediana; si no, crea una imagen con valores 0
    dw_median = ee.Image(
        ee.Algorithms.If(count.gt(0), dw.median(), ee.Image(0).rename("built"))
    )

    # Mensaje informativo
    print(f"📅 Dynamic World {start_date} → {end_date}: {count.getInfo()} imágenes disponibles")

    return dw_median.clip(geometry)


def export_image(image, geometry, output_path):
    print(f"💾 Descargando imagen a: {output_path}")
    geemap.download_ee_image(
        image=image,
        filename=output_path,
        region=geometry.bounds(),
        scale=10,
        crs='EPSG:4326'
    )
    print("✅ Descarga completada.")

def download_sentinel_rgb(geometry, start_date, end_date, output_path, scale=10):
    """
    Descarga una imagen Sentinel-2 RGB (B4, B3, B2) como mediana del periodo especificado.
    Si no hay imágenes disponibles, no intenta descargar y muestra advertencia.
    """
    import geemap
    import ee

    start = ee.Date(start_date)
    end = ee.Date(end_date)

    print(f"⬇️ Descargando Sentinel-2 RGB entre {start_date} y {end_date}...")

    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(geometry)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
        .select(["B4", "B3", "B2"])
    )

    count = collection.size().getInfo()
    print(f"📦 {count} imágenes disponibles en el rango.")

    if count == 0:
        print("⚠️ No hay imágenes Sentinel-2 disponibles en este rango. Se omite descarga.")
        return None

    image = collection.median().clip(geometry)

    try:
        geemap.download_ee_image(
            image=image,
            filename=output_path,
            region=geometry.bounds(),
            scale=scale,
            crs="EPSG:4326"
        )
        print(f"✅ Imagen Sentinel-2 descargada: {output_path}")
        return output_path
    except Exception as e:
        print(f"⚠️ Error al descargar Sentinel-2: {e}")
        return None


def create_intersections(new_urban_tif, sac_path, reserva_path, eep_path, output_dir):
    """
    Convierte el raster de expansión urbana a polígonos y calcula intersecciones
    con SAC, Reservas y EEP. También guarda los polígonos sin intersección.
    """
    print("🔍 Generando polígonos de expansión urbana e intersecciones...")
    with rasterio.open(new_urban_tif) as src:
        mask = src.read(1) > 0
        results = (
            {"properties": {"value": v}, "geometry": s}
            for s, v in shapes(src.read(1), mask=mask, transform=src.transform)
        )
        gdf_newurban = gpd.GeoDataFrame.from_features(results, crs=src.crs)

    gdf_sac = gpd.read_file(sac_path).to_crs(gdf_newurban.crs)
    gdf_res = gpd.read_file(reserva_path).to_crs(gdf_newurban.crs)
    gdf_eep = gpd.read_file(eep_path).to_crs(gdf_newurban.crs)

    gdf_inter_sac = gpd.overlay(gdf_newurban, gdf_sac, how="intersection")
    gdf_inter_res = gpd.overlay(gdf_newurban, gdf_res, how="intersection")
    gdf_inter_eep = gpd.overlay(gdf_newurban, gdf_eep, how="intersection")

    gdf_inter_sac.to_file(os.path.join(output_dir, "intersec_sac.geojson"))
    gdf_inter_res.to_file(os.path.join(output_dir, "intersec_reserva.geojson"))
    gdf_inter_eep.to_file(os.path.join(output_dir, "intersec_eep.geojson"))

    intersected = gpd.GeoDataFrame(pd.concat([gdf_inter_sac, gdf_inter_res, gdf_inter_eep], ignore_index=True))
    intersected.to_file(os.path.join(output_dir, "new_urban_intersections.geojson"))
    no_inter = gpd.overlay(gdf_newurban, intersected, how="difference")
    no_inter.to_file(os.path.join(output_dir, "new_urban_no_intersections.geojson"))

    print("✅ Intersecciones creadas correctamente.")

def calculate_expansion_areas(
    output_dir,
    input_dir,
    upl_path,
    bogota_buffer_path=None,
    save_summary=True
):
    """
    Calcula áreas de expansión urbana (en m²) por UPL,
    diferenciando intersección y no intersección.
    Si se proporciona bogota_buffer_path, también calcula el área
    de expansión dentro del buffer urbano.
    """

    print("📏 Calculando áreas de expansión urbana (m²)...")
    crs = "EPSG:9377"
    
    # === Leer intersecciones y no intersecciones ===
    gdf_no  = gpd.read_file(os.path.join(input_dir, "new_urban_no_intersections.geojson")).to_crs(crs)
    gdf_no["area_m2"] = gdf_no.geometry.area
    
    gdf_inter = gpd.read_file(os.path.join(input_dir, "new_urban_intersections.geojson")).to_crs(crs)
    gdf_inter["area_m2"] = gdf_inter.geometry.area

    # === Leer UPL ===
    gdf_upl = gpd.read_file(upl_path).to_crs(crs)

    # === Calcular áreas por UPL ===
    inter_upl = gpd.overlay(gdf_upl, gdf_inter, how="intersection")
    nointer_upl = gpd.overlay(gdf_upl, gdf_no, how="intersection")

    inter_upl["area_ha"] = inter_upl.geometry.area/10000
    nointer_upl["area_ha"] = nointer_upl.geometry.area/10000

    resumen_upl = (
        inter_upl.groupby("NOMBRE")["area_ha"].sum().reset_index().rename(columns={"area_ha": "interseccion_ha"})
        .merge(
            nointer_upl.groupby("NOMBRE")["area_ha"].sum().reset_index().rename(columns={"area_ha": "no_interseccion_ha"}),
            on="NOMBRE",
            how="outer"
        )
        .fillna(0)
    )
    resumen_upl["total_ha"] = resumen_upl["interseccion_ha"] + resumen_upl["no_interseccion_ha"]

    # === Calcular área dentro del buffer urbano (si se proporciona) ===
    if bogota_buffer_path and os.path.exists(bogota_buffer_path):
        print("🏙️ Calculando área de expansión dentro del buffer urbano...")
        gdf_buffer = gpd.read_file(bogota_buffer_path).to_crs(crs)

        # 1️⃣ Intersección entre buffer y áreas con intersección (SAC, Reserva, EEP)
        inter_buffer_inter = gpd.overlay(gdf_buffer, gdf_inter, how="intersection")
        inter_buffer_inter["area_ha"] = inter_buffer_inter.geometry.area / 10_000

        # 2️⃣ Intersección entre buffer y áreas SIN intersección
        inter_buffer_no = gpd.overlay(gdf_buffer, gdf_no, how="intersection")
        inter_buffer_no["area_ha"] = inter_buffer_no.geometry.area / 10_000

        # 3️⃣ Total (sumando ambas)
        inter_buffer_total = pd.concat([inter_buffer_inter, inter_buffer_no], ignore_index=True)
        inter_buffer_total["area_ha"] = inter_buffer_total.geometry.area / 10_000

        # --- Resumen ---
        resumen_buffer = pd.DataFrame({
            "zona": ["Bogotá urbana (buffer)"],
            "interseccion_ha": [round(inter_buffer_inter["area_ha"].sum(), 2)],
            "no_interseccion_ha": [round(inter_buffer_no["area_ha"].sum(), 2)],
            "total_ha": [round(inter_buffer_total["area_ha"].sum(), 2)]
        })

        resumen_buffer.to_csv(os.path.join(output_dir, "resumen_buffer_ha.csv"), index=False, encoding="utf-8")
        print(f"✅ Resumen del buffer urbano guardado en: resumen_buffer_ha.csv")

    else:
        print("⚠️ No se proporcionó bogota_buffer_path o el archivo no existe. Saltando cálculo del buffer.")


    # === Guardar resultados principales ===
    if save_summary:
        resumen_upl.to_csv(os.path.join(output_dir, "resumen_expansion_upl_ha.csv"), index=False, encoding="utf-8")
        print("✅ Archivo guardado: resumen_expansion_upl_ha.csv")

    return resumen_upl, resumen_buffer

def build_urban_report_json(resumen_upl, resumen_buffer, mapa_interactivo_path, maps_dir, year1, year2, output_json):
    """
    Construye el JSON para el reporte final de expansión urbana.
    """
    print("📝 Construyendo JSON del reporte urbano...")

    # Top 5 UPLs con mayor expansión
    resumen_upl = resumen_upl.copy()
    resumen_upl["total_ha"] = resumen_upl["total_m2"] / 10000
    resumen_upl["interseccion_ha"] = resumen_upl["interseccion_m2"] / 10000
    resumen_upl["no_interseccion_ha"] = resumen_upl["no_interseccion_m2"] / 10000
    upls_top5 = resumen_upl.sort_values("total_ha", ascending=False).head(5).to_dict(orient="records")

    # Centroides de expansión (clusters)
    inter_path = os.path.join(maps_dir.replace("maps", "intersections"), "new_urban_intersections.geojson")
    if os.path.exists(inter_path):
        gdf_inter = gpd.read_file(inter_path).to_crs("EPSG:4326")
        centroids = [{"id": i+1, "lat": round(pt.y, 5), "lon": round(pt.x, 5)} for i, pt in enumerate(gdf_inter.centroid)]
    else:
        centroids = []

    data = {
        "YEAR1": year1,
        "YEAR2": year2,
        "UPLS_TOP5": upls_top5,
        "MAPA_INTERACTIVO": mapa_interactivo_path,
        "CENTROIDES": centroids
    }

    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ JSON guardado: {output_json}")
    return data

def convert_tif_to_png(tif_path, out_png):
    """Convierte un .tif RGB a .png visible para Folium."""
    import numpy as np
    import rasterio

    with rasterio.open(tif_path) as src:
        img = src.read([1, 2, 3])  # RGB
        img = np.transpose(img, (1, 2, 0))
        img = (img - img.min()) / (img.max() - img.min())  # normalizar
        plt.imsave(out_png, img)
    return out_png

from shapely.ops import unary_union

def create_growth_clusters(gdf_path, buffer_distance=500, crs="EPSG:9377"):
    """
    Crea clusters de crecimiento urbano agrupando polígonos que se tocan
    o están a una distancia menor al buffer indicado.
    """
    print("🧩 Creando clusters de expansión urbana...")

    gdf = gpd.read_file(gdf_path).to_crs(crs)
    gdf["geometry_buffer"] = gdf.geometry.buffer(buffer_distance)
    gdf = gdf.reset_index(drop=True)

    # Lista para asignar IDs
    gdf["cluster_id"] = -1
    cluster_id = 0

    for i in range(len(gdf)):
        if gdf.loc[i, "cluster_id"] != -1:
            continue  # ya asignado

        cluster_id += 1
        current = gdf.loc[[i], "geometry_buffer"].values[0]
        members = [i]
        changed = True

        # Expandir el cluster con cualquier buffer que toque al actual
        while changed:
            overlaps = gdf[gdf.geometry_buffer.intersects(current) & (gdf.cluster_id == -1)]
            if len(overlaps) > 0:
                idxs = overlaps.index.tolist()
                gdf.loc[idxs, "cluster_id"] = cluster_id
                members.extend(idxs)
                current = unary_union(gdf.loc[members, "geometry_buffer"])
            else:
                changed = False

    # Restaurar geometría original y calcular área real
    gdf["area_ha"] = gdf.geometry.area / 10000
    gdf.drop(columns=["geometry_buffer"], inplace=True)

    print(f"✅ {cluster_id} clusters creados correctamente.")
    return gdf

