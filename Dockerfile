# syntax=docker/dockerfile:1
FROM python:3.12-bookworm AS worker
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
WORKDIR /app
COPY requirements.txt .
# Install the browser matching the installed Python package, including Linux libs.
RUN pip install -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && groupadd --gid 10001 scraper \
    && useradd --uid 10001 --gid scraper --create-home scraper \
    && mkdir -p /data \
    && chown scraper:scraper /data
COPY api_sync.py linkedin_jobs.py linkedin_cookies.py ./
USER scraper
ENTRYPOINT ["python", "linkedin_jobs.py"]
CMD ["--require-session", "--profile", "/data/linkedin-profile", "--output", "/data/reports"]

# Optional one-off interactive login, accessible through an SSH tunnel only.
FROM worker AS login
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify \
    && rm -rf /var/lib/apt/lists/*
COPY docker/login.py docker/login.py
USER scraper
ENTRYPOINT ["python", "docker/login.py"]
CMD ["--profile", "/data/linkedin-profile", "--output", "/data/reports", "--login-timeout", "900"]

# The default image runs the headless worker, not the login desktop.
FROM worker AS runtime
