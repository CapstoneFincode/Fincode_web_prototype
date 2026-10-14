FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 fincode

COPY backend/ ./backend/
COPY config/ ./config/
COPY database/ ./database/
COPY public/ ./public/
USER fincode

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

CMD ["sh", "-c", "python -m backend.migrate && exec uvicorn backend.app:app --host 0.0.0.0 --port 8000 --no-access-log"]
