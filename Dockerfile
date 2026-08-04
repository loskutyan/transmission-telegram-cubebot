FROM python:3.13.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.2 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=3.13

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13.14-slim

LABEL org.opencontainers.image.source="https://github.com/loskutyan/transmission-telegram-cubebot"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN groupadd --gid 10001 cubebot \
    && useradd --uid 10001 --gid cubebot --create-home --shell /usr/sbin/nologin cubebot

COPY --from=builder --chown=cubebot:cubebot /app/.venv /app/.venv

USER cubebot

ENTRYPOINT ["python", "-m", "cubebot"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-m", "cubebot.healthcheck"]
