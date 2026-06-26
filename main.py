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

from src.config import AOI_PATH, SAC_PATH, RESERVA_PATH, EEP_PATH, UPL_PATH, HEADER_IMG1_PATH, HEADER_IMG2_PATH, FOOTER_IMG_PATH, GOOGLE_CLOUD_PROJECT, BASE_PATH, GCS_OUTPUT_BUCKET, GCS_OUTPUT_PREFIX, USE_SAR_FILTER, SAR_PARAMS, SAR_LOOKBACK_T1_DAYS, SAR_LOOKBACK_T2_DAYS, SENTINELHUB_CLIENT_ID, SENTINELHUB_CLIENT_SECRET
from src.aux_utils import authenticate_gee, load_geometry, set_dates, cleanup_temp_data
from src.stats_utils import calculate_expansion_areas, create_intersections
from src.pipeline_utils import prepare_folders, process_dynamic_world, build_report 
from src.maps_utils import generate_maps

# Importar módulo SAR (solo si está habilitado)
if USE_SAR_FILTER:
    from src.sar_filter import (
        initialize_sentinel_hub_config,
        filter_dw_polygons_with_sar,
        apply_sar_filter_to_intersections
    )

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
    dw_path = process_dynamic_world(geometry, dirs["dw"], last_day_prev, last_day_curr, anio, mes)

    # === 2. Intersecciones ===
    create_intersections(dw_path, SAC_PATH, RESERVA_PATH, EEP_PATH, dirs["intersections"], anio, mes)
    
    # === 3. Filtro SAR (NUEVO - opcional) ===
    sar_filtered_applied = False
    if USE_SAR_FILTER:
        print("\n" + "="*70)
        print("🛰️ FILTRO SAR HABILITADO")
        print("="*70)
        
        # Verificar credenciales
        if not SENTINELHUB_CLIENT_ID or not SENTINELHUB_CLIENT_SECRET:
            print("⚠️ ADVERTENCIA: Credenciales de Sentinel Hub no configuradas")
            print("   Añade SENTINELHUB_CLIENT_ID y SENTINELHUB_CLIENT_SECRET a tu .env")
            print("   Continuando sin filtro SAR...")
        else:
            try:
                # Inicializar Sentinel Hub con credenciales CDSE
                sar_config = initialize_sentinel_hub_config(
                    client_id=SENTINELHUB_CLIENT_ID,
                    client_secret=SENTINELHUB_CLIENT_SECRET
                )
                
                # Buscar archivo de intersecciones para filtrar
                import glob
                inter_files = glob.glob(os.path.join(dirs["intersections"], "new_urban_*_intersections.geojson"))
                
                if inter_files:
                    dw_inter_path = inter_files[0]
                    print(f"📂 Archivo DW a filtrar: {os.path.basename(dw_inter_path)}")
                    
                    # Aplicar filtro SAR con SentinelHub (gamma0-terrain RTC)
                    sar_filtered_path = filter_dw_polygons_with_sar(
                        dw_geojson_path=dw_inter_path,
                        output_dir=dirs["intersections"],
                        last_day_prev=last_day_prev,
                        last_day_curr=last_day_curr,
                        sar_params=SAR_PARAMS,
                        config=sar_config,
                        lookback_t1_days=SAR_LOOKBACK_T1_DAYS,
                        lookback_t2_days=SAR_LOOKBACK_T2_DAYS
                    )
                    
                    if sar_filtered_path and os.path.exists(sar_filtered_path):
                        # Aplicar filtro a todos los archivos de intersecciones
                        print("\n📊 Aplicando filtro SAR a archivos de intersecciones...")
                        apply_sar_filter_to_intersections(
                            intersections_dir=dirs["intersections"],
                            sar_filtered_path=sar_filtered_path,
                            anio=anio,
                            mes=mes
                        )
                        sar_filtered_applied = True
                        print("✅ Filtro SAR aplicado exitosamente")
                    elif sar_filtered_path is None:
                        print("⚠️ SAR falló al descargar datos - continuando sin filtro SAR")
                    else:
                        print("⚠️ No se generó archivo SAR filtrado")
                else:
                    print("⚠️ No se encontraron archivos de intersecciones DW")
                    
            except Exception as e:
                print(f"\n❌ Error en filtro SAR: {e}")
                print("⚠️ Continuando sin filtro SAR...")
                import traceback
                traceback.print_exc()
    else:
        print("\n⏭️ Filtro SAR deshabilitado (USE_SAR_FILTER=False)")
    
    # === 4. Estadísticas ===
    # Usar archivos filtrados por SAR si están disponibles
    if sar_filtered_applied:
        print("\n📊 Calculando estadísticas con datos filtrados por SAR...")
        # Las estadísticas usarán automáticamente los archivos _sar_filtered.geojson
        # porque buscan por patrón de nombre
    
    calculate_expansion_areas(dirs["intersections"], dirs["stats"], UPL_PATH, anio, mes, use_sar_filtered=sar_filtered_applied)

    # === 5. Mapas Sentinel ===
    # Usar archivos SAR filtrados para descarga de tiles si están disponibles
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
            use_sar_filtered=sar_filtered_applied  # Usar archivos SAR si existen
        )
        print(f"✅ Mapa generado: {map_html}")
        if map_html and os.path.exists(map_html):
            print("Map file exists locally")
        else:
            print("Map file not found locally")
    except Exception as e:
        print(f"❌ Error generando mapa: {e}")
        map_html = None

    # === 6. Reporte ===
    # Las imágenes se usan directamente desde GCS sin descargarlas
    # Usar archivo SAR si está disponible
    csv_suffix = "_sar" if sar_filtered_applied else ""
    stats_csv = f"{dirs['stats']}/resumen_expansion_upl_ha_{anio}_{mes:02d}{csv_suffix}.csv"
    
    if os.path.exists(stats_csv):
        # Verificar si el CSV tiene datos (SAR pudo rechazar todo)
        import pandas as pd
        try:
            df = pd.read_csv(stats_csv)
            if len(df) == 0 or df['area_ha'].sum() == 0:
                # CSV existe pero no hay datos (SAR rechazó todo)
                print(f"⚠️ SAR rechazó toda la expansión detectada por DW en {month_str} {anio}")
                print(f"📄 Generando reporte indicando validación SAR sin confirmaciones...")
                from src.pipeline_utils import build_no_expansion_report
                build_no_expansion_report(
                    header_img1_path=HEADER_IMG1_PATH,
                    header_img2_path=HEADER_IMG2_PATH,
                    footer_img_path=FOOTER_IMG_PATH,
                    output_dir=dirs["reportes"],
                    month=month_str,
                    year=anio,
                    mes_num=int(args.mes),
                    custom_message={
                        'title': 'SAR no confirmó la expansión detectada por Dynamic World.',
                        'body': f'Durante el periodo de {month_str} {anio}, Dynamic World identificó cambios en coberturas que podrían indicar expansión urbana. Sin embargo, la validación con datos SAR de Sentinel-1 (radar) no confirmó construcciones físicas en esas áreas, por lo que fueron descartadas.'
                    }
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
                    mes_num=int(args.mes)
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
                mes_num=int(args.mes)
            )
    else:
        print(f"⏭️ No se detectó expansión urbana para {month_str} {anio}")
        print(f"📄 Generando reporte sin expansión...")
        # Crear reporte básico indicando que no hubo expansión
        from src.pipeline_utils import build_no_expansion_report
        build_no_expansion_report(
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
