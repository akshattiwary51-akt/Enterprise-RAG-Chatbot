FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv/backend

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --system --uid 10001 --home /srv app \
    && mkdir -p /srv/data/uploads /srv/data/chroma /srv/data/logs \
    && chown -R app:app /srv/data /srv/backend \
    && chmod +x docker-entrypoint.sh
USER app

ENV DATA_DIR=/srv/data FLASK_APP=wsgi.py
EXPOSE 5000
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
  CMD curl -fsS http://127.0.0.1:5000/api/health || exit 1
ENTRYPOINT ["./docker-entrypoint.sh"]
