# AstroDeck relay -- standalone, host-agnostic image.
# Multi-stage: install deps into a venv, copy only the relay package + venv into
# a slim runtime. No secrets baked in; device tokens + signing seeds are mounted
# at runtime (RELAY_DEVICE_TOKENS_FILE / RELAY_OIDC_SEED_FILE / RELAY_VIEWER_SEED_FILE).

FROM python:3.12-slim AS build
WORKDIR /app
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim AS runtime
# Run as a non-root user.
RUN useradd --create-home --uid 10001 relay
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY relay ./relay
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    RELAY_BIND_HOST=0.0.0.0 \
    RELAY_BIND_PORT=8080
USER relay
EXPOSE 8080
# Liveness: the relay exposes /healthz.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"
CMD ["python", "-m", "relay"]
