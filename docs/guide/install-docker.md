# Running AstroDeck in Docker

The root Compose file is a loopback-only development and maintenance deployment. Use the separate TLS reverse-proxy deployment for other computers. The image builds the browser UI and Python server; it does not install the optional native engine or ASTAP.

## Loopback quick start

1. From the repository root, build the image and create a named administrator:

```bash
docker compose build
docker compose run --rm astrodeck python -m astrodeck create-admin yourname
docker compose up -d
```

2. Enter the password at the interactive prompt. Do not place passwords in the command line or Compose file.
3. Open `http://127.0.0.1:8800` on the Docker host and sign in. Follow [getting started](getting-started.md).
4. Check `docker compose ps` and `docker compose logs astrodeck` if the service does not start. Redact secrets and location details before sharing logs.

The service runs as UID/GID 10001 with a read-only root filesystem. Named volumes keep configuration and captures; `/tmp` is temporary. `docker compose down` preserves the volumes. `docker compose down --volumes` removes them, so do not use it for an ordinary update.

## LAN or public access

1. Read the [reverse-proxy deployment instructions](../../deploy/reverse-proxy/README.md), including certificates and hostname configuration.
2. Build that Compose deployment, then run its `create-admin` command against its configuration volume before starting the stack.
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
2. Update the checkout, then run `docker compose build --pull` and `docker compose up -d`.
3. Confirm health and sign in before removing the previous image.

The older bind-mount layout is not automatically imported into named volumes. Keep the old directories, stop the old service, and migrate their contents with deliberate ownership and permissions before deleting any copy.

Copyright (c) 2026 James Penick. Licensed under Apache-2.0.
