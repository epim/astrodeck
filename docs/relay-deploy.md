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
**everything** (`/`, `/assets`, `/api`, `/auth`, `/ws`) — there is no separate
frontend host and **zero UI component changes**. The relay is a **dumb
byte-forwarder**: it holds **no home signing secret** and **cannot forge a
principal**.

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
  -e RELAY_OIDC_SEED_FILE=/secrets/oidc_seed.bin \
  -e RELAY_VIEWER_SEED_FILE=/secrets/viewer_seed.bin \
  -e RELAY_ORIGIN=relay.example.com \
  astrodeck-relay
```

The image bakes **no secrets**: device tokens and the two signing seeds are **mounted
files** at runtime. Put a real TLS terminator (Caddy / nginx / the platform LB) in
front, or run behind Fly's TLS.

### Option B — Fly.io (bundled `fly.toml`)

```bash
cd relay
fly launch --no-deploy            # create the app, accept the bundled fly.toml
# Provision device tokens + signing seeds as MOUNTED FILES (Fly volume or
# secrets-as-files); point the RELAY_*_FILE env vars at them.
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
| `RELAY_ORIGIN` | `""` | public host (rewrites `Set-Cookie Domain`) |
| `RELAY_HTTPS` | `1` | forces cookie `Secure` |
| `RELAY_DEVICE_TOKENS_FILE` | `""` | JSON `{device_token: home_id}` (mounted) |
| `RELAY_OIDC_SEED_FILE` | `""` | 32-byte Ed25519 seed for the OIDC key |
| `RELAY_VIEWER_SEED_FILE` | `""` | 32-byte Ed25519 seed for the **separate** viewer key |
| `RELAY_PING_INTERVAL_S` / `RELAY_PING_MAX_MISSES` | `10` / `3` | keepalive |
| `RELAY_MAX_BODY` | `134217728` | max tunnelled request body bytes |

> Dev fallback: without `RELAY_OIDC_SEED_FILE` / `RELAY_VIEWER_SEED_FILE` the relay
> runs with a **loud dev-HMAC signer** (logs a warning). Fine for local dev, **never
> production**. In production mount two 32-byte Ed25519 seeds and load the matching
> **public** keys into the home (`relay_pubkey` / `viewer_link_pubkey`).

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

This file is **mounted, never baked into the image**. One token → one home; a
reconnect with a higher `generation` fences the previous scope connection.

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

Once the home's tunnel is up (`GET https://<relay>/healthz` lists the connected
home), open:

```
https://relay.example.com/h/home-1/
```

The relay forwards the **whole app** under `/h/<home_id>/…` — the SPA, `/assets`,
`/api`, `/auth`, and the `/ws` event stream at `/h/<home_id>/ws`. Browser ↔ relay is
plain **HTTPS + WSS**; there is no separate frontend to host and no UI changes beyond
the session cookie's `SameSite` (Strict → Lax) for the relay origin.

- **Admin (full remote control):** authenticate through a **real** provider (e.g.
  Google OIDC, terminated at the relay's `/auth/google/callback`, with the session
  **re-validated at the home**). The open `none` provider is **hard-denied remotely**.
- **Friends (view-only):** redeem a **viewer link** (`/share/<token>`) minted by the
  relay with explicit caps, a revocable `jti`, TTL/renew, and a max-viewers bound.
  RBAC at the home keeps them view-only.

---

## Security model (the deployment depends on this)

The relay is **UNTRUSTED and forwards only**. Every safety-relevant decision is made
**at the home**, and the home is correct even if the relay is fully compromised.

- **The home re-authenticates and re-authorizes EVERY tunneled request.** The scope
  client tags each replayed request and the tunneled `/ws` with an ASGI-scope-state
  flag (`scope['state']['astrodeck_remote'] = True`) — **not a spoofable header** — and
  the home passes `remote=True` into `resolve_principal`. With `remote=True`, the open
  **`none` provider is hard-denied** (`server/astrodeck/auth/deps.py`), so the LAN's
  open-admin default can **never** be reached from the relay. A real provider is
  required remotely.
- **RBAC is re-checked at the home on every route** (capability gates), and
  destructive capabilities require a fresh step-up. Inbound `authorization` /
  `x-auth-token` / session-cookie headers are **stripped** at the replay shim — they
  are not valid tunnel carriers.
- **The relay holds no signing secret and cannot forge a principal.** It carries a
  **public key only** (`relay_pubkey` / `viewer_link_pubkey`); the home verifies any
  relay-supplied principal token against that public key. The relay can mint nothing
  the home will trust as admin.
- **The sun-avoidance, horizon, and safety gates enforce below the API at the home**
  (e.g. `hub._check_solar`, the motion-serialization lock/epoch), independent of auth
  and independent of the relay. Loss of the relay ⇒ full local autonomy; the local
  kill switch is `RemoteConfig.enabled = false`.
- **`/ws` stays send-only.** The relay forwards server→client events only; commands
  always travel the capability-gated REST path.
- **Per-principal audit** is recorded at tunnel ingress at the home.

Net effect: a compromised relay can read tunneled traffic and behave as an
already-authenticated **viewer** in real time, but it **cannot** issue admin/control
actions, **cannot** forge a principal, and **cannot** bypass the sun/RBAC/safety
floors.

---

## Trust posture: hardened TRUSTED-TRANSPORT interim

This is the **hardened trusted-transport interim** described in the ADR
(`docs/architecture/2026-06-17-remote-access-architecture.md`). TLS terminates **at
the relay** (browser ↔ relay is TLS **to the relay**), so a compromised relay can
observe tunneled plaintext and act as a live, already-authenticated viewer. That risk
is **accepted and documented**, and it is **bounded** by the home-side enforcement
above: token **minting is withheld** from the relay (public key only), and the
privilege-defining writes (`admin` / `config` / remote-config) are **tunnel-blocked at
the home**.

Hardening to apply in production: bind the scope ↔ relay dial with **mTLS**, mount
real Ed25519 seeds (no dev-HMAC fallback), and load the matching public keys into the
home.

**Documented future step:** an **end-to-end / blind-relay** design where the relay
never sees plaintext and is a pure ciphertext mover, removing the "relay-as-live-
viewer" risk entirely. That is the next evolution of this architecture, not what ships
in this interim.
