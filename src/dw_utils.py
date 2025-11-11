import ee
import geopandas as gpd
import geemap

def get_dw_mosaic_1year(end_date, geometry):
    """Mosaico de Dynamic World (built) de los últimos 365 días hasta end_date."""
    end = ee.Date(end_date.strftime("%Y-%m-%d"))
    start = end.advance(-365, "day")
    collection = (
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterDate(start, end)
        .filterBounds(geometry)
        .select("built")
        .sort("system:time_start", False)
        .sort("system:index")
    )
    return collection.mosaic().clip(geometry)

def download_sentinel_rgb_period(aoi_path, start_date, end_date, output_path):
    
    """Descargar raster Sentinel-2 RGB para un período dado."""
    
    gdf = gpd.read_file(aoi_path)
    minx, miny, maxx, maxy = gdf.total_bounds
    bbox = ee.Geometry.BBox(minx, miny, maxx, maxy)

    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(bbox)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
        .select(["B4", "B3", "B2"])
    )

    image = collection.median().clip(bbox)
    geemap.download_ee_image(image, filename=output_path, region=bbox, scale=10, crs="EPSG:4326")
    print(f"✅ Sentinel-2 guardada: {output_path}")
    return output_path

def download_sentinel_rgb_visualized(aoi_path, start_date, end_date, output_png):
    
    """Descargar imagen Sentinel-2 RGB visualizada (formato png) para un período dado."""

    gdf = gpd.read_file(aoi_path)
    minx, miny, maxx, maxy = gdf.total_bounds
    bbox = ee.Geometry.BBox(minx, miny, maxx, maxy)

    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(bbox)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))        
        .sort("system:time_start", False)
        .sort("system:index")
        .select(["B4", "B3", "B2"])
    )

    # Mosaico
    image = collection.mosaic().clip(bbox)

    # Renderizar en RGB visual (0–3000 típico Sentinel)
    rgb_vis = image.visualize(
        bands=["B4", "B3", "B2"],
        min=0,
        max=3000,
        gamma=1.2
    )

    # Descargar directamente la imagen ya visualizada
    geemap.download_ee_image(
        image=rgb_vis,
        filename=output_png,
        region=bbox,
        scale=20,
        crs="EPSG:4326"
    )
    