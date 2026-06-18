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
GCS_OUTPUT_PREFIX = "urban_sprawl/SAR-test"

# Urban sprawl detection parameters
URB_PROB = 0.5  # Probabilidad umbral para detectar expansión urbana (rango: 0.0 a 1.0)

# ============================================
# SAR FILTER CONFIGURATION
# ============================================

# Habilitar/deshabilitar filtro SAR
USE_SAR_FILTER = True  # True = aplica filtro SAR, False = solo DW

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

# Configuración temporal SAR
SAR_LOOKBACK_T1_DAYS = 90   # t1: trimestral (90 días) - verificar que no había construcciones
SAR_LOOKBACK_T2_DAYS = 30   # t2: mensual (30 días) - verificar construcción actual

# Parámetros de clasificación urbana SAR
SAR_PARAMS = {
    # Umbrales de clasificación urbana (valores en dB)
    'vv_threshold': -12,        # VV > -12 dB sugiere superficies rugosas/urbanas
    'vh_threshold': -18,        # VH > -18 dB complementa detección urbana
    
    # Ratio VV/VH (en dB, ratio = VV - VH)
    'use_ratio': True,          # True = usa ratio VV/VH, False = solo umbrales individuales
    'vv_vh_ratio_min': 1.0,     # Ratio mínimo para áreas urbanas
    'vv_vh_ratio_max': 9.5,     # Ratio máximo para áreas urbanas
    
    # Filtros morfológicos (limpieza de ruido)
    'erosion_size': 3,          # Iteraciones de erosión (elimina píxeles aislados)
    'dilation_size': 2,         # Iteraciones de dilatación (rellena huecos)
    
    # Área mínima de clusters
    'min_cluster_pixels': 5,    # Mínimo 5 píxeles (500 m² a 10m resolución)
    'min_cluster_area_ha': 0.05 # 0.05 ha = 500 m²
}


