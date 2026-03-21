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

# 1. Restore GCS state (schedule_output.json etc) BEFORE building the dashboard
# 2. Build static dashboards (now with fresh schedule baked in)
# 3. Launch the server
CMD ["sh", "-c", "python serve.py --restore-only && python build_dashboard.py && python build_collector_map.py && python serve.py"]
