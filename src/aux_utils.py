import ee
import geopandas as gpd
import os
import geemap
import calendar
from pathlib import Path
from shapely.geometry import Polygon, MultiPolygon
from datetime import datetime

def authenticate_gee(project: str):
    """Autenticar en Google Earth Engine."""
    try:
        ee.Initialize(project=project)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=project)

def load_geometry(path):
    """Cargar geometría desde un archivo vectorial y convertir a ee.Geometry."""
    gdf = gpd.read_file(path)
    if len(gdf) == 0:
        raise ValueError("El archivo de geometría está vacío.")
    geom_union = gdf.unary_union
    if isinstance(geom_union, Polygon):
        coords = list(geom_union.exterior.coords)
        geometry = ee.Geometry.Polygon(coords)
    elif isinstance(geom_union, MultiPolygon):
        polygons = [ee.Geometry.Polygon(list(poly.exterior.coords)) for poly in geom_union.geoms]
        geometry = ee.Geometry.MultiPolygon(polygons)
    else:
        raise ValueError("La geometría no es Polygon ni MultiPolygon.")
    return geometry

def export_image(image, geometry, output_path):
    """Exportar imagen de EE a archivo local."""
    print(f"💾 Exportando a {output_path}")
    geemap.download_ee_image(
        image=image,
        filename=output_path,
        region=geometry.bounds(),
        scale=10,
        crs="EPSG:4326"
    )
    
def make_relative_path(path, base_dir):
    """Convertir una ruta absoluta a relativa respecto a base_dir."""
    path = Path(path)
    base_dir = Path(base_dir)
    try:
        return str(path.relative_to(base_dir))
    except ValueError:
        return str(os.path.relpath(path, base_dir))

def set_dates(mes, anio):
    
    """Establecer las fechas finales del mes actual y el mes previo."""

    last_day_curr = datetime(anio, mes, calendar.monthrange(anio, mes)[1])
    if mes == 1:
        prev_month = 12
        prev_year = anio - 1
    else:
        prev_month = mes - 1
        prev_year = anio
        
    last_day_prev = datetime(prev_year, prev_month, calendar.monthrange(prev_year, prev_month)[1])
    
    return last_day_curr, last_day_prev