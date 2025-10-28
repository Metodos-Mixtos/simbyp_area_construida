import os
import ee
import json
import pandas as pd
import calendar
import argparse
from dotenv import load_dotenv
from pathlib import Path

from src.utils import (
    authenticate_gee,
    load_geometry,
    export_image,
    create_intersections,
    calculate_expansion_areas,
    create_growth_clusters,
    get_monthly_periods,
    get_dw_median_period
)
from src.maps import plot_expansion_interactive, get_sentinel_tiles_from_ee
from reporte.render_report import render


# === CONFIGURACIÓN ===
load_dotenv('dot_env_content.env')

INPUTS_PATH = os.getenv("INPUTS_PATH")
AOI_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "aestudio_bogota.geojson")
LOGO_PATH = os.path.join(INPUTS_PATH, "gfw", "Logo_SDP.jpeg")

# Meses en español
MONTHS_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
    5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
    9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
}


def main(annio: int, mes: int):
    """Ejecuta el pipeline de expansión urbana para un mes y año dados."""
    print(f"🗓️ Ejecutando análisis para {MONTHS_ES[mes].capitalize()} {annio}")

    # === Periodos ===
    PERIODO_ANTES, PERIODO_DESPUES = get_monthly_periods(mes, annio)
    print(f"🗓️ Comparando {PERIODO_ANTES} vs {PERIODO_DESPUES}")

    # === Carpeta mensual ===
    OUTPUT_BASE = os.path.join(INPUTS_PATH, "urban_sprawl", "outputs")
    OUTPUT_DIR = os.path.join(OUTPUT_BASE, f"{annio}_{mes:02d}")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # === Subcarpetas ===
    DIRS = {
        k: os.path.join(OUTPUT_DIR, k)
        for k in ["dw", "sentinel", "intersections", "maps", "stats", "reportes"]
    }
    for d in DIRS.values():
        os.makedirs(d, exist_ok=True)

    # === Capas base ===
    SAC_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "situacion_amb_conflictiva.geojson")
    RESERVA_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "reserva_cerrosorientales.geojson")
    EEP_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "estructuraecologicaprincipal", "EstructuraEcologicaPrincipal.shp")
    UPL_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "unidadplaneamientolocal", "UnidadPlaneamientoLocal.shp")
    BUFFER_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "bogota_urbana_buffer.geojson")

    # === Autenticación y AOI ===
    authenticate_gee()
    geometry = load_geometry(AOI_PATH)

    # === 1. Dynamic World ===
    new_urban_path = os.path.join(DIRS["dw"], f"new_urban_{PERIODO_ANTES[0]}_{PERIODO_DESPUES[1]}.tif")
    if not os.path.exists(new_urban_path):
        print("📊 Calculando imágenes de Dynamic World...")
        before = get_dw_median_period(*PERIODO_ANTES, geometry)
        after = get_dw_median_period(*PERIODO_DESPUES, geometry)
        new_urban = before.lt(0.2).And(after.gt(0.5)).rename("new_urban")
        export_image(new_urban, geometry, new_urban_path)
    else:
        print(f"⏭️ Dynamic World ya existente: {new_urban_path}")

    # === 2. Intersecciones ===
    inter_dir = DIRS["intersections"]
    inter_file = os.path.join(inter_dir, "new_urban_intersections.geojson")
    if not os.path.exists(inter_file):
        print("📍 Calculando intersecciones con capas base...")
        create_intersections(new_urban_path, SAC_PATH, RESERVA_PATH, EEP_PATH, inter_dir)

    clusters_gdf = create_growth_clusters(inter_file, buffer_distance=500)
    clusters_gdf.to_file(os.path.join(inter_dir, "new_urban_intersections_cluster.geojson"))

    # === 3. Estadísticas ===
    print("📊 Calculando áreas por UPL y buffer urbano...")
    resumen_upl, resumen_buffer = calculate_expansion_areas(
        input_dir=inter_dir,
        output_dir=DIRS["stats"],
        upl_path=UPL_PATH,
        bogota_buffer_path=BUFFER_PATH
    )

    # === 4. Mapa interactivo ===
    print("🗺️ Creando mapa interactivo básico de expansión urbana...")
    tiles = get_sentinel_tiles_from_ee(
        aoi_path=AOI_PATH,
        start_before=PERIODO_ANTES[0],
        end_before=PERIODO_ANTES[1],
        start_after=PERIODO_DESPUES[0],
        end_after=PERIODO_DESPUES[1]
    )

    map_html = os.path.join(DIRS["maps"], f"map_expansion_ee_{annio}_{mes:02d}.html")
    plot_expansion_interactive(
    intersections_dir=inter_dir,
    sac_path=SAC_PATH,
    reserva_path=RESERVA_PATH,
    eep_path=EEP_PATH,
    aoi_path=AOI_PATH,
    output_path=map_html,
    annio=annio,
    mes=mes,
    tiles_before=tiles["before"],
    tiles_after=tiles["after"]
    )

    # === 5. JSON y reporte final ===
    report_json = os.path.join(DIRS["reportes"], "urban_sprawl_reporte.json")
    report_html = os.path.join(DIRS["reportes"], f"urban_sprawl_reporte_{annio}_{mes:02d}.html")

    summary_csv = os.path.join(DIRS["stats"], "resumen_expansion_upl_ha.csv")
    df = pd.read_csv(summary_csv)
    df_top = df.nlargest(5, "interseccion_ha")
    top_upls = [
        {
            "UPL": r["NOMBRE"],
            "TOTAL_HA": round(r["total_ha"], 2),
            "INTER_HA": round(r["interseccion_ha"], 2),
            "NO_INTER_HA": round(r["no_interseccion_ha"], 2)
        }
        for _, r in df_top.iterrows()
    ]

    report_data = {
        "TITLE": "Reporte de expansión urbana en Bogotá",
        "FECHA_REPORTE": f"{MONTHS_ES[mes].capitalize()} {annio}",
        "PERIODO_ANALISIS":f"de {PERIODO_ANTES[0]}-{PERIODO_ANTES[1]} a {PERIODO_DESPUES[0]}-{PERIODO_DESPUES[1]}", 
        "LOGO": LOGO_PATH,
        "TOP_UPLS": top_upls,
        "MAPA_INTERACTIVO": map_html,
        "CLUSTERS": [
            {"NOMBRE": "Zona Noroccidental", "COORDS": "4.75, -74.09"},
            {"NOMBRE": "Zona Suroriental", "COORDS": "4.55, -74.11"}
        ]
    }

    with open(report_json, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    TPL_PATH = Path("urban_sprawl/reporte/report_template.html")
    render(TPL_PATH, Path(report_json), Path(report_html))
    print(f"✅ Reporte generado en: {report_html}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline de expansión urbana mensual")
    parser.add_argument("--annio", type=int, required=True, help="Año en formato YYYY")
    parser.add_argument("--mes", type=int, required=True, help="Mes en formato numérico (1–12)")
    args = parser.parse_args()

    main(args.annio, args.mes)
