# Docker runtime is required (not the plain Python buildpack) because the Live Stream Viewers
# service needs ffmpeg on PATH for pytgcalls to join a voice/group call with a real media stream.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        libopus0 \
        libsodium23 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "main:api", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
