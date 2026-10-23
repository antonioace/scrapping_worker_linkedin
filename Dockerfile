# syntax=docker/dockerfile:1
FROM python:3.12-bookworm AS runtime
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
COPY api_sync.py linkedin_jobs.py linkedin_cookies.py job_details.py stack_filter.py ./
USER scraper
ENTRYPOINT ["python", "linkedin_jobs.py"]
CMD ["--cookies-file", "/run/secrets/linkedin.cookies.json", "--output", "/data/reports"]
