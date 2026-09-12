# Dockerfile — для BotHost (бот-публикатор)
FROM python:3.11-slim

WORKDIR /app

# Не буферизуем вывод Python — сразу видим логи
ENV PYTHONUNBUFFERED=1
ENV PIP_DEFAULT_TIMEOUT=120
ENV PIP_RETRIES=10

# Системные зависимости (минимум)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Зависимости Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Код
COPY . .

# Папка для данных
ENV DATA_DIR=/app/data
RUN mkdir -p /app/data && chmod 777 /app/data

# Порт
EXPOSE 3000

# Запуск
CMD ["python", "app.py"]
