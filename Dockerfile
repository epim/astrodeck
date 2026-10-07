# AstroDeck — the whole controller in one image.
#
# Multi-arch by construction (no arch-specific steps), so the same Dockerfile
# produces the amd64 image for a NUC or mini-PC and the arm64 image for a
# Raspberry Pi 4/5. Build both with:
#
#   docker buildx build --platform linux/amd64,linux/arm64 -t astrodeck:latest .
#
# Two build stages: node builds the SPA, python installs the server. The runtime
# stage carries neither toolchain. The native engine (astrodeck_native, the Rust
# crate behind native guiding and autofocus) is NOT compiled here: the release
# builds one wheel per platform (packaging/build_native.py) and the image installs
# exactly that wheel, handed in as the `native` build context (see below).
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
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS build
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

# ------------------------------------------------------- native engine input
# The release's native wheels arrive through a NAMED BUILD CONTEXT:
#
#   docker buildx build --build-context native=<directory of .whl files> --build-arg REQUIRE_NATIVE=1 .
#
# This empty stage is what the name `native` resolves to when no context is
# given, which is what keeps a plain `docker build .` and docker-compose.yml's
# `build: .` working: they produce a development image with no native engine, and
# say so. A named context called `native` replaces this stage. BuildKit is
# required (the default since Docker 23), because RUN --mount is BuildKit syntax.
FROM scratch AS native

# ------------------------------------------------------------------ runtime
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime
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

# The release's native engine, installed from the `native` context and proven by
# the probe in the same RUN: a wheel pip refuses (for example a manylinux tag newer
# than this base image's glibc) or one that installs but does not run fails the
# build, so no image is ever pushed with an engine nothing exercised. There is no
# --force flag on purpose: refusing an incompatible wheel is the point. (Measured
# 2026-10: the pinned base is Debian 13 with glibc 2.41, the hosted build runners
# that tag the wheels have 2.39, and pip refused a synthetic manylinux_2_42 wheel.)
#
# The wheel is chosen by this image's architecture. The glob names linux so a
# release folder that also holds the windows and macOS wheels never matches, and
# two wheels for one architecture is an error rather than a guess.
#
# REQUIRE_NATIVE=1 (the release image job) also fails a build that was given no
# wheel for this architecture. Without it, a missing wheel makes a DEVELOPMENT
# image: it runs, it has no native engine, and the build says so on stderr. The
# probe stays in the image either way, so `docker run --rm <image> python
# /opt/native_probe.py` reports which kind of image this is.
COPY packaging/native_probe.py /opt/native_probe.py
ARG REQUIRE_NATIVE=0
RUN --mount=type=bind,from=native,target=/native \
    set -eu; \
    say() { printf '%s\n' "$@" >&2; }; \
    case "${REQUIRE_NATIVE}" in 0|1) ;; *) say "ERROR: REQUIRE_NATIVE must be 0 or 1"; exit 2 ;; esac; \
    set -- /native/astrodeck_native-*-abi3-*linux*"$(uname -m)"*.whl; \
    if [ ! -e "$1" ]; then \
        if [ "${REQUIRE_NATIVE}" = 1 ]; then \
            say "ERROR: REQUIRE_NATIVE=1 but the native build context holds no astrodeck_native wheel for $(uname -m)"; \
            exit 1; \
        fi; \
        say "" \
            "================================================================" \
            "WARNING: DEVELOPMENT IMAGE. THE NATIVE ENGINE IS NOT INSTALLED." \
            "No astrodeck_native wheel was supplied for $(uname -m), so this image has" \
            "no native guiding and no native autofocus. Release images are built with" \
            "REQUIRE_NATIVE=1 and the release wheels (--build-context native=<dir>)." \
            "Confirm: docker run --rm <image> python /opt/native_probe.py" \
            "================================================================"; \
        exit 0; \
    fi; \
    if [ "$#" -ne 1 ]; then \
        say "ERROR: several astrodeck_native wheels match this architecture; supply exactly one:" "$@"; \
        exit 1; \
    fi; \
    pip install --no-cache-dir --disable-pip-version-check --no-deps "$1"; \
    python /opt/native_probe.py

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
