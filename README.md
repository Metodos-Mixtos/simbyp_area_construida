# Urban Sprawl - SIMBYP Área Construida

Análisis de expansión urbana para Bogotá utilizando Google Earth Engine - Dynamic World.

## Descripción

Este proyecto analiza la expansión urbana mensual en el área de Bogotá mediante el procesamiento de imágenes satelitales de Dynamic World. Genera reportes con mapas interactivos, estadísticas y análisis de intersecciones con áreas protegidas (SAC, Reserva de Cerros Orientales y Estructura Ecológica Principal).

## Características

- Procesamiento automatizado de imágenes satelitales Dynamic World
- Análisis de expansión urbana mensual
- **Validación espectral con NDBI (Sentinel-2)** para confirmar construcciones reales
- Generación de mapas interactivos con Sentinel-2
- Cálculo de estadísticas de área construida
- Análisis de intersecciones con áreas protegidas
- Generación automática de reportes HTML

## Metodología: Validación NDBI

### ¿Qué es NDBI?

El **Índice de Diferencia Normalizada de Construcción (NDBI)** combina las bandas de infrarrojo cercano (NIR, B8) e infrarrojo de onda corta (SWIR, B11) de Sentinel-2 para identificar superficies construidas y se calcula de la siguente manera:

```
NDBI = (SWIR - NIR) / (SWIR + NIR) = (B11 - B8) / (B11 + B8)
```

- **NDBI ≥ Umbral establecido**: Alta probabilidad de construcción 
- **NDBI < 0 **: Vegetación, suelo desnudo, agua u otros usos no urbanos

### Pipeline Dual de Validación

1. **Detección con Dynamic World**: Identifica píxeles donde `DW_T1 < 0.2 AND DW_T2 > threshold`
   - `new_urban`: threshold = 0.5 (expansión urbana estándar)
   - `new_urban_strict`: threshold = 0.7 (expansión urbana estricta)

2. **Validación NDBI (Sentinel-2)**: Filtra con imágenes de los últimos 60 días
   - **CONFIRMADAS** (NDBI ≥ Umbral establecido): Se reportan en HTML
   - **NO CONFIRMADAS** (NDBI < Umbral establecido o sin datos): Se guardan para análisis interno

### Ventajas

- Reduce falsos positivos: solo reporta construcciones espectralmente validadas
- Mayor precisión: combina clasificación temporal (DW) con firma espectral (NDBI)
- Coherencia temporal: ventana de 60 días mantiene especificidad mensual



- Python 3.8+
- Cuenta de Google Cloud Platform con Google Earth Engine habilitado
- Credenciales de servicio de Google Cloud
- Acceso a Google Cloud Storage

## Instalación

1. Clonar el repositorio:
```bash
git clone <repository-url>
cd simbyp_area_construida
```

2. Instalar dependencias:
```bash
pip install -r requirement.txt
```

3. Configurar variables de entorno:
   - Creación `.env` con información de paths
   - Completar las credenciales de Google Cloud
   - Ajustar las rutas de GCS según sea necesario


## Uso

Ejecutar el análisis para un mes específico:

```bash
python main.py --anio 2025 --mes 12
```

### Parámetros

- `--anio`: Año del análisis en formato YYYY (requerido)
- `--mes`: Mes del análisis en formato numérico 1-12 (requerido)

### Ejemplo

```bash
# Analizar diciembre 2025
python main.py --anio 2025 --mes 12
```

## Estructura del Proyecto

```
simbyp_area_construida/
├── main.py                    # Script principal
├── requirement.txt            # Dependencias del proyecto
├── .env                       # Variables de entorno
├── src/
│   ├── config.py             # Configuración y variables de entorno
│   ├── aux_utils.py          # Utilidades auxiliares
│   ├── maps_utils.py         # Generación de mapas
│   ├── pipeline_utils.py     # Pipeline de procesamiento
│   └── stats_utils.py        # Cálculo de estadísticas
└── reporte/
    ├── render_report.py      # Renderización de reportes
    └── report_template.html  # Plantilla HTML del reporte
```

## Salidas

El script genera las siguientes salidas en `BASE_PATH/urban_sprawl/outputs/YYYY_MM/`:

- **dynamic_world/**: Imágenes procesadas de Dynamic World
- **intersections/**: GeoJSON de intersecciones con áreas protegidas
- **stats/**: Estadísticas en formato JSON y CSV
- **maps/**: Mapas interactivos en HTML
- **reportes/**: Reportes finales en PDF

## Seguridad

- **NUNCA** subir el archivo `.env` al repositorio
- Mantener las credenciales de Google Cloud seguras
- Usar service accounts con permisos mínimos necesarios
- Rotar credenciales regularmente

## Despliegue en Google Cloud Run

Este proyecto está configurado para desplegarse como un Cloud Run Job en Google Cloud Platform.

### Requisitos Previos

- Google Cloud CLI (`gcloud`) instalado
- Docker instalado
- Proyecto de GCP con APIs habilitadas (Cloud Run, Cloud Build, Container Registry)
- Credenciales de Google Cloud con permisos adecuados

### Despliegue Rápido

```bash
chmod +x deploy.sh
./deploy.sh your-gcp-project us-central1
```

El script automáticamente:
1. Construye la imagen Docker
2. La sube a Google Container Registry
3. Crea un Cloud Run Job

### Ejecutar el Job

```bash
# Con parámetros por defecto (2025-03)
gcloud run jobs execute simbyp-analysis --region us-central1

# Con parámetros personalizados
gcloud run jobs execute simbyp-analysis --region us-central1 -- --anio 2025 --mes 4
```

### Configuración Manual

Si prefieres hacer el despliegue manualmente:

```bash
# Establecer proyecto
gcloud config set project your-gcp-project

# Habilitar APIs necesarias
gcloud services enable run.googleapis.com cloudbuild.googleapis.com containerregistry.googleapis.com

# Construir imagen
docker build -t gcr.io/your-gcp-project/simbyp-analysis:latest .

# Empujar a Container Registry
docker push gcr.io/your-gcp-project/simbyp-analysis:latest

# Crear Cloud Run Job
gcloud run jobs create simbyp-analysis \
  --image gcr.io/your-gcp-project/simbyp-analysis:latest \
  --region us-central1 \
  --memory 4Gi \
  --cpu 2 \
  --task-timeout 3600s
```

### Archivos de Despliegue

- **Dockerfile** - Imagen Docker del proyecto
- **.dockerignore** - Archivos excluidos de la imagen
- **cloudbuild.yaml** - Configuración de Cloud Build para CI/CD
- **deploy.sh** - Script de despliegue automatizado

## Desarrollo Local

### Configuración del Entorno

```bash
# Crear entorno virtual
python -m venv .venv

# Activar entorno
source .venv/bin/activate  # En macOS/Linux
# o
.venv\Scripts\activate  # En Windows

# Instalar dependencias
pip install -r requirements.txt
```

### Ejecutar Localmente

```bash
# Configurar variables de entorno
cp .env.example .env
# Editar .env con tus credenciales

# Ejecutar análisis
python main.py --anio 2025 --mes 3
```

## Solución de Problemas

### Error de autenticación con Earth Engine

```bash
# Autenticar con Google Cloud
gcloud auth application-default login

# Autenticar con Earth Engine
earthengine authenticate
```

### Timeout en Cloud Run

Aumenta los recursos del job:

```bash
gcloud run jobs update simbyp-analysis \
  --region us-central1 \
  --memory 8Gi \
  --task-timeout 7200s
```

### Problemas con dependencias geoespaciales

Asegúrate de que el Dockerfile instala todas las dependencias del sistema necesarias (GDAL, GEOS, PROJ).

## Contribución

Para contribuir al proyecto:

1. Fork el repositorio
2. Crea una rama con tu feature (`git checkout -b feature/AmazingFeature`)
3. Commit tus cambios (`git commit -m 'Add AmazingFeature'`)
4. Push a la rama (`git push origin feature/AmazingFeature`)
5. Abre un Pull Request

## Licencia

Este proyecto está bajo la licencia MIT. Ver `LICENSE` para más detalles.

## Contacto

Para preguntas o soporte, contacta a través de las issues del repositorio.

