# AstroDeck UI Rebuild — 03 · Personas, Roles & Attention Model

> **Purpose.** "Beginner to advanced" is a range, not a design target. This grounds the user spectrum in the real RBAC model and the remote/multi-user spec, defines design personas with goals and contexts, and — the part both UX reviewers flagged as missing — an **attention model** (what demands eyes *now* vs. what you sleep through). Sources: `server/astrodeck/auth/*`, the design spec `docs/superpowers/specs/2026-06-16-pluggable-backends-rbac-remote-design.md`, and the four persona reviews.
>
> Companion: [`01-screen-ia-map.md`](01-screen-ia-map.md), [`04-failure-2am-ux-spec.md`](04-failure-2am-ux-spec.md).

---

## 1. The role model (ground truth)

Three roles, ranked by privilege; resolution is **fail-closed** (unknown role holds nothing). `GET /api/me` → `{role, email, caps[]}`.

| Role | Holds | Can do | Cannot do |
|---|---|---|---|
| **viewer** | `view.status`, `view.preview` | watch live status + downsized previews | no raw FITS, no precise coords, **no controls at all (incl. STOP)** |
| **operator** | + `control.capture`, `control.guide` | run capture/loop, autofocus, cooler, filter, guiding | **no mount motion, no sequence start, no power, no config, no media** |
| **admin** | all 15 caps | everything | — |

**Capabilities** (drive control visibility — render off `caps`, fail-closed to viewer):

`view.status` · `view.preview` · `view.media` (raw FITS) · `view.site_precise` · `control.capture` · `control.mount` · `control.guide` · `control.power` · `config.safety` · `config.solar_override` · `config.backend` · `config.site_optics` · `config.alerts` · `admin.users` · `system.update`.

**`DESTRUCTIVE_CAPS`** = `{config.safety, config.solar_override, control.mount, control.power, admin.users, system.update}` — the UI **double-confirms these even for admin**. This set is pinned server-side; read it from one place, don't hardcode a parallel list.

> Key escalation detail: **sequence-start and polar are `control.mount`-gated.** An operator can run a single capture + guiding but **cannot start a slewing sequence.** Design the operator surface accordingly.

---

## 2. Auth methods (pluggable)

| Method | Identity | Role source | Login UI |
|---|---|---|---|
| **none** (default) | every caller → admin | `ALL_CAPS` | no login screen; today's open LAN behavior |
| **google** (OIDC + PKCE) | verified Google email | `role_allowlist[email]` → else `default_role` → else deny (re-evaluated every request) | redirect to Google |
| **local** (user/pass) | case-folded username | stored `User.role` | username/password form |

`GET /api/auth/methods` (open) → `{methods, google_configured, first_run}` tells the UI which login to show. First-run creates the first admin (`POST /auth/setup/local`), then self-closes. Last-admin protection prevents deleting/demoting the final admin. Disabling all methods re-opens the server to admin-for-all on the LAN — a loud one-toggle foot-gun the UI should warn about.

---

## 3. Remote / multi-user model (spec intent)

- **Remote = a relay tunnel, not a VPN.** The scope dials one outbound WSS to a public relay; the relay forwards opaque bytes; **RBAC and safety are re-enforced at home on every tunneled request.** The `none` provider is hard-denied for any remote request (interlock is per-request, not just boot).
- **Many concurrent viewers, one home subscription.** The relay projects a single home-side bus subscription to N remote watchers (cheap on a Pi). The design explicitly supports a crowd of spectators plus the local owner.
- **Control is NOT an exclusive lock, and there is no "request control" flow.** Authority is purely capability-based. Decided for first ship: **viewers get no motion controls, including STOP; the operator/owner is the safety authority.** Viewer motion controls are *hidden, not just disabled,* behind a "View-only — you're watching, not driving" badge.

---

## 4. Design personas

Grounded strictly in what the roles + spec support. Each has a goal, a context, and what the UI owes them.

### P1 · "First-light Fiona" — the absolute beginner (admin, solo)
- **Context:** new rig, cold backyard, phone in hand, anxious about breaking expensive gear. Default `none` auth → full admin, but zero domain fluency.
- **Goal:** get *one* good image without understanding declination or HFR.
- **Needs:** a guided, gated first-night path; safe defaults; "what do I do now" at every step; plain-language verdicts ("focus is good") not raw arcseconds; loud reassurance that the software won't crash the mount while she sleeps.
- **Reviewer evidence:** Tom — *"a beginner doesn't need SHO on night one"*; the scary BREAKS lines (frost, empty sky, crash into pier) need paired reassurance.

### P2 · "Operator Owen" — the experienced imager (admin, solo)
- **Context:** known rig, runs a saved profile, wants speed and density. Comfortable with V-curves and RMS.
- **Goal:** set up fast, tune automation, walk away, occasionally check from bed.
- **Needs:** an expert console (the 11 views as a dashboard), every number visible, fast access to overrides, dense layout, keyboard/large-touch targets in the dark.

### P3 · "Remote Reza" — the away-from-rig owner (admin, remote)
- **Context:** rig at home or a dark site; he's elsewhere, authenticated over the relay; every action re-authorized at home.
- **Goal:** confirm the night is healthy and intervene only when needed.
- **Needs:** a glanceable remote dashboard, trustworthy *"is my night OK?"* status, push/alert on exceptions, graceful behavior on flaky links (the UI already has WS-down/stale handling).

### P4 · "Spectator Sam" — the read-only viewer (viewer, possibly many)
- **Context:** a shared link to watch someone else's session; could be one of many.
- **Goal:** watch the pretty picture build, see live status.
- **Needs:** live status + previews, **no controls visible at all**, coarsened coordinates, "you're watching, not driving" framing. No confusion about why there's no STOP button.

### P5 · "Assistant Ada" — the trusted helper (operator)
- **Context:** a club member / friend running subs on already-pointed gear; a future-tier role that exists in code but isn't first-ship-complete.
- **Goal:** run imaging + guiding without touching the mount, power, or config.
- **Needs:** an operator surface that cleanly *hides* mount/power/config/sequence-start and explains the boundary ("you can image; the owner drives the mount").

### P6 · "Admin Aria" — the multi-user steward (admin)
- **Context:** club or multi-night deployment with `local`/`google` enabled.
- **Goal:** provision accounts, set roles, manage safety/auth.
- **Needs:** the Users/Auth panels, last-admin guardrails, clear destructive-action confirms.

> **The central UX tension** (Maya & Devin): P1 and P2 use the *same engine* with opposite postures. The recommended resolution is **two front doors over one set of views** — a *Guided Night* wizard (P1) and an *Expert Console* (P2/P3) — not two apps. The onboarding workflow doc's 10 gated steps are already a near-perfect wizard spine.

---

## 5. The attention model (the unattended-run heart of the product)

A night is **minutes of high-touch setup, then hours of exception-driven monitoring.** The most important design axis is *what deserves the user's attention, and how loudly.* Map every state to one of three tiers. Backend signals in parentheses (see docs 02 & 04).

### Tier 0 · Ambient — "glance and relax"
Calm, non-interrupting. The Monitor home answers *"is my night OK?"* at a glance.
- Normal run progress (`sequence.progress`), guiding within tolerance (`rms_total` low), cooler at target (`at_target`), frames accumulating, meridian flip counting down (`meridian.status="counting"`).
- **Treatment:** quiet dashboard, sparklines, finish-time clock. No toasts.

### Tier 1 · Notice — "you'll want to know, no panic"
Persistent but non-blocking. Render as banners/chips, **not** transient toasts (these are ongoing conditions, not events).
- Frame rejected / HFR spike (`hfr_reject`, `log` warn), guiding briefly lost then recovering (`recover_guiding`), single dropped frame, disk getting low (`disk.low`), NINA link warming up (`nina_link.warming_up`), cooling not yet at target, plate-solve degraded to raw GoTo.
- **Treatment:** amber chip in the run strip + a line in the log; clears itself when resolved.

### Tier 2 · Act / Wake — "the night is at risk right now"
Loud, sticky, and (for remote) push-worthy. These are the states the current UI already makes sticky.
- **Safety unsafe / pause** (`safety.is_safe=false`, `on_unsafe` pause/abort), **stale safety reading** (fail-closed = unsafe), **sequence aborted/error** (`end_reason ∈ unsafe|aborted|error`), **disk critical** (`disk.critical`), **device dropped mid-run**, **mount-floor/pier abort**, **WS link down** (`telemetryStale` / ConnectionBanner), **meridian flip due but disabled** (`meridian.status="due"` + `flip_enabled=false`).
- **Treatment:** sticky toast (ttl 0) + persistent banner + (remote) push/alert sink. Never auto-dismiss.

### Wiring it
- **Local owner:** Tier 2 = loud on-screen + optional sound; Tier 1 = banner; Tier 0 = ambient.
- **Remote owner (P3):** Tier 2 = push notification via the configured alert sink / deadman URL; Tiers 0–1 = pull-only (open the dashboard).
- **Viewer (P4):** sees Tier 0–1 as status; Tier 2 framed as "the operator has been alerted" — viewers can't act.

> The `log` channel carries `level ∈ info|warning|error` with a per-source tag and a 200-entry replay buffer — a ready-made feed for the Notice/Act tiers. But **ongoing conditions belong in status fields rendered as banners**, not as a scroll of transient log lines (doc 04 §5).

---

## 6. Gaps to decide before building

1. **No "who's connected / who's driving" presence.** The relay knows `ws_id → jti` but nothing surfaces a viewer/operator roster to the owner. A presence indicator is unbuilt — decide if the rebuild adds it.
2. **No control-handoff / "request control" gesture** — explicitly deferred in the spec. Concurrent `control.mount` holders are uncoordinated (last-write-wins at the device). If multi-operator is a real scenario, this needs a design.
3. **Operator role is incomplete** — caps exist, but no operator-specific surface beyond gated buttons. The Guided-Night vs Expert-Console split should account for it.
4. **Viewer-link issuance + per-link `view.media`/`view.site_precise` opt-in** is a spec seam, not built. So is the long-lived WS revocation re-check (the socket only auth-checks at accept time today).
5. **No personas/goals existed before this doc** — validate P1–P6 against real users before committing the IA.
