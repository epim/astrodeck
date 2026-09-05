# AstroDeck relay -- standalone, host-agnostic image.
# Multi-stage: install deps into a venv, copy only the relay package + venv into
# a slim runtime. No secrets are baked in; mount the device-token map at runtime.
# Optional signing seeds are used only by reserved OIDC/share components.

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS build
WORKDIR /app
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime
# Use an explicit numeric identity so Compose, rootless engines, and persistent
# mounts all agree on runtime ownership after an image replacement.
RUN groupadd --gid 10001 relay && useradd --no-log-init --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin relay
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY relay ./relay
ENV PATH="/opt/venv/bin:$PATH" \
    HOME=/tmp/home \
    TMPDIR=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    XDG_CONFIG_HOME=/tmp/.config \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RELAY_BIND_HOST=0.0.0.0 \
    RELAY_BIND_PORT=8080
USER 10001:10001
EXPOSE 8080
STOPSIGNAL SIGTERM
# Liveness: the relay exposes /healthz.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"
CMD ["python", "-m", "relay"]
