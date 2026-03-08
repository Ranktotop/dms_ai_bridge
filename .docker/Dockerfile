######################################
############### STAGE 1 ##############
###### INSTALL PYTHON PACKAGES #######
######################################
FROM python:3.12-slim AS builder
WORKDIR /app

# BUILDER PACKAGES
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    python3-dev \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/builder_venv \
    && /opt/builder_venv/bin/pip install --upgrade pip \
    && /opt/builder_venv/bin/pip install --no-cache-dir -r requirements.txt

######################################
############### STAGE 2 ##############
############ FINAL IMAGE #############
######################################
FROM python:3.12-slim
WORKDIR /app

# ENV for Python behavior
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    ROOT_DIR=/app \
    HOME=/home/dmsaibridge

# RUNTIME PACKAGES
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl iputils-ping libreoffice \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# SUPERCRONIC (cron for non-root containers)
ARG SUPERCRONIC_VERSION=0.2.33
RUN curl -fsSL "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    -o /usr/local/bin/supercronic \
    && chmod +x /usr/local/bin/supercronic

# VENV COPY
COPY --from=builder /opt/builder_venv/ /opt/venv/

# COPY APP
COPY . .

# Add non-root user
RUN useradd --create-home --home-dir /home/dmsaibridge --shell /bin/bash dmsaibridge

# Prepare directories and make entrypoints executable
RUN mkdir -p /app/temp /app/logs \
    && chmod +x /app/.docker/entrypoint.sh \
    && chmod +x /app/.docker/entrypoint-ingestion.sh \
    && chmod +x /app/.docker/entrypoint-sync.sh

# Set ownership of /app and home
RUN chown -R dmsaibridge:dmsaibridge /app /home/dmsaibridge

# Set permissions for readability/executability for all users
RUN chmod -R a+rX /app

USER dmsaibridge

EXPOSE 8000

ENTRYPOINT [ "/app/.docker/entrypoint.sh" ]