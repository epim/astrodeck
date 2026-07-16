# Weather

AstroDeck can watch the sky for you: it fetches a cloud forecast for your
observing site, warns you when a cloudy night is coming, holds automatic
resume when it is too cloudy to bother, and draws a live radar/satellite map
with your telescope's line of sight projected onto it.

Weather is **off by default** and does nothing until you turn it on. When
disabled, the server makes **zero** outbound weather requests.

> **Who can see weather?** Everything on this page is visible only to
> **admins** (principals holding `view.site_precise`). Operators and viewers —
> including remote users on the relay — never see the forecast, the Sky
> Conditions panel, or the radar map, and the weather data never crosses the
> wire to them. See [remote-access-and-roles.md](remote-access-and-roles.md).

---

## Turning it on

Go to **Settings → Connect**. The **Weather** panel appears there (only for
admins). It has:

- **WEATHER ENABLED** — the master toggle. Off means no forecasts are fetched.
- **Cloud threshold (%)** — default `50`. Cloud cover at or above this counts
  as "too cloudy". Range 0–100.
- **Sustained for (min)** — default `30`. A breach only counts once cloud
  stays over the threshold this long (range 15–240). This one threshold+sustain
  pair drives **both** the night warning and the auto-resume hold.
- **Astrospheric API key** — optional (see below). Write-only.

If you enable weather while the site is still the default (0, 0), the panel
shows an inline warning — *"Requires a valid observing site to fetch
forecasts."* — so you must [set your site](site-and-locations.md) first; on
the default location weather fetches nothing even if enabled.

Any unsaved edit (including just flipping the toggle) shows an **"Unsaved
changes — Save to apply"** note next to the Save button, so a change you make
and then navigate away from is never silently discarded without at least a
visible hint. Press **Save weather settings** to apply.

The forecast source is **Open-Meteo**, polled every 15 minutes (about 96 calls
per day). Data older than 45 minutes is shown as **stale**.

---

## The Sky Conditions panel

On the [Monitor](monitor.md) view, admins see a **Sky Conditions** panel: a
24-hour cloud-cover forecast chart. It has four states, and only ever shows
one of them:

- **disabled** — weather is off: *"Weather is off — enable it in
  Settings → Connect."*
- **waiting for first forecast** — weather was just enabled and the server's
  poller hasn't landed its first fetch yet (up to ~60s): *"waiting for first
  forecast…"* — so turning weather on gives you immediate feedback instead of
  the panel looking broken for a minute.
- **no data in the current window** — a forecast exists but none of it falls
  in the next 24 hours (e.g. a very stale fetch): *"no forecast data for the
  current window"*.
- **live** — the chart itself, described below.

The live chart:

- Four lines are drawn — **total**, **low**, **mid**, and **high** cloud — each
  distinguished by line style and dash pattern (not by colour alone, so it
  stays readable in night mode). End-of-line labels are collision-consolidated
  when two or more series land on the same value (e.g. an all-zero night
  renders one `total+mid+high 0%` label instead of four overlapping ones).
- A dashed horizontal rule marks your **cloud threshold**, labeled with the
  actual hold policy it's drawing — *"hold ≥{threshold}% for {sustain}m"* —
  so the rule and the policy driving it can never drift apart on screen.
  Axis, series, and threshold-label text were all bumped up a size (the
  original 8–9px was reported unreadable; the threshold label got a second,
  larger bump after a follow-up pass still called it out).
- Shaded bands mark tonight's **dark window** (astronomical night) and any
  **sustained breach** where cloud is forecast to exceed the threshold long
  enough to matter.
- A chip row shows the **source** — *"Open-Meteo"*, or *"Open-Meteo +
  Astrospheric"* once that's configured — and how long ago the forecast was
  fetched (e.g. *"updated 12 min ago"*, turning amber and prefixed **STALE**
  past 45 minutes), plus, if Astrospheric is configured, current **seeing**,
  **transparency**, and credits used today.

### ignore weather tonight

The panel has an **ignore weather tonight** toggle. Turning it on tells the
auto-resume gate to proceed despite clouds **for tonight only** — it is keyed
to tonight's dusk and expires automatically when the next night begins. It is
a runtime override, not saved config. Turning it on does **not** dismiss a
warning already shown; it records "proceed anyway". Changing it needs
**operator or admin** access (`control.capture`).

---

## The high-cloud night warning

On each successful forecast refresh, AstroDeck scans **tonight's dusk-to-dawn
window** for a sustained breach. The first time it finds one, it:

- raises a warning that rides in the weather data and stays visible until dawn,
  surfaced as a chip: *"high cloud tonight — auto-resume will hold unless
  overridden"*; and
- logs one warning that also reaches your configured **ntfy / webhook /
  Telegram** alert sinks (see [safety-and-automation.md](safety-and-automation.md)).

The warning fires **once per night**. The alert text contains only times and
percentages (peak cloud %, dominant layer, the affected time range) — **never
your coordinates**.

---

## Auto-resume weather veto (fail-open)

If you [arm a session for auto-resume at dusk](sessions-multi-night.md), the
resume service asks the weather service for a **veto** before it touches any
device. The veto triggers when a sustained breach (using the same threshold and
sustain minutes) is forecast **within the next hour**. When vetoed, resume logs
the reason and retries in 10 minutes rather than starting the rig into clouds.

This gate is deliberately **fail-open**: weather is advisory only. If the
forecast is missing or stale, or weather is disabled, or you enabled *ignore
weather tonight*, the veto does not fire. The **safety monitor** remains the
hard guard — weather never substitutes for it. (This is why arming auto-resume
with no safety monitor still warns you; see the sessions guide.)

---

## The radar map

On the [Monitor](monitor.md) view, admins also see a **Radar** panel — but
only once weather is **enabled** (it stays hidden while weather is off,
unlike Sky Conditions above, which shows its own disabled state instead): a
small slippy radar/satellite map centred on your site.

- Two layers, chosen with the **Radar** and **IR satellite** buttons. **Radar**
  is roughly 5 minutes delayed.
- **refresh** re-fetches tiles; **recenter** returns the map to your site.
- Drag to pan, mouse-wheel to zoom, or use the visible **−** / **+** zoom
  stepper (44px targets, same range the wheel uses) — so zooming isn't
  mouse-only. Arrow keys nudge the pan. The tile layer is dimmed in night
  mode just like sky-survey imagery.
- **Per-layer tile health.** A broken tile hides itself rather than showing a
  broken-image glyph, but a badge in the corner still tells you the layer
  failed: **"loading…"** while the current viewport's tiles haven't painted
  yet, or **"tiles unavailable"** once every tile in view has errored. The
  point is that a blank map must never silently read as "no clouds" — if
  imagery failed to load, the badge says so.
- A chip shows the mount's current **Az / Alt**, or "no mount" when the
  telescope isn't reporting a position.

Imagery comes from the **Iowa Environmental Mesonet (IEM)**. It is fetched
**through the AstroDeck server** (the `/api/weather/tile/...` proxy) — your
browser never contacts IEM directly. Tiles are cached briefly on the server;
failures are never cached.

### The scope pierce-point overlay — what it means physically

Your telescope points along a line of sight into the sky. Clouds sit in decks
at roughly fixed heights (low, mid, high). The overlay projects **where your
line of sight crosses each cloud deck** onto the map:

- A scope glyph marks your site; a shaded **wedge** shows the azimuth the mount
  is pointing (the map is north-up).
- A dashed ray runs out from the site to the **farthest** cloud-deck crossing.
- The **high deck** crossing is drawn as a labelled crosshair ("high cloud")
  because that is usually the pixel that decides whether your sub-exposures
  survive; the low and mid crossings are small dots along the same ray.

The physical intuition: the lower your target sits over the horizon, the longer
that ray, because the geometry is `deck_height / tan(altitude)` — a low target
looks through much more sideways sky. Point near the zenith and the crossings
sit almost on top of your site; point low and they march far downrange, so the
weather that matters is the radar pixel out there, not the one overhead.
Crossings for altitudes below ~3° or beyond ~150 km are hidden.

---

## Astrospheric (optional seeing / transparency)

**Astrospheric** adds hourly **seeing** and **transparency** forecasts. It is
**optional**, **North-America-only**, and requires **your own Astrospheric Pro
membership API key**.

Enter the key in the **Astrospheric API key** field in the Weather panel. The
field is **write-only**: the key is a stored secret, scrubbed from every config
payload, and the UI only ever learns whether it is *set* or *not set*. It is
never logged and rides only in the request body to Astrospheric. To remove it,
press **Clear key**.

Astrospheric is polled every **6 hours** and **never faster**. Two reasons,
both from the provider: each call costs **5 API credits** against a **100/day**
Pro budget, and their forecast model itself only updates every 6 hours — so a
faster poll would just burn credits for no new data. Data older than 12 hours
(two model cycles) is shown as stale.

---

## Related

- [Monitor](monitor.md) — where the Sky Conditions and Radar panels live.
- [Sessions & multi-night imaging](sessions-multi-night.md) — where the
  auto-resume veto and the weather-override toggles live.
- [Safety & automation](safety-and-automation.md) — the safety monitor, the
  hard guard weather never replaces.
- [Remote access & roles](remote-access-and-roles.md) — why non-admins never
  see any of this.
