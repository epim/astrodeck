# Deploying the AstroDeck Relay

This guide covers how to stand up the public **relay**, hand your **home** ("scope")
a device token, point the scope at the relay, and reach the whole app from a remote
browser. It also states the **security model** the deployment depends on and the
**hardened trusted-transport interim** posture this design ships today.

The relay source and its own deploy notes live in `relay/` (`relay/README.md`,
`relay/Dockerfile`, `relay/fly.toml`). This document is the operator-facing
walkthrough; the design rationale is the council ADR
`docs/architecture/2026-06-17-remote-access-architecture.md`.

---

## What the relay is (one paragraph)

The relay is a small, standalone, **host-agnostic** public service. The home dials
ONE persistent **outbound WSS** to it (`wss://<relay>/scope`) and keeps it open, so
there is **no home port-forwarding and no NAT pain**. A remote browser hits the
relay over **HTTPS + WSS**; the relay **tunnels** each browser request (and each
`/ws`) down that scope connection to the home app and streams the response back. The
home serves the **whole app** (SPA + REST API + `/ws`), so the relay forwards
the application under `/h/<home_id>/`. It transforms transport headers and
forwards the home's signed session cookie; it is therefore a trusted
bearer-token intermediary, not a blind byte-forwarder.

---

## 1. Deploy the relay

Persistent connections drive the host choice.

### Why Fly.io / a VPS, and why NOT Cloud Run

The relay holds a home's persistent outbound WSS open for hours, and **every** remote
browser for that home must reach the **same** instance (affinity — the home registry
is in memory on the instance that owns the tunnel). Cloud Run is **disqualified**:
its request-scoped lifetime + scale-to-zero + instance recycling fight a kept-open
bidi WSS and an in-memory registry. Use **Fly.io** (always-on, Anycast, suited to
long-lived bidi streams) or a **plain VPS**. Start single-instance (one machine holds
the registry); scale out later via a shared bus.

### Option A — Docker (any host)

```bash
cd relay
docker build -t astrodeck-relay .
docker run -p 8080:8080 \
  -v $PWD/secrets:/secrets:ro \
  -e RELAY_DEVICE_TOKENS_FILE=/secrets/device_tokens.json \
  -e RELAY_ORIGIN=relay.example.com \
  astrodeck-relay
```

The image bakes **no secrets**: the device-token map is mounted at runtime. Put a
real TLS terminator (Caddy / nginx / the platform LB) in front, or run behind
Fly's TLS.

### Option B — Fly.io (bundled `fly.toml`)

```bash
cd relay
fly launch --no-deploy            # create the app, accept the bundled fly.toml
# Provision the device-token map as a mounted file or encrypted environment
# secret. Signing seeds are unnecessary while the reserved OIDC/share routes
# remain disabled.
fly secrets set RELAY_ORIGIN=relay.example.com
fly deploy
```

The bundled `fly.toml` pins **one always-on machine** (`min_machines_running = 1`,
`auto_stop_machines = false`) so a home's WSS stays pinned to one instance, exposes
`443`/`80` (force-HTTPS), sets a generous WSS idle timeout, and health-checks
`/healthz`.

### Key environment variables

| var | default | meaning |
|-----|---------|---------|
| `RELAY_BIND_HOST` / `RELAY_BIND_PORT` | `0.0.0.0` / `8080` | listen address |
| `RELAY_ORIGIN` | `""` | canonical public browser host for exact Origin checks; required on non-loopback binds |
| `RELAY_HTTPS` | `1` | forces cookie `Secure` |
| `RELAY_DEVICE_TOKENS_FILE` | `""` | JSON `{device_token: home_id}` (mounted) |
| `RELAY_OIDC_SEED_FILE` | `""` | 32-byte Ed25519 seed for the OIDC key |
| `RELAY_VIEWER_SEED_FILE` | `""` | 32-byte Ed25519 seed for the **separate** viewer key |
| `RELAY_PING_INTERVAL_S` / `RELAY_PING_MAX_MISSES` | `10` / `3` | keepalive |
| `RELAY_MAX_BODY` | `8388608` | max tunnelled request body bytes |

> No implicit signer: without `RELAY_OIDC_SEED_FILE` /
> `RELAY_VIEWER_SEED_FILE`, the network server constructs no signer and cannot
> mint tokens. That is the recommended posture for this build because it exposes
> neither relay-terminated OIDC nor `/share`. If those features are implemented,
> provision distinct Ed25519 seeds and matching home public keys before enabling
> their routes.

---

## 2. Generate and set the device token

The device token registers one home with the relay and maps it to a stable
`home_id` (the routing key in the browser URL). It is a **shared secret** between the
relay and that home.

**Generate** a high-entropy token (any side):

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**On the relay**, add it to the mounted token map (`RELAY_DEVICE_TOKENS_FILE`),
mapping the token to the `home_id` you want in the URL:

```json
{ "<the-generated-token>": "home-1" }
```

This file is **mounted, never baked into the image**. Tokens shorter than 32
printable ASCII characters are rejected; use the generated token rather than a
human password. Multiple rotation tokens
may map to the same home, but one relay hostname/instance may contain only one
distinct home: path tenants would otherwise share a browser origin and cookie
namespace. A reconnect with a higher `generation` physically evicts the prior
scope socket.

**On the home**, put the same token in `RemoteConfig.device_token` (next section).

---

## 3. Point a scope (home) at the relay

The home-side config is `RemoteConfig` on `AppConfig`
(`server/astrodeck/config.py`). Fields:

| field | example | meaning |
|-------|---------|---------|
| `enabled` | `true` | **master kill switch.** `False` (default) ⇒ the home **never dials**; byte-for-byte today's local-only behavior. |
| `relay_url` | `wss://relay.example.com/scope` | outbound WSS endpoint — the relay's `/scope` route. |
| `device_token` | `<the-generated-token>` | SECRET shared with the relay (scrubbed by `redacted()`). |
| `home_id` | `home-1` | stable home id; must match the relay's token→home_id map. |

When `enabled and relay_url` are set, the home launches an **opt-in background task**
from the app lifespan (`server/astrodeck/remote/relay_client.py`) that dials the
relay, sends `HELLO {device_token, home_id, generation, proto_version}`, and replays
each tunneled request against the in-process app. It is **isolated**: a relay outage
never blocks or crashes startup — the home just stays local-only and reconnects with
capped backoff + jitter.

**Writing the config.** `RemoteConfig` is a SECRET-bearing block and is written
through the typed setter `config_store.set_remote(...)` (mirrors `set_auth`), **not**
the `extra="forbid"` `POST /api/config` merge. The companion relay public keys
(`relay_pubkey` / `viewer_link_pubkey`) live on `AuthConfig` and are written by the
`admin.users`-gated `POST /api/remote/config` route (which is **tunnel-blocked** — it
cannot be set through the relay). `redacted()` blanks `device_token` and surfaces
`remote_token_configured` / `remote_configured` booleans so the UI never sees the
secret.

> Local kill switch: set `RemoteConfig.enabled = false` and the home stops dialing
> immediately — full local autonomy, no relay.

---

## 4. Reach it from a remote browser

Once the home's tunnel is up (confirm it in the home/relay logs; `/healthz`
intentionally exposes liveness only), open:

```
https://relay.example.com/h/home-1/
```

The relay forwards the whole app under `/h/<home_id>/`: SPA/assets, home-served
`/auth` routes, capability-gated `/api`, and the send-only `/ws` event stream.
Browser-to-relay transport must be HTTPS/WSS. Home cookies are rewritten to be
host-only and `Secure`; an explicit SameSite policy is preserved.

Authenticate through a **real home provider** (local or Google session). The open
`none` provider is hard-denied remotely. `ASTRODECK_TOKEN` is deliberately a
direct-transport credential and is stripped in both header and query form at the
tunnel boundary. This build does not expose relay-terminated OIDC or `/share`
viewer-link endpoints; do not design deployment access around those reserved
components.

---

## Security model (the deployment depends on this)

The current relay is **trusted transport**. TLS terminates there and the home
session cookie passes through it, so compromise of the relay can expose and
replay a bearer session. Treat the relay host, its logs, and its runtime with the
same care as an authentication proxy.

Defense in depth still applies at the home:

- Every tunneled HTTP and WebSocket scope carries a non-header remote flag. The
  open `none` provider hard-denies remote scopes; a real home provider must
  authenticate the cookie.
- Raw `Authorization`, `X-Auth-Token`, and `?token=` carriers are stripped, so
  the direct `ASTRODECK_TOKEN` break-glass credential is never tunnel authority.
- RBAC and physical sun/horizon/safety gates execute at the home. The event
  WebSocket is send-only; commands use capability-gated REST routes.
- Identity and auth configuration, user management, relay configuration,
  factory reset, update check/apply/config, connection discovery/setup, and
  host/LAN destination configuration are denied on remote scopes even for an
  otherwise-valid admin session.
- The codec, request bodies, queues, handshakes, and concurrent stream counts
  are bounded. Exact browser Origin is required for mutations and WebSockets.
- Setting `RemoteConfig.enabled = false` closes the live connection promptly;
  a changed URL/token/home id also fences the old generation before redial.

These controls limit persistence and trust-root changes, but they do **not** make
a compromised relay view-only: it can replay a captured operator/admin cookie
for any capability that remains intentionally available over the tunnel,
including rig control. mTLS protects the home-to-relay dial from impersonation;
it does not cure compromise of the relay endpoint.

Use a dedicated hardened relay, one home per hostname/instance, strict secret
mount permissions, HTTPS/WSS, and mTLS where available. A future end-to-end
encrypted blind relay would remove plaintext/cookie access; that is not what
this implementation ships today.
