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
are locked for their role.

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
- **`config.backend`** — connect rigs, apply/activate profiles. **Admin only**
  (operator does not hold it, despite some in-app copy that reads "operator or
  admin access" — see [equipment-and-profiles.md](equipment-and-profiles.md)).
- **`config.site_optics`** — edit the observing site, optics, and weather
  config. **Admin only** (same caveat — see
  [site-and-locations.md](site-and-locations.md)).
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
| **Plan** — Run / Monitor's Pause / Resume / Abort | visible but **disabled**, with a lock note naming the required access | **disabled — same lock note as viewer** (`control.mount` is admin-only; operator doesn't hold it despite holding `control.capture`) | full |
| **Sessions** | cards + review drawer visible; regrade controls **disabled with a lock note** (`control.mount`) | resume/auto-resume-arm/update-from-plan/delete need `control.mount` (disabled); regrade controls carry the **same lock note**, for the same reason | full |
| **Monitor** | dashboard fully visible, controls row **disabled** with a shared lock note | dashboard fully visible, controls row **disabled** (`control.mount` is admin-only — same note as viewer) | full |
| **Sky Conditions / Radar** (on Monitor) and the **Weather** settings panel | never rendered — no request even fires | never rendered | full (`view.site_precise`) |
| **Settings → Connect** (drivers, site, weather, Sky Atlas pack) | read-only | read-only (`config.backend`/`config.site_optics` — operator holds neither) | full |
| **Settings → Safety** (sun avoidance) | current value shown, toggle disabled | disabled (`config.solar_override` is admin-only) | full |
| **Settings → Profiles** | hidden (a "needs admin access" note instead) | **read-only, same note as viewer** (`config.backend` is admin-only) | full |
| **Settings → Updates / Users / Auth** | tabs don't exist in the nav | tabs don't exist in the nav | full (`system.update` / `admin.users`) |

> **What "disabled" actually looks like.** Every row above that reads
> "disabled" or "hidden" for a role is enforced **in the UI itself**, not
> just on the server: the control renders in its normal position, disabled,
> next to a short lock note naming the real requirement — e.g. the Session
> review drawer prints *"Read-only — regrading frames needs admin access."*
> next to a greyed-out **mark accepted** / **mark rejected** pair, and the
> Plan view shows a disabled **≡ Run Sequence** button over a *"Running a
> sequence needs admin access."* note. The lock-note wording is generated
> from the same capability table the gate enforces (`accessPhrase` in
> `ui/src/lib/caps.ts`, mirroring the server's role table), so the text
> always names the role that actually holds the capability — and no role
> gets a surprise 403 from a control that looked enabled.

### A disposable way to verify this yourself

Because capability sets can be edited over time, don't just trust this
table — check it against your own build in a few minutes. **Order matters**:
creating a local user account does **not** by itself let anyone sign in as
that account — the `local` sign-in *method* has to be enabled first, or
there is no login path at all (see
[`docs/SECURITY.md`](../SECURITY.md#testing-role-gating-from-loopback-authtrust_loopback)
and `server/astrodeck/auth/deps.py` `build_provider`).

1. **Enable the local method.** As an admin, go to **Settings → Auth**
   (the tab only exists for `admin.users` holders) and turn on **Enable
   local accounts** under Sign-in methods, then press **Save methods**. This
   is the step that actually turns on logins — creating users before this
   does nothing observable.
2. **Get an admin account signed in.** On a fresh install with no users yet,
   the next unauthenticated visit shows the **First-run create-admin**
   screen (username, optional email, password) — use it to create the
   initial administrator. Already have an admin? Just stay signed in as
   them. (`python -m astrodeck create-admin <username>` also seeds/resets a
   local admin from the CLI, but — same caveat — it only writes the user
   record; it does not enable the `local` method for you.)
3. **Add a disposable operator/viewer.** As the admin, go to **Settings →
   Users → Add user**, fill in a throwaway username/email/password, pick
   **operator** or **viewer** as the role, and press **Create user**.
4. **Sign in as it.** Sign out (**Settings → Account**) or open a
   private/incognito window, and sign in as the disposable account.
5. Walk the screens in the table above, noting what's hidden vs. disabled
   (with a lock note) vs. usable — it should match.
6. Delete the throwaway account(s) from **Settings → Users** when you're
   done — they're accounts only, not tied to state a real user would care
   about losing.

**Optional strict-mode check (`auth.trust_loopback`).** Everything above
verifies role gating through an authenticated session, which is the
mechanism that actually matters. Separately, **Settings → Auth** also has a
**Trust this machine (loopback) as admin** toggle
(`auth.trust_loopback`, default **on**). It only does something when **no**
sign-in method is enabled at all: on, an unauthenticated loopback caller
still gets the open-admin default (today's behavior); off, that same
unauthenticated loopback caller is denied, exactly like a remote caller. Use
it only to verify that specific denial — it is **not** a way to reach
operator/viewer behavior, and flipping it off with no method enabled just
locks that browser out (a loud in-app warning says so before you save).
Recover by editing `trust_loopback` back to `true` in the server's config
file and restarting, or by seeding an account with
`python -m astrodeck create-admin`. See
[`docs/SECURITY.md`](../SECURITY.md#testing-role-gating-from-loopback-authtrust_loopback)
for the full mechanics.

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
