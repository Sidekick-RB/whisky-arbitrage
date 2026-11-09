# Whisky deal-hunt scraper — weekly run on AWS (ECS Fargate)
# Python 3.12 slim + Playwright Chromium (headless). The hunt job is a
# CLI (python -m hunt.run), not the old Flask prototype in app.py.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Playwright Chromium system dependencies (Debian slim has none of them).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libasound2 libpango-1.0-0 libcairo2 libx11-6 libxcb1 \
    libxext6 fonts-liberation curl \
    && rm -rf /var/lib/apt/lists/*

# Hunt dependencies only. The old requirements.txt at the repo root is
# UTF-16 with CRLF line endings and pins the Flask prototype's stack;
# it is intentionally NOT used here.
COPY hunt/requirements.txt ./hunt-requirements.txt
RUN pip install -r hunt-requirements.txt \
    && python -m playwright install chromium

COPY hunt/ ./hunt/

# No default command arguments: the scheduler supplies --out/--state paths.
ENTRYPOINT ["python", "-m", "hunt.run"]
