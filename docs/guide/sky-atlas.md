# Sky Atlas

The **Atlas** view is your framing and planning map of the sky: search for a
target, overlay your camera's field, plan a mosaic, check tonight's visibility,
and send targets straight to your plan. It renders survey imagery as a smoothly
pan/zoomable tile map and works **offline** at the scope.

---

## Searching for a target

Use the search box (placeholder *"Search catalog — frame a target"*, or *"Search
catalog — e.g. M 31"* in the empty state). Type an object id, name, or type; the
Atlas queries the catalog and shows the top matches. Each result lists the
catalog id, the common name, and the object's **current altitude** (colour-coded
— green when well up, amber when low). Pick one to frame it.

The catalog is a curated list of Messier objects plus bright NGC/IC favourites
(and a couple of Sh2 objects) — not an exhaustive survey catalog. Matching is
case-insensitive across id, name, and object type, sorted brightest-first.

**While you type**, the dropdown always shows something rather than going
silent: *"Searching…"* while the debounced query is in flight, the matching
rows once they land, or — if nothing matches — an explicit *"No matches for
'…'."* with a one-line hint. That hint is context-aware: a query that looks
like the Sun, Moon, or a planet name gets *"Planets aren't supported yet —
use manual coordinates or free-roam."*; anything else gets *"Catalog covers
Messier/NGC/IC deep-sky objects only — try a name or ID (e.g. M31)."*

**No planet ephemerides.** This isn't a bug to work around with a better
search term — the catalog genuinely has no Sun/Moon/planet positions today.
If you need to frame one, use **Free-roam the sky** to point manually and
dial in coordinates yourself; there's no scripted workaround beyond that.

If you don't have a target in mind, use **Free-roam the sky** to just explore.

---

## The survey tile map

The sky is drawn from real survey imagery. Behind the scenes the browser fetches
raw **HiPS** tiles through the server and warps them through the exact projection,
upsampling from parent tiles so the view never blanks while you drag.

- **Drag** pans the sky on both axes — it's "grab the sky": drag right and the
  sky follows your hand (map-style). Mouse-wheel or `+`/`-` zooms; arrow keys
  nudge; `[` and `]` rotate the framing.
- **Survey** picker offers **DSS2 color**, **DSS2 red (mono)**, **2MASS color**,
  and **Schematic (offline)**. DSS2 red and 2MASS are marked *(online only)* and
  are disabled unless online fetch is on (see below).
- **FOV (zoom)**, **Fit object**, and a **Match camera** toggle set how much sky
  you see. **Survey brightness** dims the imagery (it also respects night mode).

Where WebGL is available the Atlas uses the tile engine; where it isn't (or in
Schematic mode) it falls back to a classic whole-field `<img>` cutout — same
framing, just fetched differently. If the survey service is briefly unreachable
it keeps the last good image and retries automatically, showing a small
*"Survey unreachable — showing the last image"* banner.

---

## Framing your camera's field

The Atlas overlays your **camera's field of view** on the sky as a rectangle,
scaled from your optics. Set them in the header: **Focal length** (mm), **Pixel
size** (µm), **Sensor W/H** (px). A connected camera fills blanks automatically
(a *from camera* chip marks those), and **Calibrate from last solve**
back-computes focal length from your last plate solve.

- Drag the FOV box, or grab the rotation handle on its top edge to set the
  **camera angle** (position angle). A manual **Camera angle** stepper (0–360°,
  ±5° buttons) sets it precisely.
- Below the canvas a verdict tells you whether the object *fills N% of the frame*
  or *is N× your frame — needs a mosaic*.
- **Position-angle honesty:** if a rotator is connected, the Atlas notes the
  camera *"will rotate to PA N° automatically on slew"*; with no rotator it
  reminds you *"set your camera to PA N° before the run"* — because AstroDeck
  can't rotate what it can't drive.

### Mosaics

The **Mosaic** panel builds a multi-panel mosaic: **Rows** and **Cols** (1–10
each) and **Overlap** (0–50%). It shows the total field and panel count. The
mosaic geometry is computed server-side so the panels are canonical.

### Sending to the plan

The **Add target to Plan** button (or **Send N panels to Plan** for a mosaic)
adds the framed target(s) — with your position angle — to the **Plan** view. If
the target sits below tonight's altitude limit you're warned first (*"Below
tonight's limit → Add anyway"*), but it's never blocked.

---

## "What can I image tonight?"

If you don't have a target in mind, **Tonight** answers the beginner's question
directly. It's its own destination — in the left rail on a tablet or desktop,
and under **More → Tonight** on a phone — and it needs no equipment at all: it's
pure sky maths for your saved site, so it works with the rig switched off.
(That's why the *"Equipment not connected"* screen offers a **See what's up
tonight** shortcut.) The same list also appears on the Atlas when you haven't
framed anything yet.

The list is ranked by how high each object climbs tonight — higher means less
air to shoot through — and each row carries the peak altitude (`↑42°`, or `low`
when it never clears your horizon limit) and a difficulty rating: **● Easy**,
**◐ Moderate**, **○ Hard**, from how bright the object is spread over its size.
The filter starts on **Beginner**, which shows Easy and Moderate only; switch it
to **All** to see everything. Tap a row and the target is framed, exactly as if
you'd searched for it.

If your site is still the default one you're warned to set it in Settings — the
whole ranking depends on where you are. While a sequence is running the view
also shows what you're imaging right now at the top, so opening Tonight mid-run
never looks like nothing is happening.

---

## Tonight's visibility for the framed target

The **Tonight** panel on the Atlas computes tonight's visibility for the framed
target:

- An **altitude curve** for the target across the night, with the **moon's**
  altitude, an astronomical-**dark** band, and a **NOW** marker.
- **Transit** — when (and how high) the target crosses the meridian (its best
  moment); if that happens in daylight it reports the highest-while-dark peak.
- **Best window** — an advisory bracket of the best dark hours (advisory only —
  runs start immediately today).
- **Above limit** — hours the target spends above your altitude limit.
- **Moon** phase/illumination and **Moon sep** (angular separation), with a hint
  about expected gradient/glow when the moon is close.

If a target never clears the limit it says so (*"Does not rise above N° tonight…
it will sit low"*) but still lets you frame and send it.

---

## Offline at the scope

You don't need internet under the stars. Two settings (under **Settings →
Connect → Sky Atlas**) control online use:

- **Online survey fetch (CDS)** — **off by default**. When off, the Atlas is
  offline-first: the local pack is the only source. When on, small fields pull
  full-resolution imagery from CDS and the pack remains the fallback.
- **Download offline sky pack (~250 MB)** — downloads the DSS2-color survey pack
  to disk (once present, the button becomes **Update offline sky pack**, with a
  **Delete pack** option). The download is resumable — re-running it retries any
  tiles that failed.

You can also fetch the pack from the command line:

```
cd server && python -m astrodeck.catalog.survey_pack fetch
```

(The default depth is order 4, about 250 MB; the pack lands under the capture
directory at `_survey_pack/dss2color`.) The pack stays the automatic fallback
whenever the online service is unreachable. DSS2 imagery © AAO/STScI, served
from CDS/ESA HiPS mirrors.

---

## Related

- [Capture](capture.md) · [Plan & sequences](plan-and-sequences.md) — where
  framed targets end up.
- [Equipment & profiles](equipment-and-profiles.md) — the rotator that makes PA
  automatic.
