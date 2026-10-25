# syntax=docker/dockerfile:1

# The web pages' stylesheet, fonts and scripts, built by Vite from the locked packages.
FROM node:24-slim AS assets

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0

WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile
COPY vite.config.js ./
COPY src/openfesl/web src/openfesl/web
RUN pnpm build


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

COPY LICENSE.md THIRD-PARTY-NOTICES.md ./
COPY src ./src
COPY --from=assets /app/src/openfesl/web/static/build src/openfesl/web/static/build
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 1000 app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

USER app
# Admin commands run in the container write files such as ProviderId.dat here.
WORKDIR /home/app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --start-interval=2s --retries=3 \
    CMD ["python", "-m", "openfesl.server.health"]
CMD ["openfesl", "serve"]
