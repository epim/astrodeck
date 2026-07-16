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

**Session expiry does not touch the imaging engine.** If your sign-in session
times out (or you sign out, or your tab loses the connection), all that
happens is your browser gets logged back out to the **Login** gate. A
running [sequence](plan-and-sequences.md) or armed
[auto-resume](sessions-multi-night.md#resuming--manual-and-auto-at-dusk)
keeps going untouched — the engine runs server-side and has no dependency on
any particular browser tab staying authenticated. Signing back in just
reconnects you to whatever's already happening.

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

### Screen-by-screen: what each role actually sees

This table is built from the same capability checks the UI itself makes (the
`useCan*` hooks in `ui/src/lib/caps.ts`, one per screen's component), so it
tracks what's really hidden/disabled per role, not just the intent:

| Screen | viewer | operator | admin |
|---|---|---|---|
| **Equipment** (drivers, device assignment, Rig Actions, Tasks, Rotator, Profiles) | read-only | read-only (`config.backend` required; operator doesn't hold it) | full |
| **Capture** | preview only, **Read-only** badge | full (`control.capture`) | full |
| **Focus** | preview only | full (`control.capture`) | full |
| **Mount** | pointing visible, controls disabled | disabled (`control.mount` — operator doesn't hold it) | full |
| **Align** (polar) | visible, disabled | disabled (`control.mount`) | full |
| **Guide** | graph visible, disabled | full (`control.guide`) | full |
| **Power** | read-only ports | disabled (`control.power`) | full |
| **Sky Atlas** | full (search, framing, mosaic, visibility are all local/client-side) | full | full |
| **Plan** — building the on-screen draft | full (local-only, no capability check) | full | full |
| **Plan** — plan library save/import/delete | disabled | enabled (`control.capture`) | enabled |
| **Plan** — Run / Monitor's Pause / Resume / Abort | hidden (viewer gets a passive "View only" note) | **shown and clickable**, but see the caveat below | full |
| **Sessions** | cards + review drawer visible; regrade controls are **not disabled in the UI** — see the caveat below | resume/auto-resume-arm/update-from-plan/delete need `control.mount` (disabled); regrade controls not disabled in the UI either | full |
| **Monitor** | dashboard fully visible, controls row hidden | dashboard visible, controls row shown (same caveat as Plan Run) | full |
| **Sky Conditions / Radar** (on Monitor) and the **Weather** settings panel | never rendered — no request even fires | never rendered | full (`view.site_precise`) |
| **Settings → Connect** (drivers, site, weather, Sky Atlas pack) | read-only | read-only (`config.backend`/`config.site_optics`) | full |
| **Settings → Safety** (sun avoidance) | current value shown, toggle disabled | disabled (`config.solar_override` is admin-only) | full |
| **Settings → Profiles** | hidden (a "needs operator or admin" note instead) | full (`config.backend`) | full |
| **Settings → Updates / Users / Auth** | tabs don't exist in the nav | tabs don't exist in the nav | full (`system.update` / `admin.users`) |

> **A caveat worth knowing before you rely on it.** The Plan **≡ Run
> Sequence** button and Monitor's **Pause / Resume / Abort** row are shown to
> anyone holding `control.capture` — which includes operator. But the server
> routes behind all of them (`/api/sequence/start`, `/pause`, `/resume`,
> `/abort`, `/recover`) require `control.mount`, which **only admin holds**.
> So today an operator account can open these controls and will get a
> permission error the moment they press one. Until that's reconciled,
> treat sequence run-control as **admin-only** in practice, and don't be
> surprised if an operator reports a button that "doesn't work" here — it's
> this gap, not a bug in their setup.
>
> The **Session review drawer**'s regrade controls (mark accepted/rejected)
> have the same shape of gap, but wider: the drawer applies **no capability
> check at all** in the UI, so even a **viewer** sees fully-interactive
> regrade buttons. The server route behind them
> (`PATCH /api/sessions/{id}/frames/{id}`) requires `control.mount` —
> admin-only — so a viewer or operator who regrades a frame will get a
> permission error, not a silent no-op. Treat regrading, like sequence
> run-control, as admin-only in practice.

### A disposable way to verify this yourself

Because capability sets can be edited over time, don't just trust this
table — check it against your own build in a couple of minutes:

1. Create a throwaway account of each role you care about:
   `python -m astrodeck create-admin _verify_admin` (or use **Settings →
   Users** once you have an admin) and add a `viewer`/`operator` user the
   same way.
2. Sign in as each in turn (a private/incognito window keeps sessions from
   colliding) and walk the screens above, noting what's hidden vs. disabled
   vs. usable.
3. Delete the throwaway accounts from **Settings → Users** when you're done
   — they're accounts only, not tied to state a real user would care about
   losing.

If the app disagrees with this table, the app is right — open an issue, and
in the meantime the source of truth is
`server/astrodeck/auth/capabilities.py` on the server side and the
`useCan*` hooks in `ui/src/lib/caps.ts` on the UI side.

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
