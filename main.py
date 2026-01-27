#!/usr/bin/env python3
import argparse
from datetime import datetime
import locale
import sys
import os
from src.config import AOI_PATH, SAC_PATH, RESERVA_PATH, EEP_PATH, UPL_PATH, HEADER_IMG1_PATH, HEADER_IMG2_PATH, FOOTER_IMG_PATH, GOOGLE_CLOUD_PROJECT, BASE_PATH
from src.aux_utils import authenticate_gee, load_geometry, set_dates
from src.stats_utils import calculate_expansion_areas, create_intersections
from src.pipeline_utils import prepare_folders, process_dynamic_world,build_report 
from src.maps_utils import generate_maps  
import warnings

import dotenv
dotenv.load_dotenv()

# Suppress urllib3 SSL warning
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL 1.1.1+")

# Suppress pkg_resources warning
warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")

#Authenticate with Google Cloud
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# === Configurar idioma español para nombres de meses ===
try:
    locale.setlocale(locale.LC_TIME, "es_ES.UTF-8")
except:
    locale.setlocale(locale.LC_TIME, "es_CO.UTF-8")


def main(anio: int, mes: int):
    # Check required environment variables
    if not GOOGLE_CLOUD_PROJECT:
        raise ValueError("GOOGLE_CLOUD_PROJECT environment variable is not set. Please add it to your .env file.")
    
    month_str = datetime(anio, mes, 1).strftime("%B").capitalize()
    previous_month_str = datetime(anio, mes-1, 1).strftime("%B").capitalize()
    print(f"🗓️ Ejecutando análisis para {month_str} {anio}")

    # === Fechas ===
    last_day_curr, last_day_prev = set_dates(mes, anio)

    # === Preparar carpetas de salida ===
    dirs = prepare_folders(BASE_PATH, anio, mes)
    fecha_rango = f"{anio}_{mes:02d}"
    OUTPUT_FOLDER = os.path.join(BASE_PATH, "urban_sprawl", "outputs", fecha_rango)

    # === Autenticación y carga del AOI ===
    authenticate_gee(project=GOOGLE_CLOUD_PROJECT)
    print(f"Debug: AOI_PATH = {AOI_PATH}")  # Added logging to check the path
    geometry = load_geometry(AOI_PATH)

    # === 1. Dynamic World ===
    dw_paths = process_dynamic_world(geometry, dirs["dw"], last_day_prev, last_day_curr)

    # === 2. Intersecciones ===
     # Intersecciones
    create_intersections(dw_paths["new_urban"], SAC_PATH, RESERVA_PATH, EEP_PATH, dirs["intersections"])
     
     # Intersecciones estrictas
    create_intersections(dw_paths["new_urban_strict"], SAC_PATH, RESERVA_PATH, EEP_PATH, dirs["intersections"])
    
    # === 3. Estadísticas ===
    calculate_expansion_areas(dirs["intersections"], dirs["stats"], UPL_PATH)
    
    calculate_expansion_areas(dirs["intersections"], dirs["stats"], UPL_PATH, prefix="strict_", file_suffix="new_urban_strict")

    # === 4. Mapas Sentinel ===
    try:
        map_html = generate_maps(AOI_PATH, last_day_prev, last_day_curr, dirs, month_str, previous_month_str, SAC_PATH, RESERVA_PATH, EEP_PATH)
        print(f"✅ Mapa generado: {map_html}")
        if map_html and os.path.exists(map_html):
            print("Map file exists locally")
        else:
            print("Map file not found locally")
    except Exception as e:
        print(f"❌ Error generando mapa: {e}")
        map_html = None

    # === Descargar imágenes de encabezado y pie de página desde GCS ===
    from google.cloud import storage

    def download_gcs_to_local(gcs_path, local_path):
        _, rest = gcs_path.split("gs://", 1)
        bucket_name, blob_path = rest.split("/", 1)
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.download_to_filename(local_path)

    local_header1 = os.path.join(dirs["reportes"], "asi_4.png")
    download_gcs_to_local(HEADER_IMG1_PATH, local_header1)
    local_header2 = os.path.join(dirs["reportes"], "bogota_4.png")
    download_gcs_to_local(HEADER_IMG2_PATH, local_header2)
    local_footer = os.path.join(dirs["reportes"], "secre_5.png")
    download_gcs_to_local(FOOTER_IMG_PATH, local_footer)

    # === 5. Reporte ===
    build_report(
        df_path=f"{dirs['stats']}/resumen_expansion_upl_ha.csv",
        strict_path=f"{dirs['stats']}/resumen_expansion_upl_ha_strict.csv",
        map_html=map_html,
        header_img1_path=local_header1,
        header_img2_path=local_header2,
        footer_img_path=local_footer,
        output_dir=dirs["reportes"],
        month=month_str,
        year=anio,
        mes_num=int(args.mes)
    )

    # === Subir carpeta completa a GCS ===
    def upload_folder_to_gcs(local_folder, gcs_bucket, gcs_prefix):
        client = storage.Client()
        bucket = client.bucket(gcs_bucket)
        for root, dirs_files, files in os.walk(local_folder):
            for file in files:
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, local_folder)
                gcs_path = os.path.join(gcs_prefix, relative_path).replace("\\", "/")
                blob = bucket.blob(gcs_path)
                blob.upload_from_filename(local_path)
                print(f"✅ Subido {local_path} a gs://{gcs_bucket}/{gcs_path}")

    print("☁️ Subiendo outputs a GCS...")
    fecha_rango = f"{anio}_{mes:02d}"
    upload_folder_to_gcs(OUTPUT_FOLDER, "reportes-simbyp", f"urban_sprawl/{fecha_rango}")

    print("✅ Proceso completo. Archivos guardados en:")
    print(f"   - GCS: gs://reportes-simbyp/urban_sprawl/{fecha_rango}/")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de expansión urbana mensual (mosaico 1 año atrás)")
    parser.add_argument("--anio", type=int, required=True, help="Año en formato YYYY")
    parser.add_argument("--mes", type=int, required=True, help="Mes en formato numérico (1–12)")
    args = parser.parse_args()

    main(args.anio, args.mes)
