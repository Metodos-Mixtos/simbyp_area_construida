#!/usr/bin/env python3
import argparse
from datetime import datetime
import locale
import sys
import os
import json
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

from src.config import AOI_PATH, SAC_PATH, RESERVA_PATH, EEP_PATH, UPL_PATH, HEADER_IMG1_PATH, HEADER_IMG2_PATH, FOOTER_IMG_PATH, GOOGLE_CLOUD_PROJECT, BASE_PATH, GCS_OUTPUT_BUCKET, GCS_OUTPUT_PREFIX, NDVI_THRESHOLD, SENTINELHUB_CLIENT_ID, SENTINELHUB_CLIENT_SECRET, BUFFER_CONSTRUCCIONES_METROS, SENTINEL1_RESOLUTION, SENTINEL1_LOOKBACK_DAYS
from src.aux_utils import authenticate_gee, load_geometry, set_dates, cleanup_temp_data
from src.stats_utils import calculate_expansion_areas, create_intersections
from src.pipeline_utils import prepare_folders, build_report, initialize_sentinel_hub_config, process_new_constructions
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
    print(f"Debug: AOI_PATH = {AOI_PATH}")
    geometry = load_geometry(AOI_PATH)

    # === 1. Detección de Construcciones Nuevas (Sentinel-1 VV + NDVI) ===
    print("\n" + "="*70)
    print("🛰️ INICIANDO DETECCIÓN DE CONSTRUCCIONES NUEVAS")
    print("   Metodología: Sentinel-1 VV Temporal + NDVI")
    print("="*70)
    
    # Verificar credenciales Sentinel Hub
    if not SENTINELHUB_CLIENT_ID or not SENTINELHUB_CLIENT_SECRET:
        raise ValueError(
            "Credenciales de Sentinel Hub no configuradas.\n"
            "Asegúrate de que los secretos estén en GCP Secret Manager:\n"
            "  - sentinelhub-client-id\n"
            "  - sentinelhub-client-secret"
        )
    
    # Inicializar Sentinel Hub
    sh_config = initialize_sentinel_hub_config(
        client_id=SENTINELHUB_CLIENT_ID,
        client_secret=SENTINELHUB_CLIENT_SECRET
    )
    print(f"✅ Sentinel Hub configurado")
    
    # Ejecutar detección
    try:
        new_urban_path = process_new_constructions(
            geometry=geometry,
            output_dir=dirs["new_constructions"],
            year=anio,
            month=mes,
            sh_config=sh_config
        )
        
        if new_urban_path and os.path.exists(new_urban_path):
            print(f"\n✅ Construcciones nuevas detectadas: {new_urban_path}")
        else:
            print(f"\n⏭️ No se detectaron construcciones nuevas para {month_str} {anio}")
            print(f"📄 Generando reporte sin expansión...")
            from src.pipeline_utils import build_no_expansion_report
            build_no_expansion_report(
                header_img1_path=HEADER_IMG1_PATH,
                header_img2_path=HEADER_IMG2_PATH,
                footer_img_path=FOOTER_IMG_PATH,
                output_dir=dirs["reportes"],
                month=month_str,
                year=anio,
                mes_num=mes
            )
            # Limpiar y salir
            cleanup_temp_data()
            return
            
    except Exception as e:
        print(f"\n❌ Error en detección de construcciones: {e}")
        import traceback
        traceback.print_exc()
        raise

    # === 2. Intersecciones con Áreas Protegidas ===
    print("\n" + "="*70)
    print("📊 CALCULANDO INTERSECCIONES CON ÁREAS PROTEGIDAS")
    print("="*70)
    create_intersections(new_urban_path, SAC_PATH, RESERVA_PATH, EEP_PATH, dirs["intersections"], anio, mes)
    
    # === 3. Estadísticas ===
    print("\n" + "="*70)
    print("📊 CALCULANDO ESTADÍSTICAS")
    print("="*70)
    calculate_expansion_areas(dirs["intersections"], dirs["stats"], UPL_PATH, anio, mes)

    # === 4. Mapas Sentinel ===
    print("\n" + "="*70)
    print("🗺️ GENERANDO MAPAS")
    print("="*70)
    try:
        map_html = generate_maps(
            aoi_path=AOI_PATH,
            bounds_prev=last_day_prev,
            bounds_curr=last_day_curr,
            dirs=dirs,
            month_str=month_str,
            previous_month_str=previous_month_str,
            year=anio,
            mes=mes,
            sac=SAC_PATH,
            reserva=RESERVA_PATH,
            eep=EEP_PATH,
            construcciones_path=new_urban_path  # Pasar ruta de construcciones nuevas
        )
        print(f"✅ Mapa generado: {map_html}")
    except Exception as e:
        print(f"❌ Error generando mapa: {e}")
        import traceback
        traceback.print_exc()
        map_html = None

    # === 5. Reporte ===
    print("\n" + "="*70)
    print("📄 GENERANDO REPORTE")
    print("="*70)
    stats_csv = f"{dirs['stats']}/resumen_expansion_upl_ha_{anio}_{mes:02d}.csv"
    
    if os.path.exists(stats_csv):
        # Verificar si el CSV tiene datos
        import pandas as pd
        try:
            df = pd.read_csv(stats_csv)
            if len(df) == 0 or df['total_ha'].sum() == 0:
                print(f"⚠️ No se detectó expansión urbana para {month_str} {anio}")
                print(f"📄 Generando reporte sin expansión...")
                from src.pipeline_utils import build_no_expansion_report
                build_no_expansion_report(
                    header_img1_path=HEADER_IMG1_PATH,
                    header_img2_path=HEADER_IMG2_PATH,
                    footer_img_path=FOOTER_IMG_PATH,
                    output_dir=dirs["reportes"],
                    month=month_str,
                    year=anio,
                    mes_num=mes
                )
            else:
                # CSV tiene datos, generar reporte normal
                build_report(
                    df_path=stats_csv,
                    map_html=map_html,
                    header_img1_path=HEADER_IMG1_PATH,
                    header_img2_path=HEADER_IMG2_PATH,
                    footer_img_path=FOOTER_IMG_PATH,
                    output_dir=dirs["reportes"],
                    month=month_str,
                    year=anio,
                    mes_num=mes
                )
        except Exception as e:
            print(f"⚠️ Error leyendo CSV: {e}")
            # Si hay error leyendo CSV, intentar generar reporte normal
            build_report(
                df_path=stats_csv,
                map_html=map_html,
                header_img1_path=HEADER_IMG1_PATH,
                header_img2_path=HEADER_IMG2_PATH,
                footer_img_path=FOOTER_IMG_PATH,
                output_dir=dirs["reportes"],
                month=month_str,
                year=anio,
                mes_num=mes
            )
    else:
        print(f"⏭️ No se detectó expansión urbana para {month_str} {anio}")
        print(f"📄 Generando reporte sin expansión...")
        from src.pipeline_utils import build_no_expansion_report
        build_no_expansion_report(
            header_img1_path=HEADER_IMG1_PATH,
            header_img2_path=HEADER_IMG2_PATH,
            footer_img_path=FOOTER_IMG_PATH,
            output_dir=dirs["reportes"],
            month=month_str,
            year=anio,
            mes_num=mes
        )

    # === Subir carpeta completa a GCS ===
    def upload_folder_to_gcs(local_folder, gcs_bucket, gcs_prefix):
        # Archivos legacy del sistema Dynamic World que no deben subirse
        legacy_patterns = ['dw_and_sar', 'dw_only', 'sar_only', '_sar.csv']
        
        client = storage.Client()
        bucket = client.bucket(gcs_bucket)
        for root, dirs_files, files in os.walk(local_folder):
            for file in files:
                # Saltar archivos legacy de Dynamic World
                if any(pattern in file for pattern in legacy_patterns):
                    print(f"⏭️ Omitiendo {file} (archivo legacy)")
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
