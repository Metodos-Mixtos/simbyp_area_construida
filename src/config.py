import os
from pathlib import Path
from dotenv import load_dotenv

# Buscar .env en la raíz del proyecto (3 niveles arriba: src -> urban_sprawl -> bosques-bog -> raíz)
load_dotenv()

# === Paths base ===
BASE_PATH = os.getenv("INPUTS_PATH")
GOOGLE_CLOUD_PROJECT = os.getenv("GCP_PROJECT")

# === Archivos de entrada ===
AOI_PATH = os.path.join(BASE_PATH, "area_estudio", "urban_sprawl", "aestudio_bogota.geojson")
HEADER_IMG1_PATH = os.path.join(BASE_PATH, "area_estudio", "asi_4.png")
HEADER_IMG2_PATH = os.path.join(BASE_PATH, "area_estudio", "bogota_4.png")
FOOTER_IMG_PATH = os.path.join(BASE_PATH, "area_estudio", "secre_5.png")
SAC_PATH = os.path.join(BASE_PATH, "area_estudio", "urban_sprawl", "situacion_amb_conflictiva.geojson")
RESERVA_PATH = os.path.join(BASE_PATH, "area_estudio", "urban_sprawl", "reserva_cerrosorientales.geojson")
EEP_PATH = os.path.join(BASE_PATH, "area_estudio", "urban_sprawl", "estructuraecologicaprincipal", "EstructuraEcologicaPrincipal.shp")
UPL_PATH = os.path.join(BASE_PATH, "area_estudio", "urban_sprawl", "unidadplaneamientolocal", "UnidadPlaneamientoLocal.shp")

