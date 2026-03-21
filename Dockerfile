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

# Rebuild static dashboards at container start, then launch server
CMD ["sh", "-c", "python build_dashboard.py && python build_collector_map.py && python serve.py"]
