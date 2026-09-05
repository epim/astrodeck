# AstroDeck Relay

A small, **standalone, host-agnostic** public service that gives an AstroDeck
home a remote front door **without any port-forwarding or NAT pain**.

The home ("scope") dials ONE persistent **outbound WSS** to this relay and keeps
it open. A remote browser hits the relay over **HTTPS + WSS**; the relay
**tunnels** each browser request (and each `/ws`) down the scope connection to
the home app and streams the response back. The home serves the **whole app**
(SPA + REST API + `/ws`), so the relay forwards **everything** — there is no
separate frontend host and **zero UI component changes**.

The relay transforms hop-by-hop headers and forwards the home's signed session
cookie. Every tunnelled request is **re-authenticated and re-authorized at the
home** (`remote=True`, so the open "none" provider is denied remotely), and the
sun-avoidance, RBAC, and safety gates still enforce at the home.

> Security note: TLS terminates **at** the relay. It is therefore a trusted
> bearer-token intermediary, not a blind pipe. A compromised relay can observe
> and replay a captured session cookie and exercise that session's tunneled
> capabilities, including rig control. Identity/trust-root changes, host/LAN
> destination and connection setup, relay configuration, factory reset, and
> self-update mutations are blocked on the tunnel at the home, but that does not
> make relay compromise harmless. Use a
> dedicated, hardened relay host and mTLS where available; mTLS protects the
> dial from impersonation, not from compromise of the relay itself.

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

Rules: relay allocates `stream_id`; headers and payloads are bounded; duplicate
live request or WebSocket ids are rejected; orphan HTTP response frames are an
error; late WebSocket frames after a normal close are dropped. `PING`/`PONG` and
`REVOKE` ride reserved `stream_id=0`. `WINDOW` and the `class` field are reserved
for a future credit scheduler; they are not active flow control today. Current
backpressure comes from bounded wire queues, bounded per-browser queues, request
timeouts, and per-home/process concurrency caps.

The codec lives in `relay/relay/protocol.py` and is kept **byte-for-byte compatible**
with the home-side `server/astrodeck/remote/protocol.py` (the scope client).

### HTTP/WS endpoints the relay exposes

| route                              | who         | purpose |
|------------------------------------|-------------|---------|
| `GET /healthz`                     | anyone      | liveness only |
| `WS  /scope`                       | the **home**| outbound tunnel (device-token auth via `HELLO`) |
| `WS  /h/{home_id}/ws`              | a browser   | tunnelled `/ws` event stream |
| `ANY /h/{home_id}/{path:path}`     | a browser   | tunnelled HTTP (SPA, `/assets`, `/api`, `/auth`) |

`home_id` in the path is the stable routing key. Because path tenants share one
browser origin and cookie namespace, this build deliberately accepts tokens for
only **one distinct home per relay hostname/instance**. Deploy a separate relay
hostname/instance per home until subdomain isolation is implemented.

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
python -c "import json,secrets; print(json.dumps({secrets.token_urlsafe(32): 'home-1'}))" > /tmp/device_tokens.json
RELAY_DEVICE_TOKENS_FILE=/tmp/device_tokens.json \
RELAY_ORIGIN=localhost:8080 \
RELAY_HTTPS=0 \
  .venv/Scripts/python -m relay
```

Without `RELAY_OIDC_SEED_FILE` / `RELAY_VIEWER_SEED_FILE`, the network server
constructs **no signer** and cannot mint principal or viewer tokens. The current
build exposes neither relay-terminated OIDC nor viewer-link routes, so that is
the recommended minimal-secret posture. If those reserved features are later
enabled, mount distinct 32-byte Ed25519 seeds and load the matching **public**
keys into the home config as `relay_pubkey` / `viewer_link_pubkey`; never use the
unit-test-only dev-HMAC helper in a network process.

On the **home** side, enable the scope client (`RemoteConfig.enabled = True`,
`relay_url = wss://<relay>/scope`, `device_token` = the token in the JSON file).
Plain
`ws://` is rejected except for a loopback development relay. The home dials out;
a browser then loads `https://<relay>/h/home-1/`.

Device tokens must contain 32-256 printable ASCII characters. Generate them
with `secrets.token_urlsafe(32)`; do not use a human password.

### Rotating or revoking a home token

The device token is a long-lived bearer credential (OPEN-002). It can be rotated
or revoked WITHOUT restarting the relay by editing the mounted token file and
sending the relay `SIGHUP`:

```bash
# mint a fresh token (run on the relay host, print once, never log it):
python -c "from relay.config import new_device_token; print(new_device_token())"
# edit /secrets/device_tokens.json, then apply it live:
kill -HUP "$(pgrep -f 'python -m relay')"
```

The relay logs a counts-only line (`homes=.. tokens=.. evicted=..`), never the
token itself. Two paths, chosen by intent:

- **Planned rotation (seamless):** add the new token alongside the old, reload,
  point the home at the new token and let it re-dial (its `generation` bumps and
  fences the old socket), then remove the old token and reload again. The live
  tunnel is never dropped by the reload while a valid token for the home remains.
- **Compromise (immediate):** delete the leaked token and reload. The home's
  live tunnel is evicted at once, so a stolen token cannot keep or resume a
  session; the owner reconnects only with a still-valid token.

Programmatic equivalents on `HomeRegistry` are `rotate(old, new)`,
`revoke(token)`, and `replace_tokens(map)`. Asymmetric/mTLS device identity that
would remove the static-bearer blast radius entirely is a future redesign.

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
  -e RELAY_ORIGIN=relay.example.com \
  astrodeck-relay
```

### Fly.io

```bash
fly launch --no-deploy            # accept the bundled fly.toml
# Provision the token map as a mounted file (or RELAY_DEVICE_TOKENS secret).
# Set the public origin:
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
| `RELAY_ORIGIN` | `""` | canonical public browser host used for exact Origin checks; required on non-loopback binds |
| `RELAY_HTTPS` | `1` | forces cookie `Secure` |
| `RELAY_PING_INTERVAL_S` / `RELAY_PING_MAX_MISSES` | `10` / `3` | keepalive |
| `RELAY_WS_EGRESS_MAX` | `64` | per-browser `/ws` egress buffer bound |
| `RELAY_HTTP_RATE` / `RELAY_HTTP_BURST` | `20` / `40` | per-IP HTTP token bucket |
| `RELAY_WS_RATE` / `RELAY_WS_BURST` | `1` / `5` | per-IP `/ws`-open token bucket |
| `RELAY_MAX_BODY` | `8388608` | max tunnelled request body bytes |
| `RELAY_SCOPE_RATE` / `RELAY_SCOPE_BURST` | `2` / `10` | per-IP `/scope` handshake bucket |
| `RELAY_SCOPE_PENDING_MAX` | `32` | process-wide incomplete `/scope` handshakes |
| `RELAY_SCOPE_HELLO_TIMEOUT_S` | `5` | deadline for the first authenticated HELLO |
| `RELAY_REQUEST_BODY_TIMEOUT_S` | `30` | maximum idle time between browser upload chunks |
| `RELAY_REQUEST_TOTAL_TIMEOUT_S` | `900` | total browser upload lifetime |
| `RELAY_UPSTREAM_TIMEOUT_S` | `30` | maximum idle wait on home response data / tunnel writes |
| `RELAY_HTTP_EGRESS_CHUNKS` | `8` | per-browser response chunks buffered at the relay |
| `RELAY_HTTP_MAX_PER_HOME` / `RELAY_HTTP_MAX_TOTAL` | `32` / `128` | concurrent tunneled HTTP exchanges |
| `RELAY_WS_MAX_PER_HOME` / `RELAY_WS_MAX_TOTAL` | `16` / `64` | concurrent browser WebSockets |
| `RELAY_DEVICE_TOKENS_FILE` | `""` | JSON `{device_token: home_id}` (mounted) |
| `RELAY_DEVICE_TOKENS` | `""` | same JSON inline, when a file mount isn't available (e.g. Fly/most-PaaS secrets-as-env) |
| `RELAY_OIDC_SEED_FILE` | `""` | 32-byte Ed25519 seed for the OIDC key |
| `RELAY_VIEWER_SEED_FILE` | `""` | 32-byte Ed25519 seed for the **separate** viewer key |
