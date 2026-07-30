# First-light QA — 2026-07-30

Feedback from the first real imaging session on 0.2.18, plus what I could
establish from the box while it was running. Ordered by whether it stops you
working, not by how hard it is to fix.

Everything under "Established" was measured on astrotown against real frames.
Everything under "Not established" is honest about what I could not determine —
I got two diagnoses wrong tonight by reasoning ahead of the evidence, and both
are recorded below so the same mistakes are not repeated.

---

## Established

### The sky was fine. Twice I said otherwise; twice I was wrong.

The 00:07 light frame has **200 real stars, median HFR 4.57 px**, and the cloud
detector scores it *"clear (200 bright stars, 11× noise)"*. Nothing about the
night was the problem.

- **Wrong call #1:** I read `detect_stars(...)[0].hfr` as the brightest star and
  got 0.96 px, and concluded the detections were hot pixels. `detect_stars`
  orders by **peak**, not flux, and a hot pixel leads. `stars.py` documents this
  and `livestack.brightest_centroid` exists specifically to work around it — I
  had read that code the same day.
- **Wrong call #2:** I read `focal_length_mm = 530` from a Python session that
  never set `ASTRODECK_CONFIG_DIR`, so it loaded model defaults rather than the
  real config, and I announced that the deploy had reset it. **It had not.** The
  value has been 800 throughout. (I wrote 800 back over 800; config version went
  91 → 92 with no setting changed.)

Both errors share one cause: stating a conclusion before running the check that
would have falsified it.

### The Atlas has no image source at all

`survey.online_fetch = false` (correct default — offline first) **and** the
bundled HiPS pack is absent from the release: `build_release.py` treats
`catalog/_bundled_pack` as optional and warns-and-omits when the build machine
does not have it. Mine did not, so 0.2.18 shipped with neither source.

- **Now:** turn on online fetch in Settings → Sky Atlas.
- **Fix:** either ship the pack in the release, or make a release that omits it
  fail loudly instead of warning. A silent warning during a build nobody watches
  produced an Atlas with no imagery.

### Plate solving fails inside ASTAP, not in AstroDeck

Run directly against the saved frame, ASTAP returns `PLTSOLVD=F` — with a
full-sky search (`-r 180`) and no downsampling (`-z 0`). The FOV hint of 1.125°
is **correct**: ASTAP's `-fov` is field *height*, and 1.125° is exactly the
height at 800 mm with this sensor. AstroDeck is passing the right numbers.

Only one star-database family is installed: `d80_*.1476`, 1476 files, 1.27 GB.
ASTAP emits no diagnostic beyond the failure. The leading hypothesis is that the
installed database does not suit a 1.68° × 1.125° field — but that is a
hypothesis, and it is cheap to test by installing H18 alongside.

### Autofocus finds 0 stars on frames that have 200

The log shows `native autofocus: only 0 stars at <position>` at every step of
the sweep, ending in `not_enough_spread`. The imaging frames from minutes either
side have 200 stars.

Ruled out: **binning is not the cause.** Star counts on the real frame binned
2×, 3× and 4× are 200, 200, 200.

**Not established:** what autofocus actually captured. The log records neither
the exposure/gain it used nor any statistic of the frame it got, so from the
outside there is no way to tell a bad exposure from a bad frame from a bad
camera selection. That gap is the first thing to fix — see below.

### The autofocus UI never learns the run ended

Timeline: `23:13:52 native autofocus failed: not_enough_spread` — the run was
**over**. The UI still read "autofocusing" until `23:52:30 autofocus cancelled`,
39 minutes later, when Halt was finally pressed.

So the run ends, the failure is published, and the UI keeps spinning. Halt
"doing nothing" is the same bug seen from the other side: there was nothing left
to halt.

---

## Fix first

1. **Log what autofocus captured.** On a 0-star frame, record exposure, gain,
   binning, which camera, and the frame's median/max. Tonight's failure is
   undiagnosable without it, and it will happen again.
2. **Clear the autofocus UI state on failure and on completion**, not only on
   cancel. A terminal event the UI ignores is worse than no event.
3. **Halt must be honest.** If there is no run to halt, say so instead of
   accepting the press silently.
4. **Ship the survey pack, or fail the release build.** A warning nobody reads
   shipped an Atlas with no sky.

---

## UX — the focus screen

The controls and the thing they control are not on screen together, which makes
the whole screen unusable one-handed on a phone.

- **Step size becomes a speed dial** — 1 / 10 / 100 / 1000 — replacing the
  eight-button grid.
- **Speed dial, `+`/`−`, and the live preview must be visible at once.** This is
  the whole complaint: adjust, look, adjust. Today it is scroll down, tap,
  scroll up, look.
- **Autofocus moves off the bottom of the page**, and the modal shrinks. The
  full-width "Autofocus" button does not need to be full width.
- **Advanced autofocus options collapse to a gear icon** on the autofocus
  button's own line.
- **Drop the second "Run autofocus" button** an inch below "Focus my scope".

## UX — the capture screen

- **Annotations become a speed dial** with stars / clip / reticle / etc.
  multi-selectable from it.
- **Presets open from a "Presets" button** into a picker, rather than occupying
  the screen permanently.
- **Camera photometry belongs behind an Advanced disclosure.** It is the
  clearest case on the screen for progressive disclosure.
- **The filter wheel wants to be a picker or a dial** — an engine-telegraph
  style control — not a row of buttons.

## UX — the Atlas

- **Equipment-spec fields are still the wrong size.** A 12-character box for
  pixel size, sensor dimensions, or focal length. (These were resized once
  before; whatever was fixed did not cover this screen.)
- **The flow is inside out.** Target selector at the top, equipment specs in the
  middle, "go to this target" at the bottom. The two things you actually do are
  separated by configuration you set once.
- **All equipment selection moves behind a gear icon.**

## UX — equipment

- **"Simulator rig" should not be a first-class citizen** in the device list.
- **The live preview does not fit its box** — intermittently, a large black band
  appears beneath the image. Intermittent, which suggests it depends on the
  frame's aspect ratio or on a measurement taken before layout settles.

## UX — drivers

- **A local USB driver must offer every role it discovered.** The `zwo-usb`
  backend serves both the EAF focuser and the CAA rotator, and its discovery
  returns both, verified. But adding it creates one driver row named after the
  first device found ("ZWO EAF (USB)"), leaving the rotator no way to surface —
  so the CAA looks unsupported natively and the user is pushed to ASCOM.
  Also: do not name a multi-device driver after one of its devices.
