# EnAACT Walk Dashboard — Handoff Guide

This document covers everything needed to keep the server running after the original developer leaves.

---

## Architecture Overview

```
[Traccar Client on phones] ──GPS push──▶ [serve.py on Fly.io] ◀── [Browser / Dashboard]
[Google Drive folder]      ──polling──▶  [serve.py on Fly.io]
                                              │
                                         walk_scheduler.py
                                         build_dashboard.py
                                         Walks_Log.txt
```

- **serve.py** is the single always-running process. It serves the dashboard, receives GPS data, and polls Google Drive every 60 seconds.
- **Walks_Log.txt** is the source of truth for completed walks. Drive polling auto-appends to it.
- **dashboard.html** is regenerated whenever the Drive poller detects new files or when "Rerun Scheduler" is clicked.

---

## Environment Variables

Set these in the Fly.io dashboard (under **Secrets**), never in code or files:

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key for walk_scheduler.py |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Yes (for Drive) | Full JSON content of the GCP service account key |
| `GOOGLE_DRIVE_FOLDER_ID` | Yes (for Drive) | The Google Drive folder ID where collectors upload data |
| `GPS_AUTH_TOKEN` | Recommended | Secret token that GPS devices must include in requests |
| `GPS_STALE_SECONDS` | Optional | Seconds before a GPS position is marked stale (default: 300) |
| `DRIVE_POLL_INTERVAL` | Optional | Seconds between Drive polls (default: 60) |
| `PORT` | Set by Fly.io | Do not set manually |

To set a secret on Fly.io:
```
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
fly secrets set GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
fly secrets set GOOGLE_DRIVE_FOLDER_ID=1aBcDeFgHiJkLmN...
fly secrets set GPS_AUTH_TOKEN=choose-a-long-random-string
```

To rotate a key: just run `fly secrets set KEY=newvalue` and the app restarts automatically.

---

## Hosting: Fly.io

### First-time deploy
```bash
# Install flyctl: https://fly.io/docs/hands-on/install-flyctl/
fly auth login
fly launch          # reads fly.toml, creates the app
fly secrets set ... # set all env vars above
fly volumes create walk_data --size 1   # persistent storage
fly deploy
```

### Redeploy after code changes
```bash
fly deploy
```

### View logs
```bash
fly logs
```

### Check status
```bash
fly status
fly ssh console   # SSH into the running machine if needed
```

The app URL will be `https://enact-walk-dashboard.fly.dev` (or whatever name was chosen at launch).

---

## GPS Tracking: Traccar Client Setup

**App**: Traccar Client (free, Android/iOS) — install on the two field phones.

**Settings in the app:**
- **Device identifier**: `BP_A` (for Backpack A) or `BP_B` (for Backpack B)
- **Server URL**: `https://your-app.fly.dev/api/gps`
- **Frequency**: 5–10 seconds
- **Token** (add as URL parameter): append `?token=YOUR_GPS_AUTH_TOKEN` to the server URL

Or the full URL format:
```
https://your-app.fly.dev/api/gps?id=BP_A&lat={lat}&lon={lon}&speed={speed}&batt={batt}&token=YOUR_GPS_AUTH_TOKEN
```

Traccar Client handles the `{lat}`, `{lon}` etc. substitutions automatically.

**To verify GPS is working:**
```
curl "https://your-app.fly.dev/api/gps/status"
```
Should return positions for BP_A and BP_B with non-null lat/lon.

---

## Google Drive Integration Setup

### One-time Google Cloud setup
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project (or use existing)
3. Enable **Google Drive API**
4. Go to **IAM & Admin → Service Accounts** → Create service account
5. No roles needed (it only reads Drive)
6. Create a JSON key for the service account → download it
7. Set `GOOGLE_SERVICE_ACCOUNT_JSON` to the full contents of the JSON file

### Share the Drive folder
1. Open the shared Google Drive folder in a browser
2. Click **Share**
3. Add the service account email (looks like `name@project.iam.gserviceaccount.com`) with **Viewer** access
4. Copy the folder ID from the URL: `drive.google.com/drive/folders/THIS_IS_THE_ID`
5. Set `GOOGLE_DRIVE_FOLDER_ID` to this ID

### File naming convention
For automatic walk log ingestion, uploaded files **must** be named in this format:
```
BP_COL_BORO_NEIGH_YYYYMMDD_TOD.ext
```
Example: `A_SOT_MN_HT_20260320_AM.csv`

- **BP**: `A`, `B`, or `X`
- **COL**: Collector code (SOT, AYA, ALX, TAH, JAM, JEN, SCT, TER, etc.)
- **BORO**: `MN`, `BK`, `QN`, `BX`
- **NEIGH**: Route code (HT, LE, WH, etc.)
- **YYYYMMDD**: Date
- **TOD**: `AM`, `MD`, or `PM`

Files that don't match this pattern are still tracked as "seen" but not added to the walk log.

---

## Walk Log Format

`Walks_Log.txt` is a plain text file, one entry per line:
```
A_SOT_MN_HT_20260320_AM
B_JEN_QN_LI_20260321_PM
RECAL_03_25_2026
```

To manually add a walk: just append a line in the correct format and click **Rebuild** on the dashboard.

---

## Rebuilding the Dashboard

The dashboard (`dashboard.html`) is generated from the walk log and route data. It rebuilds automatically when:
- Drive polling finds a new file
- "Rerun Scheduler" button is clicked in the browser

To force a manual rebuild:
```
curl -X POST https://your-app.fly.dev/api/rebuild
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| GPS badges show "offline" | Check that Traccar Client is running on the phone and the server URL/token are correct |
| Drive badge shows "not configured" | `GOOGLE_DRIVE_FOLDER_ID` or `GOOGLE_SERVICE_ACCOUNT_JSON` is not set |
| Drive sync finds no new files | Check that files are named correctly and the service account has Viewer access to the folder |
| Dashboard not updating after Drive sync | Files were found but names don't match the walk format — check filenames |
| Server not responding | Run `fly status` and `fly logs` to diagnose |
