FROM python:3.12-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG TZ=Asia/Shanghai
ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    TZ=${TZ}

WORKDIR /app

# Runtime dependencies for backend + frontend preview server.
# Default to Aliyun mirrors for faster/more stable download in CN networks.
RUN set -eux; \
  if [ -f /etc/apt/sources.list ]; then \
    sed -i "s|http://deb.debian.org/debian|https://${APT_MIRROR}/debian|g" /etc/apt/sources.list; \
    sed -i "s|http://security.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g" /etc/apt/sources.list; \
    sed -i "s|https://deb.debian.org/debian|https://${APT_MIRROR}/debian|g" /etc/apt/sources.list; \
    sed -i "s|https://security.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g" /etc/apt/sources.list; \
  fi; \
  if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
    sed -i "s|http://deb.debian.org/debian|https://${APT_MIRROR}/debian|g" /etc/apt/sources.list.d/debian.sources; \
    sed -i "s|http://security.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g" /etc/apt/sources.list.d/debian.sources; \
    sed -i "s|https://deb.debian.org/debian|https://${APT_MIRROR}/debian|g" /etc/apt/sources.list.d/debian.sources; \
    sed -i "s|https://security.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g" /etc/apt/sources.list.d/debian.sources; \
  fi; \
  apt-get update; \
  apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    tzdata; \
  ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime; \
  echo "${TZ}" > /etc/timezone; \
  rm -rf /var/lib/apt/lists/*

# Install backend Python dependencies first for better layer cache.
COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --upgrade --retries 5 --timeout 120 pip setuptools wheel \
  && python -m pip install --retries 5 --timeout 120 -r /app/backend/requirements.txt

# Copy prebuilt frontend dist produced on host machine.
# Build it first with: cd frontend && npm ci && npm run build
COPY frontend/dist /app/frontend/dist

# Copy backend and runtime resources.
COPY backend /app/backend
COPY prompts /app/prompts
COPY skills-builtin /app/skills-builtin
COPY docker/teamclaw/entrypoint.sh /app/docker/teamclaw/entrypoint.sh
COPY docker/teamclaw/serve_frontend.py /app/docker/teamclaw/serve_frontend.py
RUN chmod +x /app/docker/teamclaw/entrypoint.sh
RUN test -f /app/frontend/dist/index.html

EXPOSE 8000 8080

ENTRYPOINT ["/app/docker/teamclaw/entrypoint.sh"]
