# Urban Sprawl - SIMBYP Área Construida

Análisis de expansión urbana para Bogotá utilizando Google Earth Engine - Dynamic World.

## Descripción

Este proyecto analiza la expansión urbana mensual en el área de Bogotá mediante el procesamiento de imágenes satelitales de Dynamic World. Genera reportes con mapas interactivos, estadísticas y análisis de intersecciones con áreas protegidas (SAC, Reserva de Cerros Orientales y Estructura Ecológica Principal).

## Características

- Procesamiento automatizado de imágenes satelitales Dynamic World
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

