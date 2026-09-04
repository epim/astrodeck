# Bare-metal service installation

These reference units run the AstroDeck server and relay as stable, locked
service identities. Both applications listen only on loopback and are intended
to sit behind a host TLS reverse proxy. Never change either `User` to `root` and
never publish the Uvicorn listener directly.

## AstroDeck server

Install the application under `/opt/astrodeck/venv`, then create and prepare the
service identity before installing the unit:

```bash
sudo useradd --system --user-group --home-dir /var/lib/astrodeck \
  --create-home --shell /usr/sbin/nologin astrodeck
sudo install -d -o astrodeck -g astrodeck -m 0700 \
  /var/lib/astrodeck /var/lib/astrodeck/config /var/lib/astrodeck/captures
sudo install -m 0644 deploy/systemd/astrodeck.service \
  /etc/systemd/system/astrodeck.service
```

Bootstrap the first account without starting a listener. The password is read
from the terminal and must not be supplied in argv or an environment variable:

```bash
sudo -u astrodeck env \
  ASTRODECK_CONFIG_DIR=/var/lib/astrodeck/config \
  ASTRODECK_CAPTURE_DIR=/var/lib/astrodeck/captures \
  /opt/astrodeck/venv/bin/python \
  -m astrodeck create-admin <username>
```

Then run `systemd-analyze verify`, configure host nginx for
`127.0.0.1:8800`, and enable the service. AstroDeck secret files must be owned
by `astrodeck:astrodeck` with mode `0600`; private directories use `0700`.
The reference unit trusts forwarded metadata only from `127.0.0.1`, so the
proxy must connect over IPv4 loopback and the upstream listener must remain
unpublished.

## Relay

Install the relay under `/opt/astrodeck-relay/venv`, then:

```bash
sudo useradd --system --user-group --home-dir /var/lib/astrodeck-relay \
  --create-home --shell /usr/sbin/nologin astrodeck-relay
sudo install -d -o astrodeck-relay -g astrodeck-relay -m 0700 \
  /var/lib/astrodeck-relay /etc/astrodeck-relay
sudo install -o astrodeck-relay -g astrodeck-relay -m 0600 \
  device_tokens.json /etc/astrodeck-relay/device_tokens.json
sudo install -m 0644 deploy/systemd/astrodeck-relay.service \
  /etc/systemd/system/astrodeck-relay.service
```

Configure host nginx for `127.0.0.1:8080`. The relay token file must remain
readable only by the relay identity. As with the home service, forwarded
metadata is accepted only from the IPv4 loopback proxy; do not point an
untrusted local workload at either upstream listener.

## Hardware access

The base server unit has a private `/dev` and is suitable for simulation and
network devices. For directly attached equipment, copy
`astrodeck-hardware.conf.example` into the service drop-in directory, replace
the placeholder with reviewed stable device paths, and assign those nodes to a
dedicated udev group. The drop-in keeps the capability bounding set empty and
uses a closed device policy.

Validate the effective target configuration and runtime before release:

```bash
systemd-analyze verify /etc/systemd/system/astrodeck.service
systemd-analyze verify /etc/systemd/system/astrodeck-relay.service
systemctl show astrodeck -p User -p NoNewPrivileges -p CapabilityBoundingSet
grep -E '^(Uid|CapEff|NoNewPrivs):' /proc/$(systemctl show -p MainPID --value astrodeck)/status
```

The effective UID must be nonzero, `CapEff` must be all zeroes, and
`NoNewPrivs` must be `1`. Exercise every serial/libusb device and hotplug path
on the intended kernel; a static unit check is not hardware evidence.
