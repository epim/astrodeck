# Site & locations

Your **observing site** — latitude, longitude, elevation — is the foundation for
everything sky-related: altitude and azimuth, transit and meridian times,
twilight and dark windows, Atlas visibility, and (if enabled) weather. Until you
set it, AstroDeck uses a default (0, 0) location and **all of those are wrong**.

Everything here lives under **Settings → Connect**, in the **Observing Site**
panel. Editing the site needs `config.site_optics`, which by default only an
**admin** account holds — operator does not, despite the panel's own
read-only note (*"Read-only — changing the site needs `config.site_optics`
access."*) not spelling that out. See
[remote-access-and-roles.md](remote-access-and-roles.md).

---

## Setting your site manually

The panel opens with a persistent line — **"Active site: `<name>` ·
`<source>`"** — naming the currently *saved* site and where it came from
(`default`, `manual`, `saved preset`, or `hidden` for a low-role user). It
never changes just because you're mid-edit in the form below; only an actual
save moves it.

Below that, the panel has:

- **Site name** — e.g. `My Observatory` (the default).
- **Latitude** — a magnitude field (0–90) plus an **N / S** selector.
- **Longitude** — a magnitude field (0–180) plus an **E / W** selector.
- **Elevation (m)** — metres above sea level (forwarded to the mount; ignored by
  the sky math).

You enter latitude and longitude as a **positive magnitude with a hemisphere**;
AstroDeck converts to its signed internal convention (North-positive,
East-positive) at the boundary. Press **Set site** to persist the form as your
active site — that's the one verb that actually saves your location; the
buttons in the next section only *fill the form* or manage a separate library
of presets, they never save on their own. Saving flips the site off the
"default" flag and updates the "Active site" line above.

While the site is still the default, the panel shows an amber notice:
*"Using default location (0, 0) — sequencing windows and Atlas visibility are
wrong until set."*

---

## Filling it in automatically

Two shortcuts sit next to **Set site**:

### Use my location (browser geolocation)

The **Use my location** button asks your browser for its GPS/location fix and
fills the fields (review, then save). It appears **only in a secure context** —
HTTPS or `localhost`. The LAN UI is plain HTTP, so on a tablet reaching the rig
by its LAN address this button is replaced by the note *"Browser location needs
HTTPS or localhost — enter manually or use mount GPS."*

### Use mount GPS

The **Use mount GPS** button reads the position back from a mount that reports
GPS (many go-to mounts do). If the mount has no GPS it reports that and changes
nothing.

Both shortcuts only *fill the form* — you still review and press **Set site**.

---

## Saved locations

If you observe from more than one place (home, a dark-sky site, a star party),
the **Saved locations** row lets you keep a small library — up to **50** named
sites, entirely separate from the one active site above. It appears only for
`config.site_optics` holders.

- The **Saved locations** dropdown just tracks *which* preset is picked —
  selecting one does **not** touch the form by itself (three review rounds
  flagged the old auto-apply-on-select behaviour as concealing which action
  actually did something). Press **Load selected preset** to actually copy
  its coordinates into the form; you still then need **Set site** to make it
  the active site. After **Load selected preset**, the panel makes the next
  step hard to miss: an inline note reads *"Loaded `<name>` into the form —
  not active yet. Press Set site above to activate it."*, and the **Set
  site** button itself is visually promoted while that note is showing.
  Editing the form away from the loaded values, or pressing **Set site**,
  clears the notice.
- **Save as location preset…** opens an inline name field (prefilled from the
  current **Site name**); press **Save** to add the form's current
  coordinates to the library as a new entry, or **Cancel** to back out. If
  the name already exists (case-insensitively) you're asked to **Overwrite**
  it.
- **Delete** removes the selected saved location (with a confirm).
- If you edit the form after loading a preset, the row shows
  *"Modified — differs from …"* so you know the form no longer matches what's
  saved in the library.

A saved location can also carry a **horizon profile** (a single degrees
number today — its own tree/ridge altitude limit, a property of the *place*,
not the rig). When you **Load selected preset** and then **Set site**, that
stored horizon is carried through and applied **only if** you hold
`config.safety` — otherwise the site's previously-stored horizon is preserved
untouched. There is currently **no UI field to type a new horizon number
directly** (for a preset or otherwise) — the config API is the only way to
set one from scratch; see
[safety-and-automation.md](safety-and-automation.md#altitude-floors-horizon-and-pier-limits).

Saved locations are stored separately from your rig profiles and from the main
config, in their own file that survives updates.

---

## Privacy: who can see your coordinates

Your exact position is treated as sensitive. Only **admins** (holding
`view.site_precise`) ever see the site name, latitude, longitude, or elevation.

For a **viewer or operator** — including any remote user on the relay — those
four fields are **removed** from every status, summary, and config payload
(they are made absent, not blanked). The Site panel shows **"Hidden"** in place
of every coordinate for such users, and the saved-locations routes refuse them.
The `is_default` flag and the horizon limit are kept (neither reveals a
location), so the default-site nudge and the altitude-limit display still work.

### Values computed from your position

Removing those four fields is necessary and is **not** sufficient, and until
2026-08-04 it was all that happened. A great many answers are computed *from*
your coordinates without containing them: the mount's altitude and azimuth, a
target's altitude verdict, the sun's altitude, tonight's dark window, every
visibility and framing answer. Combined with the telescope's RA and Dec — which
is not site data and is not hidden — an altitude places the observer on a circle
around the Earth, and a second reading narrows that circle to a point. An audit
recovered this rig's position to within **2.9 km** using three ordinary requests
a viewer is entitled to make, none of which contained the word "latitude".

Rounding them off does not help: averaging many coarse samples recovers the
precise value, which is exactly how that 2.9 km figure was produced.

So those computed values are governed by a second capability,
**`view.site_derived`**, held by **operators and admins**:

- **Viewers** — including anyone you hand a relay link to — get none of them.
  They still see the image, the status, and what is in the sky; they cannot work
  out where the sky is being observed from.
- **Operators** keep them, because you cannot plan or run a session blind. This
  is the same line already drawn for the radar map: an operator may learn the
  region, never the exact fix.

See [remote-access-and-roles.md](remote-access-and-roles.md).

---

## A note on the longitude sign

Internally AstroDeck stores longitude as **East-positive** (matching ISO 6709
and the browser's geolocation API). The UI's magnitude + **E/W** selector
handles the conversion, so you never deal with signs directly — just pick the
hemisphere. (This convention is load-bearing for transit and the polar-alignment
compass; the UI is the only place you should set it.)
