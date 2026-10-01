"""End-to-end browser proof for PATROLTUBE-001.

Skipped unless a Chrome binary and `node` are both present, because the unit
harness cannot prove that a hostile title actually lands as inert text in a real
engine. Serves the real templates from a local stub, feeds one poisoned record
through the page's own renderVideos(), and asserts document.title is untouched.
"""

import functools
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "templates"

PAYLOAD = '<img src=x onerror="window.__XSS__=1;document.title=\'PWNED-TITLE\'">'

POISON = {
    "id": "dQw4w9WgXcQ",
    "title": PAYLOAD,
    "channel": PAYLOAD,
    "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
    "duration": 212,
    "view_count": 12345,
    "upload_date": "20260101",
    "description": "bodycam",
    "confidence": 99.9,
    "cam_score": 100,
    "ohio_score": 80,
    "match_reason": PAYLOAD,
    "matched_cities": [PAYLOAD],
}


def _chrome():
    for p in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ):
        if os.path.exists(p):
            return p
    return shutil.which("chrome") or shutil.which("google-chrome")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.skipif(_chrome() is None, reason="no Chrome binary available")
@pytest.mark.skipif(shutil.which("node") is None, reason="node required to drive the page script")
@pytest.mark.parametrize("template", ["index.html", "mobile.html"])
def test_hostile_title_does_not_execute_in_a_real_browser(template):
    chrome = _chrome()
    port = _free_port()
    served = Path(os.environ.get("TEMP", ".")) / f"ptxss-{port}"
    served.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "mobile.html", "style.css"):
        src = TEMPLATES / name if (TEMPLATES / name).exists() else REPO_ROOT / "static" / name
        if src.exists():
            (served / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    # Neutralise the page's own loader so its async render cannot clear the grid
    # after the probe renders, then run renderVideos() with the poisoned record.
    head = "<script>window.fetch = () => new Promise(() => {});</script>\n"
    page = (served / template).read_text(encoding="utf-8")
    page = page.replace("<head>", "<head>\n" + head, 1)
    driver = """
<script>
(async () => {
  const poison = %s;
  if (typeof renderVideos !== 'function') { document.title = 'NOFN'; return; }
  try { renderVideos([poison]); } catch (e) { document.title = 'THREW:' + e.message; return; }
  await new Promise(r => setTimeout(r, 500));
  const card = document.querySelector('.video-card');
  const title = card && card.querySelector('.title');
  document.title = (card ? 'RENDERED:' : 'NOCARD:')
    + 'xss=' + (window.__XSS__ ? 1 : 0)
    + '|onerror=' + (document.querySelectorAll('[onerror]').length)
    + '|text=' + (title ? title.textContent.indexOf('<img') >= 0 ? 1 : 0 : -1);
})();
</script>
""" % json.dumps(POISON)
    (served / template).write_text(page.replace("</body>", driver + "</body>"), encoding="utf-8")

    handler = type("H", (SimpleHTTPRequestHandler,), {"log_message": lambda *a, **k: None})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), functools.partial(handler, directory=str(served)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        proc = subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--virtual-time-budget=6000", "--dump-dom", f"http://127.0.0.1:{port}/{template}"],
            capture_output=True, text=True, timeout=90,
        )
        dom = proc.stdout
    finally:
        httpd.shutdown()
        shutil.rmtree(served, ignore_errors=True)

    title = re.search(r"<title>(.*?)</title>", dom, re.S)
    assert title, dom[:400]
    result = title.group(1)
    assert "PWNED-TITLE" not in result, f"hostile title EXECUTED: {result}"
    assert "RENDERED:" in result, f"card did not render: {result}"
    assert "xss=0" in result, f"injected handler fired: {result}"
    assert "onerror=0" in result, f"onerror attribute present in DOM: {result}"
    assert "text=1" in result, f"payload did not land as literal text: {result}"
