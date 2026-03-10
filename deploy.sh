#!/bin/bash

# Simple Cloud Run Job Deployment
# Prerequisites: gcloud CLI and docker installed

set -e

# Load environment variables from .env file
if [ -f .env ]; then
    export $(cat .env | grep -v '#' | xargs)
fi

# Configuration
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT}}"
REGION="${2:-us-central1}"
JOB_NAME="simbyp-area-construida"

if [ -z "$PROJECT_ID" ]; then
    echo "Usage: ./deploy.sh <PROJECT_ID> [REGION]"
    echo "Example: ./deploy.sh my-gcp-project us-central1"
    echo ""
    echo "Or set GOOGLE_CLOUD_PROJECT in .env file"
    exit 1
fi

# Set Google Cloud credentials if available
if [ ! -z "$GOOGLE_APPLICATION_CREDENTIALS" ] && [ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
    echo "✓ Using service account credentials: $GOOGLE_APPLICATION_CREDENTIALS"
    export GOOGLE_APPLICATION_CREDENTIALS="$GOOGLE_APPLICATION_CREDENTIALS"
fi

echo "Deploying to GCP..."
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo ""

# Note: Skipping gcloud auth commands due to system issues
# Docker push will authenticate using credentials in Docker config

# Build and push image
echo "Building Docker image for linux/amd64..."
docker build --platform linux/amd64 -t gcr.io/$PROJECT_ID/$JOB_NAME:latest .

echo "Pushing to Google Container Registry..."
docker push gcr.io/$PROJECT_ID/$JOB_NAME:latest

echo ""
echo "✓ Docker image built and pushed successfully!"
echo ""
echo "Next steps - Run in a new terminal with timeout:"
echo "=============================================="
echo "timeout 30 gcloud config set project $PROJECT_ID"
echo "timeout 60 gcloud services enable run.googleapis.com --quiet"
echo "timeout 60 gcloud run jobs create $JOB_NAME --image gcr.io/$PROJECT_ID/$JOB_NAME:latest --region $REGION --memory 4Gi --cpu 2 --task-timeout 3600s --quiet"
echo ""
echo "To create a Cloud Scheduler job that runs on the first Friday of every month:"
echo "=================================================================="
echo ""
echo "1. First, create a service account for Cloud Scheduler:"
echo "timeout 60 gcloud iam service-accounts create cloud-scheduler-sa --display-name='Cloud Scheduler Service Account' --quiet"
echo ""
echo "2. Grant Cloud Run Invoker role:"
echo "timeout 60 gcloud run jobs add-iam-policy-binding $JOB_NAME --region=$REGION --member=serviceAccount:cloud-scheduler-sa@$PROJECT_ID.iam.gserviceaccount.com --role=roles/run.invoker --quiet"
echo ""
echo "3. Create the scheduler job (first Friday of month at midnight UTC):"
echo "timeout 60 gcloud scheduler jobs create http ${JOB_NAME}-scheduler --location=$REGION --schedule='0 0 1-7 * 5' --http-method=POST --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/$JOB_NAME:run" --oidc-service-account-email=cloud-scheduler-sa@$PROJECT_ID.iam.gserviceaccount.com --oidc-token-audience="https://${REGION}-run.googleapis.com" --quiet"
echo ""
echo "Or try creating the job from Cloud Console:"
echo "https://console.cloud.google.com/run/jobs?project=$PROJECT_ID"
echo ""
echo "Cloud Scheduler console:"
echo "https://console.cloud.google.com/cloudscheduler?project=$PROJECT_ID"
