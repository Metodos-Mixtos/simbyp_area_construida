import os
import json
import pandas as pd
import argparse
from dotenv import load_dotenv
from pathlib import Path
from datetime import timedelta

from src.stats_utils import create_intersections,calculate_expansion_areas
from src.dw_utils import get_dw_mosaic_1year, download_sentinel_rgb_visualized
from src.aux_utils import authenticate_gee, load_geometry, export_image, make_relative_path, set_dates
from src.maps_utils import plot_expansion_interactive
from reporte.render_report import render


# === CONFIGURACIÓN ===
load_dotenv("dot_env_content.env")

INPUTS_PATH = os.getenv("INPUTS_PATH")
AOI_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "aestudio_bogota.geojson")
LOGO_PATH = os.path.join(INPUTS_PATH, "Logo_SDP.jpeg")
GOOGLE_CLOUD_PROJECT = os.getenv("GCP_PROJECT")

SAC_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "situacion_amb_conflictiva.geojson")
RESERVA_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "reserva_cerrosorientales.geojson")
EEP_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "estructuraecologicaprincipal", "EstructuraEcologicaPrincipal.shp")
UPL_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "unidadplaneamientolocal", "UnidadPlaneamientoLocal.shp")

MONTHS_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
}

def main(anio: int, mes: int):
    print(f"🗓️ Ejecutando análisis para {MONTHS_ES[mes].capitalize()} {anio}")

    # === Fechas ===
    
    last_day_curr, last_day_prev = set_dates(mes, anio)
    
    OUTPUT_BASE = os.path.join(INPUTS_PATH, "urban_sprawl", "outputs")
    OUTPUT_DIR = os.path.join(OUTPUT_BASE, f"{anio}_{mes:02d}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # === Crear folders ===
    
    DIRS = {k: os.path.join(OUTPUT_DIR, k) for k in ["dw", "sentinel", "intersections", "maps", "stats", "reportes"]}
    for d in DIRS.values():
        os.makedirs(d, exist_ok=True)

    # === Autenticarse en EE ===
    
    authenticate_gee(project=GOOGLE_CLOUD_PROJECT)
    geometry = load_geometry(AOI_PATH)

    # === 1. Dynamic World mosaics (últimos 365 días de cada fecha) ===
    print("📊 Calculando imágenes de Dynamic World...")

    new_urban_path = os.path.join(DIRS["dw"], f"new_urban.tif")
    new_urban_strict_path = os.path.join(DIRS["dw"], f"new_urban_strict.tif")
    
    before = get_dw_mosaic_1year(last_day_prev, geometry)
    current = get_dw_mosaic_1year(last_day_curr, geometry)
    
    if not os.path.exists(new_urban_path): 
        new_urban = before.lt(0.2).And(current.gt(0.5)).rename("new_urban") 
        export_image(new_urban, geometry, new_urban_path) 
    
    else: print(f"⏭️ Dynamic World ya existente: {new_urban_path}")
    
    # Umbral más estricto
    
    if not os.path.exists(new_urban_strict_path): 
        new_urban_strict = before.lt(0.2).And(current.gt(0.7)).rename("new_urban_strict") 
        export_image(new_urban_strict, geometry, new_urban_strict_path) 
    
    else: print(f"⏭️ Dynamic World ya existente: {new_urban_strict_path}")

    # === 2. Intersecciones ===
    inter_dir = DIRS["intersections"]
    inter_file = os.path.join(inter_dir, "new_urban_intersections.geojson")
    inter_strict_file = os.path.join(inter_dir, "new_urban_strict_intersections.geojson")

    if not os.path.exists(inter_file):
        create_intersections(new_urban_path, SAC_PATH, RESERVA_PATH, EEP_PATH, inter_dir)
    if not os.path.exists(inter_strict_file):
        create_intersections(new_urban_strict_path, SAC_PATH, RESERVA_PATH, EEP_PATH, inter_dir)

    # === 3. Estadísticas ===
    resumen_upl, _ = calculate_expansion_areas(inter_dir, DIRS["stats"], UPL_PATH)
    resumen_upl_strict, _ = calculate_expansion_areas(
        inter_dir, DIRS["stats"], UPL_PATH, prefix="strict_", file_suffix="new_urban_strict"
    )

    # === 4. Sentinel RGB mosaics ===
    print("🛰️ Descargando imágenes Sentinel RGB (mosaico 1 año atrás)...")
    rgb_before = os.path.join(DIRS["sentinel"], f"sentinel_rgb_before_{last_day_prev.date()}.png")
    rgb_current = os.path.join(DIRS["sentinel"], f"sentinel_rgb_current_{last_day_curr.date()}.png")

    if not os.path.exists(rgb_before):    
        download_sentinel_rgb_visualized( AOI_PATH,
                                         (last_day_prev - timedelta(days=365)).strftime("%Y-%m-%d"),
                                         last_day_prev.strftime("%Y-%m-%d"),
                                         rgb_before)    
    else:
        pass
        print(f"⏭️ La imagen de Sentinel antes ya existente: {rgb_before}")

    
    if not os.path.exists(rgb_current):
        download_sentinel_rgb_visualized(AOI_PATH,
                                         (last_day_curr - timedelta(days=365)).strftime("%Y-%m-%d"),
                                         last_day_curr.strftime("%Y-%m-%d"),
                                         rgb_current)
    else:
        pass
        print(f"⏭️ La imagen de Sentinel después ya existente: {rgb_current}")

    # === 5. Mapa interactivo ===
    map_html = os.path.join(DIRS["maps"], f"map_expansion_{anio}_{mes:02d}.html")
    
    plot_expansion_interactive(
       intersections_dir=inter_dir,
        sac_path=SAC_PATH,
        reserva_path=RESERVA_PATH,
        eep_path=EEP_PATH,
        output_path=map_html,
        aoi_path=AOI_PATH,
        tiles_before=rgb_before,
        tiles_current=rgb_current)
    
    #download_sentinel_rgb_visualized(aoi_path=AOI_PATH, start_date=(last_day_prev - timedelta(days=365)).strftime("%Y-%m-%d"), end_date=last_day_prev.strftime("%Y-%m-%d"), output_png="/Users/javierguerra/Library/CloudStorage/OneDrive-VestigiumMétodosMixtosAplicadosSAS/SIMBYP_DATA/urban_sprawl/outputs/2025_09/maps/sentinel_before_test.png")

    # === 6. Reporte ===
    summary_csv = os.path.join(DIRS["stats"], "resumen_expansion_upl_ha.csv")
    strict_csv = os.path.join(DIRS["stats"], "resumen_expansion_upl_ha_strict.csv")

    df = pd.read_csv(summary_csv)
    if os.path.exists(strict_csv):
        df_strict = pd.read_csv(strict_csv)[["NOMBRE", "interseccion_ha"]].rename(columns={"interseccion_ha": "interseccion_ha_strict"})
        df = df.merge(df_strict, on="NOMBRE", how="left")
    else:
        df["interseccion_ha_strict"] = 0

    df_top = df.nlargest(5, "interseccion_ha")
    top_upls = [
        {
            "UPL": r["NOMBRE"],
            "INTER_HA": round(r["interseccion_ha"], 2),
            "INTER_HA_STRICT": round(r["interseccion_ha_strict"], 2),
            "TOTAL_HA": round(r["total_ha"], 2)
        }
        for _, r in df_top.iterrows()
    ]

    report_json = os.path.join(DIRS["reportes"], "urban_sprawl_reporte.json")
    report_html = os.path.join(DIRS["reportes"], f"urban_sprawl_reporte_{anio}_{mes:02d}.html")

    BASE_DIR = Path(DIRS["reportes"])
    report_data = {
        "TITULO": "Reporte de expansión urbana en Bogotá",
        "FECHA_REPORTE": f"{MONTHS_ES[mes].capitalize()} {anio}",
        "PERIODO_ANALISIS": f"{last_day_prev.date()} vs {last_day_curr.date()}",
        "LOGO": make_relative_path(LOGO_PATH, BASE_DIR),
        "TOP_UPLS": top_upls,
        "MAPA_INTERACTIVO": make_relative_path(map_html, BASE_DIR)
    }

    with open(report_json, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    render(Path("urban_sprawl/reporte/report_template.html"), Path(report_json), Path(report_html))
    print(f"✅ Reporte generado en: {report_html}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de expansión urbana mensual (mosaico 1 año atrás)")
    parser.add_argument("--anio", type=int, required=True, help="Año en formato YYYY")
    parser.add_argument("--mes", type=int, required=True, help="Mes en formato numérico (1–12)")
    args = parser.parse_args()
    main(args.anio, args.mes)