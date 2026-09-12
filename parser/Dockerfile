# parser/Dockerfile — для парсера (GitHub Actions)
FROM python:3.11-slim

WORKDIR /parser

# Переменные для Playwright
ENV PYTHONUNBUFFERED=1
ENV PIP_DEFAULT_TIMEOUT=120

# Системные зависимости для Chromium (для Ubuntu 22.04 / Debian 12)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    wget \
    gnupg \
    fonts-liberation \
    fonts-noto-color-emoji \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxtst6 \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Playwright + Chromium
COPY parser/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && \
    playwright install chromium

# Код парсера
COPY parser/ ./parser/

# Запуск
CMD ["python", "-m", "parser.parser"]
