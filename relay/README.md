# AstroDeck Relay

A small, **standalone, host-agnostic** public service that gives an AstroDeck
home a remote front door **without any port-forwarding or NAT pain**.

The home ("scope") dials ONE persistent **outbound WSS** to this relay and keeps
it open. A remote browser hits the relay over **HTTPS + WSS**; the relay
**tunnels** each browser request (and each `/ws`) down the scope connection to
the home app and streams the response back. The home serves the **whole app**
(SPA + REST API + `/ws`), so the relay forwards **everything** — there is no
separate frontend host and **zero UI component changes**.

The relay is a **dumb byte-forwarder**. It holds **NO home signing secret** and
**cannot forge a principal**. Every tunnelled request is **re-authenticated and
re-authorized at the home** (`remote=True`, so the open "none" provider is denied
remotely), and the sun-avoidance / RBAC / safety gates all enforce at the home —
even if the relay is fully compromised.

> Security note: TLS terminates **at** the relay, so a compromised relay can read
> tunnelled traffic and act as an already-authenticated **viewer** in real time.
> This is an accepted, documented risk. Token **minting** is withheld (the relay
> holds a PUBLIC key only), and the privilege-defining writes (`admin`/`config`/
> remote-config) are **tunnel-blocked at the home**. Bind the scope↔relay dial
> with **mTLS** in production. See `docs/architecture/2026-06-17-remote-access-architecture.md`.

---

## Protocol surface

ONE outbound WSS carries everything, multiplexed by a per-message **`stream_id`**.
Each frame (in EITHER direction, inside one WSS binary message) is:

```
[ type:1B | stream_id:8B (uint64 BE) | header_len:4B (BE) | header:JSON | payload:bytes ]
```

- **`type`** — one frame type (table below).
- **`stream_id`** — uint64. The **relay** allocates one per browser HTTP exchange
  and per browser `/ws`; it is opaque to the home and only echoed back.
  `stream_id == 0` is the reserved **control** channel.
- **`header`** — small JSON metadata (sorted keys → deterministic on the wire).
- **`payload`** — raw bytes (a body chunk, one `/ws` JSON event); bounded per
  frame at **64 KiB** so one big body never head-of-line-blocks status.

| type   | name        | dir            | header                                          | payload |
|--------|-------------|----------------|-------------------------------------------------|---------|
| `0x01` | `REQ_OPEN`  | relay→scope    | `{method,path,query,headers[],has_body,class}`  | —       |
| `0x02` | `REQ_DATA`  | relay→scope    | `{eof}`                                          | req-body chunk |
| `0x03` | `REQ_ABORT` | relay→scope    | `{reason}`                                       | —       |
| `0x04` | `RESP_HEAD` | scope→relay    | `{status,headers[]}`                             | —       |
| `0x05` | `RESP_DATA` | scope→relay    | `{eof}`                                          | resp-body chunk |
| `0x06` | `WS_OPEN`   | relay→scope    | `{path,query,headers[],ws_id,class}`            | —       |
| `0x07` | `WS_DATA`   | scope→relay    | `{ws_id,seq}`                                    | one `/ws` JSON event (server→client only) |
| `0x08` | `WS_CLOSE`  | both           | `{ws_id,code}`                                   | —       |
| `0x10` | `PING`      | both           | `{ts}`  (stream_id=0)                            | —       |
| `0x11` | `PONG`      | both           | `{ts}`  (stream_id=0)                            | —       |
| `0x12` | `HELLO`     | scope→relay    | `{device_token,home_id,generation,proto_version}` | —     |
| `0x13` | `HELLO_ACK` | relay→scope    | `{ok,endpoint,reason}`                           | —       |
| `0x14` | `REVOKE`    | both           | `{jti[],ws_id[]}`  (stream_id=0)                 | —       |
| `0x15` | `WINDOW`    | both           | `{stream_id,credit}`                             | —       |

Rules: relay allocates `stream_id`; payload bounded (64 KiB); per-stream credit
backpressure (`WINDOW`); class round-robin (`event`|`control`|`bulk`) so a
125 MB FITS never head-of-line-blocks status; a duplicate live `stream_id`
`REQ_OPEN`/`WS_OPEN` is rejected; an orphan `REQ_DATA`/`RESP_DATA`/`WS_DATA`
(unknown `stream_id`) is an **error** (never silently buffered); `PING`/`PONG`/
`REVOKE` ride reserved `stream_id=0` and are exempt from the orphan rule.

The codec lives in `relay/relay/protocol.py` and is kept **byte-for-byte compatible**
with the home-side `server/astrodeck/remote/protocol.py` (the scope client).

### HTTP/WS endpoints the relay exposes

| route                              | who         | purpose |
|------------------------------------|-------------|---------|
| `GET /healthz`                     | anyone      | liveness + connected-home list |
| `WS  /scope`                       | the **home**| outbound tunnel (device-token auth via `HELLO`) |
| `WS  /h/{home_id}/ws`              | a browser   | tunnelled `/ws` event stream |
| `ANY /h/{home_id}/{path:path}`     | a browser   | tunnelled HTTP (SPA, `/assets`, `/api`, `/auth`) |
| `GET /auth/google/callback`        | a browser   | relay-terminated Google OIDC (mints a home-verifiable principal) |
| `GET /share/{token}`               | a browser   | redeem a viewer link |

`home_id` in the path is the **stable routing key** (single-instance affinity).

---

## Running it locally

The relay keeps its deps **separate** from the server venv. Create its own venv:

```bash
cd relay
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # dev: + pytest/httpx
# or, runtime only:
.venv/Scripts/python -m pip install -r requirements.txt
```

Run the relay (binds `0.0.0.0:8080` by default):

```bash
# Provision a device token -> home_id map (NEVER bake into an image):
echo '{"my-device-token":"home-1"}' > /tmp/device_tokens.json
RELAY_DEVICE_TOKENS_FILE=/tmp/device_tokens.json \
RELAY_ORIGIN=localhost:8080 \
  .venv/Scripts/python -m relay
```

Without `RELAY_OIDC_SEED_FILE` / `RELAY_VIEWER_SEED_FILE` the relay runs with a
**LOUD dev-HMAC signer** (logs a warning) — fine for local dev, never production.
For production, mount two 32-byte Ed25519 seeds and load the matching **public**
keys into the home config as `relay_pubkey` / `viewer_link_pubkey`.

On the **home** side, enable the scope client (`RemoteConfig.enabled = True`,
`relay_url = wss://<relay>/scope`, `device_token = my-device-token`). The home
dials out; a browser then loads `http://<relay>/h/home-1/`.

### Tests

```bash
cd relay
.venv/Scripts/python -m pytest tests/ -q
```

- **Off-wire unit gate** (fast inner loop, the per-stage gate): every
  `tests/test_*.py` over a **fake in-memory tunnel-frame channel** — no WSS on
  the wire. Covers the wire codec, the browser↔frame round-trip + orphan/dedup
  rules, the bidirectional header transform (incl. `Set-Cookie` rewrite), OIDC
  termination (faked JWKS), fan-out isolation + slow-browser-dropped, device
  registration + generation fencing, principal/viewer-link minting, and rate
  limiting.
- **One on-wire WSS integration test** (`tests/test_integration_wss.py`) — a
  separate gate that launches a real relay (uvicorn) + a real WSS home client and
  drives an `httpx` request round-trip. It SKIPS cleanly if the wire deps are
  absent.

The off-wire suite needs only the stdlib + `pytest`; the dev-HMAC fallback means
`cryptography` is **not** required to run the unit tests.

---

## Deploy

Persistent connections ⇒ **Fly.io / VPS**, **not** Cloud Run (its request-scoped
lifetime + scale-to-zero + instance recycling fight a kept-open bidi WSS + the
in-memory registry).

### Docker

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

### Fly.io

```bash
fly launch --no-deploy            # accept the bundled fly.toml
# Provision tokens + seeds as MOUNTED FILES (a Fly volume / secrets-as-files),
# then point the RELAY_*_FILE env vars at them. Set the public origin:
fly secrets set RELAY_ORIGIN=relay.example.com
fly deploy
```

The bundled `fly.toml` pins **one always-on machine** (`min_machines_running=1`,
no scale-to-zero) so a home's persistent WSS stays on the same instance, exposes
`443`/`80` (force-HTTPS), and health-checks `/healthz`.

### Environment variables

| var | default | meaning |
|-----|---------|---------|
| `RELAY_BIND_HOST` / `RELAY_BIND_PORT` | `0.0.0.0` / `8080` | listen address |
| `RELAY_ORIGIN` | `""` | public host (rewrites `Set-Cookie Domain`) |
| `RELAY_HTTPS` | `1` | forces cookie `Secure` |
| `RELAY_PING_INTERVAL_S` / `RELAY_PING_MAX_MISSES` | `10` / `3` | keepalive |
| `RELAY_WS_EGRESS_MAX` | `200` | per-browser `/ws` egress buffer bound |
| `RELAY_HTTP_RATE` / `RELAY_HTTP_BURST` | `20` / `40` | per-IP HTTP token bucket |
| `RELAY_WS_RATE` / `RELAY_WS_BURST` | `1` / `5` | per-IP `/ws`-open token bucket |
| `RELAY_MAX_BODY` | `134217728` | max tunnelled request body bytes |
| `RELAY_DEVICE_TOKENS_FILE` | `""` | JSON `{device_token: home_id}` (mounted) |
| `RELAY_DEVICE_TOKENS` | `""` | same JSON inline, when a file mount isn't available (e.g. Fly/most-PaaS secrets-as-env) |
| `RELAY_OIDC_SEED_FILE` | `""` | 32-byte Ed25519 seed for the OIDC key |
| `RELAY_VIEWER_SEED_FILE` | `""` | 32-byte Ed25519 seed for the **separate** viewer key |
