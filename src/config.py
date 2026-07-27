import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# Base path para outputs locales - apunta a temp_data/ del repositorio
BASE_PATH = str(Path(__file__).parent.parent / "temp_data")

# Define AOI_PATH and other paths; must be GCS paths
AOI_PATH = "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/aoi_bog_ssum.geojson"
SAC_PATH = "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/sac.geojson"
RESERVA_PATH = "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/reserva.geojson"
EEP_PATH = "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/eep.geojson"
UPL_PATH = "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/upl.geojson"
HEADER_IMG1_PATH = "gs://material-estatico-sdp/SIMBYP_DATA/SDP Logos/asi_4.png"
HEADER_IMG2_PATH = "gs://material-estatico-sdp/SIMBYP_DATA/SDP Logos/bogota_4.png"
FOOTER_IMG_PATH = "gs://material-estatico-sdp/SIMBYP_DATA/SDP Logos/secre_5.png"  
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "bosques-bogota-416214")

# GCS output configuration
GCS_OUTPUT_BUCKET = "desarrollo-reportes-simbyp"
GCS_OUTPUT_PREFIX = "urban_sprawl/VV_difference"

# Urban sprawl detection parameters
URB_PROB = 0.5  # Probabilidad umbral para detectar expansión urbana (rango: 0.0 a 1.0)

# ============================================
# CONSTRUCCIONES NUEVAS - CONFIGURACIÓN
# ============================================

# Metodología: Sentinel-1 VV Temporal + NDVI
# Pipeline:
# 1. Descarga construcciones existentes desde:
#    a) GPKG local (si existe): C:\Users\Laura Tamayo\Downloads\const.gpkg.0626\CONST_repaired.gpkg
#    b) Catastro Bogotá REST API (fallback): https://serviciosgis.catastrobogota.gov.co/arcgis/rest/services/catastro/construccion/MapServer/0
# 2. GPKG disuelto para visualización en mapa: C:/Users/Laura Tamayo/Downloads/const.gpkg.0626/Const_dissolve.gpkg
# 3. Aplica buffer de 3m para excluir bordes de construcciones existentes
# 4. Descarga Sentinel-1 VV para mes completo anterior y mes completo actual
#    Ejemplo: abril 2025 → Anterior: 2025-03-01 a 2025-03-31, Actual: 2025-04-01 a 2025-04-30
# 5. Aplica filtro Lee 5×5 para reducción de speckle
# 6. Calcula diferencia temporal VV (actual - anterior)
# 7. Extrae polígonos con cambio significativo (P99 percentil)
# 8. Filtra por NDVI < threshold para excluir vegetación

# Rutas a archivos locales de construcciones
# 
# CONST_repaired.gpkg: 2.4M construcciones INDIVIDUALES
#   - Más preciso: Detecta construcciones en huecos entre edificios
#   - Más lento: 100-1000x tiempo de procesamiento
#   - Usa en: Análisis detallados, zonas específicas, replicar notebook
#
# CONST_repaired_dissolve.gpkg: 1 polígono ÚNICO fusionado
#   - Más rápido: 1000x velocidad (producción mensual)
#   - Más conservador: NO detecta construcciones en espacios internos
#   - Usa en: Pipeline automatizado, procesamiento masivo
#
CONSTRUCCIONES_GPKG_LOCAL = r"C:\Users\Laura Tamayo\Downloads\const.gpkg.0626\CONST_repaired.gpkg"
CONSTRUCCIONES_GPKG_DISSOLVE = r"C:\Users\Laura Tamayo\Downloads\const.gpkg.0626\CONST_repaired_dissolve.gpkg"

# Credenciales Sentinel Hub (Copernicus Dataspace)
# Almacenadas en GCP Secret Manager para seguridad
# Obtener en: https://dataspace.copernicus.eu/

def get_secret_from_gcp(secret_id, project_id=None):
    """
    Obtiene un secreto desde GCP Secret Manager
    
    Args:
        secret_id: ID del secreto (ej: 'sentinelhub-client-id')
        project_id: ID del proyecto GCP (si None, usa GOOGLE_CLOUD_PROJECT)
    
    Returns:
        str: Valor del secreto
    """
    try:
        from google.cloud import secretmanager
        
        if project_id is None:
            project_id = GOOGLE_CLOUD_PROJECT
        
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode('UTF-8')
    except Exception as e:
        print(f"⚠️ No se pudo leer secreto '{secret_id}' desde GCP: {e}")
        # Fallback: intentar leer desde variable de entorno
        env_var = secret_id.upper().replace('-', '_')
        return os.getenv(env_var, "")

# Leer credenciales desde GCP Secret Manager
SENTINELHUB_CLIENT_ID = get_secret_from_gcp("sentinelhub-client-id")
SENTINELHUB_CLIENT_SECRET = get_secret_from_gcp("sentinelhub-client-secret")

# ============================================
# PARÁMETROS DE DETECCIÓN
# ============================================

# Buffer para construcciones existentes (metros)
BUFFER_CONSTRUCCIONES_METROS = 3  # Excluye construcciones + 3m de borde

# Resolución espacial Sentinel-1 (metros)
SENTINEL1_RESOLUTION = 10  # 10m × 10m por píxel

# Período temporal de análisis
# Se compara mes completo actual vs mes completo anterior
# Ejemplo: abril 2025 (2025-04-01 a 2025-04-30) vs marzo 2025 (2025-03-01 a 2025-03-31)
SENTINEL1_LOOKBACK_DAYS = 30  # [DEPRECADO] No se usa - se usa mes completo

# Filtro NDVI para excluir vegetación
# NDVI = (NIR - Red) / (NIR + Red) usando Sentinel-2 B8/B4
# 
# Filtro NDVI para excluir vegetación
# NDVI = (NIR - Red) / (NIR + Red) usando Sentinel-2 B8/B4
# Solo se conservan polígonos con NDVI < threshold (sin vegetación)
# 
# Valores de referencia para Bogotá (2600 msnm):
# < 0.0    = agua, sombras profundas
# 0.0-0.05 = suelo desnudo, concreto, asfalto
# 0.05-0.1 = urbano denso, techos
# 0.1-0.3  = vegetación dispersa, jardines
# 0.3-0.5  = pastizales, vegetación moderada
# > 0.5    = bosque denso andino
NDVI_THRESHOLD = 0.1  # < 0.1 = sin vegetación significativa

# Filtro de cobertura de nubes Sentinel-2
# Porcentaje máximo de nubes permitido en imágenes
# Valores más bajos = imágenes más limpias pero menos disponibilidad
CLOUD_THRESHOLD = 35  # < 35% nubes por imagen
