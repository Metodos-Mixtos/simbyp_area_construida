import ee
import geopandas as gpd
import os
import geemap
import calendar
from pathlib import Path
from shapely.geometry import Polygon, MultiPolygon
from datetime import datetime
from google.cloud import storage
from google.auth import _helpers
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.api_core.exceptions import NotFound
import tempfile
import shutil
import json

# Temp data directory - carpeta temporal dentro del repositorio
TEMP_DATA_DIR = Path(__file__).parent.parent / "temp_data"
TEMP_DATA_DIR.mkdir(exist_ok=True)

def download_gcs_to_temp(path):
    """Download file from GCS to temp file or dir, return local path."""
    os.makedirs(str(TEMP_DATA_DIR), exist_ok=True)  # Ensure temp_data exists
    if not str(path).startswith("gs://"):
        return str(path)
    _, rest = str(path).split("gs://", 1)
    bucket_name, blob_path = rest.split("/", 1)
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    suffix = Path(path).suffix.lower()
    if suffix == '.shp':
        # Shapefile, download all related files to a temp dir in temp_data
        stem = Path(blob_path).stem
        dir_path = Path(blob_path).parent
        temp_dir = tempfile.mkdtemp(dir=str(TEMP_DATA_DIR))
        shapefile_extensions = ['.shp', '.shx', '.dbf', '.prj', '.cpg', '.sbn', '.sbx']
        try:
            for ext in shapefile_extensions:
                blob_name = f"{stem}{ext}"
                blob_full = str(dir_path / blob_name)
                current_blob = bucket.blob(blob_full)
                if current_blob.exists():
                    local_file = os.path.join(temp_dir, blob_name)
                    current_blob.download_to_filename(local_file)
        except NotFound:
            raise FileNotFoundError(f"GCS shapefile not found: gs://{bucket_name}/{dir_path}/{stem}.*")
        return os.path.join(temp_dir, f"{stem}.shp")
    else:
        # Single file
        blob = bucket.blob(blob_path)
        with tempfile.NamedTemporaryFile(suffix=suffix, dir=str(TEMP_DATA_DIR), delete=False) as tmp:
            try:
                blob.download_to_file(tmp)
            except NotFound:
                raise FileNotFoundError(f"GCS file not found: gs://{bucket_name}/{blob_path}")
            tmp_path = tmp.name
        return tmp_path

def authenticate_gee(project=None):
    """Autenticar con Google Earth Engine usando credenciales de cuenta de servicio."""
    if not project:
        raise ValueError("GOOGLE_CLOUD_PROJECT not set. Please check your .env file or environment variables.")
    
    # Get credentials path from environment variable
    credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    
    if credentials_path and os.path.exists(credentials_path):
        try:
            print(f"🔐 Autenticando con Earth Engine usando credenciales de servicio...")
            print(f"   Archivo de credenciales: {credentials_path}")
            print(f"   Proyecto: {project}")
            
            # Load service account credentials
            with open(credentials_path) as f:
                credentials_dict = json.load(f)
            
            # Create credentials from service account
            credentials = service_account.Credentials.from_service_account_info(
                credentials_dict,
                scopes=[
                    'https://www.googleapis.com/auth/cloud-platform',
                    'https://www.googleapis.com/auth/earthengine'
                ]
            )
            
            # Initialize Earth Engine with service account credentials
            ee.Initialize(
                credentials=credentials,
                project=project,
                opt_url='https://earthengine-highvolume.googleapis.com'
            )
            print("✅ Autenticación exitosa con Earth Engine")
            
        except FileNotFoundError:
            raise FileNotFoundError(f"Archivo de credenciales no encontrado: {credentials_path}")
        except json.JSONDecodeError:
            raise ValueError(f"Archivo de credenciales inválido (no es JSON válido): {credentials_path}")
        except Exception as e:
            print(f"⚠️ Error con autenticación de servicio: {e}")
            print("Intentando autenticación alternativa...")
            try:
                ee.Initialize(project=project)
            except Exception as init_error:
                raise RuntimeError(
                    f"No se pudo autenticar con Earth Engine.\n"
                    f"Error de servicio: {e}\n"
                    f"Error de inicialización: {init_error}\n"
                    f"Por favor verifica que GOOGLE_APPLICATION_CREDENTIALS apunta a un archivo JSON válido."
                )
    else:
        # No credentials file, try default authentication
        print("⚠️ GOOGLE_APPLICATION_CREDENTIALS no configurado o archivo no existe")
        print("   Intentando autenticación por defecto...")
        try:
            ee.Initialize(project=project)
            print("✅ Autenticación por defecto exitosa")
        except ee.EEException as e:
            raise RuntimeError(
                f"No se pudo autenticar con Earth Engine.\n"
                f"Error: {e}\n"
                f"Por favor configura GOOGLE_APPLICATION_CREDENTIALS en tu archivo .env"
            )

def load_geometry(path):
    """Cargar geometría desde un archivo vectorial en GCS y convertir a ee.Geometry."""
    if not str(path).startswith("gs://"):
        raise ValueError(f"Path must be a GCS URI starting with 'gs://': {path}")
    local_path = download_gcs_to_temp(path)
    gdf = gpd.read_file(local_path)
    # Clean up temp file
    temp_dir = os.path.dirname(local_path)
    # Only delete if it's a subdirectory within TEMP_DATA_DIR, not TEMP_DATA_DIR itself
    if temp_dir != str(TEMP_DATA_DIR) and os.path.isdir(temp_dir):
        try:
            shutil.rmtree(temp_dir)
        except PermissionError as e:
            print(f"⚠️ No se pudo eliminar el directorio temporal {temp_dir}: {e}. Continuando...")
    elif os.path.isfile(local_path):
        # Delete individual file
        try:
            os.unlink(local_path)
        except PermissionError as e:
            print(f"⚠️ No se pudo eliminar el archivo temporal {local_path}: {e}. Continuando...")
    if len(gdf) == 0:
        raise ValueError("El archivo de geometría está vacío.")
    geom_union = gdf.unary_union
    if isinstance(geom_union, Polygon):
        coords = list(geom_union.exterior.coords)
        geometry = ee.Geometry.Polygon(coords)
    elif isinstance(geom_union, MultiPolygon):
        polygons = [ee.Geometry.Polygon(list(poly.exterior.coords)) for poly in geom_union.geoms]
        geometry = ee.Geometry.MultiPolygon(polygons)
    else:
        raise ValueError("La geometría no es Polygon ni MultiPolygon.")
    return geometry
    geom_union = gdf.unary_union
    if isinstance(geom_union, Polygon):
        coords = list(geom_union.exterior.coords)
        geometry = ee.Geometry.Polygon(coords)
    elif isinstance(geom_union, MultiPolygon):
        polygons = [ee.Geometry.Polygon(list(poly.exterior.coords)) for poly in geom_union.geoms]
        geometry = ee.Geometry.MultiPolygon(polygons)
    else:
        raise ValueError("La geometría no es Polygon ni MultiPolygon.")
    return geometry

def export_image(image, geometry, output_path):
    """Exportar imagen de EE a archivo local."""
    print(f"💾 Exportando a {output_path}")
    geemap.download_ee_image(
        image=image,
        filename=output_path,
        region=geometry.bounds(),
        scale=10,
        crs="EPSG:4326"  # EE trabaja en WGS84, conversión a EPSG:9377 se hace después
    )
    
def make_relative_path(path, base_dir):
    """Convertir una ruta absoluta a relativa respecto a base_dir."""
    path = Path(path)
    base_dir = Path(base_dir)
    try:
        return str(path.relative_to(base_dir))
    except ValueError:
        return str(os.path.relpath(path, base_dir))

def set_dates(mes, anio):
    """Establecer las fechas finales del mes actual y el mes previo."""
    if mes > 1:
        prev_year = anio
        prev_month = mes - 1
    else:
        prev_year = anio - 1
        prev_month = 12
    last_day_prev = datetime(prev_year, prev_month, calendar.monthrange(prev_year, prev_month)[1])
    last_day_curr = datetime(anio, mes, calendar.monthrange(anio, mes)[1])
    return last_day_curr, last_day_prev

def cleanup_temp_data():
    """Limpiar todos los archivos temporales en TEMP_DATA_DIR."""
    if TEMP_DATA_DIR.exists():
        for item in TEMP_DATA_DIR.iterdir():
            try:
                if item.is_file():
                    item.unlink()
                    print(f"🗑️  Eliminado archivo temporal: {item.name}")
                elif item.is_dir():
                    shutil.rmtree(item)
                    print(f"🗑️  Eliminado directorio temporal: {item.name}")
            except Exception as e:
                print(f"⚠️  No se pudo eliminar {item.name}: {e}")
        print(f"✅ Carpeta temp_data limpiada")