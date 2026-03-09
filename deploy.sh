#!/bin/bash

# Simple Cloud Run Job Deployment
# Prerequisites: gcloud CLI and docker installed

set -e

# Configuration
PROJECT_ID="${1}"
REGION="${2:-us-central1}"
JOB_NAME="simbyp-analysis"

if [ -z "$PROJECT_ID" ]; then
    echo "Usage: ./deploy.sh <PROJECT_ID> [REGION]"
    echo "Example: ./deploy.sh my-gcp-project us-central1"
    exit 1
fi

echo "Deploying to GCP..."
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo ""

# Set project
gcloud config set project $PROJECT_ID

# Enable APIs
gcloud services enable run.googleapis.com cloudbuild.googleapis.com containerregistry.googleapis.com --quiet

# Build and push image
echo "Building Docker image..."
docker build -t gcr.io/$PROJECT_ID/$JOB_NAME:latest .

echo "Pushing to Google Container Registry..."
docker push gcr.io/$PROJECT_ID/$JOB_NAME:latest

# Create or update job
echo "Creating Cloud Run Job..."
if gcloud run jobs describe $JOB_NAME --region $REGION >/dev/null 2>&1; then
    gcloud run jobs update $JOB_NAME \
        --image gcr.io/$PROJECT_ID/$JOB_NAME:latest \
        --region $REGION \
        --memory 4Gi \
        --cpu 2 \
        --task-timeout 3600s \
        --quiet
else
    gcloud run jobs create $JOB_NAME \
        --image gcr.io/$PROJECT_ID/$JOB_NAME:latest \
        --region $REGION \
        --memory 4Gi \
        --cpu 2 \
        --task-timeout 3600s \
        --quiet
fi

echo ""
echo "✓ Deployment complete!"
echo ""
echo "Run the job with:"
echo "  gcloud run jobs execute $JOB_NAME --region $REGION"
echo ""
echo "Run with custom month/year:"
echo "  gcloud run jobs execute $JOB_NAME --region $REGION -- --anio 2025 --mes 4"
