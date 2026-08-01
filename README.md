# PatrolTube
FastAPI + yt-dlp web app for searching and displaying Ohio police bodycam and dashcam videos from YouTube. Deployed via Docker at port 8001.

<img width="1904" height="956" alt="image" src="https://github.com/user-attachments/assets/f5dc01ad-08be-4ebe-86c3-13a6008fc8e1" />

## Features

- Ohio city/county/agency detection with confidence scoring
- Body/dash cam filtering
- Client-side confidence and sort controls (Relevance, Newest/Oldest, Most Views, Duration)
- Persistent cache with background refresh
- YouTube Data API v3 metadata enrichment

## Quick Start

```powershell
$env:YOUTUBE_API_KEY = "YOUR_KEY"
docker compose up --build
```

App runs at `http://localhost:8001`.

## Environment

| Variable | Required | Description |
|----------|----------|-------------|
| `YOUTUBE_API_KEY` | Yes | YouTube Data API v3 key |

## Project Structure

- `app.py` — FastAPI server, cache management, background refresh
- `scraper.py` — YouTube search and video extraction
- `ohio_detector.py` — Ohio-specific entity matching and confidence scoring
- `templates/index.html` — Dashboard UI
- `static/style.css` — Dark theme styling
- `docker-compose.yml` — Docker config with volume mount for cache persistence

## Tech Stack

- Python 3.11
- FastAPI + Uvicorn
- yt-dlp
- Jinja2
- Docker