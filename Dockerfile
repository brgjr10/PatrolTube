# Replace python:3.11-slim with a digest pin (python:3.11-slim@sha256:<digest>)
# before the next production build; a floating tag can move underneath you.
# PATROLTUBE-010.
FROM python:3.11-slim

WORKDIR /app

# ffmpeg was installed but never invoked: yt-dlp runs with skip_download=True and
# there is no subprocess call anywhere in the app, so it only doubled image size.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The cache lives in /app/data. docker-compose.yml mounts a named volume here,
# so Docker seeds it from this directory and appuser keeps ownership — no host
# uid/gid is imposed, which is what used to force the container back to root.
# Direct `docker run` still gets the baked-in appuser and its writable data dir.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app/data

USER appuser

EXPOSE 8000

# Without a healthcheck, `restart: unless-stopped` cannot tell a live-but-500ing
# container from a healthy one, which is how PATROLTUBE-006 stayed hidden.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/cache/status', timeout=4)" || exit 1

CMD ["python", "app.py"]
