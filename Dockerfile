FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    GIF_PORT=8765 \
    HISTORY_DIR=/data

# tzdata so TZ=Europe/London etc. applies to log timestamps (slim images don't ship it)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin radar \
    && mkdir /data && chown radar /data
COPY src/pixoo_radar.py .

USER radar
EXPOSE 8765

# 200 while the main loop is alive, 503 if it stalls (urlopen raises on 503).
# Checked every 10s, not 30s: Docker Swarm keeps a task out of the ingress routing mesh
# until its first check passes, and the app's own published address has to answer before
# it will hand the Pixoo a URL to fetch.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('GIF_PORT', '8765') + '/health', timeout=4)"

CMD ["python", "/app/pixoo_radar.py"]
