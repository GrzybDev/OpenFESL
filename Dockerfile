# syntax=docker/dockerfile:1

FROM python:3.14-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/

WORKDIR /app

# Dependencies first, so a change to the code does not reinstall them.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY LICENSE.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 1000 app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD ["openfesl", "health"]
CMD ["openfesl", "serve"]
