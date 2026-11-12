# One container for the whole demo: build the React app, then serve it and the API from FastAPI.

# ---- 1. Build the frontend
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2. Run the backend (serves frontend/dist at /)
FROM python:3.10-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# DejaVu font so the generated sample bill images have readable text on Linux
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.lock.txt backend/requirements.lock.txt
RUN pip install -r backend/requirements.lock.txt
COPY backend/app backend/app
COPY --from=web /web/dist frontend/dist

# Render (and most hosts) pass the port in $PORT; the app reads it too, for calls to its own mock Paytm API.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000}"]
