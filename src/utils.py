import ee
import geemap
import geopandas as gpd
from shapely.geometry import Polygon, MultiPolygon

def authenticate_gee(project='bosques-bogota-416214'):
    try:
        ee.Initialize(project="bosques-bogota-416214")
    except Exception:
        print("🔐 Autenticando por primera vez...")
        ee.Authenticate()
        ee.Initialize(project="bosques-bogota-416214")


def load_geometry(path):
    gdf = gpd.read_file(path)

    geom = gdf.geometry.iloc[0]

    if isinstance(geom, Polygon):
        return ee.Geometry.Polygon(list(geom.exterior.coords))
    elif isinstance(geom, MultiPolygon):
        # tomar el primer polígono del multipolígono
        poly = list(geom.geoms)[0]
        return ee.Geometry.Polygon(list(poly.exterior.coords))
    else:
        raise ValueError("La geometría no es Polygon ni MultiPolygon")



def get_dw_median(year, geometry):
    start = ee.Date(f"{year}-01-01")
    end = start.advance(1, 'year')
    dw = ee.ImageCollection('GOOGLE/DYNAMICWORLD/V1') \
        .filterDate(start, end) \
        .filterBounds(geometry) \
        .select('built')
    return dw.median().clip(geometry)

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
    """
    collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                  .filterBounds(geometry)
                  .filterDate(start_date, end_date)
                  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 30))
                  .select(['B4', 'B3', 'B2']))

    image = collection.median().clip(geometry)

    geemap.download_ee_image(
        image=image,
        filename=output_path,
        region=geometry,
        scale=scale,
        crs="EPSG:4326"
    )


def get_year_dates(year):
    """
    Retorna las fechas de inicio y fin del año como strings.
    Ejemplo: get_year_dates(2023) → ('2023-01-01', '2024-01-01')
    """
    start = f"{year}-01-01"
    end_year = year + 1
    end = f"{end_year}-01-01"
    return start, end
