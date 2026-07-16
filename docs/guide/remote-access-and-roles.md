# Remote access & roles

This page is for **Remote Rae** — anyone reaching AstroDeck from outside the
home network — and for anyone who needs to understand what each login role can
see and do.

---

## The default: open on the LAN

Out of the box, AstroDeck requires **no sign-in**. On your local network every
client is treated as an **admin** with full control. There is no login screen,
no accounts, nothing to configure. This is intentional and stays byte-for-byte
the same until you enable a sign-in method.

Enabling authentication (Settings → **Auth**, admin only) turns on roles. Once
any method is enabled, the app can show a **Login** screen.

> A local-only rig that never leaves the LAN can stay open. Bind the server to
> loopback (`--host 127.0.0.1`) if you want to be sure nothing off-box can reach
> it — see [getting-started.md](getting-started.md).

---

## Signing in

When a sign-in method is enabled, unauthenticated users get the full-screen
**Login** gate. It offers, depending on what the admin configured:

- **First-run create-admin** — on a fresh install with a method enabled, the
  first person creates the initial administrator account (username, optional
  email, password). Passwords are limited to 72 bytes.
- **Local username / password** — a **Sign in** button.
- **Sign in with Google** — a full-page redirect through the server's OAuth flow
  (only shown when Google is configured).

You can also seed or reset a local admin from the command line without the UI:

```
python -m astrodeck create-admin <username>
```

It prompts for a password and exits without starting the server — the recovery
path if you ever lock yourself out.

Your role and email appear under **Settings → Account**, which also hosts
sign-out. Viewers get a **View-only** badge there and an explainer that controls
are hidden for their role.

---

## The three roles and what they actually gate

Capabilities are the real unit of access; roles are just named bundles of them.
The source of truth is `server/astrodeck/auth/capabilities.py`.

| Role | Holds | Cannot |
|------|-------|--------|
| **viewer** | `view.status`, `view.preview` | everything else |
| **operator** | viewer + `control.capture`, `control.guide` | mount, power, config, media, precise site |
| **admin** | **all** capabilities | — |

What the individual capabilities gate:

- **`view.status`** — read-only status and the live event stream (WebSocket).
- **`view.preview`** — downsized preview frames (not raw FITS).
- **`view.media`** — raw FITS / full-resolution science downloads. **Admin only.**
- **`view.site_precise`** — the exact site coordinates, and (new in the weather
  feature) the whole forecast / Sky Conditions / radar surface. **Admin only.**
- **`control.capture`** — imaging: capture, loop, autofocus, cooler, dew heater,
  focuser, filter wheel. (Operator can image but **cannot start a slewing
  sequence**.)
- **`control.mount`** — all mount motion (slew, park, tracking) **and** starting
  a multi-target sequence (which slews). Admin only.
- **`control.guide`** — start/stop/dither guiding.
- **`control.power`** — switch outputs (can brown out the rig). Admin only.
- **`config.backend`** — connect rigs, apply/activate profiles.
- **`config.site_optics`** — edit the observing site, optics, and weather config.
- **`config.safety`**, **`config.solar_override`** — safety floors and the
  sun-avoidance disarm. Admin only.
- **`config.alerts`**, **`admin.users`**, **`system.update`** — alerts, user/auth
  admin, and applying updates. Admin only.

Some capabilities are **destructive-tier** (mount, power, safety, solar
override, user admin, update): the UI double-confirms them even for an admin.

In the UI, controls you lack are **hidden or disabled** — you never tap a button
and get a 403. A viewer sees status and previews and can sign in; that's it.

---

## Reaching the rig from outside — the relay

AstroDeck's **relay** gives you a remote front door **without port-forwarding or
NAT configuration**. The full design is in
[`relay/README.md`](../../relay/README.md); the essentials:

- The home ("scope") dials **one outbound WSS** connection to the relay and
  keeps it open. Your phone or laptop hits the relay over HTTPS/WSS, and the
  relay **tunnels** each request down to the home, which serves the whole app.
- The relay is a **dumb byte-forwarder**. It holds no home secret and cannot
  forge a login. Every tunnelled request is **re-authenticated and
  re-authorized at the home**.
- Over the relay the open "none" posture is **denied** — remote requests are
  treated as `remote=True`, so remote users must actually authenticate.
- The privilege-defining writes (admin config, auth config, remote config) are
  **tunnel-blocked at the home** even for an authenticated admin, so a
  compromised relay cannot escalate.

The scope side is enabled under the remote config (admin only): a relay URL, a
device token that proves which home is dialing, and a home id the relay pins a
path to. When disabled (the default), the scope never dials out at all.

> **Documented risk.** TLS terminates at the relay, so a compromised relay can
> read tunnelled traffic and act as an already-authenticated **viewer** in real
> time. Bind the scope-to-relay link with mTLS in production. Token minting is
> withheld from the relay (it holds a public key only).

---

## Site privacy for remote and low-role users

Because remote viewers are often on a limited role, the location rules matter:

- A principal **without `view.site_precise`** (every viewer and operator) never
  sees the site's **name, latitude, longitude, or elevation** — anywhere. The
  coordinates are **removed** (made absent, not blanked) from every status,
  summary, and config payload at a single server seam.
- **Weather events are dropped entirely** for non-holders over the WebSocket —
  not just stripped — so even location-free forecast numbers never reach them.
- The `GET /api/weather` and radar-tile routes **require** `view.site_precise`
  outright.

So a shared read-only remote link shows the live rig and previews but reveals
nothing about *where* the observatory is. See
[site-and-locations.md](site-and-locations.md) and [weather.md](weather.md).
