# Single image: builds the React app, then serves it and the API with Uvicorn.

FROM node:24-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_PROJECT_ENVIRONMENT=/opt/venv \
    RENTBOOK_ENV=production RENTBOOK_DATA_DIR=/data RENTBOOK_FRONTEND_DIST=/app/frontend/dist
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend/ ./
COPY --from=frontend /build/dist /app/frontend/dist
RUN useradd --create-home --uid 1000 rentbook && mkdir -p /data && chown rentbook /data
USER rentbook
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["/opt/venv/bin/python", "-m", "app.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
