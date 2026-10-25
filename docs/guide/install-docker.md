# Running AstroDeck in Docker

The root Compose file is a loopback-only development and maintenance deployment. Use the separate TLS reverse-proxy deployment for other computers. The image builds the browser UI and Python server and installs the native engine from the release's wheel; it does not install ASTAP.

## Loopback quick start

1. From the repository root, put the release's native wheel in `native-wheels/` (see [the native engine](#the-native-engine)), then build the image and create a named administrator:

```bash
docker compose build
docker compose run --rm astrodeck python -m astrodeck create-admin yourname
docker compose up -d
```

2. Enter the password at the interactive prompt. Do not place passwords in the command line or Compose file.
3. Open `http://127.0.0.1:8800` on the Docker host and sign in. Follow [getting started](getting-started.md).
4. Check `docker compose ps` and `docker compose logs astrodeck` if the service does not start. Redact secrets and location details before sharing logs.

The service runs as UID/GID 10001 with a read-only root filesystem. Named volumes keep configuration and captures; `/tmp` is temporary. `docker compose down` preserves the volumes. `docker compose down --volumes` removes them, so do not use it for an ordinary update.

## The native engine

The image carries the native engine (`astrodeck_native`, behind native guiding and native autofocus) or the build stops. `docker compose build` installs the release wheel for the build machine's architecture from `native-wheels/` and runs the engine probe in the same step. A wheel that pip refuses, or one that installs but does not run, fails the build. With no wheel the build stops and names the directory to fill; Compose never produces an image without the engine.

1. Download the wheel for your architecture from the assets of the release you are building. Its name starts with `astrodeck_native-` and its platform tag contains `linux` and either `x86_64` or `aarch64`. Save it in `native-wheels/`; exactly one wheel for the architecture may be there.
2. To keep wheels elsewhere, set `ASTRODECK_NATIVE_DIR` to the directory's absolute path. The reverse-proxy deployment reads the same variable and the same default directory.
3. After the build, `docker compose run --rm astrodeck python /opt/native_probe.py` prints the engine version and `"native_available": true`.

To skip the build, pull the published image `ghcr.io/epim/astrodeck`. The release workflow runs that probe in it on both architectures, and checks its installed packages and licences, before the image gets its version tag.

The base image is Debian 13 (trixie) with glibc 2.41, so a wheel tagged for a newer glibc is refused at build time. A plain `docker build .` outside Compose, given no wheel, still makes a development image with no native engine and says so on stderr; do not use one for imaging.

## LAN or public access

1. Read the [reverse-proxy deployment instructions](../../deploy/reverse-proxy/README.md), including certificates and hostname configuration.
2. Build that Compose deployment (it reads the same `native-wheels/` directory), then run its `create-admin` command against its configuration volume before starting the stack.
3. Open its HTTPS address. nginx is the published listener; keep the application port private.

The root Compose profile's `127.0.0.1` mapping is intentionally unavailable to a phone. Changing it alone is not the documented network deployment.

## Letting the container see hardware

Network backends use configured host addresses and ports. Native USB or serial devices need explicit host access; the default profile grants none.

1. Identify the exact serial device or approved USB vendor on the Docker host.
2. For serial hardware, use a local Compose override mapping its stable `/dev/serial/by-id/` path and the host device group's numeric GID.
3. For supported libusb access, inspect `docker-compose.usb.yml`, configure its `ASTRODECK_USB_GID`, and apply that override. Host udev permissions still need to grant the intended group access.
4. Check discovery and connection on the target host before relying on it outdoors. Rootless USB support and hotplug depend on the host; this guide does not certify them.

Do not give the container blanket privileged mode or the Docker socket to make discovery work. [Equipment and profiles](equipment-and-profiles.md) distinguishes implemented drivers from recorded device checks.

<a id="upgrading-from-the-former-bind-mount-profile"></a>

## Raspberry Pi and Orange Pi

Use a 64-bit OS and build on the target when possible. A container deployment and the [Orange Pi provisioning appliance](orange-pi-appliance.md) are different installation paths. Put captures on durable storage and retain a backup of the configuration volume.

## Updating

1. Back up both named volumes and record the image currently running.
2. Update the checkout and replace the wheel in `native-wheels/` with the one from the same release, then run `docker compose build --pull` and `docker compose up -d`.
3. Confirm health and sign in before removing the previous image.

The older bind-mount layout is not automatically imported into named volumes. Keep the old directories, stop the old service, and migrate their contents with deliberate ownership and permissions before deleting any copy.

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
