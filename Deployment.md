# SIMBYP Area Construida - Step-by-Step Deployment Guide

## Overview
This guide will walk you through deploying the application to Google Cloud Run as a Cloud Run Job.

---

## Prerequisites

Before starting, ensure you have:
- ✅ `gcloud` CLI installed: https://cloud.google.com/sdk/docs/install
- ✅ `docker` CLI installed: https://www.docker.com/products/docker-desktop
- ✅ Google Cloud project created: https://console.cloud.google.com
- ✅ Service account credentials configured locally
- ✅ Docker Desktop running (on macOS/Windows)

---

## Step-by-Step Deployment

### Step 1: Prepare Environment Variables

Open Terminal and set your project ID:

```bash
export PROJECT_ID="bosques-bogota-416214"
export REGION="us-central1"
export JOB_NAME="simbyp-area-construida"
```

**Note:** Replace `bosques-bogota-416214` with your actual GCP project ID.

---

### Step 2: Navigate to Project Directory

```bash
cd /Users/Daniel/Desktop/code/simbyp_area_construida
```

---

### Step 3: Configure Google Cloud CLI

Set your default project:

```bash
gcloud config set project $PROJECT_ID
```

Expected output:
```
Updated property [core/project].
```

---

### Step 4: Enable Required APIs

Enable the necessary Google Cloud APIs:

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  containerregistry.googleapis.com \
  earthengine.googleapis.com \
  --quiet
```

This may take 1-2 minutes. Expected output:
```
Operation "operations/..." finished successfully.
```

---

### Step 5: Authenticate with Docker

Authenticate Docker with Google Container Registry:

```bash
gcloud auth configure-docker
```

Press Enter when prompted. You should see:
```
✓ Docker configuration updated.
```

---

### Step 6: Build Docker Image

Build the Docker image specifically for Linux/AMD64 (required for Cloud Run):

```bash
docker build --platform linux/amd64 \
  -t gcr.io/$PROJECT_ID/$JOB_NAME:latest \
  .
```

This will take approximately 2-5 minutes. You'll see output like:
```
[+] Building 82.6s (11/11) FINISHED
 => [1/6] FROM docker.io/library/python:3.11-slim
 => [2/6] WORKDIR /app
 => [3/6] RUN apt-get update && apt-get install...
 => ...
 => => naming to gcr.io/bosques-bogota-416214/simbyp-analysis:latest
```

---

### Step 7: Push Image to Google Container Registry

Push the Docker image to GCP:

```bash
docker push gcr.io/$PROJECT_ID/$JOB_NAME:latest
```

This will take 2-5 minutes depending on your internet connection. You'll see:
```
latest: digest: sha256:c2809350f0e229b3d45d18d94ed5df182744de3cc1576ae675e02e5562ad802e size: 856
```

---

### Step 8: Create or Update Cloud Run Job

Check if the job already exists, then create or update it:

#### Option A: Using the automated deploy script (Recommended)

```bash
chmod +x deploy.sh
./deploy.sh $PROJECT_ID $REGION
```

#### Option B: Manual commands

**Check if job exists:**

```bash
gcloud run jobs describe $JOB_NAME --region $REGION >/dev/null 2>&1
JOB_EXISTS=$?
```

**If job exists (update it):**

```bash
gcloud run jobs update $JOB_NAME \
  --image gcr.io/$PROJECT_ID/$JOB_NAME:latest \
  --region $REGION \
  --memory 4Gi \
  --cpu 2 \
  --task-timeout 3600s \
  --quiet
```

**If job doesn't exist (create it):**

```bash
gcloud run jobs create $JOB_NAME \
  --image gcr.io/$PROJECT_ID/$JOB_NAME:latest \
  --region $REGION \
  --memory 4Gi \
  --cpu 2 \
  --task-timeout 3600s \
  --quiet
```

Expected output:
```
Creating Cloud Run job [simbyp-analysis] in project [bosques-bogota-416214] region [us-central1]...
✓ Cloud Run job [simbyp-analysis] created successfully.
```

---

### Step 9: Verify Deployment

Confirm the job was created successfully:

```bash
gcloud run jobs describe $JOB_NAME --region $REGION
```

You should see output including:
```
apiVersion: run.googleapis.com/v1
kind: Job
metadata:
  name: simbyp-analysis
spec:
  template:
    spec:
      containers:
      - image: gcr.io/bosques-bogota-416214/simbyp-analysis:latest
        ...
```

---

## Running the Job

### Run with Default Parameters (Previous Month)

```bash
gcloud run jobs execute $JOB_NAME --region $REGION
```

This will analyze **February 2026** (current date is March 10, 2026, so previous month = February).

### Run with Specific Month/Year

```bash
gcloud run jobs execute $JOB_NAME --region $REGION -- --anio 2026 --mes 3
```

This analyzes **March 2026**.

### View Job Execution Logs

```bash
gcloud run jobs log $JOB_NAME --region $REGION --limit 50
```

View more detailed logs:

```bash
gcloud logging read --resource=cloud_run_job --limit 100
```

---

## Complete Deployment Script (One-Liner)

If you want to do everything at once, use the automated script:

```bash
cd /Users/Daniel/Desktop/code/simbyp_area_construida && \
export PROJECT_ID="bosques-bogota-416214" && \
export REGION="us-central1" && \
gcloud config set project $PROJECT_ID && \
gcloud services enable run.googleapis.com cloudbuild.googleapis.com containerregistry.googleapis.com earthengine.googleapis.com --quiet && \
gcloud auth configure-docker && \
./deploy.sh $PROJECT_ID $REGION
```

---

## Troubleshooting

### Issue: "Cloud Run does not support image 'gcr.io/...:' Container manifest type must support amd64/linux"

**Solution:** Make sure you built the image with `--platform linux/amd64`:
```bash
docker build --platform linux/amd64 -t gcr.io/$PROJECT_ID/$JOB_NAME:latest .
```

### Issue: "docker: command not found"

**Solution:** Install Docker Desktop from https://www.docker.com/products/docker-desktop

### Issue: "gcloud: command not found"

**Solution:** Install Google Cloud CLI from https://cloud.google.com/sdk/docs/install

### Issue: Docker push fails with authentication error

**Solution:** Re-authenticate:
```bash
gcloud auth configure-docker
```

### Issue: gcloud commands require reauthentication

**Solution:** Re-login to Google:
```bash
gcloud auth login
```

### Issue: Job execution times out

**Solution:** Increase timeout and resources:
```bash
gcloud run jobs update $JOB_NAME \
  --region $REGION \
  --memory 8Gi \
  --cpu 4 \
  --task-timeout 7200s
```

---

## Monitoring and Management

### List all Cloud Run Jobs

```bash
gcloud run jobs list --region $REGION
```

### View Job Execution History

```bash
gcloud run jobs executions list $JOB_NAME --region $REGION
```

### View Specific Execution Details

```bash
gcloud run jobs executions detail EXECUTION_NAME --region $REGION
```

### Delete the Job

```bash
gcloud run jobs delete $JOB_NAME --region $REGION
```

---

## Cost Optimization

- **Memory:** Currently set to 4Gi. Reduce to 2Gi to lower costs (may increase execution time)
- **CPU:** Currently set to 2. Can reduce to 1 to lower costs
- **Timeout:** Currently 3600s (1 hour). Adjust based on actual execution time

Example: Lower cost configuration

```bash
gcloud run jobs update $JOB_NAME \
  --region $REGION \
  --memory 2Gi \
  --cpu 1 \
  --task-timeout 1800s
```

---

## Next Steps

1. ✅ Complete the deployment steps above
2. ✅ Test the job with default parameters: `gcloud run jobs execute $JOB_NAME --region $REGION`
3. ✅ Check logs to verify successful execution
4. ✅ (Optional) Set up Cloud Scheduler to run the job automatically on a schedule
5. ✅ (Optional) Set up Cloud Monitoring alerts for job failures

---

## Service Account Setup (If Needed)

If you need to create a service account with proper permissions:

```bash
# Create service account
gcloud iam service-accounts create simbyp-runner \
  --display-name="SIMBYP Area Construida Runner"

# Grant Earth Engine permissions
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:simbyp-runner@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/earthengine.editor"

# Grant Cloud Storage permissions
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:simbyp-runner@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/storage.admin"

# Update job to use this service account
gcloud run jobs update $JOB_NAME \
  --region $REGION \
  --service-account=simbyp-runner@$PROJECT_ID.iam.gserviceaccount.com
```

---

## Questions?

Refer to:
- Google Cloud Run Documentation: https://cloud.google.com/run/docs
- Google Earth Engine Documentation: https://developers.google.com/earth-engine
- Cloud Run Jobs Guide: https://cloud.google.com/run/docs/manage/job-queuing
