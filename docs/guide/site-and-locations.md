# Site & locations

Your **observing site** — latitude, longitude, elevation — is the foundation for
everything sky-related: altitude and azimuth, transit and meridian times,
twilight and dark windows, Atlas visibility, and (if enabled) weather. Until you
set it, AstroDeck uses a default (0, 0) location and **all of those are wrong**.

Everything here lives under **Settings → Connect**, in the **Observing Site**
panel. Editing the site needs **operator or admin** access
(`config.site_optics`).

---

## Setting your site manually

The panel has:

- **Site name** — e.g. `My Backyard` (the default).
- **Latitude** — a magnitude field (0–90) plus an **N / S** selector.
- **Longitude** — a magnitude field (0–180) plus an **E / W** selector.
- **Elevation (m)** — metres above sea level (forwarded to the mount; ignored by
  the sky math).

You enter latitude and longitude as a **positive magnitude with a hemisphere**;
AstroDeck converts to its signed internal convention (North-positive,
East-positive) at the boundary. Press **Save site**. Saving flips the site off
the "default" flag.

While the site is still the default, the panel shows an amber notice:
*"Using default location (0, 0) — sequencing windows and Atlas visibility are
wrong until set."*

---

## Filling it in automatically

Two shortcuts sit next to **Save site**:

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

Both shortcuts only *fill the form* — you still review and **Save site**.

---

## Saved locations

If you observe from more than one place (home, a dark-sky site, a star party),
the **Saved locations** row lets you keep a small library — up to **50** named
sites. It appears only for `config.site_optics` holders.

- The **Saved locations** dropdown lists your sites by name. Pick one and press
  **Apply** to load it into the form (then Save site to make it active).
- **Save current…** prompts for a name and saves the coordinates currently in
  the form. If the name already exists (case-insensitively) you're asked to
  **Overwrite** it.
- **Delete** removes the selected saved location (with a confirm).
- If you edit the form after applying a saved location, the row shows
  *"Modified — differs from …"* so you know the form no longer matches.

A saved location can also carry a **horizon profile** (its own tree/ridge
altitude limit, a property of the *place*, not the rig). When you apply such a
location and save, its horizon is applied **only if** you hold `config.safety` —
otherwise the stored horizon is preserved untouched. Horizon limits themselves
are edited on the Safety surface; see
[safety-and-automation.md](safety-and-automation.md).

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

This is enforced at one server seam, so there is no surface anywhere that leaks
the coordinates to a low-role user. Weather, which is derived from the site, is
hidden from the same users. See
[remote-access-and-roles.md](remote-access-and-roles.md).

---

## A note on the longitude sign

Internally AstroDeck stores longitude as **East-positive** (matching ISO 6709
and the browser's geolocation API). The UI's magnitude + **E/W** selector
handles the conversion, so you never deal with signs directly — just pick the
hemisphere. (This convention is load-bearing for transit and the polar-alignment
compass; the UI is the only place you should set it.)
