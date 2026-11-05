# Docker runtime is required (not the plain Python buildpack) because the Live Stream Viewers
# service needs ffmpeg on PATH for pytgcalls to join a voice/group call with a real media stream.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libopus0 \
        libsodium23 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# run as an unprivileged user: a bug in the web panel / session import must not hand out root in the container
USER app
EXPOSE 8000
# Render (and most hosts) tell the app which port to bind through $PORT
CMD ["sh", "-c", "exec uvicorn main:api --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
