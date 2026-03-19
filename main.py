#!/usr/bin/env python3
import argparse
from datetime import datetime
import locale
import sys
import os
from google.cloud import storage
import warnings
import dotenv

# Load environment variables FIRST, before any other imports that depend on them
dotenv.load_dotenv()

# Set GOOGLE_APPLICATION_CREDENTIALS if specified in .env
credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if credentials_path:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
    # Verify the credentials file exists
    if os.path.exists(credentials_path):
        print(f"✓ Archivo de credenciales encontrado: {credentials_path}")
    else:
        print(f"✗ ADVERTENCIA: Archivo de credenciales no encontrado: {credentials_path}")
        print(f"  Por favor verifica la ruta en tu archivo .env")
        sys.exit(1)

from src.config import AOI_PATH, SAC_PATH, RESERVA_PATH, EEP_PATH, UPL_PATH, HEADER_IMG1_PATH, HEADER_IMG2_PATH, FOOTER_IMG_PATH, GOOGLE_CLOUD_PROJECT, BASE_PATH, GCS_OUTPUT_BUCKET, GCS_OUTPUT_PREFIX
from src.aux_utils import authenticate_gee, load_geometry, set_dates, cleanup_temp_data
from src.stats_utils import calculate_expansion_areas, create_intersections
from src.pipeline_utils import prepare_folders, process_dynamic_world, build_report 
from src.maps_utils import generate_maps

# Suppress warnings
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL 1.1.1+")
warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API")

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
    # Handle January wraparound to previous year's December
    if mes == 1:
        previous_month_str = datetime(anio - 1, 12, 1).strftime("%B").capitalize()
    else:
        previous_month_str = datetime(anio, mes - 1, 1).strftime("%B").capitalize()
    print(f"🗓️ Ejecutando análisis para {month_str} {anio}")

    # === 1. Limpiar temp_data al inicio ===
    print("\n🧹 Limpiando carpeta temporal antes de iniciar...")
    cleanup_temp_data()

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
        map_html = generate_maps(AOI_PATH, last_day_prev, last_day_curr, dirs, month_str, previous_month_str, anio, SAC_PATH, RESERVA_PATH, EEP_PATH)
        print(f"✅ Mapa generado: {map_html}")
        if map_html and os.path.exists(map_html):
            print("Map file exists locally")
        else:
            print("Map file not found locally")
    except Exception as e:
        print(f"❌ Error generando mapa: {e}")
        map_html = None

    # === 5. Reporte ===
    # Las imágenes se usan directamente desde GCS sin descargarlas
    build_report(
        df_path=f"{dirs['stats']}/resumen_expansion_upl_ha.csv",
        strict_path=f"{dirs['stats']}/resumen_expansion_upl_ha_strict.csv",
        map_html=map_html,
        header_img1_path=HEADER_IMG1_PATH,
        header_img2_path=HEADER_IMG2_PATH,
        footer_img_path=FOOTER_IMG_PATH,
        output_dir=dirs["reportes"],
        month=month_str,
        year=anio,
        mes_num=int(args.mes)
    )

    # === Subir carpeta completa a GCS ===
    def upload_folder_to_gcs(local_folder, gcs_bucket, gcs_prefix):
        # Archivos a excluir (imágenes de header/footer que ya están en GCS)
        exclude_files = {'asi_4.png', 'bogota_4.png', 'secre_5.png'}
        
        client = storage.Client()
        bucket = client.bucket(gcs_bucket)
        for root, dirs_files, files in os.walk(local_folder):
            for file in files:
                # Saltar archivos excluidos
                if file in exclude_files:
                    print(f"⏭️ Omitiendo {file} (ya está en GCS)")
                    continue
                    
                local_path = os.path.join(root, file)
                relative_path = os.path.relpath(local_path, local_folder)
                gcs_path = os.path.join(gcs_prefix, relative_path).replace("\\", "/")
                blob = bucket.blob(gcs_path)
                blob.upload_from_filename(local_path)
                print(f"✅ Subido {local_path} a gs://{gcs_bucket}/{gcs_path}")

    print("☁️ Subiendo outputs a GCS...")
    fecha_rango = f"{anio}_{mes:02d}"
    upload_folder_to_gcs(OUTPUT_FOLDER, GCS_OUTPUT_BUCKET, f"{GCS_OUTPUT_PREFIX}/{fecha_rango}")

    print("✅ Proceso completo. Archivos guardados en:")
    print(f"   - Local: {OUTPUT_FOLDER}")
    print(f"   - GCS: gs://{GCS_OUTPUT_BUCKET}/{GCS_OUTPUT_PREFIX}/{fecha_rango}/")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de expansión urbana mensual (mosaico 1 año atrás)")
    
    # Calculate default values: previous month and current year
    today = datetime.now()
    if today.month == 1:
        default_year = today.year - 1
        default_month = 12
    else:
        default_year = today.year
        default_month = today.month - 1
    
    parser.add_argument("--anio", type=int, default=default_year, 
                        help=f"Año en formato YYYY (default: {default_year})")
    parser.add_argument("--mes", type=int, default=default_month, 
                        help=f"Mes en formato numérico 1-12 (default: {default_month})")
    args = parser.parse_args()

    main(args.anio, args.mes)
