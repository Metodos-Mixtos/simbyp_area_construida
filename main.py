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

import dotenv
dotenv.load_dotenv()

#Authenticate with Google Cloud
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# === Configurar idioma español para nombres de meses ===
try:
    locale.setlocale(locale.LC_TIME, "es_ES.UTF-8")
except:
    locale.setlocale(locale.LC_TIME, "es_CO.UTF-8")


def main(anio: int, mes: int):
    month_str = datetime(anio, mes, 1).strftime("%B").capitalize()
    previous_month_str = datetime(anio, mes-1, 1).strftime("%B").capitalize()
    print(f"🗓️ Ejecutando análisis para {month_str} {anio}")

    # === Fechas ===
    last_day_curr, last_day_prev = set_dates(mes, anio)

    # === Preparar carpetas de salida ===
    dirs = prepare_folders(BASE_PATH, anio, mes)

    # === Autenticación y carga del AOI ===
    authenticate_gee(project=GOOGLE_CLOUD_PROJECT)
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
    map_html = generate_maps(AOI_PATH, last_day_prev, last_day_curr, dirs, month_str, previous_month_str, SAC_PATH, RESERVA_PATH, EEP_PATH)

    # === 5. Reporte ===
    build_report(
        df_path=f"{dirs['stats']}/resumen_expansion_upl_ha.csv",
        strict_path=f"{dirs['stats']}/resumen_expansion_upl_ha_strict.csv",
        map_html=map_html,
        header_img1_path=HEADER_IMG1_PATH,
        header_img2_path=HEADER_IMG2_PATH,
        footer_img_path=FOOTER_IMG_PATH,
        output_dir=dirs["reportes"],
        month=month_str,
        year=anio
    )

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de expansión urbana mensual (mosaico 1 año atrás)")
    parser.add_argument("--anio", type=int, required=True, help="Año en formato YYYY")
    parser.add_argument("--mes", type=int, required=True, help="Mes en formato numérico (1–12)")
    args = parser.parse_args()

    main(args.anio, args.mes)
