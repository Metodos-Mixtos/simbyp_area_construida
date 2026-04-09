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
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")

# GCS output configuration
GCS_OUTPUT_BUCKET = "desarrollo-reportes-simbyp"
GCS_OUTPUT_PREFIX = "urban_sprawl/Entropy-validation"

# ============================================================================
# ENTROPÍA - Parámetros de validación por confianza de clasificación
# ============================================================================
# NOTA: Esta rama SIEMPRE calcula entropía para validación de expansión urbana
# Ver: docs/ENTROPY_VALIDATION.md para calibración del umbral

# Umbral de entropía Shannon (bits)
# INTERPRETACIÓN:
#   H ≈ 0.0: Certidumbre máxima (95%+ en una clase)
#   H < 1.0: Alta certidumbre (80-90% en una clase) ← MUY CONSERVADOR
#   H < 2.0: Confianza moderada-alta (60-70% en clase dominante) ← MODERADO
#   H < 2.5: Menos restrictivo (50-60% en clase dominante)
#   H ≈ 3.17: Máxima incertidumbre (todas las clases equiprobables)
#
# Rango típico: 1.0 - 2.5
# DEFAULT: 2.0 (balancea detección vs. precisión)
# Para calibrar: python calibrate_entropy.py --anio YYYY --mes MM --compare
ENTROPY_THRESHOLD = 2.0  # Cambiar a 1.0 para ser MÁS conservador

