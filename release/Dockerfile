FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.119.0 httpx==0.28.1 uvicorn==0.37.0
COPY backend/summary_quality.py backend/deployment.py ./backend/
COPY datasets/ground_truth_set/v1/emails.json ./datasets/ground_truth_set/v1/emails.json
COPY public/style.css public/dashboard.css public/favicon.svg public/result-comparison.js public/result-comparison.css ./public/
COPY release/public_site.py release/history.py release/matched.py ./release/
COPY release/web/ ./release/web/
USER 65534:65534
EXPOSE 8000
CMD ["python", "-m", "release.public_site"]
