import os
from dotenv import load_dotenv

load_dotenv("dot_env_content.env")

# === Paths base ===
BASE_PATH = os.getenv("INPUTS_PATH")
GOOGLE_CLOUD_PROJECT = os.getenv("GCP_PROJECT")

# === Archivos de entrada ===
AOI_PATH = os.path.join(BASE_PATH, "area_estudio", "urban_sprawl", "aestudio_bogota.geojson")
LOGO_PATH = os.path.join(BASE_PATH, "Logo_SDP.jpeg")
SAC_PATH = os.path.join(BASE_PATH, "area_estudio", "urban_sprawl", "situacion_amb_conflictiva.geojson")
RESERVA_PATH = os.path.join(BASE_PATH, "area_estudio", "urban_sprawl", "reserva_cerrosorientales.geojson")
EEP_PATH = os.path.join(BASE_PATH, "area_estudio", "urban_sprawl", "estructuraecologicaprincipal", "EstructuraEcologicaPrincipal.shp")
UPL_PATH = os.path.join(BASE_PATH, "area_estudio", "urban_sprawl", "unidadplaneamientolocal", "UnidadPlaneamientoLocal.shp")

