# CASEDD Dockerfile
# Runs the daemon with CASEDD_NO_FB=1 (no framebuffer access inside container).
# WebSocket port 8765 and HTTP port 8080 are exposed for LAN access.

FROM python:3.12-slim

# Install system dependencies for Pillow
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        zlib1g \
        libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for runtime security
RUN useradd --system --create-home --home-dir /app --shell /usr/sbin/nologin casedd

WORKDIR /app

# Install Python dependencies first (Docker layer cache optimisation)
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy project source
COPY casedd/ ./casedd/
COPY templates/ ./templates/
COPY assets/ ./assets/

# Runtime directories
RUN mkdir -p /app/run /app/logs \
    && chown -R casedd:casedd /app

USER casedd

# Health check — HTTP viewer must respond
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/image')" \
    || exit 1

EXPOSE 8765 8080

ENV CASEDD_NO_FB=1 \
    CASEDD_LOG_LEVEL=INFO \
    CASEDD_WS_PORT=8765 \
    CASEDD_HTTP_PORT=8080

CMD ["python", "-m", "casedd"]
