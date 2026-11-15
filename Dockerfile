# One container serves the API and the frontend, so judges get a single link and there is no cross-origin setup.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# backend/demo_bundle holds the pre-built demo data (see backend/services/bundle.py); it is the data directory. Empty is fine.
COPY backend backend
COPY frontend frontend

# The frontend branch ships with mock data on. The deployed site must call the real backend, so flip it here at build time
# (the frontend source is left untouched). The build fails loudly if the switch is not found.
RUN sed -i 's/useMock: true/useMock: false/' frontend/js/config.js && grep -q "useMock: false" frontend/js/config.js

# A few lines of fixed text in the frontend still say the demo uses sample data (the badges tied to mock mode switch off by
# themselves, these do not). Correct them in the DEPLOYED copy only; the frontend source is untouched. Tolerant: if the frontend
# developer changes or removes that text, nothing here fails.
RUN sed -i 's#badge badge-illustrative">Sample / illustrative data in this demo#badge">Real SPHEREx data from the public NASA IRSA archive#; s# Demo content is illustrative\.##' frontend/index.html  && sed -i 's# Sample data is illustrative\.##' frontend/app.html  && sed -i 's#"Illustrative data", "Everything in this demo is sample content made to show how the tools work\. It is not real SPHEREx data\."#"About the data", "The images and measurements come from the public SPHEREx archive at NASA IRSA. Navi writes its explanations with AI from those measurements; they are not scientific findings."#' frontend/js/views/learn.js

ENV PYTHONPATH=/app/backend \
    SPX_FRONTEND_DIR=/app/frontend \
    SPX_DATA_DIR=/app/backend/demo_bundle \
    SPX_CORS_ORIGINS=""

WORKDIR /app/backend
# Cloud Run supplies $PORT. One worker: the app keeps in-memory caches and quotas.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
