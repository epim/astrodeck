# AstroDeck Remote Access Architecture

> **STATUS: DRAFT — council synthesis** · **Date: 2026-06-17** · Author: Chief Architect (synthesized from 6 council assessments)
>
> This is a decision record for review, not yet ratified. It evaluates the owner's 5-point remote-access
> proposal and converts it into concrete, opinionated architecture decisions. Code references were spot-checked
> against the tree on 2026-06-17 (`auth/deps.py:133`, `config.py:155-161`, `auth/session.py:295`,
> `auth/capabilities.py:45/58`, `auth/rbac.py:37/147`, `hub.py:697`). Where the council split, that is flagged.

---

## 0. Context

AstroDeck runs a FastAPI **control API** + static React **SPA** on the scope computer (`:8800`, LAN). The control
API drives **real hardware that can slew at the sun**. RBAC (viewer/operator/admin, capability-based) is already
**enforced at the home** on every REST route and at the `/ws` accept gate; the home is **both the source of truth
and the authorization enforcement point**. Auth multiplexes providers (`none`=open LAN default, local bcrypt,
Google OIDC, HMAC session cookies, break-glass admin token).

**Goal:** drive the scope from anywhere; let friends VIEW; phone/tablet clients; traverse home NAT **with no
port-forwarding**; **do not weaken** the safety/RBAC story.

**The owner's 5-point proposal, scored by the council:**

| # | Proposal | Verdict | Why |
|---|----------|---------|-----|
| 1 | Decouple UI from API; UI is "just another web client" | **ENDORSE (already true)** | SPA is static `ui/dist`, talks to API via relative `fetch` + one `/ws`. |
| 2 | On-scope UI and cloud UI are the SAME UI (differ only in serving + auth) | **ENDORSE (already true)** | `ws.ts` derives host from `location`; one bundle works behind a TLS cloud origin. |
| 3 | Scope INITIATES an outbound connection to dodge NAT | **ENDORSE** | The only move that satisfies "no port-forwarding"; the W3 seams already assume it. |
| 4 | The cloud component is the frontend AND the relay | **REJECT the conflation** | Two services with opposite scaling, trust, and failure properties. Split them. |
| 5 | Mobile = Electron thin clients | **REJECT Electron** | Electron is desktop-only. Mobile = the same SPA as a PWA, wrapped in Capacitor only if needed. |

The owner was explicitly unsure whether "the frontend IS the relay." **The answer is no — they are two services.**

---

## 1. Recommended Topology

**Three trust zones**, scope dials **outbound only**, relay is a dumb forwarder, home is the sole authorizer.

```
        HOME (trusted)                 CLOUD (untrusted infra)            CLIENTS
  ┌───────────────────────┐        ┌──────────────────────────┐
  │  FastAPI control API   │        │  ┌────────────────────┐  │      ┌──────────────┐
  │  (RBAC + safety gates) │        │  │ FRONTEND (static)  │  │◄─────│ Browser SPA  │
  │  127.0.0.1:8800        │        │  │ CDN / object store │  │ HTTPS│ (PWA on      │
  │        ▲               │        │  │ serves ui/dist     │  │      │  phone/tablet)│
  │        │ loopback HTTP │        │  └────────────────────┘  │      └──────┬───────┘
  │  ┌─────┴───────────┐   │        │  ┌────────────────────┐  │             │ WSS+HTTPS
  │  │ tunnel agent    │───┼────────┼─►│ RELAY (stateful    │◄─┼─────────────┘
  │  │ (outbound dial) │   │ 1 long │  │  broker): pairs    │  │      ┌──────────────┐
  │  └─────────────────┘   │ lived  │  │  scope tunnel↔     │  │◄─────│ Viewer (link)│
  │                        │ mTLS   │  │  clients, forwards │  │      └──────────────┘
  │  Hardware: mount,      │ stream │  │  OPAQUE frames     │  │
  │  camera, focuser, roof │        │  └────────────────────┘  │   relay reads/forges
  └───────────────────────┘        └──────────────────────────┘        NOTHING
        no inbound ports                                          authz re-checked at HOME
```

**Data path:** browser loads the SPA from the **frontend (CDN)**; the SPA opens WSS + HTTPS to the **relay**;
the relay forwards frames down the **scope's single outbound stream** into a loopback call against the local
FastAPI app, where `require(cap)` runs **unchanged**. Response frames flow back up.

### Frontend vs. Relay: TWO services (not one)

**DECISION: Split them.** Deploy behind one hostname via path/subdomain routing (`app.astrodeck` → static
bundle; `relay.astrodeck/api` + `/ws` → broker) so the owner's "one thing" UX is preserved, but they are
independently deployable.

- **Frontend** = stateless, immutable, cacheable, holds **no secrets** → belongs on a CDN/object store.
- **Relay** = stateful, long-lived, sticky (a scope's tunnel lives on **one** node), the most abuse-exposed
  component → must be hardened, is the natural metering/rate-limit point, and is **untrusted**.

Conflating them means every static asset request hits your stateful broker, every relay restart blanks the UI,
and the trust boundary goes fuzzy. **Unanimous across all 6 council members.**

---

## 2. The Central Tension: Trusted (TLS-terminating) relay vs. Blind (E2E) relay

This is the one place the council **split**, and it is the most important call in the document.

- **Trusted relay** (terminates TLS, sees plaintext, forwards a principal): simplest, but a relay compromise =
  full MITM. It can **read** every command/status frame and **forge** a slew-to-sun or roof-open. The
  three security personas reject this as the permanent answer; the `remote=True` interlock and the
  `relay_pubkey`/`viewer_link_pubkey` seams were written precisely to avoid trusting the relay.
- **Blind / E2E relay** (relay forwards ciphertext only; an inner channel terminates at the home): a fully
  compromised relay is reduced to **DoS + traffic-analysis** — it cannot read or forge. This is the
  zero-trust target. Cost: a second crypto layer, and **browser E2E is genuinely hard** (no raw mTLS from
  JS → WebTransport/HTTP3 or WSS carrying a WASM Noise channel pinned to the home key).

**RECOMMENDATION — pick the target, stage to it:**

> **Target state = BLIND/E2E relay.** The relay must be able to read nothing and forge nothing. This is
> non-negotiable for the *permanent* design because the device can slew at the sun.
>
> **Stage to it.** Ship an **interim** where (a) the relay's TLS protects transport, (b) the home re-runs
> full per-request RBAC with `remote=True`, and (c) the relay **holds no signing secret** (home-only token
> verification during the interim, since today's sessions are HMAC and HMAC **cannot** be shared with an
> untrusted relay). This interim is **strictly better** than a trusted-relay design and unblocks delivery.
> **Clearly label** in `SECURITY.md` that until inner E2E lands, the transport vendor/relay can read
> status/preview frames. **Do not let the interim become permanent** for a sun-slewing device.

**What it costs:** two crypto layers, a WASM Noise/WebTransport implementation in the SPA, loss of relay-side
L7 inspection/debugging (metadata only), more reconnect complexity. The safety stakes justify it.

**Council split flagged:** the three security personas (Identity, Zero-Trust Infra, Red-Team) want **E2E from
the start**; the two pragmatic personas (Relay architect, Ops architect) want to **ship on a managed tunnel
first** and earn E2E/relay later. The synthesis above honors both: E2E is the *target*, a hardened
re-verifying interim is what *ships first*.

---

## 3. Decisions (ADR-style)

### ADR-1 — Transport: TUNNEL existing HTTP+WS; do NOT build a native gRPC API
- **Decision:** Run one persistent **outbound** stream from the scope to the relay and **multiplex** the
  existing HTTP request/response + the `/ws` event stream inside it. Each remote request becomes a framed
  message `{stream_id, method, path, headers, body}` forwarded to a loopback `httpx` call against
  `127.0.0.1:8800`. The transport stream itself may be gRPC bidi / HTTP/2 / a single outbound WebSocket —
  that is an implementation detail. **Do NOT re-express capture/goto/autofocus/etc. as `.proto` services.**
- **Why:** The home FastAPI app is the authorization enforcement point — every mutating route is
  `Depends(require(cap))`, the WS gates `view.status` at accept. Tunneling forwards **opaque** HTTP so the
  **same** `require(cap)` runs unchanged; RBAC/safety cannot be re-implemented or bypassed. A native gRPC
  rewrite duplicates ~40 routes + the deadman/horizon/safety gates into a second contract.
- **Alternatives rejected:** Native gRPC control API (parallel auth surface; browsers can't speak raw gRPC →
  also needs grpc-web/Connect + Envoy). REST polling through the relay (kills the event stream the UI needs).
- **Tradeoff:** The relay sees opaque frames → no per-method routing/validation at the edge (exactly what you
  want for a security boundary); observability at the relay is path-level, not typed-method-level.
- **Council:** **Unanimous.** "Tunnel, don't re-RPC" is the single most-agreed decision.

### ADR-2 — Frontend and Relay are TWO services
- **Decision:** Relay = stateful blind byte-forwarder (transport only). Frontend = stateless static origin
  serving `ui/dist`. One hostname via path/subdomain split.
- **Why / Alternatives / Tradeoff:** See §1. **Council: unanimous.**

### ADR-3 — Identity at the edge, authorization at the home (the home is the final word)
- **Decision:** The **relay/frontend edge proves identity** (OIDC: PKCE, state/nonce, `email_verified`,
  `hd` domain-pin where applicable). The **home mints authority and enforces it.** Every tunneled request and
  every tunneled WS accept passes through `resolve_principal(request, remote=True)`, which **HARD-DENIES** the
  open `none` provider (`auth/deps.py:133`). The relay may *tag* a role; the home **re-derives caps** and
  403s independently. The relay can never promote.
- **Why:** OIDC says *who*, not *what*. Re-deriving caps at home means a lying/compromised relay cannot pass a
  control route. The cap model fails closed for unknown roles (`capabilities.py`).
- **Alternatives rejected:** Enforce at the relay (a compromise grants authority). Trust an
  `X-Forwarded-User` header (the exact anti-pattern the `remote=True` hard-deny was written to kill).
- **Tradeoff:** Re-verification at home costs a little latency and **forbids the LAN open-default remotely** —
  operators MUST enable a real auth method (local bcrypt / Google) before remote works. That friction is
  correct: an open admin default must never be internet-reachable.
- **Council:** Unanimous on "home is final word." **Concern flagged below** about the interlock being opt-in.

### ADR-4 — Signing: home is the SOLE issuer; finish the EdDSA seam; never share a symmetric secret
- **Decision:** The home is the only issuer of short-lived, audience-scoped capability tokens. **Implement the
  stubbed EdDSA path** (`auth/session.py:295`; `AuthConfig.session_private_key/public_key` exist,
  `config.py:155-161`). The relay holds **only the public key** (`relay_pubkey`) → it can drop obviously-bad
  attempts but never mint or escalate; the home re-verifies every request. Home **LOCAL** sessions stay HMAC.
  **Viewer links use a SEPARATE key** (`viewer_link_pubkey`). Device-bind tokens via a `cnf` claim to the
  client channel/cert so a token exfiltrated from the relay/client cannot be replayed elsewhere.
- **Why:** Any party that can *verify* an HMAC can *forge* it; the relay must never hold signing material.
  EdDSA lets the home stay issuer while the relay holds no minting-capable secret — the whole reason the
  asymmetric seam exists.
- **Alternatives rejected:** Share the home HMAC secret with the relay (hands the cloud the power to mint
  admin). Reuse the session key for viewer links (couples friend-revocation to owner-logout).
- **Tradeoff:** EdDSA needs an asymmetric-crypto wheel currently **absent from the venv** (Ed25519 via
  PyNaCl/`cryptography`), plus a WASM verifier in the SPA for full E2E. Device binding complicates reconnect.
- **Council:** Unanimous that EdDSA + separate viewer key is a **prerequisite, not a nice-to-have**, for
  friends-view over the relay. (Pragmatic personas allow an **HMAC interim for viewer links** as long as the
  relay never touches the secret, migrating to EdDSA when a real relay needs key-separation.)

### ADR-5 — Device identity + mTLS on the scope→relay dial
- **Decision:** The scope→relay connection uses **mutual TLS** (or a rotatable per-home device cert) so the
  relay binds a tunnel to a **known scope**, plus a restart-surviving **stream-generation fencing token** so a
  stale tunnel can't shadow a fresh one. Per-client session tokens ride the tunnel to the home for the real
  `require(cap)` check. mTLS authenticates the **pipe**; it is **never** an input to AstroDeck authorization.
- **Why:** mTLS authenticates both directions vs. a replayable bearer; fencing prevents split-brain on reconnect.
- **Alternatives rejected:** Device-token-only (replayable). Off-the-shelf tunnel keys as the only auth.
- **Tradeoff:** Cert lifecycle/renewal on a Pi-class home box → automate it.

### ADR-6 — Viewer links: frozen scope, short TTL, individually revocable
- **Decision:** Friend viewer links are a **separate token class** signed with `viewer_link_pubkey`, carrying
  the **frozen explicit cap set** `VIEWER_LINK_CAPS = {view.status, view.preview}` (`capabilities.py:58`) as an
  **explicit per-link list, not a role reference**, so role→cap drift can never silently widen a friend link.
  Short TTL (hours, not forever), unique `jti`, revocable via the existing append-only `revoked_jti` registry
  (admin.users-gated). Prefer a `#fragment` share form so the token never hits server access logs. Close the
  send-only-WS revocation gap with a home-pushed revoke signal (≤30s SLA).
- **Why:** "Let friends view" must **never** become "friends can slew" via a refactor. `view.media` and
  precise site coords (the owner's home location) are **deliberately OUT** of `VIEWER_LINK_CAPS`.
- **Alternatives rejected:** Long-lived bearer links (ASIAIR-style share-forever) — the exfiltration jackpot.
  Shared viewer key with admin sessions. Coarse vendor ACLs (force every friend into a vendor account).
- **Tradeoff:** Short TTL + device-binding means a friend on a train re-auths more; "open the link on my other
  phone" needs a deliberate re-issue. Intended friction.
- **Council:** Unanimous. This is the cleanest pre-cut seam in the codebase.

### ADR-7 — Browsers connect over WSS+HTTPS; SPA served from a CDN; client gets a tiny API-base indirection
- **Decision:** Browsers reach the relay over standard **WSS + HTTPS** (no raw gRPC in the browser). Serve the
  **identical `ui/dist`** from three places with zero code difference: (1) the scope's FastAPI `StaticFiles`
  mount (LAN/offline), (2) a CDN behind the relay (cloud web), (3) bundled in the mobile wrapper. The only
  client change needed: a single `resolveApiBase()` + token indirection in `ui/src/api.ts` and `ui/src/ws.ts`
  (relative same-origin stays the default, so the scope-local UI is byte-identical), and flip the `ad_session`
  cookie from **`SameSite=Strict` → `Lax`** (a server change that gates the whole remote-web story).
  Same-origin web clients keep using the **cookie**; the **Bearer-token path** (already accepted by the server
  via header/`?token=`) is reserved strictly for native/cross-origin wrappers so browser XSS has no token to steal.
- **Why:** The SPA is already a thin client; this is the only structural gap. Pin the CDN bundle with
  **Subresource Integrity (SRI)** + strict CSP so a compromised CDN cannot inject script into an admin browser.
- **Tradeoff:** Reverse-proxying API+WS through the relay origin keeps the cookie working and kills CORS, at the
  cost of the relay terminating transport TLS (which it does for the tunnel anyway). The stale-bundle problem on
  the service worker is handled with a `skipWaiting` + "new version, reload" toast.
- **Council:** Unanimous; the frontend persona owns the surgical detail.

### ADR-8 — Mobile: PWA first, Capacitor if needed, NEVER Electron
- **Decision:** Primary mobile client = the **responsive PWA** (the UI is already touch-first: BottomNav,
  TouchGuard, wake-lock, 44px targets, `viewport-fit=cover`). Add `manifest.json` (server already whitelists
  it) + a service worker (cache-first app shell only — **never** cache `/api` or `/ws`). Wrap the **same
  bundle** in **Capacitor** *only* if you need App Store presence or reliable native push (APNs/FCM). The
  mobile client is a **full E2E peer** with its own device-bound key + `jti` (the lost-phone story). **No
  Electron** (desktop-only, fails the phone/tablet goal). React Native only if a fully-native UI ever becomes a
  product requirement (it would fork the codebase).
- **Why:** One artifact, three delivery channels. Per-device `jti` gives precise least-privilege + instant
  per-device revocation. Honors the owner's correct point 2, extended to mobile.
- **Tradeoff:** iOS PWA limits (Web Push needs iOS 16.4+ home-screen install; background throttling). That
  specific need is the **only** strong trigger to adopt Capacitor.
- **Council:** Unanimous reject of Electron; unanimous PWA-first.

### ADR-9 — Stream lifecycle: reconnect, heartbeat, backpressure, no head-of-line blocking
- **Decision:** Scope→relay reconnect = exponential backoff + jitter capped ~15s (mirror `ws.ts`); an
  **application-level heartbeat** (~5–10s) with a missed-beat dead-tunnel timeout, kept **separate** from rig
  telemetry liveness (a legitimate 3-minute exposure must not look like a dead tunnel). On tunnel loss the
  scope keeps running the rig locally and stops fanning out. **Give large media its own logical stream/conn**
  so a ~125MB FITS download never head-of-line-blocks the 2s status poll or guide graph. Backpressure =
  **drop-oldest** on preview/status (matches the existing `EventBus` `maxsize=500`), **never drop** on
  log/command-response.
- **Why:** The system is already drop-tolerant for telemetry and must-deliver for commands; the tunnel must
  preserve that asymmetry, and the preview/FITS path is the bandwidth monster.
- **Tradeoff:** Remote viewers can miss intermediate preview frames under congestion (always get the latest) —
  correct for live astro telemetry; full fidelity is pulled on demand.

### ADR-10 — Large media (FITS/lossless) is PULL-based and out-of-band
- **Decision:** Broadcast the cheap `preview` path (display JPEG + histograms) to everyone; serve
  `/lossless.png`, `/fits` (up to ~125MB, `CAP_VIEW_MEDIA`), `/thumb.jpg` as **HTTP GETs with range-request
  support** on their **own** logical streams. The relay does **streaming pass-through** (no whole-object
  buffering) to bound relay memory under multi-client fan-out.
- **Why:** Honors the existing cheap-broadcast / heavy-pull split; range support lets a flaky mobile link
  resume a 125MB FITS instead of restarting.
- **Tradeoff:** The relay needs HTTP semantics (range, content-type), not just opaque framing — standard
  reverse-proxy behavior.

---

## 4. NON-NEGOTIABLE invariants the HOME must enforce (regardless of the relay)

These hold **even if the relay is fully compromised**. The relay is untrusted infrastructure.

1. **Sun-avoidance is a hardware-safety gate below the API.** Today there is a `_check_horizon` gate
   (`hub.py:697`) and a 250ms move-axis deadman, but **NO** hub-level sun-cone/daytime motion gate —
   `solar_override` is only a capability string. **Add `_check_solar` on the same slew / `set_tracking` /
   sequence-start chokepoint as `_check_horizon`, gated by `config.solar_override` + explicit confirmation,
   BEFORE any remote write path ships.** No client path — remote, relay, or buggy — may point optics at the sun.
   *(This is the single top blocker; flagged by Red-Team and seconded by Zero-Trust Infra.)*
2. **The open `none` provider can NEVER be served remotely.** Every tunneled request and WS accept must set
   `remote=True` so `resolve_principal` hard-denies (`auth/deps.py:133`). This is **opt-in per call site** —
   the relay-ingress path MUST force it, with an explicit test. A missing flag here is a **catastrophic silent
   auth bypass** (the whole internet becomes admin on a sun-slewing device). With a *transparent* tunnel the
   home sees a localhost-looking request, so `remote` must be derived from a trusted proxy header the tunnel
   sets — **do not rely on the interlock alone when the transport hides its remoteness.**
3. **The relay can never forge a principal or an admin command.** Home is the sole token issuer (EdDSA);
   the relay holds only a public key. No `X-Forwarded-User`-style trusted header.
4. **RBAC is re-checked at the home on every request**, never decided at the edge. Caps fail closed for
   unknown roles. The remote surface is a **deliberate subset** of the LAN surface (a remote allow-list at
   relay-ingress), and **DESTRUCTIVE_CAPS** (`capabilities.py:45`: mount, power, safety, solar_override,
   admin.users) require a **fresh, short-TTL step-up** — a long-idle remote session cannot slew or open the roof.
5. **The `/ws` channel stays send-only (pure egress).** Commands stay on the REST path where each is
   individually cap-gated. Never add an inbound command channel over the WS to "simplify the tunnel" — it would
   bypass `require(cap)`.
6. **Mount limits / horizon / safety floors are independent of auth.** Authz decides *may-you-ask*; safety
   decides *is-this-physically-allowed*. Remote access bypasses **neither**.
7. **Loss of the relay degrades to safe local autonomy.** The home's autonomous safety (`on_unsafe`
   abort/park/warm) is the backstop; the relay is a convenience path and an availability choke point, never a
   safety dependency.
8. **A local-only kill switch** tears down the outbound tunnel instantly — master revocation for all remote
   access, needing no relay cooperation and unreachable from the remote path.
9. **Per-principal audit + rate-limit at the home relay-ingress.** Every relay-borne command is logged at the
   home with the **verified** principal (role/email/jti from the signed token, not a relay assertion),
   append-only, so a compromised relay cannot rewrite history.

---

## 5. Where the council AGREED vs. SPLIT

**Agreed (all 6, or 5/6):**
- Decouple UI from API, and the on-scope/cloud UI is one bundle (already true).
- Scope dials **outbound**; no inbound at home.
- **Frontend ≠ relay** — split into two services.
- **Tunnel HTTP+WS; do NOT build a native gRPC control API.**
- Home is the sole authorization enforcement point; `remote=True` interlock is mandatory.
- EdDSA home-issuer + separate `viewer_link_pubkey` is a prerequisite for friends-view.
- Viewer links: frozen `VIEWER_LINK_CAPS`, short TTL, revocable `jti`.
- Mobile = PWA (+ Capacitor if needed); **Electron rejected.**
- The missing `_check_solar` sun gate must land before remote write ships.

**Split:**
- **E2E from day one vs. ship-on-managed-tunnel-first.** Security trio (Identity, Zero-Trust Infra, Red-Team)
  want the blind/E2E relay as the gating requirement. Pragmatists (Relay architect, Ops architect) want to
  **buy** transport first (Tailscale / Cloudflare Tunnel) and build the bespoke relay/E2E only when a concrete
  product need appears. **Synthesis: E2E is the target; a hardened re-verifying interim ships first** (§2).
- **Build vs. buy the relay now.** Ops architect argues most of the proposal collapses onto a managed tunnel +
  one interlock flip (2–3 weeks, no custom relay). The relay architect agrees with bootstrapping on managed
  infra but expects to build the broker for multi-tenant fan-out. **Adopted: buy first, build the relay only
  when it earns its keep** (§6).
- **mTLS necessity for v1.** Security personas want mTLS + device identity immediately; pragmatists are fine
  with a managed tunnel's own auth for the owner-only stage. **Adopted: managed tunnel for Stage 1; mTLS +
  device cert when the custom relay lands.**

---

## 6. Staged delivery path

Each stage ships independently and is a strict superset of the prior one. Custom infra is deferred until a
managed-infra gap forces it.

- **Stage 1 — Owner remote (this week, off-the-shelf).** `tailscaled` on the scope box (outbound-only via
  DERP, zero inbound) + owner's phone on the tailnet. Bind AstroDeck to `127.0.0.1` so the tunnel is the only
  listener. **Enable local bcrypt** so the `remote=True` interlock enforces a real admin principal. **Make the
  home treat tunneled traffic as `remote=True`** (trusted proxy header) — this is the gating safety fix.
  Land **`_check_solar`** before any remote *write* is exposed. Near-zero custom code.
- **Stage 2 — Friends view (1–2 weeks).** Viewer-link mint/revoke on `viewer_link_pubkey` (EdDSA, or HMAC
  interim with the relay holding no secret) + Tailscale Funnel / Cloudflare Tunnel read exposure on a public
  hostname. Ship the **PWA** (manifest + service worker) and the `resolveApiBase()` + `SameSite=Lax` client
  change. Add login/viewer-link **rate-limiting at the edge** and an **audit line** on every motion-capable action.
- **Stage 3 — Off-tailnet friends (when warranted).** Google OIDC (already built) for friends without a
  tailnet device; `role_allowlist` with a **viewer ceiling** re-evaluated per request.
- **Stage 4 — Custom relay (only on a real product need).** Build the bespoke outbound-gRPC/WSS broker + the
  blind/E2E inner channel **only** for multi-tenant fan-out, per-scope metering, branded `scope.astrodeck.app`,
  or fleet management — none of which exist for "owner at dinner + friends viewing." Host on **Fly.io**
  (always-on, Anycast, suited to long-lived bidi streams); **avoid Cloud Run** (request-scoped, scale-to-zero,
  fights persistent streams). When it lands, add mTLS device identity, the EdDSA verify-only relay, and the
  WASM-Noise browser E2E. The seams (`relay_pubkey`, EdDSA session, `viewer_link_pubkey`,
  `/api/remote/config`) are already pinned, so deferring costs almost nothing.

**The relay earns its keep** at Stage 4 — when you have multi-tenant scale or a branded product, not before.
Running a public service that holds persistent connections to people's telescopes is a high-value target with a
permanent security-maintenance burden; don't own it until you must.

---

## 7. Open questions the owner must answer

1. **E2E now or staged?** Accept the interim (relay-TLS transport + home re-verification, relay reads
   status/preview) to ship in weeks, or block remote on full browser E2E (WASM Noise / WebTransport)? *Recommend:
   stage, but commit in writing to E2E as the target and a date.*
2. **Friend identity model.** Tailnet device per friend (more friction, no public host) vs. public hostname +
   viewer links + OIDC (more exposure to harden)? Stage 2 vs. Stage 3 ordering.
3. **What may the owner do remotely by default?** Does remote include **mount slews and power**, or are those
   off-remote / behind break-glass step-up only? (Affects the remote allow-list and step-up UX.)
4. **Metadata-leakage tolerance.** Even a blind relay sees scope-id, timing, and frame sizes — enough to infer
   "owner is away" and "rig is imaging" for a device at a known home address. Acceptable, or do we pad/batch?
5. **Crypto backend.** Approve adding an Ed25519 wheel (PyNaCl / `cryptography`) to the home venv + a WASM
   verifier to the SPA? This gates ADR-4 and full E2E.
6. **`SameSite=Strict → Lax` on `ad_session`.** Confirm the server-side cookie change (required for the
   cloud/relay web story) with whoever owns auth.
7. **Reliable push-to-phone** (sequence-complete / safety alerts) — is this a hard requirement? If yes, that is
   the trigger for Capacitor over pure PWA (and possibly earlier).
8. **Single-operator concurrency.** Confirm the home serializes/locks motion so a stale **remote** client
   cannot queue a slew after a **local** abort. RBAC controls *who*, not concurrent conflicting *whats*.

---

## 8. The one change to decide first

**Write down now:** the relay is an **untrusted transport broker** that tunnels the existing HTTP+WS, and the
home FastAPI app remains the **sole authorization enforcement point** — every tunneled request and WS accept
goes through `resolve_principal(..., remote=True)` so the LAN open-default can never be served remotely, and a
hub-level **`_check_solar`** gate makes pointing real optics at the sun structurally impossible from any client
path. That single decision (tunnel don't re-RPC; relay transports, home authorizes; safety lives below the API)
collapses the owner's biggest ambiguities and turns the remaining work into wiring up seams the codebase already
stubbed.
