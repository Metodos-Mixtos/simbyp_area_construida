import geopandas as gpd
import pandas as pd
import rasterio
import os
import shutil
from shapely.geometry import shape
from rasterio.features import shapes
from pathlib import Path
from src.aux_utils import download_gcs_to_temp, TEMP_DATA_DIR

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
        num_pixels = mask.sum()
        area_teorica_ha = (num_pixels * 100) / 10000  # 100 m² por píxel (10m × 10m)
        print(f"   📊 {base_name}: {num_pixels} píxeles = {area_teorica_ha:.2f} ha teórico")
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
            # Only delete if it's a subdirectory within TEMP_DATA_DIR, not TEMP_DATA_DIR itself
            if temp_dir != str(TEMP_DATA_DIR) and os.path.isdir(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except PermissionError as e:
                    print(f"⚠️ No se pudo eliminar el directorio temporal {temp_dir}: {e}. Continuando...")
            elif os.path.isfile(lp):
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

    # Verificar si los archivos existen (pueden no existir si no hubo expansión)
    if not os.path.exists(path_no) or not os.path.exists(path_inter):
        print(f"⏭️ Omitiendo cálculo para {file_suffix}: archivos de intersección no existen (sin expansión detectada)")
        return

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
        # Only delete if it's a subdirectory within TEMP_DATA_DIR, not TEMP_DATA_DIR itself
        if temp_dir != str(TEMP_DATA_DIR) and os.path.isdir(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except PermissionError as e:
                print(f"⚠️ No se pudo eliminar el directorio temporal {temp_dir}: {e}. Continuando...")
        elif os.path.isfile(upl_local):
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


def extract_dw_bands_from_expansion(expansion_tif, dw_all_bands_tif, output_csv):
    """Extrae valores de todas las bandas DW para píxeles de expansión urbana.
    
    Args:
        expansion_tif: Raster binario de expansión (new_urban.tif o new_urban_strict.tif)
        dw_all_bands_tif: Raster DW con todas las bandas
        output_csv: Ruta de salida para el CSV
    """
    if not os.path.exists(expansion_tif):
        print(f"⏭️ No se encontró: {expansion_tif}")
        return
    
    if not os.path.exists(dw_all_bands_tif):
        print(f"⏭️ No se encontró: {dw_all_bands_tif}")
        return
    
    # Leer expansión urbana
    with rasterio.open(expansion_tif) as exp_src:
        expansion_data = exp_src.read(1)
        transform = exp_src.transform
        rows, cols = (expansion_data > 0).nonzero()
        
        if len(rows) == 0:
            print(f"⚠️ Sin píxeles de expansión en {os.path.basename(expansion_tif)}")
            empty_df = pd.DataFrame(columns=['pixel_id', 'row', 'col', 'lon', 'lat', 
                                            'water', 'trees', 'grass', 'flooded_vegetation', 
                                            'crops', 'shrub_and_scrub', 'built', 'bare', 'snow_and_ice'])
            empty_df.to_csv(output_csv, index=False)
            return
    
    # Leer bandas DW
    with rasterio.open(dw_all_bands_tif) as dw_src:
        band_names = ["water", "trees", "grass", "flooded_vegetation", "crops", 
                     "shrub_and_scrub", "built", "bare", "snow_and_ice"]
        
        records = []
        skipped = 0
        
        for idx, (row, col) in enumerate(zip(rows, cols)):
            try:
                # Coordenadas geográficas desde raster de expansión
                lon, lat = rasterio.transform.xy(transform, row, col, offset='center')
                
                # Convertir a índices del raster DW
                dw_row, dw_col = rasterio.transform.rowcol(dw_src.transform, lon, lat)
                
                # Verificar límites
                if dw_row < 0 or dw_row >= dw_src.height or dw_col < 0 or dw_col >= dw_src.width:
                    skipped += 1
                    continue
                
                pixel_values = {
                    'pixel_id': idx + 1,
                    'row': int(row),
                    'col': int(col),
                    'lon': lon,
                    'lat': lat
                }
                
                # Leer valores de cada banda
                for band_idx, band_name in enumerate(band_names):
                    value = dw_src.read(band_idx + 1, window=((dw_row, dw_row + 1), (dw_col, dw_col + 1)))
                    pixel_values[band_name] = float(value[0, 0])
                
                records.append(pixel_values)
            except:
                skipped += 1
                continue
        
        if skipped > 0:
            print(f"   ⚠️ {skipped} píxeles omitidos")
        
        df = pd.DataFrame(records)
        df.to_csv(output_csv, index=False)
        print(f"✅ Extraídos {len(records)} píxeles → {output_csv}")
        return df