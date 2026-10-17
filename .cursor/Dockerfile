# Cursor Cloud base image for Python + Node repos.
# Do NOT COPY the project here — Cursor checks out the commit into the workspace.
# Reuse: copy this file (and a thin environment.json) into other repos, or keep
# the portable copy under docs/cursor-cloud/reusable-base/.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NODE_MAJOR=20

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    wget \
    git \
    gnupg \
    jq \
    tmux \
    unzip \
    zip \
    build-essential \
    gcc \
    g++ \
    cmake \
    pkg-config \
    python3 \
    python3-pip \
    python3-venv \
    python3.12-venv \
    sudo \
    && rm -rf /var/lib/apt/lists/*

# Node.js 20.x (LTS line used by this repo's frontend Docker image)
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Non-root ubuntu user (matches Cursor Cloud conventions)
RUN id -u ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash ubuntu \
    && usermod -aG sudo ubuntu \
    && echo "ubuntu ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/ubuntu \
    && chmod 0440 /etc/sudoers.d/ubuntu

USER ubuntu
WORKDIR /home/ubuntu

# Smoke-check toolchains are present at image build time
RUN python3 --version && node --version && npm --version && git --version
