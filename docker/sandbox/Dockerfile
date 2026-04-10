FROM python:3.12-slim

ARG TZ=Asia/Shanghai
ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com
ARG NPM_REGISTRY=https://registry.npmmirror.com

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    NPM_CONFIG_REGISTRY=${NPM_REGISTRY} \
    NODE_PATH=/usr/local/lib/node_modules \
    TZ=${TZ}

# System tools + office/pdf tooling required by built-in skills
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
    wget \
    procps \
    iputils-ping \
    netcat-openbsd \
    tree \
    git \
    htop \
    jq \
    openssl \
    tzdata \
    pandoc \
    zip \
    unzip \
    qpdf \
    poppler-utils \
    imagemagick \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-chi-sim \
    libreoffice \
    nodejs \
    npm \
    build-essential \
    libmagic1 \
    fonts-dejavu-core \
    fonts-noto-cjk \
 ; \
 ln -snf /usr/share/zoneinfo/${TZ} /etc/localtime; \
 echo "${TZ}" > /etc/timezone; \
 rm -rf /var/lib/apt/lists/*

# Python libraries used by skills-builtin scripts and common workflows in SKILL.md
RUN python -m pip install --upgrade --retries 5 --timeout 120 pip setuptools wheel && \
    python -m pip install --retries 5 --timeout 120 \
      defusedxml \
      lxml \
      Pillow \
      openpyxl \
      pypdf \
      pdfplumber \
      pdf2image \
      pytesseract \
      PyYAML \
      pandas \
      reportlab \
      python-magic \
      requests \
      "markitdown[pptx]"

RUN python -m pip install --retries 5 --timeout 120 \
      weasyprint \
      python-docx \
      mammoth \
      pdf2docx \
      PyMuPDF \
      xlrd \
      xlwt

RUN set -eux; \
 apt-get update; \
 wanted="\
   texlive-latex-base \
   texlive-latex-extra \
   texlive-fonts-recommended \
   texlive-fonts-extra \
   wkhtmltopdf \
   fonts-wqy-zenhei \
   fonts-wqy-microhei \
 "; \
 installable=""; \
 for pkg in $wanted; do \
   candidate="$(apt-cache policy "$pkg" | awk '/Candidate:/ {print $2; exit}')"; \
   if [ -n "$candidate" ] && [ "$candidate" != "(none)" ]; then \
     installable="$installable $pkg"; \
   else \
     echo "WARN: package has no install candidate on this distro/arch, skipping: $pkg"; \
   fi; \
 done; \
 if [ -n "$installable" ]; then \
   for pkg in $installable; do \
     if apt-get install -y --no-install-recommends "$pkg"; then \
       echo "Installed optional package: $pkg"; \
     else \
       echo "WARN: failed to install optional package, skipping: $pkg"; \
     fi; \
   done; \
 fi; \
 rm -rf /var/lib/apt/lists/*


# JS libs referenced by built-in office skills
RUN npm install -g --registry=${NPM_REGISTRY} docx pptxgenjs

WORKDIR /workspace

# docker_sandbox.py will override command when creating containers, keep a safe default.
CMD ["/bin/sh", "-lc", "while true; do sleep 3600; done"]
