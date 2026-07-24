# Build from the repository root so the release VERSION file is included:
#   docker build -f backend/Dockerfile .
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt "gunicorn>=22"

COPY VERSION ./VERSION
COPY backend/ ./backend/

RUN useradd --create-home appuser \
    && chown -R appuser:appuser /srv
USER appuser

WORKDIR /srv/backend
EXPOSE 8000

ENTRYPOINT ["./docker/entrypoint.sh"]
