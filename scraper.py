import yt_dlp
import asyncio
import os
import re
import json
import time
import threading
import logging
import urllib.parse
from typing import Optional, Dict, List
from ohio_detector import calculate_ohio_confidence, is_body_cam_or_dash_cam

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CACHE_PATH = os.path.join(DATA_DIR, "cache.json")
CACHE_LOCK = threading.Lock()
CACHE_TTL_SECONDS = 60 * 60
REQUEST_DELAY = 1.5
BATCH_DELAY = 2.0

DEFAULT_QUERIES = [
    "police body cam Ohio",
    "police dash cam Ohio",
    "Ohio police bodycam",
    "Ohio police dashcam",
    "Columbus police body cam",
    "Columbus police dash cam",
    "Cleveland police body cam",
    "Cleveland police dash cam",
    "Cincinnati police body cam",
    "Cincinnati police dash cam",
    "Toledo police body cam",
    "Toledo police dash cam",
    "Akron police body cam",
    "Akron police dash cam",
    "Dayton police body cam",
    "Dayton police dash cam",
    "Canton police body cam",
    "Canton police dash cam",
    "Youngstown police body cam",
    "Youngstown police dash cam",
    "Hamilton police body cam",
    "Hamilton police dash cam",
    "Lorain police body cam",
    "Lorain police dash cam",
    "Springfield police body cam",
    "Springfield police dash cam",
    "Parma police body cam",
    "Parma police dash cam",
    "Franklin County police body cam",
    "Franklin County police dash cam",
    "Cuyahoga County police body cam",
    "Cuyahoga County police dash cam",
    "Hamilton County police body cam",
    "Hamilton County police dash cam",
    "Summit County police body cam",
    "Summit County police dash cam",
    "Montgomery County police body cam",
    "Montgomery County police dash cam",
    "Lucas County police body cam",
    "Lucas County police dash cam",
    "Stark County police body cam",
    "Stark County police dash cam",
    "Ohio State Highway Patrol body cam",
    "Ohio State Highway Patrol dash cam",
    "OSHP body cam",
    "OSHP dash cam",
    "Ohio sheriff body cam",
    "Ohio sheriff dash cam",
    "Ohio deputy body cam",
    "Ohio deputy dash cam",
    "Ohio police BWC",
    "Ohio police body worn",
    "Ohio police body worn camera",
    "Ohio police officer body cam",
    "Ohio police officer dash cam",
    "Ohio police body camera",
    "Ohio police dash camera",
    "Ohio law enforcement body cam",
    "Ohio law enforcement dash cam",
    "police body cam footage Ohio",
    "police dash cam footage Ohio",
    "Ohio police car camera",
    "Ohio police vehicle camera",
    "Ohio police cruiser camera",
    "Ohio police windshield camera",
    "Ohio police in-car camera",
    "Ohio police patrol car camera",
    "Ohio state police body cam",
    "Ohio highway patrol body cam",
    "Ohio BCI body cam",
    "Ohio police department body cam",
    "Ohio police department dash cam",
    "police body cam Ohio 2025",
    "police dash cam Ohio 2025",
    "Ohio police body cam 2025",
    "Ohio police dash cam 2025",
    "police body cam Ohio 2024",
    "police dash cam Ohio 2024",
    "Ohio police body cam 2024",
    "Ohio police dash cam 2024",
]

def _now() -> float:
    return time.time()

def load_cache() -> dict:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    if not os.path.exists(CACHE_PATH):
        return {"videos": [], "updated_at": 0.0}
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"videos": [], "updated_at": 0.0}
        data.setdefault("videos", [])
        data.setdefault("updated_at", 0.0)
        return data
    except Exception:
        return {"videos": [], "updated_at": 0.0}

def save_cache(data: dict) -> None:
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with CACHE_LOCK:
        try:
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Cache save error: {e}")

def _score_video(video: dict) -> dict:
    ohio_score, cam_score, reason, matched_cities = calculate_ohio_confidence(video)
    confidence = (ohio_score * 0.6) + (cam_score * 0.4)
    return {
        **video,
        "ohio_score": round(ohio_score, 1),
        "cam_score": round(cam_score, 1),
        "confidence": round(confidence, 1),
        "match_reason": reason,
        "matched_cities": matched_cities,
    }

def _filter_scored(scored: list[dict], min_confidence: float, require_cam: bool) -> list[dict]:
    filtered = []
    for v in scored:
        if v["confidence"] < min_confidence:
            continue
        if require_cam and v["cam_score"] <= 0:
            continue
        if v["ohio_score"] <= 0:
            continue
        filtered.append(v)
    filtered.sort(key=lambda x: x["confidence"], reverse=True)
    return filtered

def _merge_videos(old: list[dict], new: list[dict]) -> list[dict]:
    merged = {v["id"]: v for v in old if v.get("id")}
    for v in new:
        vid_id = v.get("id")
        if not vid_id:
            continue
        existing = merged.get(vid_id)
        if existing:
            merged_video = {**existing, **v}
            for field in ("upload_date", "description"):
                if not merged_video.get(field) and existing.get(field):
                    merged_video[field] = existing[field]
            merged[vid_id] = merged_video
        else:
            merged[vid_id] = v
    return list(merged.values())

def refresh_cache_background() -> dict:
    raw_ydl = asyncio.run(search_ohio_police_videos(max_per_query=25, pages=1))

    existing = load_cache()
    merged = _merge_videos(existing.get("videos", []), raw_ydl)
    data = {
        "videos": merged,
        "updated_at": _now(),
    }
    save_cache(data)
    logger.info(f"Cache refreshed (yt-dlp): {len(merged)} videos")

    time.sleep(30 * 60)

    raw_api = _search_recent_ohio_videos_api(max_results=50)

    existing = load_cache()
    merged = _merge_videos(existing.get("videos", []), raw_api)

    scored = [_score_video(v) for v in merged]
    filtered = _filter_scored(scored, min_confidence=15.0, require_cam=False)

    missing_ids = [v["id"] for v in filtered if v.get("id") and not v.get("upload_date")][:20]
    if missing_ids:
        meta = enrich_videos_metadata(missing_ids)
        for v in filtered:
            vid_id = v.get("id")
            if vid_id and vid_id in meta:
                v.update(meta[vid_id])

    scored = [_score_video(v) for v in filtered]
    filtered = _filter_scored(scored, min_confidence=15.0, require_cam=False)

    existing = load_cache()
    merged = _merge_videos(existing.get("videos", []), filtered)
    merged.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    data = {
        "videos": merged,
        "updated_at": _now(),
    }
    save_cache(data)
    retry_null_upload_dates()
    logger.info(f"Cache refreshed (api): {len(merged)} videos")
    return data

def get_cached_videos(
    query: str = "",
    min_confidence: float = 40.0,
    sort_by: str = "confidence",
    require_cam: bool = True,
    max_results: int = 100,
    offset: int = 0,
) -> dict:
    data = load_cache()
    videos = data.get("videos", [])
    
    scored = [_score_video(v) for v in videos]
    filtered = _filter_scored(scored, min_confidence=min_confidence, require_cam=require_cam)
    
    query_lower = (query or "").strip().lower()
    if query_lower:
        filtered = [v for v in filtered if query_lower in v.get("title", "").lower() or query_lower in v.get("channel", "").lower() or query_lower in v.get("description", "").lower()]
    
    if sort_by == "upload_date_desc":
        filtered.sort(key=lambda x: x.get("upload_date") or "", reverse=True)
    elif sort_by == "upload_date_asc":
        filtered.sort(key=lambda x: x.get("upload_date") or "")
    elif sort_by == "views":
        filtered.sort(key=lambda x: x.get("view_count") or 0, reverse=True)
    elif sort_by == "duration":
        filtered.sort(key=lambda x: x.get("duration") or 0, reverse=True)
    else:
        filtered.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    
    total = len(filtered)
    paged = filtered[offset:offset + max_results]
    
    return {
        "count": total,
        "videos": paged,
        "updated_at": data.get("updated_at", 0.0),
    }

def search_youtube(query: str, max_results: int = 20, pages: int = 1) -> list[dict]:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "ignoreerrors": True,
        "skip_download": True,
        "dump_single_json": False,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.youtube.com/",
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["android"],
                "skip": ["dash", "hls"],
            },
        },
    }

    cookie_file = os.environ.get("YTDL_COOKIE_FILE")
    if cookie_file and os.path.exists(cookie_file):
        ydl_opts["cookiefile"] = cookie_file

    results = []
    seen_ids = set()
    search_query = f"ytsearch{max_results}:{query}"

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
                if info and "entries" in info:
                    for entry in info["entries"]:
                        if entry:
                            vid_id = entry.get("id")
                            if vid_id and vid_id not in seen_ids:
                                seen_ids.add(vid_id)
                                results.append({
                                    "id": vid_id,
                                    "title": entry.get("title", ""),
                                    "channel": entry.get("channel", "") or entry.get("uploader", ""),
                                    "url": entry.get("webpage_url", f"https://www.youtube.com/watch?v={vid_id}"),
                                    "thumbnail": entry.get("thumbnail") or (f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg" if vid_id else None),
                                    "duration": entry.get("duration"),
                                    "view_count": entry.get("view_count"),
                                    "upload_date": entry.get("upload_date"),
                                    "description": entry.get("description", "") or "",
                                })
            break
        except Exception as e:
            err_str = str(e)
            is_forbidden = "403" in err_str or "Forbidden" in err_str
            if is_forbidden and attempt < max_retries - 1:
                wait = 2.0 * (2 ** attempt)
                logger.warning(f"yt_dlp 403 for '{query}', retry {attempt + 1}/{max_retries} in {wait}s")
                time.sleep(wait)
            else:
                logger.error(f"Search error for '{query}': {e}")
                break
    time.sleep(REQUEST_DELAY)

    return results

def search_youtube_api(query: str, max_results: int = 20, order: str = "date") -> list[dict]:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        return []
    
    search_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&q={urllib.parse.quote(query)}&type=video&order={order}&maxResults={max_results}&key={api_key}"
    
    results = []
    video_ids = []
    try:
        req = urllib.request.Request(search_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        
        for item in data.get("items", []):
            vid_id = item.get("id", {}).get("videoId")
            if not vid_id:
                continue
            video_ids.append(vid_id)
            snippet = item.get("snippet", {})
            published_at = snippet.get("publishedAt", "")
            upload_date = published_at.split("T")[0] if published_at else None
            results.append({
                "id": vid_id,
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "url": f"https://www.youtube.com/watch?v={vid_id}",
                "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url") or snippet.get("thumbnails", {}).get("default", {}).get("url"),
                "upload_date": upload_date,
                "description": snippet.get("description", ""),
            })
    except Exception as e:
        logger.error(f"YouTube API search error for '{query}': {e}")
        return results
    
    if not video_ids:
        return results
    
    stats_map = _fetch_youtube_video_stats(video_ids, api_key)
    for v in results:
        vid_id = v.get("id")
        if vid_id in stats_map:
            v.update(stats_map[vid_id])
    
    time.sleep(REQUEST_DELAY)
    return results

def _fetch_youtube_video_stats(video_ids: list[str], api_key: str) -> dict[str, dict]:
    if not video_ids or not api_key:
        return {}
    
    id_map = {}
    batch_size = 50
    for i in range(0, len(video_ids), batch_size):
        batch = video_ids[i:i + batch_size]
        ids_param = ",".join(batch)
        url = f"https://www.googleapis.com/youtube/v3/videos?part=statistics,contentDetails&id={ids_param}&key={api_key}"
        
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            
            for item in data.get("items", []):
                vid_id = item.get("id")
                stats = item.get("statistics", {})
                content = item.get("contentDetails", {})
                view_count = stats.get("viewCount")
                if view_count is not None:
                    try:
                        view_count = int(view_count)
                    except (TypeError, ValueError):
                        view_count = None
                duration_str = content.get("duration", "")
                duration = _parse_iso8601_duration(duration_str) if duration_str else None
                id_map[vid_id] = {
                    "view_count": view_count,
                    "duration": duration,
                }
        except Exception as e:
            logger.error(f"YouTube API stats error: {e}")
        if i + batch_size < len(video_ids):
            time.sleep(BATCH_DELAY)
    
    return id_map

def _parse_iso8601_duration(duration: str) -> int:
    import re
    pattern = re.compile(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')
    m = pattern.match(duration)
    if not m:
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds

RECENT_OHIO_QUERIES = [
    "Ohio police body cam",
    "Ohio police dash cam",
    "Ohio bodycam",
    "Ohio dashcam",
    "OSHP body cam",
    "OSHP dash cam",
    "Columbus police body cam",
    "Cleveland police body cam",
    "Cincinnati police body cam",
    "Ohio police arrest",
    "Ohio police traffic stop",
    "Ohio law enforcement body cam",
    "Ohio sheriff body cam",
    "Ohio police footage",
    "body cam Ohio 2025",
    "dash cam Ohio 2025",
    "body cam Ohio 2026",
    "dash cam Ohio 2026",
]

def _search_recent_ohio_videos_api(max_results: int = 50) -> list[dict]:
    all_videos = {}
    for query in RECENT_OHIO_QUERIES:
        results = search_youtube_api(query, max_results=max_results, order="date")
        logger.info(f"API query '{query}' returned {len(results)} results")
        for v in results:
            vid_id = v.get("id")
            if vid_id and vid_id not in all_videos:
                all_videos[vid_id] = v
        time.sleep(REQUEST_DELAY)
    return list(all_videos.values())

def score_video(video: dict) -> dict:
    ohio_score, cam_score, reason, matched_cities = calculate_ohio_confidence(video)
    confidence = (ohio_score * 0.6) + (cam_score * 0.4)
    
    return {
        **video,
        "ohio_score": round(ohio_score, 1),
        "cam_score": round(cam_score, 1),
        "confidence": round(confidence, 1),
        "match_reason": reason,
        "matched_cities": matched_cities,
    }

def filter_ohio_videos(videos: list[dict], min_confidence: float = 40.0, require_cam: bool = True) -> list[dict]:
    scored = [score_video(v) for v in videos]
    
    filtered = []
    for v in scored:
        if v["confidence"] >= min_confidence:
            if require_cam and v["cam_score"] <= 0:
                continue
            if v["ohio_score"] <= 0:
                continue
            filtered.append(v)
    filtered.sort(key=lambda x: x["confidence"], reverse=True)
    return filtered

def sort_videos(videos: list[dict], sort_by: str = "confidence") -> list[dict]:
    sorted_videos = list(videos)
    if sort_by == "upload_date_desc":
        sorted_videos.sort(key=lambda x: x.get("upload_date") or "", reverse=True)
    elif sort_by == "upload_date_asc":
        sorted_videos.sort(key=lambda x: x.get("upload_date") or "")
    elif sort_by == "views":
        sorted_videos.sort(key=lambda x: x.get("view_count") or 0, reverse=True)
    elif sort_by == "duration":
        sorted_videos.sort(key=lambda x: x.get("duration") or 0, reverse=True)
    else:
        sorted_videos.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return sorted_videos

async def search_ohio_police_videos(max_per_query: int = 15, pages: int = 2) -> list[dict]:
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, search_youtube, query, max_per_query, pages)
        for query in DEFAULT_QUERIES
    ]
    results_lists = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_videos = {}
    for results in results_lists:
        if isinstance(results, Exception):
            logger.error(f"Query failed: {results}")
            continue
        for v in results:
            vid_id = v.get("id")
            if vid_id and vid_id not in all_videos:
                all_videos[vid_id] = v
    
    raw = list(all_videos.values())
    filtered = filter_ohio_videos(raw, min_confidence=40.0, require_cam=True)
    
    return filtered

async def search_custom(query: str, max_results: int = 20, min_confidence: float = 40.0, require_cam: bool = True, sort_by: str = "confidence", pages: int = 1) -> list[dict]:
    loop = asyncio.get_event_loop()
    results = await loop.run_in_executor(None, search_youtube, query, max_results, pages)
    filtered = filter_ohio_videos(results, min_confidence=min_confidence, require_cam=require_cam)
    return sort_videos(filtered, sort_by=sort_by)

def enrich_videos_metadata(video_ids: list[str]) -> dict[str, dict]:
    if not video_ids:
        return {}
    
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if api_key:
        return _enrich_with_youtube_api(video_ids, api_key)
    
    id_map = {}
    for vid_id in video_ids:
        try:
            import urllib.request
            url = f"https://www.youtube.com/watch?v={vid_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            
            upload_date = None
            m = re.search(r'"uploadDate"\s*:\s*"([^"]+)"', html)
            if m:
                upload_date = m.group(1)
            else:
                m = re.search(r'\b(20\d{2})(\d{2})(\d{2})\b', html)
                if m:
                    upload_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            
            description = ""
            m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', html)
            if m:
                description = m.group(1).encode('utf-8').decode('unicode_escape', errors='ignore')
            
            id_map[vid_id] = {
                "upload_date": upload_date,
                "description": description,
            }
        except Exception as e:
            logger.error(f"HTML enrichment failed for {vid_id}: {e}")
        time.sleep(REQUEST_DELAY)
    
    return id_map

def retry_null_upload_dates(max_retries: int = 3, delay_seconds: float = 2.0) -> int:
    data = load_cache()
    videos = data.get("videos", [])
    null_ids = [v["id"] for v in videos if v.get("id") and not v.get("upload_date")]
    if not null_ids:
        return 0
    fixed = 0
    for attempt in range(max_retries):
        remaining = [vid_id for vid_id in null_ids if not _has_upload_date(videos, vid_id)]
        if not remaining:
            break
        batch_size = 50
        for i in range(0, len(remaining), batch_size):
            batch = remaining[i:i + batch_size]
            api_key = os.environ.get("YOUTUBE_API_KEY")
            if api_key:
                meta = _enrich_with_youtube_api(batch, api_key)
            else:
                meta = {}
            still_null = []
            for vid_id in batch:
                if vid_id in meta and meta[vid_id].get("upload_date"):
                    _update_video_field(videos, vid_id, "upload_date", meta[vid_id]["upload_date"])
                    fixed += 1
                else:
                    still_null.append(vid_id)
            if still_null and attempt == max_retries - 1:
                html_meta = _enrich_via_html(still_null)
                for vid_id, info in html_meta.items():
                    if info.get("upload_date"):
                        _update_video_field(videos, vid_id, "upload_date", info["upload_date"])
                        fixed += 1
            if i + batch_size < len(remaining):
                time.sleep(delay_seconds)
        if attempt < max_retries - 1:
            time.sleep(delay_seconds * 2)
    if fixed > 0:
        data["videos"] = videos
        data["updated_at"] = _now()
        save_cache(data)
        logger.info(f"Retry fixed {fixed} null upload dates")
    return fixed

def _has_upload_date(videos: list[dict], vid_id: str) -> bool:
    for v in videos:
        if v.get("id") == vid_id:
            return bool(v.get("upload_date"))
    return False

def _update_video_field(videos: list[dict], vid_id: str, field: str, value: str) -> None:
    for v in videos:
        if v.get("id") == vid_id:
            v[field] = value
            return

def _enrich_via_html(video_ids: list[str]) -> dict[str, dict]:
    id_map = {}
    for vid_id in video_ids:
        try:
            url = f"https://www.youtube.com/watch?v={vid_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            upload_date = None
            m = re.search(r'"uploadDate"\s*:\s*"([^"]+)"', html)
            if m:
                upload_date = m.group(1)
            else:
                m = re.search(r'\b(20\d{2})(\d{2})(\d{2})\b', html)
                if m:
                    upload_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            id_map[vid_id] = {"upload_date": upload_date}
        except Exception as e:
            logger.error(f"HTML retry enrichment failed for {vid_id}: {e}")
    return id_map

def _enrich_with_youtube_api(video_ids: list[str], api_key: str) -> dict[str, dict]:
    if not video_ids:
        return {}
    
    id_map = {}
    batch_size = 50
    for i in range(0, len(video_ids), batch_size):
        batch = video_ids[i:i + batch_size]
        ids_param = ",".join(batch)
        url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet&id={ids_param}&key={api_key}"
        
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            
            for item in data.get("items", []):
                vid_id = item.get("id")
                snippet = item.get("snippet", {})
                published_at = snippet.get("publishedAt", "")
                if published_at:
                    upload_date = published_at.split("T")[0]
                else:
                    upload_date = None
                description = snippet.get("description", "") or ""
                id_map[vid_id] = {
                    "upload_date": upload_date,
                    "description": description,
                }
        except Exception as e:
            logger.error(f"YouTube API enrichment error: {e}")
        if i + batch_size < len(video_ids):
            time.sleep(BATCH_DELAY)
    
    return id_map
