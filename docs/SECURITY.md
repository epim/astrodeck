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

### Testing role gating from loopback (`auth.trust_loopback`)

Separately from the bind interface above: as long as no sign-in method is
actually **enabled** (`auth.methods` empty), every direct caller — loopback
included — is granted admin automatically. Creating local user accounts does
NOT change this by itself; the open-admin provider stays active until a method
is enabled.

**To verify operator/viewer gating from the machine running the server**, the
primary mechanism is: enable the `local` method, create a disposable
operator/viewer account, and sign in as it — the logged-in session carries its
own role regardless of `trust_loopback`.

`auth.trust_loopback: false` (via `POST /api/auth/config` or the Settings →
Sign-in methods panel) is an additional **strict mode** on top of that: it
removes the automatic open-admin fallback for loopback callers, so an
*unauthenticated* loopback client is denied exactly like a remote one. Use it
to verify unauthenticated-loopback denial, or to ensure a loopback browser
can never silently fall back to admin while methods are empty. Flipping the
flag alone, with no method enabled, only produces 401s — it does not create a
login path. Default is `true` (today's behavior, unchanged). **Footgun:**
disabling it while no method is enabled and no session cookie already exists
locks that browser out immediately — recover by editing `trust_loopback` back
to `true` in the server's config file and restarting, or by seeding an
account with `python -m astrodeck create-admin`.

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
