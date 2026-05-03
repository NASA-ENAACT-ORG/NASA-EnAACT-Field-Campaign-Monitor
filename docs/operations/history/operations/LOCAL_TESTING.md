# Local Docker Testing Guide

This guide walks you through testing the Docker image locally before deploying to Cloud Run.

## Prerequisites

- Docker installed and running
- All required environment variables available (from your Fly.io or `.env` file)

## Step 1: Build the Docker Image

```bash
# Navigate to project directory
cd /path/to/Claude\ Code\ Setup\ Data

# Build the Docker image
docker build -t enact-walk-dashboard:latest .

# Verify build succeeded
docker images | grep enact-walk-dashboard
```

## Step 2: Create Local Environment File

Create a `.env.local` file with your environment variables (do NOT commit this):

```bash
cat > .env.local << 'EOF'
PORT=8080
ANTHROPIC_API_KEY=sk-ant-xxxxx
GOOGLE_SERVICE_ACCOUNT_JSON={"type": "service_account", "project_id": ...}
GOOGLE_DRIVE_FOLDER_ID=1ABCD1234567890abcdefg
GPS_AUTH_TOKEN=your-gps-bearer-token
GAS_SECRET=your-gas-secret
GPS_STALE_SECONDS=300
DRIVE_POLL_INTERVAL=0
GCS_BUCKET=
EOF

# ⚠️  DO NOT commit .env.local to Git
echo ".env.local" >> .gitignore
```

**Important:**
- Leave `GCS_BUCKET` empty for local testing (GCS integration is optional)
- Set `DRIVE_POLL_INTERVAL=0` to disable background polling during testing
- Use your actual secrets from Fly.io configuration

## Step 3: Run the Docker Container Locally

```bash
# Run the container with environment variables
docker run -p 8080:8080 --env-file .env.local enact-walk-dashboard:latest

# Or manually pass individual variables
docker run -p 8080:8080 \
  -e PORT=8080 \
  -e ANTHROPIC_API_KEY="your-key" \
  -e GOOGLE_SERVICE_ACCOUNT_JSON='{...}' \
  -e GOOGLE_DRIVE_FOLDER_ID="..." \
  -e GPS_AUTH_TOKEN="..." \
  -e GAS_SECRET="..." \
  -e DRIVE_POLL_INTERVAL=0 \
  enact-walk-dashboard:latest
```

You should see output like:
```
  NYC Walk Scheduler — server
  Dashboard  : http://localhost:8080
  Rerun API  : POST http://localhost:8080/api/rerun
  Press Ctrl+C to stop.
```

## Step 4: Test Endpoints

### Test 1: Dashboard loads
```bash
curl http://localhost:8080/dashboard.html | head -20
# Should return HTML content, not 404
```

### Test 2: Status endpoint
```bash
curl -s http://localhost:8080/api/status | jq .
# Should return JSON with file timestamps and GPS data
```

### Test 3: GPS endpoint
```bash
curl "http://localhost:8080/api/gps?id=BP_A&lat=40.71&lon=-73.96&speed=1.5&batt=85&token=your-gps-bearer-token"
# Should return: {"status": "ok"}
```

### Test 4: GPS status endpoint
```bash
curl http://localhost:8080/api/gps/status | jq .
# Should return GPS positions for both backpacks
```

### Test 5: Drive poll trigger
```bash
curl -X POST http://localhost:8080/api/drive/poll \
  -H "Authorization: Bearer your-gas-secret"
# Should return: {"status": "ok", "new_files": 0}
```

### Test 6: Rebuild dashboards
```bash
curl -X POST http://localhost:8080/api/rebuild
# Returns chunked response with build output
```

## Step 5: Check Container Logs

In another terminal while the container is running:

```bash
# Get container ID
CONTAINER_ID=$(docker ps --filter "ancestor=enact-walk-dashboard:latest" --format "{{.ID}}")

# View logs
docker logs -f $CONTAINER_ID

# Or use
docker logs $CONTAINER_ID | tail -50
```

## Step 6: Cleanup

Stop the running container:

```bash
# Ctrl+C in the terminal where it's running, or:
docker kill <container_id>

# Remove the container
docker container prune

# Optional: Remove the image
docker image rm enact-walk-dashboard:latest
```

## Troubleshooting

### "Port 8080 is already in use"
```bash
# Find what's using the port
lsof -i :8080

# Or use a different port
docker run -p 9090:8080 enact-walk-dashboard:latest
```

### "Permission denied" when accessing GCS
- This is expected during local testing if `GCS_BUCKET` is not set
- GCS integration only works when running on Cloud Run with proper IAM

### "Module not found" errors
```bash
# Rebuild without cache to reinstall dependencies
docker build --no-cache -t enact-walk-dashboard:latest .
```

### Container starts but exits immediately
```bash
# View the exit logs
docker logs <container_id>

# Common issues:
# - Missing environment variables (check .env.local)
# - Port conflict
# - Invalid JSON in GOOGLE_SERVICE_ACCOUNT_JSON
```

## Testing with GCS (Advanced)

If you want to test GCS integration locally:

### 1. Create a GCP service account locally
```bash
# Download service account key from GCP Console
# Place it in your project directory as `gcp-key.json`

# Set environment variable in .env.local
GCS_BUCKET=your-bucket-name
GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-key.json
```

### 2. Mount the credentials in Docker
```bash
docker run -p 8080:8080 \
  --env-file .env.local \
  -v $(pwd)/gcp-key.json:/app/gcp-key.json:ro \
  enact-walk-dashboard:latest
```

### 3. Test GCS operations
The first request to `/api/status` should trigger:
- Download of `Walks_Log.txt` from GCS (if it exists)
- All subsequent writes to Walks_Log.txt also written to GCS

## Quick Testing Script

Save this as `test-local.sh`:

```bash
#!/bin/bash
set -e

echo "Building Docker image..."
docker build -t enact-walk-dashboard:latest .

echo "Starting container..."
docker run -p 8080:8080 --env-file .env.local --rm --name enact-test enact-walk-dashboard:latest &
CONTAINER_PID=$!

# Wait for startup
sleep 3

echo "Testing endpoints..."

echo -n "✓ Dashboard: "
curl -s http://localhost:8080/dashboard.html | head -1

echo -n "✓ Status API: "
curl -s http://localhost:8080/api/status | jq -r '.walk_log // "null"'

echo -n "✓ GPS Status: "
curl -s http://localhost:8080/api/gps/status | jq 'keys'

echo ""
echo "✓ Local tests passed!"
echo ""
echo "Server running on http://localhost:8080"
echo "Press Ctrl+C to stop..."

wait $CONTAINER_PID
```

Run it with:
```bash
chmod +x test-local.sh
./test-local.sh
```

## Next Steps

Once local testing passes:
1. Commit changes to git (except `.env.local` and `gcp-key.json`)
2. Complete GCP setup from `GCP_SETUP_GUIDE.md`
3. Update GitHub secrets with `GCP_SA_KEY` and `GCP_PROJECT_ID`
4. Push to main branch to trigger GitHub Actions deployment
