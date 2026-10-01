"""Cache read/write integrity (PATROLTUBE-004).

A non-atomic truncate-in-place write let a concurrent reader hit JSONDecodeError,
and the broad `except` turned that into an empty cache — the dashboard reported
0 videos mid-refresh and the next refresh merged against the empty list.
"""

import ast
import json
import os
import threading
from pathlib import Path

import pytest

import scraper


@pytest.fixture()
def tmp_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "cache.json"
    monkeypatch.setattr(scraper, "CACHE_PATH", str(cache_path))
    monkeypatch.setattr(scraper, "_LAST_GOOD_CACHE", {"videos": [], "updated_at": 0.0})
    monkeypatch.setattr(scraper, "_SCORED_MEMO", {"mtime": None, "videos": []})
    return str(cache_path)


def _video(i):
    return {
        "id": f"vid{i:07d}",
        "title": f"Columbus police body cam {i}",
        "channel": "Ohio News",
        "url": f"https://www.youtube.com/watch?v=vid{i:07d}",
        "thumbnail": "https://i.ytimg.com/vi/x/default.jpg",
        "description": "Ohio State Highway Patrol dash cam footage",
        "duration": 300,
        "view_count": 1000,
        "upload_date": "20260101",
    }


def test_save_cache_is_atomic_and_leaves_no_temp_file(tmp_cache):
    scraper.save_cache({"videos": [_video(i) for i in range(5)], "updated_at": 1.0})
    assert os.path.exists(tmp_cache)
    assert not os.path.exists(tmp_cache + ".tmp")
    with open(tmp_cache, encoding="utf-8") as f:
        assert len(json.load(f)["videos"]) == 5


def test_concurrent_reader_never_sees_a_partial_file(tmp_cache):
    scraper.save_cache({"videos": [_video(i) for i in range(200)], "updated_at": 1.0})
    errors = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                with open(tmp_cache, encoding="utf-8") as f:
                    json.load(f)
            except json.JSONDecodeError as e:
                errors.append(str(e))
                break
            except FileNotFoundError:
                errors.append("file disappeared mid-replace")
                break

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for i in range(40):
            scraper.save_cache({"videos": [_video(j) for j in range(200)], "updated_at": float(i)})
    finally:
        stop.set()
        t.join(timeout=5)
    assert errors == []


def test_corrupt_cache_serves_last_good_copy_and_logs(tmp_cache, caplog):
    scraper.save_cache({"videos": [_video(i) for i in range(3)], "updated_at": 1.0})
    good = scraper.load_cache()
    assert len(good["videos"]) == 3

    with open(tmp_cache, "w", encoding="utf-8") as f:
        f.write('{"videos": [{"id": "trunc')

    with caplog.at_level("ERROR"):
        recovered = scraper.load_cache()

    assert len(recovered["videos"]) == 3, "corrupt read must not report an empty cache"
    assert any("unreadable" in r.message for r in caplog.records), "read failure must be logged loudly"


def test_non_dict_cache_root_is_logged_not_swallowed(tmp_cache, caplog):
    with open(tmp_cache, "w", encoding="utf-8") as f:
        json.dump([1, 2, 3], f)
    with caplog.at_level("ERROR"):
        result = scraper.load_cache()
    assert result["videos"] == []
    assert any("expected dict" in r.message for r in caplog.records)


def test_get_cached_videos_clamps_negative_max_results(tmp_cache):
    scraper.save_cache({"videos": [_video(i) for i in range(20)], "updated_at": 1.0})
    result = scraper.get_cached_videos(min_confidence=0.0, require_cam=False, max_results=-5, offset=0)
    assert 0 < len(result["videos"]) <= 200
    assert len(result["videos"]) == result["count"] or result["count"] >= len(result["videos"])


def test_scored_memo_avoids_rescoring_on_every_request(tmp_cache, monkeypatch):
    scraper.save_cache({"videos": [_video(i) for i in range(10)], "updated_at": 1.0})
    calls = {"n": 0}
    real_score = scraper._score_video

    def counting_score(v):
        calls["n"] += 1
        return real_score(v)

    monkeypatch.setattr(scraper, "_score_video", counting_score)
    scraper.get_cached_videos(min_confidence=0.0, require_cam=False)
    first = calls["n"]
    scraper.get_cached_videos(min_confidence=0.0, require_cam=False)
    assert calls["n"] == first, "second request must reuse the mtime-keyed memo"


def test_urllib_request_is_imported_at_module_scope():
    """PATROLTUBE-013: two functions called urllib.request with no import, and
    only worked because yt-dlp imports it as a side effect. If yt-dlp stops, the
    whole YouTube Data API path raised inside a bare `except Exception`."""
    tree = ast.parse(Path(scraper.__file__).read_text(encoding="utf-8"))

    def imports_urllib_request(node):
        for child in ast.walk(node):
            if isinstance(child, ast.Import):
                if any(alias.name == "urllib.request" for alias in child.names):
                    return True
        return False

    assert imports_urllib_request(tree), "no module-level `import urllib.request`"
    function_level = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and imports_urllib_request(n)]
    assert function_level == [], f"redundant function-local urllib.request imports remain: {function_level}"
    assert scraper.urllib.request.urlopen is not None


class _FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_html_enrichment_does_not_guess_a_date_from_an_error_page(monkeypatch):
    """PATROLTUBE-007: a non-existent id still returns HTTP 200, and the old code
    scraped the first bare YYYYMMDD off that page, stamping today's date on every
    bad id."""
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    page = '<html><body>Video unavailable <span>20260930</span></body></html>'
    monkeypatch.setattr(scraper.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(page))
    monkeypatch.setattr(scraper.time, "sleep", lambda *_: None)

    result = scraper.enrich_videos_metadata(["aaaaaaaaaaa"])
    assert result["aaaaaaaaaaa"]["upload_date"] is None


def test_html_enrichment_still_reads_a_real_upload_date(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    page = '<html><script>{"uploadDate":"2024-11-02T10:00:00-04:00"}</script></html>'
    monkeypatch.setattr(scraper.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(page))
    monkeypatch.setattr(scraper.time, "sleep", lambda *_: None)

    result = scraper.enrich_videos_metadata(["dQw4w9WgXcQ"])
    assert result["dQw4w9WgXcQ"]["upload_date"] == "2024-11-02T10:00:00-04:00"
