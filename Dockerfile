FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-kor tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 fincode \
    && install -d -m 0700 -o fincode /var/lib/fincode/attachments

COPY backend/ ./backend/
COPY config/ ./config/
COPY database/ ./database/
COPY datasets/ground_truth_set/v1/ ./datasets/ground_truth_set/v1/
COPY public/ ./public/
USER fincode

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; from backend.deployment import server_port; urllib.request.urlopen('http://127.0.0.1:%s/api/ready' % server_port(), timeout=8)"

CMD ["python", "-m", "backend.start"]
