from src.utils import authenticate_gee, load_geometry, get_dw_median, export_image, download_sentinel_rgb
import os
import ee
from dotenv import load_dotenv


# Cargar variables de entorno
load_dotenv('dot_env_content.env')

# === PARÁMETROS ===
ONEDRIVE_PATH = os.getenv("ONEDRIVE_PATH")
AOI_PATH = os.path.join(ONEDRIVE_PATH, "datos/area_estudio/area_estudio_dissolved.shp")  # Cambia esto si tienes otro AOI
YEAR1=2021
YEAR2=2022
OUTPUT_DIR=os.path.join(ONEDRIVE_PATH, "monitoreo_bosques/datos/urban_sprawl")

def main():
    authenticate_gee()

    geometry = load_geometry(AOI_PATH)

    # Obtener imágenes promedio por año
    before = get_dw_median(YEAR1, geometry)
    after = get_dw_median(YEAR2, geometry)

    # Detectar cambio urbano
    new_urban = before.lt(0.2).And(after.gt(0.5)).rename("new_urban")

    # Exportar
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    export_image(before, geometry, f"{OUTPUT_DIR}/dw_{YEAR1}.tif")
    export_image(after, geometry, f"{OUTPUT_DIR}/dw_{YEAR2}.tif")
    export_image(new_urban, geometry, f"{OUTPUT_DIR}/new_urban_{YEAR1}_{YEAR2}.tif")


    # Descargar Sentinel-2
    
    before_start = ee.Date(f"{YEAR1}-01-01")
    before_end = before_start.advance(1, 'year')

    after_start = ee.Date(f"{YEAR2}-01-01")
    after_end = after_start.advance(1, 'year')

    sentinel_before_path = os.path.join(OUTPUT_DIR, f"sentinel_before_{YEAR1}.tif")
    sentinel_after_path = os.path.join(OUTPUT_DIR, f"sentinel_after_{YEAR2}.tif")

    print("📷 Descargando Sentinel-2 antes...")
    download_sentinel_rgb(geometry, before_start, before_end, sentinel_before_path)

    print("📷 Descargando Sentinel-2 después...")
    download_sentinel_rgb(geometry, after_start, after_end, sentinel_after_path)



if __name__ == "__main__":
    main()
