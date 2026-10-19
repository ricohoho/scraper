#!/usr/bin/env bash
set -e

# Configuration
IMAGE_NAME="torrent-scraper:local"
CONTAINER_NAME="torrent-scraper"
PORT="${PORT:-8765}"

# Resolve directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOWNLOADS_DIR="${SCRIPT_DIR}/downloads"

echo "=========================================="
echo " Starting Torrent Scraper in Docker (Local)"
echo "=========================================="

# 1. Build local Docker image
echo "==> [1/4] Building local Docker image '$IMAGE_NAME'..."
docker build -t "$IMAGE_NAME" "$SCRIPT_DIR"

# 2. Stop & remove existing container if running
echo "==> [2/4] Stopping existing container '$CONTAINER_NAME' if running..."
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

# 3. Ensure persistent downloads directory exists
echo "==> [3/4] Ensuring persistent downloads folder exists ($DOWNLOADS_DIR)..."
mkdir -p "$DOWNLOADS_DIR"

# 4. Prepare env file option
ENV_OPTION=()
if [ -f "$SCRIPT_DIR/.env" ]; then
  echo "==> [INFO] Loading environment variables from local .env file"
  ENV_OPTION+=("--env-file" "$SCRIPT_DIR/.env")
else
  echo "==> [WARN] .env file not found, running without --env-file"
fi

# 5. Run Docker container
echo "==> [4/4] Starting container '$CONTAINER_NAME'..."
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  -p "${PORT}:8765" \
  -v "$DOWNLOADS_DIR:/app/downloads" \
  "${ENV_OPTION[@]}" \
  -e SCRAPER_HEADLESS=true \
  "$IMAGE_NAME"

echo "=========================================="
echo " Container successfully started!"
echo " Web Interface: http://localhost:${PORT}"
echo " View logs:     docker logs -f $CONTAINER_NAME"
echo " Stop container: docker stop $CONTAINER_NAME"
echo "=========================================="