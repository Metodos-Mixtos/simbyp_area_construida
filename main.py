import os
import ee
import pandas as pd
import json
from dotenv import load_dotenv
from pathlib import Path

from src.utils import (
    authenticate_gee,
    load_geometry,
    get_dw_median,
    export_image,
    download_sentinel_rgb,
    create_intersections,
    calculate_expansion_areas, 
    create_growth_clusters
)
from src.maps import plot_expansion_interactive, get_sentinel_tiles_from_ee
from reporte.render_report import render

# === CONFIGURACIÓN ===
load_dotenv('dot_env_content.env')

INPUTS_PATH = os.getenv("INPUTS_PATH")
AOI_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "bogota_urbana.geojson")
OUTPUT_DIR = os.path.join(INPUTS_PATH, "urban_sprawl", "outputs")
LOGO_PATH = os.path.join(INPUTS_PATH, "gfw/Logo_SDP.jpeg")

# === Años de comparación ===
YEAR1 = 2023
YEAR2 = 2024

# === Capas base ===
SAC_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "situacion_amb_conflictiva.geojson")
RESERVA_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "reserva_cerrosorientales.geojson")
EEP_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "estructuraecologicaprincipal", "EstructuraEcologicaPrincipal.shp")
UPL_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "unidadplaneamientolocal", "UnidadPlaneamientoLocal.shp")
BUFFER_PATH = os.path.join(INPUTS_PATH, "urban_sprawl", "area_estudio", "bogota_urbana_buffer.geojson")

# === SUBCARPETAS ===
DIRS = {
    "dw": os.path.join(OUTPUT_DIR, "dw"),
    "sentinel": os.path.join(OUTPUT_DIR, "sentinel"),
    "intersections": os.path.join(OUTPUT_DIR, "intersections"),
    "maps": os.path.join(OUTPUT_DIR, "maps"),
    "stats": os.path.join(OUTPUT_DIR, "stats"),
    "reportes": os.path.join(OUTPUT_DIR, "reportes"),
}
for d in DIRS.values():
    os.makedirs(d, exist_ok=True)


def main():
    authenticate_gee()
    geometry = load_geometry(AOI_PATH)

    # === 1. Dynamic World ===
    new_urban_path = os.path.join(DIRS["dw"], f"new_urban_{YEAR1}_{YEAR2}.tif")
    if not os.path.exists(new_urban_path):
        print("📊 Calculando imágenes de Dynamic World...")
        before = get_dw_median(YEAR1, geometry)
        after = get_dw_median(YEAR2, geometry)
        new_urban = before.lt(0.2).And(after.gt(0.5)).rename("new_urban")
        export_image(new_urban, geometry, new_urban_path)
    else:
        print(f"⏭️ Dynamic World ya existente: {new_urban_path}")

    # === 2. Sentinel-2 ===
    sentinel_before_path = os.path.join(DIRS["sentinel"], f"sentinel_before_{YEAR1}.tif")
    sentinel_after_path = os.path.join(DIRS["sentinel"], f"sentinel_after_{YEAR2}.tif")

    before_start, after_start = ee.Date(f"{YEAR1}-01-01"), ee.Date(f"{YEAR2}-01-01")
    before_end, after_end = before_start.advance(1, 'year'), after_start.advance(1, 'year')

    if not os.path.exists(sentinel_before_path):
        print("📷 Descargando Sentinel-2 antes...")
        download_sentinel_rgb(geometry, before_start, before_end, sentinel_before_path)
    if not os.path.exists(sentinel_after_path):
        print("📷 Descargando Sentinel-2 después...")
        download_sentinel_rgb(geometry, after_start, after_end, sentinel_after_path)

    # === 3. Intersecciones ===
    inter_dir = DIRS["intersections"]
    if not os.path.exists(os.path.join(inter_dir, "new_urban_intersections.geojson")):
        print("📍 Calculando intersecciones con capas base...")
        create_intersections(
            new_urban_tif=new_urban_path,
            sac_path=SAC_PATH,
            reserva_path=RESERVA_PATH,
            eep_path=EEP_PATH,
            output_dir=inter_dir
        )
    clusters_gdf = create_growth_clusters(gdf_path=os.path.join(inter_dir, "new_urban_intersections.geojson"), buffer_distance=500)
    clusters_gdf.to_file(os.path.join(inter_dir, "new_urban_intersections_cluster.geojson"))

    # === 4. Estadísticas ===
    print("📊 Calculando áreas por UPL y buffer urbano...")
    resumen_upl, resumen_buffer = calculate_expansion_areas(
        input_dir=inter_dir,
        output_dir=DIRS["stats"],
        upl_path=UPL_PATH,
        bogota_buffer_path=BUFFER_PATH
    )

    # === 6. Mapa interactivo ===
    print("🗺️ Creando mapa interactivo básico de expansión urbana...")
    map_path = os.path.join(OUTPUT_DIR, "reportes", f"urban_map_interactivo_{YEAR2}.html")
    os.makedirs(os.path.dirname(map_path), exist_ok=True)
    
    tiles = get_sentinel_tiles_from_ee(
    aoi_path=AOI_PATH,
    start_before="2023-01-01",
    end_before="2023-12-31",
    start_after="2024-01-01",
    end_after="2024-12-31"
)
    plot_expansion_interactive(
        intersections_dir=DIRS["intersections"],
        sac_path=SAC_PATH,
        reserva_path=RESERVA_PATH,
        eep_path=EEP_PATH,
        aoi_path=AOI_PATH,
        output_path=os.path.join(DIRS["maps"], f"map_expansion_ee_{YEAR2}.html"),
        tiles_before=tiles["before"],
        tiles_after=tiles["after"]
    )

    # === 7. JSON y reporte final ===
    report_dir = os.path.join(OUTPUT_DIR, "reportes")
    os.makedirs(report_dir, exist_ok=True)

    report_json = os.path.join(report_dir, "urban_sprawl_reporte.json")
    report_html = os.path.join(report_dir, f"urban_sprawl_reporte_{YEAR2}.html")

    # Generar estructura mínima del JSON (puedes completarla después)
    summary_csv = os.path.join(DIRS["stats"], "resumen_expansion_upl_ha.csv")
    df = pd.read_csv(summary_csv)
    df_top = df.nlargest(5, "interseccion_ha").copy()
    top_upls = [
        {"UPL": r["NOMBRE"], "TOTAL_HA": round(r["total_ha"], 2),
        "INTER_HA": round(r["interseccion_ha"], 2),
        "NO_INTER_HA": round(r["no_interseccion_ha"], 2)}
        for _, r in df_top.iterrows()
    ]

    report_data = {
        "TITLE": "Reporte de expansión urbana en Bogotá",
        "FECHA_REPORTE": "Octubre 2025",
        "LOGO": LOGO_PATH,
        "TOP_UPLS": top_upls,
        "MAPA_INTERACTIVO": os.path.join(DIRS["maps"], f"map_expansion_ee_{YEAR2}.html"),
        "CLUSTERS": [
            {"NOMBRE": "Zona Noroccidental", "COORDS": "4.75, -74.09"},
            {"NOMBRE": "Zona Suroriental", "COORDS": "4.55, -74.11"}
        ]
    }

    with open(report_json, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    # === Renderizar HTML ===
    TPL_PATH = Path("urban_sprawl/reporte/report_template.html")
    render(TPL_PATH, Path(report_json), Path(report_html))
    print(f"✅ Reporte generado en: {report_html}")


if __name__ == "__main__":
    main()
