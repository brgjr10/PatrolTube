"""Route smoke tests.

`GET /` and `GET /mobile` 500'd under starlette 1.3.1 because the routes used the
legacy positional `TemplateResponse(name, context)` form. The entire product was
down and nothing caught it, so these are the acceptance bar (PATROLTUBE-006,
PATROLTUBE-025).
"""

import os
import subprocess
import sys
from pathlib import Path

import yaml
from conftest import DESKTOP_UA, MOBILE_UA

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_dashboard_returns_200_for_desktop(client):
    resp = client.get("/", headers={"user-agent": DESKTOP_UA})
    assert resp.status_code == 200, resp.text[:500]
    assert "PatrolTube" in resp.text


def test_mobile_returns_200_for_mobile_ua(client):
    resp = client.get("/mobile", headers={"user-agent": MOBILE_UA})
    assert resp.status_code == 200, resp.text[:500]
    assert "PatrolTube" in resp.text


def test_dashboard_redirects_mobile_ua(client):
    resp = client.get("/", headers={"user-agent": MOBILE_UA}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/mobile"


def test_cache_status_returns_200(client):
    resp = client.get("/api/cache/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "count" in body and "updated_at" in body and "stale" in body


def test_cache_rejects_out_of_range_max_results(client):
    assert client.get("/api/cache?max_results=0").status_code == 422
    assert client.get("/api/cache?max_results=1000000000").status_code == 422
    assert client.get("/api/cache?max_results=-5").status_code == 422
    assert client.get("/api/cache?offset=-1").status_code == 422
    assert client.get("/api/cache?max_results=abc").status_code == 422
    assert client.get("/api/cache?max_results=25").status_code == 200


def test_security_headers_present(client):
    resp = client.get("/api/cache/status")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    csp = resp.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp


def test_cors_does_not_reflect_arbitrary_origin(client):
    resp = client.get("/api/cache/status", headers={"Origin": "https://evil.example"})
    assert resp.headers.get("access-control-allow-origin") != "https://evil.example"
    assert resp.headers.get("access-control-allow-credentials") is None


def test_cors_allows_configured_origin(client):
    resp = client.get("/api/cache/status", headers={"Origin": "http://localhost:8001"})
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:8001"


def test_enrich_rejects_non_video_id_strings(client, monkeypatch):
    import app as app_module

    called = {}

    def fake_enrich(ids):
        called["ids"] = ids
        return {}

    monkeypatch.setattr(app_module, "enrich_videos_metadata", fake_enrich)
    resp = client.post(
        "/api/enrich",
        json={"video_ids": ["../../../../etc/passwd", "http://169.254.169.254/latest/", "dQw4w9WgXcQ"]},
    )
    assert resp.status_code == 200
    assert called["ids"] == ["dQw4w9WgXcQ"]


def test_enrich_rejects_bare_string_body(client):
    resp = client.post("/api/enrich", json={"video_ids": "notalist"})
    assert resp.status_code == 422


def test_refresh_returns_immediately_instead_of_blocking(client, monkeypatch):
    """PATROLTUBE-003: awaiting refresh_cache_background() pinned an executor
    worker for 30+ minutes because that function sleeps 30 minutes mid-run."""
    import app as app_module

    ran = []
    monkeypatch.setattr(app_module, "_run_refresh", lambda: ran.append(1))
    resp = client.post("/api/cache/refresh")
    assert resp.status_code == 200
    assert resp.json() == {"started": True}
    assert ran == [1], "the refresh must be handed to the background task runner"


def test_refresh_requires_the_token_when_one_is_configured(client, monkeypatch):
    import app as app_module

    ran = []
    monkeypatch.setattr(app_module, "_run_refresh", lambda: ran.append(1))
    monkeypatch.setattr(app_module, "CACHE_REFRESH_TOKEN", "s3cret-value")

    assert client.post("/api/cache/refresh").status_code == 401
    assert client.post("/api/cache/refresh", headers={"x-cache-refresh-token": "wrong"}).status_code == 401
    assert ran == [], "a rejected caller must never start a refresh"

    ok = client.post("/api/cache/refresh", headers={"x-cache-refresh-token": "s3cret-value"})
    assert ok.status_code == 200
    assert ok.json() == {"started": True}
    assert ran == [1]


def test_refresh_reports_a_second_call_while_one_is_running(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "_run_refresh", lambda: None)
    monkeypatch.setitem(app_module._refresh_state, "running", True)
    try:
        resp = client.post("/api/cache/refresh")
        assert resp.status_code == 200
        assert resp.json()["started"] is False
    finally:
        app_module._refresh_state["running"] = False


def test_expensive_routes_are_rate_limited(client, monkeypatch):
    """PATROLTUBE-003: no limiter existed, so one caller could drive 80 yt-dlp
    searches per request and get the scraper IP-banned."""
    import app as app_module

    app_module._RATE_LIMIT_HITS.clear()
    try:
        statuses = [client.post("/api/enrich", json={"video_ids": []}).status_code for _ in range(app_module.RATE_LIMIT_MAX_CALLS + 5)]
    finally:
        app_module._RATE_LIMIT_HITS.clear()
    assert statuses[: app_module.RATE_LIMIT_MAX_CALLS] == [200] * app_module.RATE_LIMIT_MAX_CALLS
    assert statuses[app_module.RATE_LIMIT_MAX_CALLS :] == [429] * 5


def test_rate_limited_response_still_carries_the_hardening_headers(client):
    import app as app_module

    app_module._RATE_LIMIT_HITS.clear()
    try:
        for _ in range(app_module.RATE_LIMIT_MAX_CALLS + 1):
            resp = client.post("/api/enrich", json={"video_ids": []})
    finally:
        app_module._RATE_LIMIT_HITS.clear()
    assert resp.status_code == 429
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-security-policy"]
    assert resp.headers["retry-after"] == "60"


def test_read_only_routes_are_not_rate_limited(client):
    for _ in range(40):
        assert client.get("/api/cache/status").status_code == 200


def _openapi_probe(env_value):
    script = (
        "from fastapi.testclient import TestClient\n"
        "import app\n"
        "c = TestClient(app.app)\n"
        "print(c.get('/openapi.json').status_code, c.get('/docs').status_code)\n"
    )
    env = dict(os.environ)
    if env_value is None:
        env.pop("PATROLTUBE_DISABLE_DOCS", None)
    else:
        env["PATROLTUBE_DISABLE_DOCS"] = env_value
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_schema_endpoints_are_published_by_default_and_switchable():
    """PATROLTUBE-024: /docs, /redoc and /openapi.json are FastAPI defaults and
    advertise the unauthenticated fan-out surface. Default behaviour is preserved;
    PATROLTUBE_DISABLE_DOCS=1 turns them off."""
    assert _openapi_probe(None) == "200 200"
    assert _openapi_probe("1") == "404 404"


def test_healthcheck_target_responds(client):
    """The Dockerfile HEALTHCHECK hits this exact URL; a 500 here is how
    PATROLTUBE-006 stayed invisible in production."""
    resp = client.get("/api/cache/status")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["count"], int)
    assert body["stale"] is False or body["stale"] is True


def test_healthcheck_url_is_pinned_in_the_dockerfile():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "HEALTHCHECK" in dockerfile
    assert "http://127.0.0.1:8000/api/cache/status" in dockerfile


def test_secret_files_stay_out_of_the_image():
    """PATROLTUBE-005: `COPY . .` with no .dockerignore baked the live key in."""
    ignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in ignore
    assert "data/" in ignore
    assert ".git" in ignore
    assert "__pycache__/" in ignore
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "YOUTUBE_API_KEY=" in example
    assert "AIza" not in example


def test_compose_does_not_override_the_non_root_user():
    """PATROLTUBE-010: a compose `user:` replaces the image's USER, so the old
    "${PATROLTUBE_UID:-0}" default ran the container as root and made the
    Dockerfile's appuser a no-op. The named volume is what makes dropping the
    override safe, so both halves are asserted together.

    Parsed with yaml.safe_load rather than scanned line by line: the previous
    line scanner only matched a `user:` whose key started the line, so a
    `user:` nested under the service block, or spelled `user :`, would have
    reintroduced the root default while the test stayed green."""
    compose = yaml.safe_load(
        (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    service = compose["services"]["police-video-app"]
    assert "user" not in service, "a compose `user:` overrides Dockerfile USER appuser"
    assert not any("PATROLTUBE_UID" in str(v) for v in service.values())
    assert "patroltube-data:/app/data" in service["volumes"]
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER appuser" in dockerfile
