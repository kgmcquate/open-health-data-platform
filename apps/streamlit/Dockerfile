# Build context is the repo root: `docker build -f apps/streamlit/Dockerfile .`
# (same convention as apps/api/Dockerfile) so this stage can COPY packages/shared/.
FROM ghcr.io/astral-sh/uv:0.5-python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY packages/shared/ packages/shared/
COPY apps/streamlit/pyproject.toml apps/streamlit/uv.lock apps/streamlit/
WORKDIR /app/apps/streamlit
# No --no-install-project needed: `[tool.uv] package = false` (pyproject.toml)
# makes this a virtual project — nothing here is ever built into a wheel.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY apps/streamlit/ /app/apps/streamlit/

FROM python:3.12-slim-bookworm AS runtime
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app/apps/streamlit
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --from=builder --chown=10001:10001 /app/apps/streamlit /app/apps/streamlit
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/app/apps/streamlit
USER 10001
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health')"
CMD ["streamlit", "run", "Home.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
