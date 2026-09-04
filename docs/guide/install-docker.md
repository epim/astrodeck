# Running AstroDeck in Docker

AstroDeck supplies two deliberately different Compose profiles:

- the root `docker-compose.yml` is a hardened, loopback-only development and
  maintenance profile;
- `deploy/reverse-proxy/docker-compose.yml` is the supported LAN or public
  profile, where nginx is the only published service and terminates TLS/WSS.

Do not publish the application's port 8800 directly to a LAN or the internet.

## Loopback quick start

Build the image, initialize a named administrator interactively, then start the
service:

```bash
git clone https://github.com/epim/astrodeck
cd astrodeck
docker compose build
docker compose run --rm astrodeck \
  python -m astrodeck create-admin <username>
docker compose up -d
```

The command prompts for the password. Do not put it in the command line, an
environment variable, `.env`, shell history, or Compose metadata. Open
`http://127.0.0.1:8800` on the Docker host.

The local profile binds only `127.0.0.1`, runs as numeric UID/GID 10001, drops
all capabilities, enables no-new-privileges, and uses a read-only root
filesystem. Only `/data/config`, `/data/captures`, and the bounded `/tmp` tmpfs
are writable. Configuration and captures live in the `astrodeck-config` and
`astrodeck-captures` named volumes, so `docker compose down` does not remove
them. Back up both volumes before upgrades; never use `down --volumes` unless
you intend to destroy their contents.

`ASTRODECK_TOKEN` is a legacy direct-API transport credential. It is not a
working browser/Compose bootstrap and is intentionally absent from the supplied
profiles. The named account created above enables browser authentication.

### Upgrading from the former bind-mount profile

Older versions mounted `./data/config` and `./data/captures`. The hardened
profile uses named volumes instead; upgrading does not delete those folders,
but it also does not import them automatically. Stop AstroDeck and back up the
entire `data` directory before migrating. Initialize the new volumes, then copy
each old tree with a one-shot container that retains the image's unprivileged
UID and sandbox:

```bash
docker compose down
docker compose build
docker compose run --rm --entrypoint true astrodeck
docker run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges \
  --mount type=bind,src="$(pwd)/data/config",dst=/source,readonly \
  --mount type=volume,src=astrodeck-local_astrodeck-config,dst=/target \
  --entrypoint sh astrodeck:latest -c \
  'cp -R /source/. /target/ && find /target -type d -exec chmod 0700 {} \; && find /target -type f -exec chmod 0600 {} \;'
docker run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges \
  --mount type=bind,src="$(pwd)/data/captures",dst=/source,readonly \
  --mount type=volume,src=astrodeck-local_astrodeck-captures,dst=/target \
  --entrypoint sh astrodeck:latest -c \
  'cp -R /source/. /target/ && find /target -type d -exec chmod 0700 {} \; && find /target -type f -exec chmod 0600 {} \;'
```

Run these commands from the repository root. If UID 10001 cannot read the old
bind mount, stop and correct its host ownership deliberately; do not make the
tree world-readable. Confirm the old account, configuration, and a sample
capture are visible before archiving the old `data` directory.

## LAN or public access

Use the TLS reverse-proxy profile and follow its deployment README. On a fresh
production config volume, bootstrap before bringing up the stack:

```bash
docker compose -f deploy/reverse-proxy/docker-compose.yml build
docker compose -f deploy/reverse-proxy/docker-compose.yml run --rm astrodeck \
  python -m astrodeck create-admin <username>
docker compose -f deploy/reverse-proxy/docker-compose.yml up -d
```

The application has only an internal `expose` entry in that profile. nginx is
the sole published listener, and the application trusts forwarded metadata only
from nginx's fixed internal address. Keep the host firewall closed to upstream
port 8800.

## Letting the container see hardware

Network devices—an Alpaca server, NINA, PHD2, or an ASIAIR elsewhere on the
network—need no host device access.

For a stable serial device, create a local rootful override using its
`/dev/serial/by-id/...` path and the GID of a narrowly scoped udev group:

```yaml
services:
  astrodeck:
    devices:
      - /dev/serial/by-id/<stable-id>:/dev/ttyUSB0:rwm
    group_add:
      - "${ASTRODECK_DEVICE_GID:?set the host device group GID}"
```

For approved hot-plugged libusb cameras, the checked-in
`docker-compose.usb.yml` override grants only USB character major 189, mounts
only `/dev/bus/usb`, and retains the zero-capability policy. Host udev rules must
limit node ownership to the known vendors:

```bash
export ASTRODECK_USB_GID=<udev-group-gid>
docker compose -f docker-compose.yml -f docker-compose.usb.yml up -d
```

Never grant the container blanket device access, the Docker socket, host
network/PID namespaces, host root, `/dev/mem`, or `/dev/gpiomem`. Do not add
capabilities. USB access is not claimed for rootless Docker until it has been
verified with the intended host, kernel, udev rules, cameras, and hotplug flow.

## Raspberry Pi and Orange Pi

Use a 64-bit operating system. Building on the target produces its native arm64
image:

```bash
docker compose build
```

Cross-building on a faster machine is also possible:

```bash
docker buildx build --platform linux/arm64 -t astrodeck:arm64 .
```

Put captures on durable SSD storage, not the boot SD card. The supported
rootless profile uses named volumes because arbitrary host bind ownership does
not map reliably to container UID 10001. If an operator chooses a rootful SSD
bind override, pre-create exact source directories as UID/GID 10001 with mode
0700 and use long Compose mount syntax with `create_host_path: false`; never let
Compose silently create a root-owned path.

Before releasing either architecture, run the live container-security gate on
native Linux amd64 and arm64. Hardware releases additionally have to prove
serial/libusb discovery and hotplug with no added capability or privilege.

## Updating

```bash
git pull
docker compose build --pull
docker compose up -d
```

Image replacement leaves the named configuration and capture volumes intact.
Confirm the service becomes healthy and perform an authenticated browser check
before pruning the previous image.
