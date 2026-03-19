#!/bin/bash

# Cloud Run deployment script for LuxeLife API

# Set your project ID
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="luxelife-api"

echo "Deploying to Cloud Run..."
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Service: $SERVICE_NAME"

# Build and push the image
echo "Building Docker image..."
gcloud builds submit --tag gcr.io/$PROJECT_ID/$SERVICE_NAME .

# Deploy to Cloud Run
echo "Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
    --image gcr.io/$PROJECT_ID/$SERVICE_NAME \
    --platform managed \
    --region $REGION \
    --allow-unauthenticated \
    --memory 1Gi \
    --cpu 1 \
    --timeout 300 \
    --concurrency 1000 \
    --max-instances 100 \
    --set-env-vars "DATABASE_URL=${DATABASE_URL}" \
    --set-env-vars "REDIS_URL=${REDIS_URL}" \
    --set-env-vars "JWT_ACCESS_SECRET=${JWT_ACCESS_SECRET}" \
    --set-env-vars "JWT_REFRESH_SECRET=${JWT_REFRESH_SECRET}" \
    --set-env-vars "ALLOWED_ORIGINS=${ALLOWED_ORIGINS:-https://yourdomain.com}" \
    --set-env-vars "STATIC_BASE_URL=${STATIC_BASE_URL:-https://storage.googleapis.com/your-bucket}" \
    --set-env-vars "GCS_BUCKET=${GCS_BUCKET}" \
    --set-env-vars "SENTRY_DSN=${SENTRY_DSN}" \
    --set-env-vars "TWILIO_ACCOUNT_SID=${TWILIO_ACCOUNT_SID}" \
    --set-env-vars "TWILIO_AUTH_TOKEN=${TWILIO_AUTH_TOKEN}" \
    --set-env-vars "TWILIO_PHONE_NUMBER=${TWILIO_PHONE_NUMBER}"

echo "Deployment complete!"
echo "Service URL: $(gcloud run services describe $SERVICE_NAME --region $REGION --format='value(status.url)')"
