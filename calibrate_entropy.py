#!/usr/bin/env python3
"""
Script de calibración manual para entropía de Shannon.

PROPÓSITO:
- Ejecutar UNA VEZ para analizar distribución de entropías
- Identificar casos límite cerca del umbral
- Decidir si ajustar ENTROPY_THRESHOLD en config.py

NO SE EJECUTA AUTOMÁTICAMENTE EN EL PIPELINE.

Uso:
    python calibrate_entropy.py --anio 2026 --mes 1
    
Resultado:
    - Estadísticas de entropía
    - CSV temporal con píxeles cerca del umbral
    - Recomendaciones de ajuste
"""

import argparse
import os
import sys
import pandas as pd
import numpy as np
import rasterio
from pathlib import Path
import dotenv

dotenv.load_dotenv()

from src.config import BASE_PATH, ENTROPY_THRESHOLD
from src.stats_utils import calculate_shannon_entropy, get_entropy_calibration_stats


def analyze_entropy_edge_cases(anio: int, mes: int, output_csv: str = None):
    """
    Analiza píxeles CERCA del umbral de entropía para calibración.
    
    Args:
        anio: Año del análisis
        mes: Mes del análisis
        output_csv: Ruta para CSV temporal (opcional)
    
    Returns:
        DataFrame con píxeles en "zona gris" del umbral
    """
    # Rutas a los archivos
    fecha_rango = f"{anio}_{mes:02d}"
    base_dir = os.path.join(BASE_PATH, "urban_sprawl", "outputs", fecha_rango)
    dw_dir = os.path.join(base_dir, "dw")
    
    # Archivos necesarios
    new_urban_tif = os.path.join(dw_dir, "new_urban.tif")
    entropy_t2_tif = os.path.join(dw_dir, "entropy_t2.tif")
    
    if not os.path.exists(new_urban_tif):
        raise FileNotFoundError(f"No se encontró: {new_urban_tif}")
    
    if not os.path.exists(entropy_t2_tif):
        raise FileNotFoundError(
            f"No se encontró: {entropy_t2_tif}\n"
            "Esta rama SIEMPRE calcula entropía. Asegúrate de haber ejecutado main.py primero."
        )
    
    print(f"\n🔍 Analizando píxeles de {fecha_rango}...")
    print(f"   - Umbral actual: H < {ENTROPY_THRESHOLD}")
    print(f"   - Zona gris: [{ENTROPY_THRESHOLD - 0.3:.2f}, {ENTROPY_THRESHOLD + 0.3:.2f}]")
    
    # Leer rasters
    with rasterio.open(new_urban_tif) as src:
        new_urban_data = src.read(1)
        transform = src.transform
        crs = src.crs
    
    with rasterio.open(entropy_t2_tif) as src:
        entropy_data = src.read(1)
    
    # Extraer píxeles de nueva área construida
    mask = new_urban_data > 0
    entropies = entropy_data[mask]
    
    # Calcular estadísticas generales
    print(f"\n📊 Estadísticas generales:")
    print(f"   - Total píxeles detectados: {len(entropies):,}")
    print(f"   - Entropía mínima: {np.min(entropies):.3f}")
    print(f"   - Entropía máxima: {np.max(entropies):.3f}")
    print(f"   - Entropía media: {np.mean(entropies):.3f}")
    print(f"   - Entropía mediana: {np.median(entropies):.3f}")
    print(f"   - Desv. estándar: {np.std(entropies):.3f}")
    
    # Percentiles
    percentiles = [10, 25, 50, 75, 90]
    print(f"\n📈 Percentiles:")
    for p in percentiles:
        val = np.percentile(entropies, p)
        print(f"   - P{p}: {val:.3f}")
    
    # Contar píxeles por categoría
    very_confident = np.sum(entropies < 1.0)
    confident = np.sum((entropies >= 1.0) & (entropies < ENTROPY_THRESHOLD))
    uncertain = np.sum(entropies >= ENTROPY_THRESHOLD)
    
    print(f"\n📋 Distribución por confianza:")
    print(f"   - Muy confiable (H < 1.0): {very_confident:,} ({very_confident/len(entropies)*100:.1f}%)")
    print(f"   - Confiable (1.0 ≤ H < {ENTROPY_THRESHOLD}): {confident:,} ({confident/len(entropies)*100:.1f}%)")
    print(f"   - Descartado (H ≥ {ENTROPY_THRESHOLD}): {uncertain:,} ({uncertain/len(entropies)*100:.1f}%)")
    
    # Identificar píxeles en "zona gris" (cerca del umbral)
    margin = 0.3
    edge_mask = (entropies >= ENTROPY_THRESHOLD - margin) & (entropies <= ENTROPY_THRESHOLD + margin)
    edge_entropies = entropies[edge_mask]
    
    print(f"\n⚠️ Píxeles en zona gris [{ENTROPY_THRESHOLD - margin:.2f}, {ENTROPY_THRESHOLD + margin:.2f}]:")
    print(f"   - Total: {len(edge_entropies):,} ({len(edge_entropies)/len(entropies)*100:.1f}%)")
    print(f"   - Rango: [{np.min(edge_entropies):.3f}, {np.max(edge_entropies):.3f}]")
    
    # Recomendaciones
    print(f"\n💡 Recomendaciones:")
    p50 = np.percentile(entropies, 50)
    p75 = np.percentile(entropies, 75)
    
    if ENTROPY_THRESHOLD < p50:
        print(f"   ⚠️ Umbral actual ({ENTROPY_THRESHOLD}) es MUY CONSERVADOR")
        print(f"      Sugerencia: Aumentar a ~{p50:.2f} (retiene ~50%)")
    elif ENTROPY_THRESHOLD > p75:
        print(f"   ⚠️ Umbral actual ({ENTROPY_THRESHOLD}) es POCO RESTRICTIVO")
        print(f"      Sugerencia: Reducir a ~{p50:.2f} (filtra más falsos positivos)")
    else:
        print(f"   ✅ Umbral está en rango razonable")
        print(f"      Alternativas:")
        print(f"      - Más conservador: {p50:.2f} (retiene ~50%)")
        print(f"      - Más permisivo: {p75:.2f} (retiene ~25%)")
    
    # Guardar CSV si se especifica
    if output_csv:
        # Crear DataFrame con píxeles de zona gris
        print(f"\n💾 Guardando análisis de zona gris en: {output_csv}")
        
        # Obtener coordenadas de píxeles en zona gris
        y_coords, x_coords = np.where(mask)
        edge_indices = np.where(edge_mask)[0]
        
        edge_data = []
        for idx in edge_indices:
            y, x = y_coords[idx], x_coords[idx]
            lon, lat = rasterio.transform.xy(transform, y, x)
            edge_data.append({
                'pixel_y': y,
                'pixel_x': x,
                'longitude': lon,
                'latitude': lat,
                'entropy': entropies[idx],
                'status': 'kept' if entropies[idx] < ENTROPY_THRESHOLD else 'filtered',
                'distance_to_threshold': abs(entropies[idx] - ENTROPY_THRESHOLD)
            })
        
        df_edge = pd.DataFrame(edge_data)
        df_edge = df_edge.sort_values('distance_to_threshold')
        df_edge.to_csv(output_csv, index=False)
        print(f"   ✅ {len(df_edge)} píxeles guardados")
    
    return entropies


def compare_thresholds(anio: int, mes: int, thresholds: list = None):
    """
    Compara tasas de retención con diferentes umbrales.
    
    Args:
        anio: Año del análisis
        mes: Mes del análisis
        thresholds: Lista de umbrales a comparar (default: [1.5, 1.8, 2.0, 2.2, 2.5])
    """
    if thresholds is None:
        thresholds = [1.5, 1.8, 2.0, 2.2, 2.5]
    
    # Rutas a los archivos
    fecha_rango = f"{anio}_{mes:02d}"
    base_dir = os.path.join(BASE_PATH, "urban_sprawl", "outputs", fecha_rango)
    dw_dir = os.path.join(base_dir, "dw")
    
    new_urban_tif = os.path.join(dw_dir, "new_urban.tif")
    entropy_t2_tif = os.path.join(dw_dir, "entropy_t2.tif")
    
    # Leer datos
    with rasterio.open(new_urban_tif) as src:
        new_urban_data = src.read(1)
    
    with rasterio.open(entropy_t2_tif) as src:
        entropy_data = src.read(1)
    
    mask = new_urban_data > 0
    entropies = entropy_data[mask]
    total = len(entropies)
    
    print(f"\n📊 Comparación de umbrales para {fecha_rango}:")
    print(f"   Total píxeles detectados: {total:,}\n")
    print(f"{'Umbral':<10} {'Retenidos':<12} {'Descartados':<12} {'% Retención':<12}")
    print("-" * 50)
    
    for threshold in thresholds:
        kept = np.sum(entropies < threshold)
        filtered = total - kept
        retention = kept / total * 100
        
        status = "← ACTUAL" if abs(threshold - ENTROPY_THRESHOLD) < 0.01 else ""
        print(f"{threshold:<10.2f} {kept:<12,} {filtered:<12,} {retention:<12.1f}% {status}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Calibración manual de umbral de entropía de Shannon"
    )
    parser.add_argument("--anio", type=int, required=True, help="Año (YYYY)")
    parser.add_argument("--mes", type=int, required=True, help="Mes (1-12)")
    parser.add_argument(
        "--csv", 
        type=str, 
        help="Ruta para guardar CSV de píxeles en zona gris (opcional)"
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Comparar diferentes umbrales"
    )
    
    args = parser.parse_args()
    
    try:
        # Análisis principal
        analyze_entropy_edge_cases(args.anio, args.mes, args.csv)
        
        # Comparación de umbrales (opcional)
        if args.compare:
            print("\n" + "="*60)
            compare_thresholds(args.anio, args.mes)
        
        print("\n" + "="*60)
        print("✅ Calibración completada")
        print("\n💡 Pasos siguientes:")
        print("   1. Revisar las estadísticas anteriores")
        print("   2. Si es necesario, ajustar ENTROPY_THRESHOLD en src/config.py")
        print("   3. Re-ejecutar pipeline: python main.py --anio YYYY --mes MM")
        
    except FileNotFoundError as e:
        print(f"\n❌ Error: {e}")
        print("\n💡 Asegúrate de:")
        print("   1. Haber ejecutado main.py primero")
        print("   2. Esta rama entropy_validation SIEMPRE calcula entropía")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error inesperado: {e}")
        sys.exit(1)
