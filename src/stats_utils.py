import geopandas as gpd
import pandas as pd
import rasterio
import os
import shutil
from shapely.geometry import shape
from rasterio.features import shapes
from pathlib import Path
from src.aux_utils import download_gcs_to_temp

def create_intersections(new_urban_tif, sac_path, reserva_path, eep_path, output_dir):
    """
    Convierte un raster binario de expansión urbana a polígonos,
    genera intersecciones con SAC, Reserva y EEP,
    y maneja el caso en que no haya geometrías válidas.
    """
    base_name = Path(new_urban_tif).stem

    with rasterio.open(new_urban_tif) as src:
        data = src.read(1)
        mask = data > 0
        results = list(shapes(data, mask=mask, transform=src.transform))

    # Validar si hay geometrías
    if not results:
        print(f"⚠️ No se encontraron píxeles positivos en {base_name}. Se omite intersección.")
        empty_path = os.path.join(output_dir, f"{base_name}_intersections.geojson")
        gpd.GeoDataFrame(geometry=[], crs=src.crs).to_file(empty_path)
        return

    features = []
    for geom, value in results:
        if geom and geom.get("type") in ("Polygon", "MultiPolygon"):
            features.append({"geometry": shape(geom), "value": value})

    if not features:
        print(f"⚠️ Ninguna geometría válida encontrada en {base_name}.")
        gpd.GeoDataFrame(geometry=[], crs=src.crs).to_file(
            os.path.join(output_dir, f"{base_name}_intersections.geojson")
        )
        return

    gdf_newurban = gpd.GeoDataFrame(features, geometry="geometry", crs=src.crs)

    sac_local = download_gcs_to_temp(sac_path)
    res_local = download_gcs_to_temp(reserva_path)
    eep_local = download_gcs_to_temp(eep_path)

    gdf_sac = gpd.read_file(sac_local).to_crs(gdf_newurban.crs)
    gdf_res = gpd.read_file(res_local).to_crs(gdf_newurban.crs)
    gdf_eep = gpd.read_file(eep_local).to_crs(gdf_newurban.crs)

    # Clean up temp files
    for p, lp in [(sac_path, sac_local), (reserva_path, res_local), (eep_path, eep_local)]:
        if str(p).startswith("gs://"):
            temp_dir = os.path.dirname(lp)
            if os.path.isdir(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except PermissionError as e:
                    print(f"⚠️ No se pudo eliminar el directorio temporal {temp_dir}: {e}. Continuando...")
            else:
                if os.path.exists(lp):
                    try:
                        os.unlink(lp)
                    except PermissionError as e:
                        print(f"⚠️ No se pudo eliminar el archivo temporal {lp}: {e}. Continuando...")

    # Clean up temp files
    # Clean up temp files
    for p, lp in [(sac_path, sac_local), (reserva_path, res_local), (eep_path, eep_local)]:
        if str(p).startswith("gs://"):
            temp_dir = os.path.dirname(lp)
            if os.path.isdir(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except PermissionError as e:
                    print(f"⚠️ No se pudo eliminar el directorio temporal {temp_dir}: {e}. Continuando...")
            else:
                if os.path.exists(lp):
                    try:
                        os.unlink(lp)
                    except (PermissionError, OSError) as e:
                        print(f"⚠️ No se pudo eliminar el archivo temporal {lp}: {e}. Continuando...")

    gdf_inter = pd.concat([
        gpd.overlay(gdf_newurban, gdf_sac, how="intersection"),
        gpd.overlay(gdf_newurban, gdf_res, how="intersection"),
        gpd.overlay(gdf_newurban, gdf_eep, how="intersection")
    ], ignore_index=True)

    gdf_inter.to_file(os.path.join(output_dir, f"{base_name}_intersections.geojson"))

    if len(gdf_inter) == 0:
        print(f"⚠️ No hubo intersecciones para {base_name}.")
        gdf_newurban.to_file(os.path.join(output_dir, f"{base_name}_no_intersections.geojson"))
        return

    no_inter = gpd.overlay(gdf_newurban, gdf_inter, how="difference")
    no_inter.to_file(os.path.join(output_dir, f"{base_name}_no_intersections.geojson"))
    print(f"✅ Intersecciones generadas correctamente para {base_name}.")


def calculate_expansion_areas(input_dir, output_dir, upl_path, prefix="", file_suffix="new_urban"):

    crs = "EPSG:9377"
    path_no = os.path.join(input_dir, f"{file_suffix}_no_intersections.geojson")
    path_inter = os.path.join(input_dir, f"{file_suffix}_intersections.geojson")

    gdf_no = gpd.read_file(path_no).to_crs(crs)
    gdf_inter = gpd.read_file(path_inter).to_crs(crs)

    upl_local = download_gcs_to_temp(upl_path)
    gdf_upl = gpd.read_file(upl_local).to_crs(crs)
    print(f"Debug: UPL columns: {list(gdf_upl.columns)}")
    if "NOMBRE" not in gdf_upl.columns:
        raise ValueError(f"La columna 'NOMBRE' no existe en el archivo UPL. Columnas disponibles: {list(gdf_upl.columns)}")

    # Clean up temp file
    if str(upl_path).startswith("gs://"):
        temp_dir = os.path.dirname(upl_local)
        if os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except PermissionError as e:
                print(f"⚠️ No se pudo eliminar el directorio temporal {temp_dir}: {e}. Continuando...")
        else:
            if os.path.exists(upl_local):
                try:
                    os.unlink(upl_local)
                except (PermissionError, OSError) as e:
                    print(f"⚠️ No se pudo eliminar el archivo temporal {upl_local}: {e}. Continuando...")

    inter_upl = gpd.overlay(gdf_upl, gdf_inter, how="intersection")
    nointer_upl = gpd.overlay(gdf_upl, gdf_no, how="intersection")

    inter_upl["area_ha"] = inter_upl.geometry.area / 10000
    nointer_upl["area_ha"] = nointer_upl.geometry.area / 10000

    resumen = (
        inter_upl.groupby("NOMBRE")["area_ha"].sum().reset_index().rename(columns={"area_ha": "interseccion_ha"})
        .merge(nointer_upl.groupby("NOMBRE")["area_ha"].sum().reset_index().rename(columns={"area_ha": "no_interseccion_ha"}),
               on="NOMBRE", how="outer").fillna(0)
    )
    resumen["total_ha"] = resumen["interseccion_ha"] + resumen["no_interseccion_ha"]

    out_csv = os.path.join(output_dir, f"resumen_expansion_upl_ha_{prefix.strip('_')}.csv") if prefix else os.path.join(output_dir, "resumen_expansion_upl_ha.csv")
    resumen.to_csv(out_csv, index=False)
    print(f"✅ Guardado: {out_csv}")
    return resumen, None