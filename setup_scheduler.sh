#!/bin/bash

# Cloud Scheduler Setup for Cloud Run Job
# Schedules the simbyp-area-construida job to run once a month, early enough
# that its report is in GCS well before the first Friday email send.
#
# NOTE: this used to be "0 0 1-7 * 5" ("day-of-month 1-7 AND Friday"), but
# standard cron treats day-of-month and day-of-week as OR when both are
# restricted, so it actually fired on every one of the 1st-7th (any weekday)
# plus every other Friday of the month - far more often than intended, and
# not reliably limited to early-month. The job doesn't need to run on a
# Friday at all: it always processes the *previous* month, which is complete
# as soon as the new month starts, so a fixed day works fine.

set -e

# Configuration
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT}}"
REGION="${2:-us-central1}"
JOB_NAME="simbyp-area-construida"
SCHEDULER_JOB_NAME="${JOB_NAME}-scheduler"
SERVICE_ACCOUNT_NAME="cloud-scheduler-sa"
SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

if [ -z "$PROJECT_ID" ]; then
    echo "Usage: ./setup_scheduler.sh <PROJECT_ID> [REGION]"
    echo "Example: ./setup_scheduler.sh my-gcp-project us-central1"
    echo ""
    echo "Or set GOOGLE_CLOUD_PROJECT in environment"
    exit 1
fi

echo "Setting up Cloud Scheduler for $JOB_NAME"
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Cron schedule: 0 6 1 * * (1st of every month at 06:00 UTC)"
echo ""

# Set project
echo "Setting project..."
gcloud config set project "$PROJECT_ID" --quiet

# Enable required services
echo "Enabling required services..."
gcloud services enable cloudscheduler.googleapis.com --quiet
gcloud services enable iam.googleapis.com --quiet

# Check if service account exists
echo "Checking service account..."
if ! gcloud iam service-accounts describe "$SERVICE_ACCOUNT_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
    echo "Creating service account: $SERVICE_ACCOUNT_EMAIL"
    gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
        --display-name="Cloud Scheduler Service Account for $JOB_NAME" \
        --project="$PROJECT_ID" \
        --quiet
else
    echo "✓ Service account already exists: $SERVICE_ACCOUNT_EMAIL"
fi

# Grant Cloud Run Invoker role
echo "Granting Cloud Run Invoker role..."
gcloud run jobs add-iam-policy-binding "$JOB_NAME" \
    --region="$REGION" \
    --member="serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
    --role="roles/run.invoker" \
    --project="$PROJECT_ID" \
    --quiet \
    2>/dev/null || echo "✓ Role already assigned"

# Check if scheduler job exists
echo "Checking scheduler job..."
if gcloud scheduler jobs describe "$SCHEDULER_JOB_NAME" --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
    echo "Scheduler job already exists. Updating..."
    gcloud scheduler jobs update http "$SCHEDULER_JOB_NAME" \
        --location="$REGION" \
        --schedule="0 6 1 * *" \
        --http-method="POST" \
        --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
        --oidc-service-account-email="$SERVICE_ACCOUNT_EMAIL" \
        --oidc-token-audience="https://${REGION}-run.googleapis.com" \
        --project="$PROJECT_ID" \
        --quiet
else
    echo "Creating scheduler job..."
    gcloud scheduler jobs create http "$SCHEDULER_JOB_NAME" \
        --location="$REGION" \
        --schedule="0 6 1 * *" \
        --http-method="POST" \
        --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
        --oidc-service-account-email="$SERVICE_ACCOUNT_EMAIL" \
        --oidc-token-audience="https://${REGION}-run.googleapis.com" \
        --project="$PROJECT_ID" \
        --quiet
fi

echo ""
echo "✓ Cloud Scheduler job setup complete!"
echo ""
echo "Job details:"
echo "  Name: $SCHEDULER_JOB_NAME"
echo "  Region: $REGION"
echo "  Schedule: 1st of every month at 06:00 UTC (0 6 1 * *)"
echo "  Triggers: $JOB_NAME Cloud Run Job"
echo ""
echo "View in Cloud Console:"
echo "https://console.cloud.google.com/cloudscheduler?project=$PROJECT_ID"
echo ""
echo "To manually trigger the job:"
echo "gcloud scheduler jobs run $SCHEDULER_JOB_NAME --location $REGION --project $PROJECT_ID"
echo ""
echo "To view job logs:"
echo "gcloud logging read \"resource.type=cloud_scheduler_job AND resource.labels.job_id=$SCHEDULER_JOB_NAME\" --limit 50 --format json --project $PROJECT_ID"
