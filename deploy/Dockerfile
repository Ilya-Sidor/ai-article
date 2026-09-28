# API and worker image
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd --create-home --uid 10001 app
WORKDIR /srv
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt
COPY backend/app backend/app
COPY backend/evals backend/evals
COPY backend/sandbox_service.py backend/sandbox_service.py
COPY frontend frontend
USER app
ENV AI_ARTICLE_DATA_DIR=/data AI_ARTICLE_SANDBOX=http AI_ARTICLE_WORKER=external MPLCONFIGDIR=/tmp HOME=/tmp
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
