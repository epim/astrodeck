# Securing an AstroDeck deployment

AstroDeck's control surface lets a client slew the mount, toggle power, and
start/abort sequences. By default the server is **open** (no auth) so a backyard
rig on a trusted home network "just works" for the LAN tablet. That default is
fine on a network you trust; it is **not** safe to expose to an untrusted network
or the public internet without the steps below.

## Optional shared-token auth (`ASTRODECK_TOKEN`)

Auth is **OFF by default**. Set the `ASTRODECK_TOKEN` environment variable to a
non-empty secret to require that token on **every** REST request and on the
WebSocket. When the variable is unset (or empty), behavior is unchanged — fully
open — so existing LAN access keeps working.

When a token is set, clients must supply it via any one of:

- HTTP header `X-Auth-Token: <token>`
- HTTP header `Authorization: Bearer <token>`
- query string `?token=<token>` (the only option a browser has for the WebSocket)

Requests without a valid token get `401`. The WebSocket upgrade is closed with
code `1008` (policy violation) before the client joins the event bus.

The UI shell (`/`, `/index.html`, `/assets/*`, and SPA deep links) is served
openly so the browser can load the bundle; the bundle then attaches the token to
its `/api/*` and `/ws` calls. No part of the control surface (`/api`, `/ws`) is
ever served without the token.

```sh
# PowerShell
$env:ASTRODECK_TOKEN = "a-long-random-secret"
python -m astrodeck

# bash
ASTRODECK_TOKEN="a-long-random-secret" python -m astrodeck
```

The comparison is constant-time. This is a single shared secret, not a user
system — it is the minimum bar to keep the rig from being wide open.

## Bind loopback for a local-only rig

If only the machine running AstroDeck needs to reach it (e.g. a kiosk browser on
the same box), bind the loopback interface so nothing off-box can connect at all:

```sh
python -m astrodeck --host 127.0.0.1
```

The default bind is `0.0.0.0` (all interfaces) so the LAN tablet can reach it.
On startup, binding a non-loopback interface **without** a token logs a loud
`SECURITY WARNING`.

## Remote / untrusted-network deployment

A shared token over plain HTTP is still sent in the clear. For anything beyond a
trusted LAN:

1. Set `ASTRODECK_TOKEN` to a long random secret.
2. Front AstroDeck with a **TLS reverse proxy** (Caddy, nginx, a tunnel such as
   Tailscale/Cloudflare) so the token and all traffic are encrypted in transit.
   Bind AstroDeck itself to `127.0.0.1` and let the proxy be the only listener on
   the public interface.
3. Consider IP allow-listing at the proxy.

There is currently no TLS, no rate-limiting, and no audit log inside AstroDeck
itself — those belong at the reverse proxy for now.
