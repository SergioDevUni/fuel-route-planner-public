ARG PYTHON_IMAGE=python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
FROM ${PYTHON_IMAGE} AS builder
WORKDIR /app
RUN python -m pip install --no-cache-dir uv==0.11.14
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

FROM ${PYTHON_IMAGE} AS runtime
ARG FUEL_ASSET_DIRECTORY=var/container-assets
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 fuel && useradd --uid 10001 --gid fuel --no-create-home fuel \
    && mkdir -p /app/var /app/data /opt/fuel \
    && chown -R fuel:fuel /app/var /app/data
COPY --from=builder /app/.venv /app/.venv
COPY app ./app
COPY config ./config
COPY scripts ./scripts
COPY docs/openapi.yaml ./docs/openapi.yaml
COPY manage.py pyproject.toml uv.lock ./
COPY ${FUEL_ASSET_DIRECTORY}/local-release.json ./local-release.json
COPY ${FUEL_ASSET_DIRECTORY}/assets.zip /opt/fuel/assets.zip
USER fuel
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD ["python", "-m", "scripts.container_health"]
CMD ["python", "-m", "scripts.container_start"]

FROM builder AS verification
ARG FUEL_ASSET_DIRECTORY=var/container-assets
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --locked
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY app ./app
COPY config ./config
COPY scripts ./scripts
COPY tests ./tests
COPY docs/openapi.yaml ./docs/openapi.yaml
COPY manage.py ./
COPY ${FUEL_ASSET_DIRECTORY}/local-release.json ./local-release.json
COPY ${FUEL_ASSET_DIRECTORY}/assets.zip /opt/fuel/assets.zip
RUN mkdir -p /app/var /app/data
CMD ["python", "-m", "scripts.verify_container_install"]
