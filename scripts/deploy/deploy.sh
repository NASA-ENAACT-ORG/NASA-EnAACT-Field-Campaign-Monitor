#!/bin/bash
set -e

# Google Cloud Run Deployment Helper
# Usage: ./deploy.sh [staging|prod]

ENVIRONMENT="${1:-prod}"
SERVICE_NAME="enact-walk-dashboard"

if [ "$ENVIRONMENT" = "staging" ]; then
  SERVICE_NAME="${SERVICE_NAME}-staging"
fi

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Google Cloud Run Deployment Helper${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Check prerequisites
echo "Checking prerequisites..."
command -v gcloud >/dev/null 2>&1 || { echo -e "${RED}✗ gcloud CLI not found${NC}"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo -e "${RED}✗ docker not found${NC}"; exit 1; }
echo -e "${GREEN}✓ Prerequisites OK${NC}"
echo ""

# Get project ID from gcloud config
PROJECT_ID=$(gcloud config get-value project)
if [ -z "$PROJECT_ID" ]; then
  echo -e "${RED}✗ GCP project not configured. Run: gcloud config set project YOUR_PROJECT_ID${NC}"
  exit 1
fi

REGION="us-east1"
IMAGE_NAME="enact-walk-dashboard"
IMAGE_URL="gcr.io/${PROJECT_ID}/${IMAGE_NAME}:latest"
BUCKET_NAME="enact-walk-dashboard-data-${PROJECT_ID}"

echo -e "Environment:  ${BLUE}${ENVIRONMENT}${NC}"
echo -e "Service Name: ${BLUE}${SERVICE_NAME}${NC}"
echo -e "Project ID:   ${BLUE}${PROJECT_ID}${NC}"
echo -e "Region:       ${BLUE}${REGION}${NC}"
echo -e "Image:        ${BLUE}${IMAGE_URL}${NC}"
echo -e "Bucket:       ${BLUE}${BUCKET_NAME}${NC}"
echo ""

# Step 1: Build Docker image
echo "Step 1: Building Docker image..."
docker build -t ${IMAGE_URL} .
echo -e "${GREEN}✓ Docker image built${NC}"
echo ""

# Step 2: Push to Google Container Registry
echo "Step 2: Authenticating to GCR..."
gcloud auth configure-docker gcr.io --quiet
echo ""

echo "Step 3: Pushing image to GCR..."
docker push ${IMAGE_URL}
echo -e "${GREEN}✓ Image pushed to gcr.io${NC}"
echo ""

# Step 4: Deploy to Cloud Run
echo "Step 4: Deploying to Cloud Run..."
echo "  Service: ${SERVICE_NAME}"
echo "  Region:  ${REGION}"
echo "  Memory:  2Gi"
echo "  CPU:     2"
echo ""

gcloud run deploy ${SERVICE_NAME} \
  --image=${IMAGE_URL} \
  --region=${REGION} \
  --platform=managed \
  --memory=2Gi \
  --cpu=2 \
  --timeout=3600 \
  --concurrency=100 \
  --max-instances=10 \
  --min-instances=1 \
  --service-account=cloud-run-sa@${PROJECT_ID}.iam.gserviceaccount.com \
  --set-env-vars="\
GPS_STALE_SECONDS=300,\
DRIVE_POLL_INTERVAL=0,\
GCS_BUCKET=${BUCKET_NAME}" \
  --set-secrets="\
ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,\
GOOGLE_SERVICE_ACCOUNT_JSON=GOOGLE_SERVICE_ACCOUNT_JSON:latest,\
GOOGLE_DRIVE_WALKS_FOLDER_ID=GOOGLE_DRIVE_WALKS_FOLDER_ID:latest,\
DRIVE_FORECASTS_FOLDER_ID=DRIVE_FORECASTS_FOLDER_ID:latest,\
GPS_AUTH_TOKEN=GPS_AUTH_TOKEN:latest,\
GAS_SECRET=GAS_SECRET:latest,\
UPLOAD_HOLDING_BUCKET=UPLOAD_HOLDING_BUCKET:latest" \
  --allow-unauthenticated \
  --quiet

echo -e "${GREEN}✓ Deployment completed${NC}"
echo ""

# Step 5: Get service URL
echo "Step 5: Getting service URL..."
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} \
  --region=${REGION} \
  --format="value(status.url)" 2>/dev/null || echo "")

if [ -z "$SERVICE_URL" ]; then
  echo -e "${RED}✗ Could not retrieve service URL${NC}"
  echo "Run manually: gcloud run services describe ${SERVICE_NAME} --region=${REGION}"
  exit 1
fi

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✓ Deployment Successful!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "Dashboard:    ${GREEN}${SERVICE_URL}/dashboard.html${NC}"
echo -e "API Status:   ${GREEN}${SERVICE_URL}/api/status${NC}"
echo -e "Rebuild API:  ${GREEN}POST ${SERVICE_URL}/api/rebuild${NC}"
echo ""
echo "To view logs:"
echo -e "  ${BLUE}gcloud run logs read ${SERVICE_NAME} --region=${REGION} --limit=50${NC}"
echo ""
