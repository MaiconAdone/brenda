FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --extra api --extra ui --extra orquestracao --extra ml --extra agentes

COPY src ./src
COPY pipelines ./pipelines
COPY config ./config
RUN uv sync --frozen --no-dev --extra api --extra ui --extra orquestracao --extra ml --extra agentes

RUN useradd --create-home lupa && mkdir -p /app/data /app/logs && chown -R lupa /app/data /app/logs
USER lupa
ENV PATH="/app/.venv/bin:$PATH"
