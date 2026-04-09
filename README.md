# EnAACT Walk Dashboard

Scheduling and monitoring system for collecting air quality measurements across NYC. Manages collector assignments, walk schedules, weather constraints, and provides real-time GPS tracking via a web dashboard.

## Key Components

- **serve.py** — Flask-style HTTP server: serves the dashboard, receives GPS data, and accepts Google Drive push triggers
- **walk_scheduler.py** — Core scheduling algorithm with load balancing and constraint satisfaction
- **build_dashboard.py** — Generates the interactive HTML dashboard
- **gas/drive_watcher.js** — Google Apps Script that triggers Drive sync on new uploads

## Running Locally

```bash
pip install -r requirements.txt
python serve.py
```

The dashboard will be available at `http://localhost:8765`.

## Deployment

Deployed to **Google Cloud Run** via GitHub Actions (`.github/workflows/gcp-deploy.yml`). See `HANDOFF.md` for full operational details.
