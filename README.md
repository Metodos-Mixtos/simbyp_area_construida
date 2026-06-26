# Urban Sprawl - SIMBYP Área Construida

Análisis de expansión urbana para Bogotá utilizando Google Earth Engine - Dynamic World.

## Descripción

Este proyecto analiza la expansión urbana mensual en el área de Bogotá mediante el procesamiento de imágenes satelitales de Dynamic World. Genera reportes con mapas interactivos, estadísticas y análisis de intersecciones con áreas protegidas (SAC, Reserva de Cerros Orientales y Estructura Ecológica Principal).

## Características

- Procesamiento automatizado de imágenes satelitales Dynamic World
- **NUEVO: Filtro SAR de Sentinel-1 para validación de expansión urbana**
- Análisis de expansión urbana mensual
- Generación de mapas interactivos con Sentinel-2
- Cálculo de estadísticas de área construida
- Análisis de intersecciones con áreas protegidas
- Generación automática de reportes HTML

## Requisitos

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

## Filtro SAR (Sentinel-1)

### ¿Qué es y por qué usarlo?

El filtro SAR es una capa adicional de validación que utiliza datos de **Sentinel-1 SAR (Synthetic Aperture Radar)** para reducir falsos positivos en la detección de expansión urbana de Dynamic World.

**Ventajas:**
- Reduce falsos positivos causados por nubes, sombras o cambios temporales
- Detecta estructuras físicas reales (SAR penetra nubes)
- Valida construcciones usando retrodispersión VV/VH
- Se aplica solo en áreas detectadas por DW (ahorra unidades de procesamiento)

### Configuración

#### 1. Obtener credenciales de Copernicus Dataspace

1. Crear cuenta en [Copernicus Dataspace](https://dataspace.copernicus.eu/)
2. Ir a **Dashboard → User Settings → OAuth clients**
3. Crear nuevo OAuth client
4. Copiar `CLIENT_ID` y `CLIENT_SECRET`

#### 2. Configurar variables de entorno

Añadir al archivo `.env`:

```bash
# Credenciales Sentinel Hub (Copernicus Dataspace)
SENTINELHUB_CLIENT_ID=tu-client-id-aqui
SENTINELHUB_CLIENT_SECRET=tu-client-secret-aqui
```

#### 3. Habilitar/deshabilitar filtro SAR

En `src/config.py`:

```python
# Habilitar filtro SAR
USE_SAR_FILTER = True  # True = aplica filtro, False = solo DW

# Configuración temporal
SAR_LOOKBACK_T1_DAYS = 90   # t1: trimestral (verificar ausencia de construcciones)
SAR_LOOKBACK_T2_DAYS = 30   # t2: mensual (verificar construcción actual)
```

**Nota temporal:** Delta de 90 días entre el FIN de t1 y el INICIO de t2 para capturar construcciones progresivas.

### Parámetros SAR

Los parámetros de clasificación urbana SAR se configuran en `src/config.py`:

```python
SAR_PARAMS = {
    # Umbrales de clasificación (valores en dB)
    'vv_threshold': -12,        # VV > -12 dB indica superficies rugosas/urbanas
    'vh_threshold': -18,        # VH > -18 dB complementa detección
    
    # Ratio VV/VH
    'use_ratio': True,          # Usar ratio para mejorar precisión
    'vv_vh_ratio_min': 1.0,     # Ratio mínimo característico de áreas urbanas
    'vv_vh_ratio_max': 9.5,     # Ratio máximo
    
    # Filtros morfológicos
    'erosion_size': 3,          # Elimina píxeles aislados
    'dilation_size': 2,         # Rellena huecos pequeños
    
    # Área mínima
    'min_cluster_pixels': 5,    # 500 m² mínimo (5 píxeles a 10m)
    'min_cluster_area_ha': 0.05
}
```

### Flujo de procesamiento con SAR

```
1. Dynamic World → Detecta expansión inicial (mediana temporal)
                ↓
2. Intersecciones → Cruza con áreas protegidas
                ↓
3. Filtro SAR → Validación con Sentinel-1 (optimizado por tiles)
   ├─ Divide AOI en grid 12x12 (144 tiles para Bogotá)
   ├─ Procesa solo tiles con expansión DW
   ├─ Descarga SAR t1 y t2 (composición mediana)
   ├─ Clasifica áreas urbanas (VV, VH, ratio)
   ├─ Detecta expansión (t2 AND NOT t1)
   ├─ Vectoriza y combina tiles
   └─ Intersección geométrica DW ∩ SAR
                ↓
4. Estadísticas → Calcula áreas validadas
                ↓
5. Mapas y reportes → Visualización final (Sentinel-2 RGB mediana)
```

**Características clave:**
- **Composición mediana:** Reduce speckle (SAR) y nubes (óptico)
- **Grid adaptativo:** 12x12 para AOIs grandes, 8x8 para medianos, 4x4 para pequeños
- **Corrección terreno:** GAMMA0_TERRAIN con DEM Copernicus 30m

### Outputs con filtro SAR

Cuando el filtro SAR está activo, se generan archivos adicionales:

```
outputs/YYYY_MM/
├── intersections/
│   ├── new_urban_YYYY_MM_intersections.geojson              # DW original
│   ├── new_urban_YYYY_MM_intersections_sar_filtered.geojson # SAR validado
│   ├── new_urban_YYYY_MM_no_intersections.geojson
│   └── new_urban_YYYY_MM_no_intersections_sar_filtered.geojson
└── stats/
    ├── resumen_expansion_upl_ha_YYYY_MM.csv                 # DW original
    └── resumen_expansion_upl_ha_YYYY_MM_sar.csv             # SAR validado
```

### Desactivar filtro SAR

Si no deseas usar el filtro SAR (por ejemplo, durante pruebas o si no tienes credenciales):

1. En `src/config.py`, cambiar:
   ```python
   USE_SAR_FILTER = False
   ```

2. El pipeline funcionará normalmente solo con Dynamic World

### Limitaciones y Recomendaciones

**Processing Units (PU):**
- Cuota gratuita: 10,000 PU/mes + 300 PU/minuto
- Si excedes límites: Crear nueva cuenta o aumentar lookback delay
Herramientas Adicionales

### Exportar visualización SAR de tile individual

Para inspección visual y debug, puedes exportar datos SAR de un tile específico:

```bash
# Ver tiles con expansión DW
python export_sar_visualization.py --date 2025-04-30 --list-tiles

# Exportar tile específico (GeoTIFF para QGIS)
python export_sar_visualization.py --date 2025-04-30 --lookback 30 --tile "11,8" --no-png
```

**Outputs:**
- `sar_visualization_YYYYMMDD.tif`: GeoTIFF con 4 bandas (VV, VH, Mask, Urban)
- `sar_visualization_YYYYMMDD.p     # Script principal
├── export_sar_visualization.py     # Exportar SAR de tiles individuales (debug)
├── requirement.txt                 # Dependencias del proyecto
├── .env                            # Variables de entorno
├── src/
│   ├── config.py                   # Configuración y parámetros SAR/DW
│   ├── aux_utils.py                # Utilidades auxiliares
│   ├── maps_utils.py               # Generación de mapas (Sentinel-2 RGB)
│   ├── pipeline_utils.py           # Pipeline Dynamic World
│   ├── stats_utils.py              # Cálculo de estadísticas
│   └── sar_filter.py               # Filtro SAR con optimización por tiles
└── reporte/
    ├── render_report.py            # Renderización de reportes
    └── report_template.html      or request
- Cobertura Sentinel-1: Revisión cada 6-12 días
- DEM Copernicus: Resolución 30m (suficiente para urbano)

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
│   ├── stats_utils.py        # Cálculo de estadísticas
│   └── sar_filter.py         # Filtro SAR de Sentinel-1
└── reporte/
    ├── render_report.py      # Renderización de reportes
    └── report_template.html  # Plantilla HTML del reporte
```

## Salidas

El script genera las siguientes salidas en `BASE_PATH/urban_sprawl/outputs/YYYY_MM/`:

- **dw/**: Imágenes procesadas de Dynamic World
- **intersections/**: GeoJSON de intersecciones con áreas protegidas (incluye versiones filtradas por SAR si está habilitado)
- **stats/**: Estadísticas en formato JSON y CSV (incluye versiones SAR si está habilitado)
- **maps/**: Mapas interactivos en HTML
- **reportes/**: Reportes finales en HTML

## Seguridad

- **NUNCA** subir el archivo `.env` al repositorio
- Mantener las credenciales de Google Cloud **Y Sentinel Hub** seguras
- Usar service accounts con permisos mínimos necesarios
- Rotar credenciales regularmente
- Las credenciales de Sentinel Hub son gratuitas pero tienen límites de uso

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

