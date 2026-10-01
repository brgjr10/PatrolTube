from fastapi import FastAPI, Request, Query, BackgroundTasks, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import asyncio
import secrets
import threading
import time
import re
import logging
import os
from collections import deque
from pydantic import BaseModel, Field, field_validator

from scraper import search_ohio_police_videos, search_custom, enrich_videos_metadata, refresh_cache_background, get_cached_videos, load_cache, CACHE_TTL_SECONDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# The generated schema advertises the unauthenticated fan-out surface, so it is
# switchable off for any deployment that is reachable from outside the LAN.
# PATROLTUBE-024.
DOCS_DISABLED = os.environ.get("PATROLTUBE_DISABLE_DOCS", "").strip().lower() in ("1", "true", "yes")

app = FastAPI(
    title="PatrolTube",
    docs_url=None if DOCS_DISABLED else "/docs",
    redoc_url=None if DOCS_DISABLED else "/redoc",
    openapi_url=None if DOCS_DISABLED else "/openapi.json",
)

MOBILE_PATTERN = re.compile(r"Android|iPhone|iPod|Opera Mini|IEMobile|WPDesktop|BlackBerry|Mobile|webOS|Tablet|iPad", re.I)

# Explicit allowlist: an allow_origins=["*"] + allow_credentials=True pair makes Starlette
# reflect the caller's Origin, which hands credentialed cross-origin reads to every site
# on the internet. PATROLTUBE-002.
CORS_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:8001,http://127.0.0.1:8001").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["content-type"],
)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
if not YOUTUBE_API_KEY:
    logger.error("YOUTUBE_API_KEY is not set. The app will fall back to HTML scraping, which YouTube throttles. Set YOUTUBE_API_KEY in the environment (docker-compose reads it from .env).")

# Optional shared secret for the one mutating route. Unset means the dashboard
# stays as open as it has always been; set means a caller must present it, which
# is what stops any LAN host (or any page that can reach it) from triggering a
# scraper refresh. PATROLTUBE-003.
CACHE_REFRESH_TOKEN = os.environ.get("CACHE_REFRESH_TOKEN", "").strip()
if not CACHE_REFRESH_TOKEN:
    logger.warning("CACHE_REFRESH_TOKEN is not set: POST /api/cache/refresh is open to anyone who can reach this port. Set it to require an X-Cache-Refresh-Token header.")

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


# Without a CSP an injected handler in templates/*.html runs with full page
# privileges, which is what turned PATROLTUBE-001 from markup injection into
# script execution. PATROLTUBE-009.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "img-src 'self' data: https://i.ytimg.com https://ytimg.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    ),
}


# Sliding-window limiter. The app has no auth and its endpoints fan out to
# YouTube, so an anonymous caller could otherwise drive the scraper into an
# IP ban that degrades the cache for everyone (PATROLTUBE-003).
RATE_LIMIT_MAX_CALLS = 30
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_MAX_CLIENTS = 2048
_RATE_LIMIT_HITS: dict[str, deque] = {}
_RATE_LIMIT_LOCK = threading.Lock()

EXPENSIVE_ROUTES = ("/api/videos", "/api/search", "/api/enrich", "/api/cache/refresh")


def _rate_limited(client_id: str) -> bool:
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with _RATE_LIMIT_LOCK:
        if client_id not in _RATE_LIMIT_HITS and len(_RATE_LIMIT_HITS) >= RATE_LIMIT_MAX_CLIENTS:
            # Keyed by remote address, so a spoofed-header flood would otherwise
            # grow this map without bound; the LAN does not have that many clients.
            for stale_id in [k for k, v in _RATE_LIMIT_HITS.items() if not v or v[-1] < cutoff]:
                del _RATE_LIMIT_HITS[stale_id]
        hits = _RATE_LIMIT_HITS.setdefault(client_id, deque())
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= RATE_LIMIT_MAX_CALLS:
            return True
        hits.append(now)
        return False


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    if request.url.path in EXPENSIVE_ROUTES:
        client_id = request.client.host if request.client else "unknown"
        if _rate_limited(client_id):
            logger.warning(f"Rate limit hit for {client_id} on {request.url.path}")
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Try again shortly."},
                headers={"Retry-After": str(int(RATE_LIMIT_WINDOW_SECONDS))},
            )
    return await call_next(request)


# Registered last so it is the outermost middleware: a 429 from the limiter and
# a 401 from the refresh gate must still carry the hardening headers.
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    return response


YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class EnrichRequest(BaseModel):
    video_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("video_ids")
    @classmethod
    def _validate_ids(cls, value):
        # Outbound URLs are built as https://www.youtube.com/watch?v=<id>, so a
        # non-conforming id can only inject path/query junk. Reject it here
        # instead of fetching it (PATROLTUBE-007).
        return [v for v in value if YOUTUBE_VIDEO_ID.match(v)]

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if request.state.is_mobile:
        return RedirectResponse(url="/mobile", status_code=302)
    return templates.TemplateResponse(request=request, name="index.html", context={})

@app.get("/mobile", response_class=HTMLResponse)
async def mobile_dashboard(request: Request):
    if not request.state.is_mobile:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse(request=request, name="mobile.html", context={})

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
async def enrich_metadata(payload: EnrichRequest):
    video_ids = payload.video_ids
    if not video_ids:
        return {"results": {}}
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, enrich_videos_metadata, video_ids[:20])
    return {"results": results}

@app.get("/api/cache")
async def get_cache(q: str = "", min_confidence: float = 40.0, sort_by: str = "confidence", require_cam: bool = True, max_results: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    return get_cached_videos(query=q, min_confidence=min_confidence, sort_by=sort_by, require_cam=require_cam, max_results=max_results, offset=offset)

@app.get("/api/cache/status")
async def cache_status():
    data = load_cache()
    return {
        "count": len(data.get("videos", [])),
        "updated_at": data.get("updated_at", 0.0),
        "age_seconds": time.time() - data.get("updated_at", 0.0) if data.get("updated_at") else None,
        "stale": bool(data.get("updated_at")) and (time.time() - data["updated_at"]) > CACHE_TTL_SECONDS,
    }

_refresh_lock = threading.Lock()
_refresh_state = {"running": False}

def _run_refresh() -> None:
    with _refresh_lock:
        if _refresh_state["running"]:
            logger.warning("Cache refresh already in progress; skipping duplicate run")
            return
        _refresh_state["running"] = True
    try:
        refresh_cache_background()
    except Exception as e:
        logger.error(f"Background cache refresh failed: {e}")
    finally:
        with _refresh_lock:
            _refresh_state["running"] = False

@app.post("/api/cache/refresh")
async def refresh_cache(request: Request, background_tasks: BackgroundTasks):
    # refresh_cache_background() sleeps 30 minutes between passes, so awaiting it
    # here pinned an executor worker for half an hour. Hand it to Starlette's
    # background task runner and return immediately (PATROLTUBE-003).
    if CACHE_REFRESH_TOKEN:
        supplied = request.headers.get("x-cache-refresh-token", "")
        if not secrets.compare_digest(supplied, CACHE_REFRESH_TOKEN):
            raise HTTPException(status_code=401, detail="Invalid or missing X-Cache-Refresh-Token header")
    if _refresh_state["running"]:
        return {"started": False, "reason": "refresh already in progress"}
    background_tasks.add_task(_run_refresh)
    return {"started": True}

def _start_cache_refresh_thread():
    def worker():
        while True:
            time.sleep(CACHE_TTL_SECONDS)
            _run_refresh()
    t = threading.Thread(target=worker, daemon=True)
    t.start()

_start_cache_refresh_thread()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
