# Use Python 3.11 slim image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for geospatial processing
RUN apt-get update && apt-get install -y \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    git \
    locales \
    && rm -rf /var/lib/apt/lists/*

# Generate Spanish locales
RUN sed -i 's/^# es_ES.UTF-8 UTF-8$/es_ES.UTF-8 UTF-8/' /etc/locale.gen && \
    sed -i 's/^# es_CO.UTF-8 UTF-8$/es_CO.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen

# Set default locale
ENV LANG=es_ES.UTF-8
ENV LC_ALL=es_ES.UTF-8

# Set GDAL environment variables
ENV GDAL_CONFIG=/usr/bin/gdal-config
ENV CPLUS_INCLUDE_PATH=/usr/include/gdal
ENV C_INCLUDE_PATH=/usr/include/gdal

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Entry point
ENTRYPOINT ["python", "main.py"]
CMD ["--anio", "2025", "--mes", "3"]
