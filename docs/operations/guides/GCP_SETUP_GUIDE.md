# Google Cloud Run Migration — GCP Setup Guide

This guide walks you through setting up all necessary Google Cloud resources for the migration.

## Prerequisites

- Google Cloud account with billing enabled
- `gcloud` CLI installed locally: https://cloud.google.com/sdk/docs/install
- `gcloud auth login` completed to authenticate locally

## Phase 1: Create GCP Project (if needed)

If you don't already have a GCP project, create one:

```bash
# Set your project name (use lowercase, hyphens only)
PROJECT_NAME="enact-walk-dashboard"
PROJECT_ID="enact-walk-dashboard-prod"  # must be globally unique

# Create the project
gcloud projects create $PROJECT_ID --name=$PROJECT_NAME

# Set as default
gcloud config set project $PROJECT_ID

# Enable billing (you'll need to do this in the console at https://console.cloud.google.com)
# Once billing is enabled, continue...
```

## Phase 2: Enable Required APIs

Enable the APIs needed for Cloud Run, Cloud Storage, and Secret Manager:

```bash
# Set your project ID
PROJECT_ID="enact-walk-dashboard-prod"
gcloud config set project $PROJECT_ID

# Enable required APIs
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  storage-api.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com
```

## Phase 3: Create GCS Bucket

Create a Cloud Storage bucket for persistent data:

```bash
BUCKET_NAME="enact-walk-dashboard-data-${PROJECT_ID}"

# Create the bucket in us-east1 region
gsutil mb -l us-east1 "gs://${BUCKET_NAME}/"

# Verify creation
gsutil ls -b "gs://${BUCKET_NAME}/"

echo "Bucket created: gs://${BUCKET_NAME}/"
```

**Save the bucket name** — you'll need it later for `GCS_BUCKET` environment variable.

## Phase 4: Create Service Accounts

Create two service accounts:
1. **Cloud Run service account** — runs the application
2. **Deployment service account** — used by GitHub Actions for building and deploying

### 4a. Create Cloud Run Service Account

This account will be used by Cloud Run to access GCS and Secret Manager:

```bash
PROJECT_ID="enact-walk-dashboard-prod"
gcloud config set project $PROJECT_ID

# Create the service account
gcloud iam service-accounts create cloud-run-sa \
  --display-name="Cloud Run Service Account"

# Get the email (save this)
CLOUD_RUN_SA=$(gcloud iam service-accounts list --filter="displayName:Cloud Run Service Account" --format="value(email)")
echo "Cloud Run Service Account: $CLOUD_RUN_SA"
```

### 4b. Grant GCS Permissions to Cloud Run SA

Grant the Cloud Run service account permissions to read/write the GCS bucket:

```bash
PROJECT_ID="enact-walk-dashboard-prod"
BUCKET_NAME="enact-walk-dashboard-data-${PROJECT_ID}"
CLOUD_RUN_SA=$(gcloud iam service-accounts list --filter="displayName:Cloud Run Service Account" --format="value(email)")

# Grant Storage Object Creator/Viewer roles
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${CLOUD_RUN_SA}" \
  --role="roles/storage.objectCreator"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${CLOUD_RUN_SA}" \
  --role="roles/storage.objectViewer"

echo "✓ Cloud Run SA can now read/write GCS bucket"
```

### 4c. Grant Secret Manager Permissions to Cloud Run SA

Grant the Cloud Run service account permission to access secrets:

```bash
PROJECT_ID="enact-walk-dashboard-prod"
CLOUD_RUN_SA=$(gcloud iam service-accounts list --filter="displayName:Cloud Run Service Account" --format="value(email)")

# Grant Secret Manager Secret Accessor role
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${CLOUD_RUN_SA}" \
  --role="roles/secretmanager.secretAccessor"

echo "✓ Cloud Run SA can now access secrets"
```

### 4d. Create Deployment Service Account

This account is used by GitHub Actions to build and deploy:

```bash
PROJECT_ID="enact-walk-dashboard-prod"

# Create the service account
gcloud iam service-accounts create github-actions-sa \
  --display-name="GitHub Actions Deployment Account"

# Get the email (save this)
GITHUB_SA=$(gcloud iam service-accounts list --filter="displayName:GitHub Actions Deployment Account" --format="value(email)")
echo "GitHub Actions Service Account: $GITHUB_SA"
```

### 4e. Grant Permissions to GitHub Actions SA

Grant GitHub Actions service account permissions to build and deploy:

```bash
PROJECT_ID="enact-walk-dashboard-prod"
GITHUB_SA=$(gcloud iam service-accounts list --filter="displayName:GitHub Actions Deployment Account" --format="value(email)")

# Cloud Run Admin (deploy)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${GITHUB_SA}" \
  --role="roles/run.admin"

# Service Account User (allow GitHub to deploy as Cloud Run SA)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${GITHUB_SA}" \
  --role="roles/iam.serviceAccountUser"

# Cloud Build Editor (build Docker image)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${GITHUB_SA}" \
  --role="roles/cloudbuild.builds.editor"

# Artifact Registry Writer (push Docker images)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${GITHUB_SA}" \
  --role="roles/artifactregistry.writer"

echo "✓ GitHub Actions SA can now deploy Cloud Run services"
```

### 4f. Create and Download GitHub Actions Service Account Key

```bash
GITHUB_SA=$(gcloud iam service-accounts list --filter="displayName:GitHub Actions Deployment Account" --format="value(email)")

# Create a JSON key
gcloud iam service-accounts keys create github-actions-key.json \
  --iam-account=${GITHUB_SA}

echo "✓ Service account key saved to: github-actions-key.json"
echo "⚠️  KEEP THIS FILE SECURE — DO NOT COMMIT TO GIT"
echo ""
echo "Next: Add this file's contents as a GitHub secret named GCP_SA_KEY"
```

**⚠️ IMPORTANT**: This key file (`github-actions-key.json`) contains sensitive credentials.
- Save it in a secure location
- **DO NOT** commit it to Git
- You'll add it to GitHub Secrets in the next phase

## Phase 5: Create Cloud Secret Manager Secrets

Create secrets in Google Cloud Secret Manager for sensitive configuration:

```bash
PROJECT_ID="enact-walk-dashboard-prod"
gcloud config set project $PROJECT_ID

# Create each secret (use values from your Fly.io configuration or .env file)

# 1. Anthropic API Key
echo -n "YOUR_ANTHROPIC_API_KEY_HERE" | gcloud secrets create ANTHROPIC_API_KEY \
  --data-file=-

# 2. Google Service Account JSON (for Google Drive access)
# First, create or use your existing GCP service account JSON
echo -n "$(cat /path/to/your/google-service-account.json)" | gcloud secrets create GOOGLE_SERVICE_ACCOUNT_JSON \
  --data-file=-

# 3. Google Drive Folder ID
echo -n "YOUR_GOOGLE_DRIVE_FOLDER_ID" | gcloud secrets create GOOGLE_DRIVE_FOLDER_ID \
  --data-file=-

# 4. GPS Auth Token (optional, but recommended)
echo -n "YOUR_GPS_BEARER_TOKEN" | gcloud secrets create GPS_AUTH_TOKEN \
  --data-file=-

# 5. GAS Secret (Google Apps Script trigger token)
echo -n "YOUR_GAS_SECRET_TOKEN" | gcloud secrets create GAS_SECRET \
  --data-file=-

# Verify all secrets were created
gcloud secrets list
```

**How to find these values:**
- **ANTHROPIC_API_KEY**: Your Anthropic console API key
- **GOOGLE_SERVICE_ACCOUNT_JSON**: Download from GCP Console > Service Accounts > Choose account > Keys > Add Key > JSON
- **GOOGLE_DRIVE_FOLDER_ID**: The folder ID from your Google Drive folder URL (e.g., `https://drive.google.com/drive/folders/FOLDER_ID_HERE`)
- **GPS_AUTH_TOKEN**: From your Fly.io secrets or .env file
- **GAS_SECRET**: From your Fly.io secrets or .env file

## Phase 6: Create Cloud Run Service (First Deployment)

Once Docker image is built and pushed, you'll deploy it with this command:

```bash
PROJECT_ID="enact-walk-dashboard-prod"
CLOUD_RUN_SA=$(gcloud iam service-accounts list --filter="displayName:Cloud Run Service Account" --format="value(email)")
REGION="us-east1"

gcloud run deploy enact-walk-dashboard \
  --image=gcr.io/${PROJECT_ID}/enact-walk-dashboard:latest \
  --platform=managed \
  --region=${REGION} \
  --memory=2Gi \
  --cpu=2 \
  --timeout=3600 \
  --concurrency=100 \
  --max-instances=10 \
  --min-instances=0 \
  --service-account=${CLOUD_RUN_SA} \
  --set-env-vars="\
GPS_STALE_SECONDS=300,\
DRIVE_POLL_INTERVAL=0,\
GCS_BUCKET=enact-walk-dashboard-data-${PROJECT_ID}" \
  --set-secrets="\
ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,\
GOOGLE_SERVICE_ACCOUNT_JSON=GOOGLE_SERVICE_ACCOUNT_JSON:latest,\
GOOGLE_DRIVE_FOLDER_ID=GOOGLE_DRIVE_FOLDER_ID:latest,\
GPS_AUTH_TOKEN=GPS_AUTH_TOKEN:latest,\
GAS_SECRET=GAS_SECRET:latest" \
  --allow-unauthenticated
```

**Note:**
- `--allow-unauthenticated` makes the service public (needed for API endpoints to work)
- `DRIVE_POLL_INTERVAL=0` disables background polling (relies on GAS push triggers instead)
- Replace `enact-walk-dashboard-data-${PROJECT_ID}` with your actual bucket name

## Phase 7: Add GitHub Secret

Add the GitHub Actions service account key to your repository secrets:

1. Go to GitHub: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
2. Create a secret named `GCP_SA_KEY`
3. Paste the contents of `github-actions-key.json`
4. Also add:
   - **GCP_PROJECT_ID**: Your GCP project ID (e.g., `enact-walk-dashboard-prod`)
   - **GCP_REGION**: `us-east1`

## Phase 8: Migrate Walks_Log.txt to GCS

Before first deployment, upload your existing Walks_Log.txt to the bucket:

```bash
BUCKET_NAME="enact-walk-dashboard-data-${PROJECT_ID}"

# Upload from your local repository
gsutil cp Walks_Log.txt "gs://${BUCKET_NAME}/Walks_Log.txt"

# Verify upload
gsutil ls "gs://${BUCKET_NAME}/"
```

## Summary of Created Resources

After completing this guide, you'll have:

| Resource | Name | Purpose |
|----------|------|---------|
| GCS Bucket | `enact-walk-dashboard-data-{PROJECT_ID}` | Persistent storage for Walks_Log.txt |
| Service Account | `cloud-run-sa@{PROJECT_ID}.iam.gserviceaccount.com` | Cloud Run execution identity |
| Service Account | `github-actions-sa@{PROJECT_ID}.iam.gserviceaccount.com` | GitHub Actions deployment |
| Secrets (5x) | ANTHROPIC_API_KEY, GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_DRIVE_FOLDER_ID, GPS_AUTH_TOKEN, GAS_SECRET | Sensitive config |
| Cloud Run | `enact-walk-dashboard` | Running application (deployed later) |

## Troubleshooting

**"Permission denied" when accessing GCS:**
- Verify Cloud Run service account has `roles/storage.objectCreator` and `roles/storage.objectViewer`
- Check: `gcloud projects get-iam-policy $PROJECT_ID --flatten="bindings[].members" --format="table(bindings.role)" --filter="bindings.members:${CLOUD_RUN_SA}"`

**Secrets not accessible:**
- Verify Cloud Run service account has `roles/secretmanager.secretAccessor`
- Verify secret name matches in environment variable references

**Deployment fails:**
- Check `gcloud run services describe enact-walk-dashboard --region=us-east1` for error details
- View logs: `gcloud run logs read enact-walk-dashboard --region=us-east1 --limit=50`

## Next Steps

1. ✅ Complete all commands in this guide
2. ⏭️ Return to main migration plan for Phase 4 (GitHub Actions workflow)
3. ⏭️ Local Docker testing
4. ⏭️ Deploy to Cloud Run staging
5. ⏭️ Production cutover
