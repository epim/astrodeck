# Securing an AstroDeck deployment

AstroDeck's control surface lets a client slew the mount, toggle power, and
start/abort sequences. A fresh server has no authentication configured, but the
CLI binds to **loopback by default** and refuses an unauthenticated non-loopback
bind. This keeps convenient local setup without silently publishing an
admin-for-all controller to the LAN or internet.

## Optional shared-token auth (`ASTRODECK_TOKEN`)

Auth is **unconfigured by default**. Set the `ASTRODECK_TOKEN` environment variable to a
non-empty secret to require that token on direct REST requests and on the direct
WebSocket. Relay-tunneled scopes deliberately cannot use this local transport
  credential; they use home-verified sessions instead. When the variable is unset
(or empty), direct loopback behavior remains open for initial setup.

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
system; it is the minimum bootstrap bar. Prefer headers over `?token=` for REST
calls because URLs are commonly retained in browser history and proxy logs. The
browser WebSocket uses the query form because browser WebSocket APIs cannot set
an authorization header.

## Bind loopback for a local-only rig

If only the machine running AstroDeck needs to reach it (e.g. a kiosk browser on
the same box), bind the loopback interface so nothing off-box can connect at all:

```sh
python -m astrodeck --host 127.0.0.1
```

The default bind is `127.0.0.1`. To serve a LAN tablet, deliberately select a
non-loopback bind and configure a shared token or local/Google authentication:

```sh
ASTRODECK_TOKEN="a-long-random-secret" \
  python -m astrodeck --host 0.0.0.0
```

An unauthenticated non-loopback bind exits with status 2. The explicit
`--allow-insecure-open` (or `ASTRODECK_ALLOW_INSECURE_OPEN=1`) override exists
only for isolated development networks; do not ship or supervise production
with that override.

## Private configuration on Windows

AstroDeck treats the configuration tree as secret-bearing state: it can contain
session keys, login credentials, relay/update tokens, exact site coordinates,
and driver/profile extras. On Windows, startup requires that tree to reside on
NTFS or ReFS and applies a protected DACL with exactly three trustees: the
process account, `SYSTEM`, and `BUILTIN\Administrators`. Broad inherited access,
reparse points, unexpected ownership, an ACL API error, or a FAT/exFAT config
volume makes startup fail before the server listens. This protects the state
from other standard local accounts; it does not protect it from administrators,
SYSTEM, same-account malware, kernel compromise, or offline disk access.

For a Windows service, use a dedicated non-administrator service account and
set `ASTRODECK_CONFIG_DIR` to an NTFS/ReFS directory created and owned by that
identity. Do not run AstroDeck as Administrator or LocalSystem, and do not let
other standard users write the installed executable, its supervisor, or its
current-release directory: protecting data does not help if another account can
replace the code that reads it.

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

## Relay and self-update trust boundaries

- The public WebSocket relay must use `wss://` (plain `ws://` is accepted only
  for a loopback development relay). The relay can observe and replay the home's
  signed session cookie, so operate it as a trusted bearer-token intermediary.
  Raw `ASTRODECK_TOKEN` headers and query parameters are never valid tunnel
  credentials. Identity-management, relay-configuration, factory-reset, and
  self-update mutation routes are direct-only at the home. Configuration,
  alert-destination, driver/profile mutation, discovery, and connection-setup
  routes are also direct-only because they can select or probe host/LAN resources.
- The home accepts only the Host names it expects on its listener: loopback,
  the bind address, and whatever `ASTRODECK_ALLOWED_HOSTS` adds (a comma-separated
  list of exact names, no wildcards). Anything else is answered 421, which is
  what stops a DNS-rebinding page from reaching the LAN listener. Relay-tunneled
  requests are exempt: they never touched the listener (the home dialed out to
  its configured relay over TLS, and the relay client marks the scope in ASGI
  state that no network client can set), and the Host they carry is the relay's
  public name, which may be a custom domain in front of it. So a relay rename
  needs no allowlist change, and a reverse proxy in front of the LAN listener
  needs its public name in `ASTRODECK_ALLOWED_HOSTS`.
- `update.repo` and `update.signing_pubkey` are code-execution trust roots. The
  HTTP API cannot set or rotate them. Provision them offline in the persistent
  config or installer, protect that file with OS permissions, and keep the
  Ed25519 private signing key outside every AstroDeck host and relay.
