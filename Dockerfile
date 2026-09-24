FROM python:3.11-slim-bookworm

# Environment settings: unbuffered output and Playwright browser path
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Install base OS dependencies for Playwright and health check
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies manifest and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium engine and system browser dependencies
RUN playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

# Copy application codebase
COPY . .

# Ensure persistent mount directories exist
RUN mkdir -p /app/data /app/logs

# Healthcheck testing the dashboard public static route
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/style.css || exit 1

EXPOSE 8000

# Start dashboard and APScheduler background publisher simultaneously
CMD ["python", "main.py", "--dashboard", "--port", "8000", "--with-scheduler"]
