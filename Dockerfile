# Use official Python runtime as base image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port 8080 (Cloud Run default)
EXPOSE 8080

# Set environment variable for Cloud Run
ENV PORT=8080

# 1. Restore runtime state from GCS.
# 2. Rebuild dashboard.html from current local artifacts.
# 3. Attempt legacy collector-map build (non-fatal if missing/fails).
# 4. Launch the server.
CMD ["sh", "-c", "python app/server/serve.py --restore-only; python pipelines/dashboard/build_dashboard.py || echo '[startup] dashboard build failed — serving GCS-restored version'; python pipelines/_retired/maps/build_collector_map.py || echo '[startup] collector map build failed'; python app/server/serve.py"]
