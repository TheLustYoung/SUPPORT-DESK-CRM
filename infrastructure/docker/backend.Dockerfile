# One image for api, worker, beat and the bot (different commands).
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# DejaVu: PDF reports with Cyrillic, Arabic and Hebrew letters
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
COPY bots ./bots
COPY workers ./workers
COPY infrastructure ./infrastructure
RUN pip install . && useradd --uid 10001 --create-home app && mkdir -p /data/files /data/backups && chown 10001:10001 /data/files /data/backups
COPY alembic.ini ./
USER 10001:10001
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-proxy-headers"]
