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
# 7. Extrae polígonos con cambio significativo (percentil configurable, por defecto 99.5)
# 8. Filtra por NDVI < threshold para excluir vegetación

# Ruta a construcciones existentes (Google Cloud Storage)
# 
# CONST_repaired_dissolve.gpkg: 1 polígono ÚNICO fusionado con buffer de 3m aplicado
#   - 2.4M construcciones fusionadas en 1 polígono
#   - Buffer de 3m aplicado en CRS proyectado (EPSG:3116 Bogotá)
#   - 1000x más rápido que construcciones individuales
#   - Más conservador: NO detecta construcciones en espacios internos
#   - Uso: Pipeline automatizado, procesamiento mensual en producción
#
CONSTRUCCIONES_GPKG_DISSOLVE = "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/CONST_buffer3m_dissolve_EPSG3116.gpkg"

# Malla vial de Bogotá (para exclusión en análisis)
MALLA_VIAL_GPKG = "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/MallaVialBog26.gpkg"

# Complejo aeroportuario El Dorado (para exclusión en análisis)
AEROPUERTO_GPKG = "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/complejo_aeroportuario_dorado.gpkg"

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
# NOTA: Con polígono disuelto MUY complejo, el buffer causa problemas de memoria/tiempo
# Alternativa: Usar buffer=0 y confiar en la resolución de 10m del raster
BUFFER_CONSTRUCCIONES_METROS = 0  # Sin buffer (optimización para polígono disuelto complejo)

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
NDVI_THRESHOLD = 0.1  # < 0.05 = sin vegetación (MÁS ESTRICTO - solo suelo/concreto)

# Filtro de cobertura de nubes Sentinel-2
# Porcentaje máximo de nubes permitido en imágenes
# Valores más bajos = imágenes más limpias pero menos disponibilidad
CLOUD_THRESHOLD = 35  # < 35% nubes por imagen

# Área mínima para polígonos detectados (metros cuadrados)
# Filtra ruido: píxeles sueltos y polígonos muy pequeños
# 100 m² = 1 píxel de 10m × 10m
# 200 m² = 2 píxeles (permite píxeles casi sueltos)
# 300 m² = 3 píxeles (elimina la mayoría de píxeles sueltos)
# 500 m² = 5 píxeles (más estricto)
MIN_AREA_M2 = 200  # Elimina polígonos < 300 m² (mínimo ~3 píxeles)

# Percentil de detección para cambios temporales
# Define qué tan "extremos" deben ser los cambios para detectarlos
# 99 = 1% superior (más inclusivo, más detecciones)
# 99.5 = 0.5% superior (más estricto, solo cambios muy significativos)
# 99.9 = 0.1% superior (muy estricto, cambios extremos)
DETECTION_PERCENTILE = 99
  # 0.5% superior de diferencias temporales
