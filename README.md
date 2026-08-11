# Urban Sprawl - SIMBYP Área Construida

Análisis de expansión urbana para Bogotá utilizando Sentinel-1 SAR + NDVI para detección de construcciones nuevas.

## Descripción

Este proyecto analiza la expansión urbana mensual en el área de Bogotá mediante el procesamiento de imágenes satelitales de **Sentinel-1 SAR (radar)** con validación por **NDVI**. Genera reportes con mapas interactivos, estadísticas y análisis de intersecciones con áreas protegidas (SAC, Reserva de Cerros Orientales y Estructura Ecológica Principal).

## Metodología de Detección

### Pipeline de Detección de Construcciones Nuevas

1. **Descarga de Construcciones Existentes**
   - Fuente: Servicio REST de Catastro Bogotá
   - Incluye buffer de 3 metros para excluir bordes
   - Reparación automática de geometrías inválidas

2. **Análisis Temporal Sentinel-1 VV**
   - Sensor: Sentinel-1 IW GRD
   - Polarización: VV (vertical-vertical)
   - Corrección: GAMMA0_TERRAIN con DEM COPERNICUS_30
   - Resolución: 10 metros
   - Períodos: Mes anterior vs Mes actual (30 días cada uno)

3. **Procesamiento SAR**
   - Filtro Lee 5×5 para reducción de speckle
   - Conversión a escala dB (decibeles)
   - Cálculo de diferencia temporal
   - Extracción de polígonos con cambio significativo (P99)

4. **Validación por NDVI**
   - Fuente: Sentinel-2 MSI
   - Umbral: NDVI < 0.1 (excluye vegetación)
   - Procesamiento: Google Earth Engine

5. **Resultado Final**
   - Polígonos de construcciones nuevas con alta confianza
   - Criterios: Sin construcciones previas + Aumento VV > P99 + Sin vegetación

### Ventajas de esta Metodología

- ✅ **Excluye construcciones existentes** desde el inicio (más eficiente)
- ✅ **Validación por NDVI** elimina falsos positivos de vegetación
- ✅ **Filtro Lee** reduce ruido inherente de SAR
- ✅ **Análisis temporal** detecta cambios reales (robusto)
- ✅ **Sin clasificación directa** (más confiable que umbrales VV/VH)

## Características

- Procesamiento automatizado mensual
- Análisis de expansión urbana basado en SAR
- Generación de mapas interactivos con Sentinel-2
- Cálculo de estadísticas de área construida
- Análisis de intersecciones con áreas protegidas
- Generación automática de reportes HTML

## Requisitos

- Python 3.8+
- Cuenta de Google Cloud Platform con Google Earth Engine habilitado
- Credenciales de servicio de Google Cloud
- Acceso a Google Cloud Storage
- Credenciales de Copernicus Dataspace (Sentinel Hub)

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
   - Crear archivo `.env` con información de paths
   - Completar las credenciales de Google Cloud
   - Ajustar las rutas de GCS según sea necesario
   - Configurar credenciales de Sentinel Hub en GCP Secret Manager

## Uso

Ejecutar el análisis para un mes específico:

```bash
python main.py --anio 2026 --mes 6
```

### Parámetros

- `--anio`: Año del análisis en formato YYYY (requerido)
- `--mes`: Mes del análisis en formato numérico 1-12 (requerido)

### Ejemplo

```bash
# Analizar junio 2026
python main.py --anio 2026 --mes 6
```

## Configuración

### 1. Obtener credenciales de Copernicus Dataspace

1. Crear cuenta en [Copernicus Dataspace](https://dataspace.copernicus.eu/)
2. Ir a **Dashboard → User Settings → OAuth clients**
3. Crear nuevo OAuth client
4. Copiar `CLIENT_ID` y `CLIENT_SECRET`

### 2. Almacenar credenciales en GCP Secret Manager

```bash
# Crear secretos
gcloud secrets create sentinelhub-client-id --data-file=- <<EOF
tu-client-id-aqui
EOF

gcloud secrets create sentinelhub-client-secret --data-file=- <<EOF
tu-client-secret-aqui
EOF

# Dar permisos al service account
gcloud secrets add-iam-policy-binding sentinelhub-client-id \
  --member="serviceAccount:tu-service-account@proyecto.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding sentinelhub-client-secret \
  --member="serviceAccount:tu-service-account@proyecto.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### 3. Configurar parámetros

En `src/config.py`:

```python
# Buffer para construcciones existentes
BUFFER_CONSTRUCCIONES_METROS = 3  # 3 metros

# Resolución espacial
SENTINEL1_RESOLUTION = 10  # 10 metros

# Período temporal
SENTINEL1_LOOKBACK_DAYS = 30  # 30 días

# Umbral NDVI
NDVI_THRESHOLD = 0.1  # < 0.1 = sin vegetación
```

## Flujo de Procesamiento

```
1. Descarga construcciones existentes (Catastro REST API)
                ↓
2. Aplica buffer de 3m
                ↓
3. Crea máscara de construcciones
                ↓
4. Descarga Sentinel-1 VV (mes anterior y actual)
                ↓
5. Aplica filtro Lee
                ↓
6. Calcula diferencia temporal
                ↓
7. Extrae polígonos P99
                ↓
8. Filtra por NDVI < 0.1 (Google Earth Engine)
                ↓
9. Genera new_urban.geojson
                ↓
10. Calcula intersecciones con áreas protegidas
                ↓
11. Genera estadísticas
                ↓
12. Crea mapas interactivos
                ↓
13. Genera reporte HTML
                ↓
14. Sube resultados a GCS
```

## Estructura de Salida

```
temp_data/urban_sprawl/outputs/YYYY_MM/
├── dw/
│   ├── new_urban.geojson           # Construcciones nuevas detectadas
│   └── construcciones_existentes.geojson
├── intersections/
│   ├── new_urban_intersections.geojson      # Con restricciones
│   └── new_urban_no_intersections.geojson   # Sin restricciones
├── stats/
│   └── resumen_expansion_upl_ha.csv
├── maps/
│   ├── map_expansion.html
│   ├── sentinel_YYYY-MM-DD_t1/    # Mosaico periodo anterior
│   └── sentinel_YYYY-MM-DD_t2/    # Mosaico periodo actual
├── reportes/
│   └── urban_sprawl_reporte_YYYY_MM.html
└── sentinel/
    └── (datos temporales Sentinel-2)
```

## Datos Técnicos

### Sentinel-1
- **Sensor**: C-band SAR
- **Modo**: Interferometric Wide (IW)
- **Producto**: GRD (Ground Range Detected)
- **Corrección**: GAMMA0_TERRAIN (Radiometric Terrain Correction)
- **DEM**: Copernicus 30m
- **Resolución espacial**: 10m × 10m
- **Polarización**: VV (vertical-vertical)
- **Órbita**: Descendente
- **Tiempo de revisita**: 12 días (con Sentinel-1A y 1B)

### Sentinel-2
- **Sensor**: MSI (Multispectral Instrument)
- **Bandas usadas**: 
  - B8 (NIR): 842 nm
  - B4 (Red): 665 nm
- **Resolución espacial**: 10m
- **NDVI**: (B8 - B4) / (B8 + B4)

### Procesamiento
- **Filtro Lee**: Ventana 5×5 píxeles
- **Percentil**: P99 (1% superior de cambios)
- **Buffer construcciones**: 3 metros
- **Umbral NDVI**: 0.1 (sin vegetación significativa)

## API y Servicios

- **Sentinel Hub API**: Acceso a Sentinel-1 corregido por terreno
- **Google Earth Engine**: Cálculo de NDVI con Sentinel-2
- **Catastro Bogotá REST API**: Descarga de construcciones existentes
- **Google Cloud Storage**: Almacenamiento de resultados

## Interpretación de Resultados

### Valores NDVI de Referencia (Bogotá, 2600 msnm)
- `< 0.0`: Agua, sombras profundas
- `0.0 - 0.1`: Suelo desnudo, concreto, asfalto ✅
- `0.1 - 0.3`: Urbano denso, vegetación dispersa
- `0.3 - 0.5`: Pastizales, vegetación moderada
- `0.5 - 0.7`: Bosque seco, eucaliptos
- `> 0.7`: Bosque denso andino

### Criterios de Detección Final
Un polígono se clasifica como **construcción nueva** si cumple:
1. ✅ No existía construcción previa (+ 3m buffer)
2. ✅ Aumento significativo de backscatter VV (> P99)
3. ✅ NDVI < 0.1 (sin vegetación)

## Solución de Problemas

### Error: "Credenciales de Sentinel Hub no configuradas"
- Verifica que los secretos existan en GCP Secret Manager
- Confirma permisos del service account
- Revisa variable `GOOGLE_CLOUD_PROJECT` en `.env`

### Error: "No se detectaron construcciones nuevas"
- Normal si no hubo expansión urbana ese mes
- Revisa que el AOI sea correcto
- Verifica disponibilidad de imágenes Sentinel-1

### Falsos positivos (vegetación detectada como construcción)
- Ajustar `NDVI_THRESHOLD` en `config.py` (aumentar a 0.2 o 0.3)
- Revisar período de imágenes Sentinel-2 (puede tener nubes)

## Contribuciones

Para contribuir al proyecto:
1. Fork del repositorio
2. Crear rama para tu feature (`git checkout -b feature/AmazingFeature`)
3. Commit de cambios (`git commit -m 'Add AmazingFeature'`)
4. Push a la rama (`git push origin feature/AmazingFeature`)
5. Abrir Pull Request

## Licencia

[Especificar licencia]

## Contacto

[Información de contacto]

## Referencias

- [Copernicus Dataspace](https://dataspace.copernicus.eu/)
- [Sentinel Hub Documentation](https://docs.sentinel-hub.com/)
- [Google Earth Engine](https://earthengine.google.com/)
- [Catastro Bogotá](https://www.catastrobogota.gov.co/)
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

### Configurar parámetros de detección

En `src/config.py` puedes ajustar los parámetros de detección:

```python
# Percentil de detección para cambios temporales
DETECTION_PERCENTILE = 99  # 99 = 1% superior (más inclusivo)

# Umbral NDVI para excluir vegetación
NDVI_THRESHOLD = 0.1  # < 0.1 = sin vegetación

# Área mínima para polígonos detectados
MIN_AREA_M2 = 200  # Elimina polígonos < 200 m²
```

### Limitaciones y Recomendaciones

**Sentinel Hub API:**
- Cuota gratuita: Variable según plan de Copernicus Dataspace
- Límite de descarga: ~50 MB por petición (manejado con tiles automáticos)

**Google Earth Engine:**
- Cuota gratuita: Generosa para uso no comercial
- Ventana temporal NDVI: Se amplía automáticamente 3 meses hacia atrás si no hay imágenes

**Recomendaciones:**
- Ejecutar análisis mensualmente para mantener continuidad temporal
- Verificar disponibilidad de imágenes Sentinel-1 en la región
- Revisar manualmente detecciones en áreas de sombras o terreno complejo
- `sar_visualization_YYYYMMDD.p     # Script principal
├── export_sar_visualization.py     # Exportar SAR de tiles individuales (debug)
├── requirement.txt                 # Dependencias del proyecto
├── .env                            # Variables de entorno
├── src/
│   ├── config.py                   # Configuración y parámetros del pipeline
│   ├── aux_utils.py                # Utilidades auxiliares
│   ├── maps_utils.py               # Generación de mapas (Sentinel-2 RGB)
│   ├── pipeline_utils.py           # Pipeline Sentinel-1 VV + NDVI
│   └── stats_utils.py              # Cálculo de estadísticas
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
│   ├── pipeline_utils.py     # Pipeline Sentinel-1 VV + NDVI
│   └── stats_utils.py        # Cálculo de estadísticas
└── reporte/
    ├── render_report.py      # Renderización de reportes
    └── report_template.html  # Plantilla HTML del reporte
```

## Salidas

El script genera las siguientes salidas en `BASE_PATH/urban_sprawl/outputs/YYYY_MM/`:

- **dw/**: Polígonos de construcciones nuevas detectadas (GeoJSON)
- **intersections/**: GeoJSON de intersecciones con áreas protegidas (SAC, Reserva, EEP)
- **stats/**: Estadísticas por UPL en formato CSV
- **maps/**: Mapas interactivos en HTML con capas Sentinel-2
- **reportes/**: Reportes finales en HTML con metodología y análisis
- **sentinel/**: Mosaicos Sentinel-2 RGB descargados

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

