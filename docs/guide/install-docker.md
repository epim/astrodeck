# Running AstroDeck in Docker (including on a Raspberry Pi)

One container, two folders, one port. Everything else is configured in the UI.

## Quick start

```bash
git clone https://github.com/epim/astrodeck
cd astrodeck
docker compose up -d
```

Then open `http://<the-machine's-address>:8800` from a phone, tablet or laptop on
the same network.

Your data lives in `./data` beside the compose file — `data/config` for the rig
profile, site and accounts, `data/captures` for every frame. They are ordinary
files: copy them off with anything, back them up with anything. `docker compose
down` does not touch them.

## Letting the container see your rig

A container cannot see a USB device that has not been passed in, so a fresh
container finds no hardware. `docker-compose.yml` has both options commented in;
uncomment one.

**Everything (simplest).** The container sees every USB device, including ones
you plug in later:

```yaml
privileged: true
volumes:
  - /dev/bus/usb:/dev/bus/usb
```

**Named devices (narrower).** Safer, but a device that appears after the
container starts stays invisible until you restart it:

```yaml
devices:
  - /dev/ttyUSB0:/dev/ttyUSB0
  - /dev/ttyACM0:/dev/ttyACM0
```

Find yours with `ls -l /dev/serial/by-id/` — that listing names the actual
hardware, so you can tell the mount from the focuser without guessing.

Network devices need none of this. An ASIAIR, a NINA instance, an Alpaca server
or PHD2 elsewhere on the network are reached over the network, so they work with
no device passthrough at all.

## Raspberry Pi

A Pi 4 or 5 with 4 GB and a 64-bit OS runs this. Build it on the Pi:

```bash
docker compose up -d --build
```

Expect the first build to take a while — it compiles nothing, but it does
install the Python scientific stack and build the web UI.

Two things worth doing on a Pi:

- **Put captures on real storage.** An SD card will fill and will eventually
  wear out under a night of writes. Point `./data/captures` at a USB SSD by
  editing the volume line in `docker-compose.yml`.
- **Give it swap** if you are on a 4 GB Pi and the UI build gets killed. The
  build is the memory-hungry part; running the server is not.

Cross-building on a faster machine also works:

```bash
docker buildx build --platform linux/arm64 -t astrodeck:arm64 .
```

**Verified so far:** the amd64 image is built, run and checked end-to-end
(health, UI, assets). The arm64 image has not yet been run on Pi hardware — if
you get there first, the failure worth reporting is anything at *build* time,
since the runtime is identical Python either way.

On an Apple-silicon Mac the arm64 build is worth doing even without a Pi to hand:
Docker runs arm64 Linux natively there rather than under emulation, so
`docker buildx build --platform linux/arm64 .` both builds and *runs* the real
Pi image at full speed. That is a genuine check; QEMU on an x86 machine often is
not, and on some setups cannot execute arm64 binaries at all.

## Notes

- The container runs as an unprivileged user (uid 10001). If you bind-mount a
  folder that already exists, make sure that uid can write it:
  `sudo chown -R 10001:10001 data`.
- `0.0.0.0` inside the container is the container's own interface. What your
  network can reach is decided by the `ports:` line, not by that.
- **There is no authentication until you configure it.** Anything that can reach
  port 8800 can move your mount. On a home network behind a router that is
  usually fine; before exposing it any further, set up sign-in under
  Settings → Auth. See [`docs/SECURITY.md`](../SECURITY.md).
- To move it off port 8800, change the LEFT side of `"8800:8800"`. The right
  side is inside the container and should stay as it is.

## Updating

```bash
git pull
docker compose up -d --build
```

Your config and captures are untouched — they are in `./data`, not in the image.
