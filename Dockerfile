FROM python:3.11-slim

WORKDIR /app

# System dependencies for build and Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt-get/lists/*

# Copy project specification and application source code
COPY pyproject.toml requirements.txt README.md ./
COPY src/ ./src/

# Install python dependencies and local package
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Install Playwright browser binaries and OS dependencies
RUN python -m playwright install-deps chromium && \
    python -m playwright install chromium

# Create output folder for downloads
RUN mkdir -p downloads

ENV PYTHONUNBUFFERED=1
ENV SCRAPER_HEADLESS=true

EXPOSE 8765

CMD ["torrent-dashboard", "--host", "0.0.0.0", "--port", "8765"]
