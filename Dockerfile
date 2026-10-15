# syntax=docker/dockerfile:1
# ProDash API image. Not built in CI yet; see docs/runbook.md.

FROM python:3.12-slim AS build
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Dependencies only; the application code is copied below and run from /app.
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim
RUN useradd --system --uid 10001 --create-home prodash
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY app ./app
COPY config ./config
COPY migrations ./migrations
COPY alembic.ini ./
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production
USER prodash
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=3).status == 200 else 1)"
# Client IPs for E05 are resolved by the app from TRUSTED_PROXIES, so uvicorn must not
# rewrite the peer address itself.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
