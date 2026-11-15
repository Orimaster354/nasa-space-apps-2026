# One container serves the API and the frontend, so judges get a single link and there is no cross-origin setup.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend backend
COPY frontend frontend

# The frontend branch ships with mock data on. The deployed site must call the real backend, so flip it here at build time
# (the frontend source is left untouched). The build fails loudly if the switch is not found.
RUN sed -i 's/useMock: true/useMock: false/' frontend/js/config.js && grep -q "useMock: false" frontend/js/config.js

ENV PYTHONPATH=/app/backend \
    SPX_FRONTEND_DIR=/app/frontend \
    SPX_DATA_DIR=/app/data \
    SPX_CORS_ORIGINS=""

WORKDIR /app/backend
# Cloud Run supplies $PORT. One worker: the app keeps in-memory caches and quotas.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
