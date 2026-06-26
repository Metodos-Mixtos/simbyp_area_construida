#!/usr/bin/env python3
"""
Script para exportar visualización SAR del AOI completo

⚠️ ADVERTENCIA: Este script descarga el AOI completo y puede consumir muchas
unidades de procesamiento de Copernicus Dataspace si el AOI es grande.

Uso:
    python export_sar_visualization.py --date 2025-11-30 --lookback 30

Esto generará:
- GeoTIFF con bandas: VV (dB), VH (dB), Mask, Urban Classification
- PNG con visualización de las 4 bandas

Los archivos se guardan en: temp_data/urban_sprawl/outputs/sar_visualization/
"""

import argparse
import os
import sys
from datetime import datetime
import dotenv

# Cargar variables de entorno
dotenv.load_dotenv()

# Set GOOGLE_APPLICATION_CREDENTIALS if specified in .env
credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if credentials_path:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path

from src.config import (
    AOI_PATH,
    BASE_PATH,
    SAR_PARAMS,
    SENTINELHUB_CLIENT_ID,
    SENTINELHUB_CLIENT_SECRET
)
from src.sar_filter import initialize_sentinel_hub_config, export_sar_visualization
from src.aux_utils import load_geometry


def main():
    parser = argparse.ArgumentParser(
        description="Exportar visualización SAR de un tile específico del AOI",
        epilog="Usa --tile para especificar un tile del grid (más fácil que --bbox)"
    )
    parser.add_argument(
        "--date",
        type=str,
        required=True,
        help="Fecha final en formato YYYY-MM-DD (ej: 2025-11-30)"
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=30,
        help="Días hacia atrás desde la fecha (default: 30)"
    )
    parser.add_argument(
        "--tile",
        type=str,
        help="Tile del grid a exportar: 'row,col' (ej: '11,8'). Grid 12x12 para Bogotá. Usa --list-tiles para ver tiles con expansión"
    )
    parser.add_argument(
        "--list-tiles",
        action="store_true",
        help="Listar tiles donde DW detectó expansión en ese mes (no exporta nada)"
    )
    parser.add_argument(
        "--bbox",
        type=str,
        help="Alternativa: Bounding box personalizado 'minx,miny,maxx,maxy' (ej: '-74.15,4.60,-74.05,4.70')"
    )
    parser.add_argument(
        "--no-geotiff",
        action="store_true",
        help="No exportar GeoTIFF (solo PNG)"
    )
    parser.add_argument(
        "--no-png",
        action="store_true",
        help="No exportar PNG (solo GeoTIFF)"
    )
    
    args = parser.parse_args()
    
    # Parsear fecha
    try:
        date_end = datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print("❌ Error: Fecha debe estar en formato YYYY-MM-DD")
        sys.exit(1)
    
    # Verificar credenciales
    if not SENTINELHUB_CLIENT_ID or not SENTINELHUB_CLIENT_SECRET:
        print("❌ Error: Credenciales de Sentinel Hub no configuradas")
        print("   Añade SENTINELHUB_CLIENT_ID y SENTINELHUB_CLIENT_SECRET a tu .env")
        sys.exit(1)
    
    # Preparar directorio de salida
    output_dir = os.path.join(BASE_PATH, "urban_sprawl", "outputs", "sar_visualization")
    os.makedirs(output_dir, exist_ok=True)
    
    # Cargar geometría AOI
    import geopandas as gpd
    from shapely.geometry import box
    
    gdf_aoi = gpd.read_file(AOI_PATH).to_crs("EPSG:4326")
    minx, miny, maxx, maxy = gdf_aoi.total_bounds
    area_km2 = ((maxx - minx) * 111) * ((maxy - miny) * 111)
    
    # Determinar grid size (mismo que pipeline)
    if area_km2 > 400:
        n_tiles = 12
    elif area_km2 > 100:
        n_tiles = 8
    else:
        n_tiles = 4
    
    tile_width = (maxx - minx) / n_tiles
    tile_height = (maxy - miny) / n_tiles
    
    # Si se pidió listar tiles con expansión
    if args.list_tiles:
        # Buscar archivo de intersecciones del mes
        anio = date_end.year
        mes = date_end.month
        inter_path = os.path.join(BASE_PATH, "urban_sprawl", "outputs", f"{anio}_{mes:02d}", "intersections", f"new_urban_{anio}_{mes:02d}_intersections.geojson")
        
        if not os.path.exists(inter_path):
            print(f"⚠️ No se encontró archivo de intersecciones para {anio}-{mes:02d}")
            print(f"   Ruta esperada: {inter_path}")
            print(f"\n💡 Primero ejecuta: python main.py --anio {anio} --mes {mes}")
            sys.exit(1)
        
        gdf_dw = gpd.read_file(inter_path).to_crs("EPSG:4326")
        dw_union = gdf_dw.unary_union
        
        print("="*70)
        print(f"📊 TILES CON EXPANSIÓN DW ({anio}-{mes:02d})")
        print("="*70)
        print(f"📍 AOI: ~{area_km2:.0f} km²")
        print(f"📦 Grid: {n_tiles}x{n_tiles} = {n_tiles*n_tiles} tiles")
        print(f"📏 Tamaño tile: ~{((tile_width*111)*(tile_height*111)):.1f} km²")
        print(f"\n✅ Tiles con expansión DW:")
        
        tiles_with_expansion = []
        for i in range(n_tiles):
            for j in range(n_tiles):
                tile_minx = minx + i * tile_width
                tile_miny = miny + j * tile_height
                tile_maxx = tile_minx + tile_width
                tile_maxy = tile_miny + tile_height
                tile_geom = box(tile_minx, tile_miny, tile_maxx, tile_maxy)
                
                if tile_geom.intersects(dw_union):
                    tiles_with_expansion.append((i, j))
                    print(f"   Tile {i},{j} - Usar: --tile '{i},{j}'")
        
        print(f"\n💡 Total: {len(tiles_with_expansion)} tiles con expansión")
        print(f"\n🚀 Ejemplo de uso:")
        if tiles_with_expansion:
            t = tiles_with_expansion[0]
            print(f"   python export_sar_visualization.py --date {args.date} --lookback {args.lookback} --tile '{t[0]},{t[1]}'")
        
        sys.exit(0)
    
    print("="*70)
    print("🛰️ EXPORTACIÓN DE VISUALIZACIÓN SAR")
    print("="*70)
    print(f"📅 Fecha: {args.date}")
    print(f"🔄 Lookback: {args.lookback} días")
    print(f"📂 Salida: {output_dir}")
    print(f"📍 AOI: ~{area_km2:.0f} km² (grid {n_tiles}x{n_tiles})")
    
    # Determinar área a exportar
    temp_geojson = None
    
    if args.tile:
        # Usar tile específico del grid
        try:
            tile_i, tile_j = map(int, args.tile.split(','))
            
            if tile_i < 0 or tile_i >= n_tiles or tile_j < 0 or tile_j >= n_tiles:
                print(f"❌ Error: Tile ({tile_i},{tile_j}) fuera de rango. Grid es {n_tiles}x{n_tiles} (0 a {n_tiles-1})")
                sys.exit(1)
            
            tile_minx = minx + tile_i * tile_width
            tile_miny = miny + tile_j * tile_height
            tile_maxx = tile_minx + tile_width
            tile_maxy = tile_miny + tile_height
            
            print(f"📦 Tile: ({tile_i},{tile_j}) del grid {n_tiles}x{n_tiles}")
            print(f"   Bounds: ({tile_minx:.4f}, {tile_miny:.4f}) → ({tile_maxx:.4f}, {tile_maxy:.4f})")
            
            # Crear GeoJSON temporal con tile
            temp_geojson = os.path.join(output_dir, f"temp_tile_{tile_i}_{tile_j}.geojson")
            gdf_tile = gpd.GeoDataFrame(geometry=[box(tile_minx, tile_miny, tile_maxx, tile_maxy)], crs="EPSG:4326")
            gdf_tile.to_file(temp_geojson, driver="GeoJSON")
            aoi_path = temp_geojson
            
        except Exception as e:
            print(f"❌ Error parseando tile: {e}")
            print("   Formato: --tile 'row,col' (ej: --tile '11,8')")
            print(f"   Usa --list-tiles para ver tiles con expansión")
            sys.exit(1)
            
    elif args.bbox:
        # Usar bbox personalizado
        try:
            bbox_minx, bbox_miny, bbox_maxx, bbox_maxy = map(float, args.bbox.split(','))
            print(f"📍 Bbox personalizado: ({bbox_minx}, {bbox_miny}) → ({bbox_maxx}, {bbox_maxy})")
            
            temp_geojson = os.path.join(output_dir, "temp_bbox.geojson")
            gdf_bbox = gpd.GeoDataFrame(geometry=[box(bbox_minx, bbox_miny, bbox_maxx, bbox_maxy)], crs="EPSG:4326")
            gdf_bbox.to_file(temp_geojson, driver="GeoJSON")
            aoi_path = temp_geojson
        except Exception as e:
            print(f"❌ Error parseando bbox: {e}")
            print("   Formato: --bbox 'minx,miny,maxx,maxy'")
            sys.exit(1)
    else:
        # Sin tile ni bbox especificado
        print("❌ Error: Debes especificar --tile o --bbox")
        print(f"\n💡 Usa --list-tiles para ver tiles con expansión DW:")
        print(f"   python export_sar_visualization.py --date {args.date} --list-tiles")
        sys.exit(1)
    
    print("="*70)
    
    # Inicializar config
    config = initialize_sentinel_hub_config(
        client_id=SENTINELHUB_CLIENT_ID,
        client_secret=SENTINELHUB_CLIENT_SECRET
    )
    
    # Exportar visualización
    results = export_sar_visualization(
        aoi_geojson_path=aoi_path,
        output_dir=output_dir,
        date_end=date_end,
        lookback_days=args.lookback,
        sar_params=SAR_PARAMS,
        config=config,
        export_geotiff=not args.no_geotiff,
        export_png=not args.no_png
    )
    
    # Limpiar archivo temporal
    if temp_geojson and os.path.exists(temp_geojson):
        os.remove(temp_geojson)
    
    if results:
        print("\n✅ EXPORTACIÓN EXITOSA")
        print("\n📦 Archivos generados:")
        for key, path in results.items():
            print(f"   - {key}: {path}")
        print("\n💡 Puedes abrir el GeoTIFF en QGIS para inspección detallada")
        print("   Bandas: 1=VV_dB, 2=VH_dB, 3=Valid_Mask, 4=Urban_Mask")
    else:
        print("\n❌ La exportación falló")
        sys.exit(1)


if __name__ == "__main__":
    main()
