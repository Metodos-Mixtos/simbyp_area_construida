import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# Base path para outputs locales - apunta a temp_data/ del repositorio
BASE_PATH = str(Path(__file__).parent.parent / "temp_data")

# Define AOI_PATH and other paths; must be GCS paths
AOI_PATH = os.getenv("AOI_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/aoi_bog_ssum.geojson")
SAC_PATH = os.getenv("SAC_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/sac.geojson")
RESERVA_PATH = os.getenv("RESERVA_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/reserva.geojson")
EEP_PATH = os.getenv("EEP_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/eep.geojson")
UPL_PATH = os.getenv("UPL_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/area_estudio/urban_sprawl/upl.geojson")  
HEADER_IMG1_PATH = os.getenv("HEADER_IMG1_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/SDP Logos/asi_4.png") 
HEADER_IMG2_PATH = os.getenv("HEADER_IMG2_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/SDP Logos/bogota_4.png")  
FOOTER_IMG_PATH = os.getenv("FOOTER_IMG_PATH", "gs://material-estatico-sdp/SIMBYP_DATA/SDP Logos/secre_5.png")  
GOOGLE_CLOUD_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT")

