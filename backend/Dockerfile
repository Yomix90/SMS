# --- Image de production du backend SMS Gateway ---
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production

# Polices DejaVu : rendu de l'arabe dans les exports PDF
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
RUN useradd --create-home --uid 1000 sms \
    && mkdir -p /app/instance/uploads /app/instance/exports \
    && chown -R sms:sms /app
USER sms

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# Migrations puis serveur. 1 seul worker gevent : Socket.IO et le scheduler vivent dans ce process.
CMD ["sh", "-c", "SMS_NO_SCHEDULER=1 flask --app wsgi:app db upgrade && exec gunicorn -k gevent -w 1 --worker-connections 1000 --bind 0.0.0.0:8000 --timeout 60 --graceful-timeout 30 --forwarded-allow-ips='*' --access-logfile - wsgi:app"]
