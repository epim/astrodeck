# AstroDeck — the whole controller in one image.
#
# Multi-arch by construction (no arch-specific steps), so the same Dockerfile
# produces the amd64 image for a NUC or mini-PC and the arm64 image for a
# Raspberry Pi 4/5. Build both with:
#
#   docker buildx build --platform linux/amd64,linux/arm64 -t astrodeck:latest .
#
# Two stages: node builds the SPA, python installs the server. The runtime stage
# carries neither toolchain.
#
# The SPA is copied INSIDE the package (astrodeck/webui) rather than left beside
# it, because a sibling directory is a repo-layout assumption that does not
# survive being packaged — see api/app.py::_resolve_ui_dist.

# ----------------------------------------------------------------- UI build
FROM --platform=$BUILDPLATFORM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS ui
WORKDIR /ui
# package files first: this layer is cached until a dependency actually changes,
# which is the difference between a 20-second and a 4-minute rebuild on a Pi.
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# ------------------------------------------------------------- python build
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS build
WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY server/pyproject.toml ./server/
COPY server/astrodeck/__init__.py ./server/astrodeck/
# Resolve dependencies against the real metadata but without the source tree, so
# a code change does not invalidate the dependency layer.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ./server
COPY server/ ./server/
RUN pip install --no-cache-dir --no-deps ./server

# ------------------------------------------------------------------ runtime
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6 AS runtime
LABEL org.opencontainers.image.title="AstroDeck" \
      org.opencontainers.image.description="Open, vendor-neutral astrophotography rig controller" \
      org.opencontainers.image.source="https://github.com/epim/astrodeck" \
      org.opencontainers.image.licenses="Apache-2.0"

# libusb is a RUNTIME dependency of the vendored camera SDKs, not a build one.
# Without it the .so resolves, ctypes tries to load it, and dies with
# "libusb-1.0.so.0: cannot open shared object file" — which surfaces as the
# camera backend registering nothing and the camera simply not being offered.
# Verified by loading libPlayerOneCamera.so in this image with and without it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Non-root. The uid is pinned so a bind-mounted capture directory has a stable
# owner across rebuilds — otherwise last night's data becomes unwritable after
# an image update.
#
# --no-log-init is defensive, not tidiness: without it useradd zero-fills
# /var/log/lastlog out to the new uid's offset, and a high uid under QEMU
# emulation is a known way for that sparse write to fail the whole build. Cheap
# insurance on the arm64 (Raspberry Pi) build, which is emulated wherever it is
# not built on real hardware.
RUN groupadd --gid 10001 astrodeck && useradd --no-log-init --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin astrodeck

COPY --from=build /opt/venv /opt/venv
# The built SPA, inside the installed package.
COPY --from=ui /ui/dist /opt/venv/lib/python3.12/site-packages/astrodeck/webui

ENV PATH="/opt/venv/bin:$PATH" \
    HOME=/tmp/home \
    TMPDIR=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    XDG_CONFIG_HOME=/tmp/.config \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ASTRODECK_CONFIG_DIR=/data/config \
    ASTRODECK_CAPTURE_DIR=/data/captures

# Both are bind/volume mount points. Config holds the rig profile, the site and
# the user store; captures holds every frame. Seed exact ownership and private
# modes so a newly created named volume inherits them on first attachment.
RUN install -d -o 10001 -g 10001 -m 0700 /data/config /data/captures
VOLUME ["/data/config", "/data/captures"]

USER 10001:10001
WORKDIR /app
EXPOSE 8800
STOPSIGNAL SIGTERM

# /healthz is unauthenticated by design (the supervisor and any load balancer
# probe it) and touches no device, so this stays green on a rig with nothing
# connected — the normal state before you plug anything in.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8800/healthz', timeout=4).status==200 else 1)"

# A published container port needs an internal 0.0.0.0 bind. AstroDeck's CLI
# refuses this non-loopback bind until a named account or another supported
# authentication method has been provisioned in the persistent config volume.
CMD ["python", "-m", "astrodeck", "run", "--host", "0.0.0.0", "--port", "8800"]
