import os
from dotenv import load_dotenv

load_dotenv()

# Define AOI_PATH and other paths; must be GCS paths
AOI_PATH = os.getenv("AOI_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/aestudio_bogota.geojson")
SAC_PATH = os.getenv("SAC_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/sac.geojson")
RESERVA_PATH = os.getenv("RESERVA_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/reserva.geojson")
EEP_PATH = os.getenv("EEP_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/eep.geojson")
UPL_PATH = os.getenv("UPL_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/upl.geojson")  # Example; adjust as needed
HEADER_IMG1_PATH = os.getenv("HEADER_IMG1_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/asi_4.png")  # Example; adjust as needed
HEADER_IMG2_PATH = os.getenv("HEADER_IMG2_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/bogota_4.png")  # Example; adjust as needed
FOOTER_IMG_PATH = os.getenv("FOOTER_IMG_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/secre_5.png")  # Example; adjust as needed
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")
BASE_PATH = os.getenv("BASE_PATH", "/tmp/urban_sprawl")  # For local fallbacks

