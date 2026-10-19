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
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; from backend.deployment import server_port; urllib.request.urlopen('http://127.0.0.1:%s/api/ready' % server_port(), timeout=8)"

CMD ["python", "-m", "backend.start"]
