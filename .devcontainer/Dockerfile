FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# libgl1 libglu1-mesa libxrender1 libxext6: runtime libraries for build123d / OCP (CAD).
# build-essential: the PlatformIO native platform compiles firmware with gcc.
# The base image ships a Yarn apt source with an expired key that breaks apt-get update.
RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        build-essential libgl1 libglu1-mesa libxrender1 libxext6 \
    && rm -rf /var/lib/apt/lists/*

# uv 0.11.x matches the uv_build requirement in pyproject.toml.
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /usr/local/bin/
