FROM node:24.14.0-bookworm-slim AS web
WORKDIR /build
RUN npm install --global pnpm@10.33.0
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.12-slim-bookworm
ARG FINCOURT_IMAGE_SOURCE=https://github.com/Fan1K413/FinCourt
ARG FINCOURT_IMAGE_REVISION
LABEL org.opencontainers.image.source=$FINCOURT_IMAGE_SOURCE \
      org.opencontainers.image.revision=$FINCOURT_IMAGE_REVISION
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.lock /app/backend/requirements.lock
RUN pip install --no-cache-dir -r backend/requirements.lock \
    && install -d -o 10001 -g 10001 /app/var /app/var/source /app/var/mcp-storage \
    && install -d -o 0 -g 10001 -m 0710 /app/var/write \
    && install -d -o 10001 -g 10002 -m 2750 /app/var/write/storage
COPY backend/ /app/backend/
COPY scripts/fincourt.py /app/scripts/fincourt.py
COPY examples/ /app/examples/
COPY deploy/serve.py /app/deploy/
COPY --from=web /build/dist /app/frontend/dist
USER 10001:10001
