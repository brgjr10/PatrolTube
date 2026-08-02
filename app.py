from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
import threading
import time
import re
import logging
from typing import Optional
from pydantic import BaseModel

from scraper import search_ohio_police_videos, search_custom, enrich_videos_metadata, refresh_cache_background, get_cached_videos, load_cache, CACHE_TTL_SECONDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="PatrolTube")

MOBILE_PATTERN = re.compile(r"Android|iPhone|iPod|Opera Mini|IEMobile|WPDesktop|BlackBerry|Mobile|webOS|Tablet|iPad", re.I)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

class SearchRequest(BaseModel):
    query: str = ""
    max_results: int = 50
    min_confidence: float = 40.0
    require_cam: bool = True
    sort_by: str = "confidence"

@app.middleware("http")
async def detect_mobile(request: Request, call_next):
    user_agent = request.headers.get("user-agent", "")
    is_mobile = bool(MOBILE_PATTERN.search(user_agent))
    request.state.is_mobile = is_mobile
    response = await call_next(request)
    return response

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if request.state.is_mobile:
        return RedirectResponse(url="/mobile", status_code=302)
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/mobile", response_class=HTMLResponse)
async def mobile_dashboard(request: Request):
    if not request.state.is_mobile:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("mobile.html", {"request": request})

@app.get("/api/videos")
async def get_default_videos():
    videos = await search_ohio_police_videos(max_per_query=15)
    return {"count": len(videos), "videos": videos}

@app.post("/api/search")
async def search_videos(req: SearchRequest):
    if not req.query.strip():
        videos = await search_ohio_police_videos(max_per_query=15)
    else:
        videos = await search_custom(
            query=req.query,
            max_results=req.max_results,
            min_confidence=req.min_confidence,
            require_cam=req.require_cam,
            sort_by=req.sort_by,
        )
    return {"count": len(videos), "videos": videos}

@app.get("/api/search")
async def search_videos_get(q: str = "", max_results: int = 50, min_confidence: float = 40.0, require_cam: bool = True, sort_by: str = "confidence"):
    if not q.strip():
        videos = await search_ohio_police_videos(max_per_query=15)
    else:
        videos = await search_custom(
            query=q,
            max_results=max_results,
            min_confidence=min_confidence,
            require_cam=require_cam,
            sort_by=sort_by,
        )
    return {"count": len(videos), "videos": videos}

@app.post("/api/enrich")
async def enrich_metadata(payload: dict):
    video_ids = payload.get("video_ids", [])
    if not video_ids:
        return {"results": {}}
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, enrich_videos_metadata, video_ids[:20])
    return {"results": results}

@app.get("/api/cache")
async def get_cache(q: str = "", min_confidence: float = 40.0, sort_by: str = "confidence", require_cam: bool = True, max_results: int = 50, offset: int = 0):
    return get_cached_videos(query=q, min_confidence=min_confidence, sort_by=sort_by, require_cam=require_cam, max_results=max_results, offset=offset)

@app.get("/api/cache/status")
async def cache_status():
    data = load_cache()
    return {
        "count": len(data.get("videos", [])),
        "updated_at": data.get("updated_at", 0.0),
        "age_seconds": time.time() - data.get("updated_at", 0.0) if data.get("updated_at") else None,
    }

@app.post("/api/cache/refresh")
async def refresh_cache():
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, refresh_cache_background)
    return {"count": len(data.get("videos", [])), "updated_at": data.get("updated_at", 0.0)}

def _start_cache_refresh_thread():
    def worker():
        while True:
            time.sleep(CACHE_TTL_SECONDS)
            try:
                refresh_cache_background()
            except Exception as e:
                logger.error(f"Background cache refresh failed: {e}")
    t = threading.Thread(target=worker, daemon=True)
    t.start()

_start_cache_refresh_thread()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
