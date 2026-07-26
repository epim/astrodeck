# Capture

The **Capture** view is where you take exposures and watch them live. It works
identically against the simulator and real gear. Imaging controls need
**operator or admin** access (`control.capture`); a viewer sees a read-only
preview with a **Read-only** badge.

---

## Exposure controls

The **Exposure** panel:

- **Exposure (s)** — exposure time, default `2`. An exposure of `0` or less is
  blocked client-side: the field outlines red and shows *"Exposure must be
  greater than 0s"* inline, and Single/Loop stay disabled until it's fixed —
  it never reaches the server to fail downstream with a misleading error.
- **Gain** — camera gain, default `120` (the label shows the camera's max, e.g.
  `Gain (max 300)`, when reported).
- **Offset** — default `30`.
- **Binning** — `1×1`, `2×2`, or `4×4`.
- **save FITS to library** — off by default. When on, a **Target name** field
  appears (e.g. `M42`); frames are saved under that name.

Three capture buttons:

- **Single** — one exposure. Reads **Exposing…** then **Reading…** while in
  flight.
- **Loop** — expose continuously (**Looping…**). A progress meter shows the
  current exposure and separate download progress.
- **Stop** — end the loop and abort the current exposure. Stop stays a single
  tap on purpose (it's the "make it stop now" control) — it does not use the
  hold-to-confirm affordance that Abort uses elsewhere.

You can't capture while polar alignment is running — stop alignment first.
Single and Loop are also disabled with an inline reason — *"Sequence
running — camera reserved"* or *"Sequence paused — camera reserved"* — the
whole time an autonomous [sequence](plan-and-sequences.md) owns the camera,
**including while it's paused**: a paused run still holds the camera between
frames rather than releasing it back to manual control, so a background
sequence never intercepts a manual exposure mid-flight.

---

## Reading the live preview

The **Live Preview** panel double-buffers so it never flashes between frames.

**Toolbar:**

- Zoom: `−` / `+`, a **Fit** button, and **100%** (100% of the preview image).
  The current zoom shows as a percentage.
- **Magnifier** — real sensor pixels, 1:1, in a small box at the centre of the
  view. See [Pixel-peeping](#pixel-peeping-the-magnifier-and-real-sensor-pixels)
  below.
- Overlay toggles:
  - **Stars** — a per-star HFR overlay. Each detected star is circled; sharp
    stars are thin solid rings, soft ones thicker, and bad ones dashed. (HFR =
    *half-flux radius*: the radius containing half a star's light — **lower is
    sharper**.)
  - **Clip** — a saturation mask, an amber hatch over pixels at full well.
    Available only for linear (un-stretched) data with a known full well.
  - **Reticle** — a full crosshair + rings.
  - **Center** — a small centre mark.
  - **Spikes** — the fitted Bahtinov spike lines, offered only while the
    [Bahtinov focus aid](focus.md#bahtinov-mask-focusing) is armed and fitting.
- **Download ▾** — **Full-res PNG** (the frame at full resolution, at the levels
  currently on screen — see below), **Save first light** (a captioned share
  image), **Stretched PNG**, **Lossless PNG** (when available), or **FITS** (raw
  science, needs `view.media`). FITS saved on a NINA host isn't downloadable
  here and shows as *FITS (on host)*. Anything this frame can't produce is shown
  dimmed with the reason rather than hidden or left to fail.

**Stretch histogram.** Below the stage, an interactive histogram controls the
display stretch (**MTF** — a midtone transfer function). Drag the black / mid /
white handles, or use **Auto** to track each frame automatically, plus
**Brightness** (and **Contrast** for pre-stretched NINA frames). A **CLIPPED**
chip appears when the frame reaches full well. Frames already stretched by NINA
are display-only ("Display levels (8-bit)") and say so.

**Frame stats** show `min`, `median`, `mean`, `max`, `σ`, then **HFR** (in
pixels and arcseconds) and the star count. A **scale bar** shows angular size
when the pixel scale is known.

**Filmstrip.** A strip of recent frames (server thumbnails) with an HFR glyph
per frame (● good, ▲ fair, ■ poor, · unknown) and a **LIVE** badge on the
current one — quick visual history of how focus/quality is trending.

**Star signal (SNR).** When your camera's photometry numbers are known — the
sensor gain and the read noise, in the **Camera photometry** row at the bottom
of the Exposure panel (see also [Sensor gain](#sensor-gain)) — a chip over the
preview reads *"Star signal strong — SNR ~38 in this photo"*, with a reminder
that four times as many photos is about twice as good. It's a **per-sub**
estimate for a typical star in **this** frame, never the stacked result, and
it's built from measured star flux, so treat it as an estimate rather than
photometry. Without those numbers, or without a trustworthy star measurement,
no chip appears at all — better nothing than a number built from noise.

### Pixel-peeping: the Magnifier and real sensor pixels

The preview you see is a display-sized image, so zooming past its native size
just enlarges pixels — it can't show you detail that isn't there. Two things fix
that, and they only work on **linear** frames (a frame NINA already stretched has
no linear data to go back to; the controls dim and say so):

- **Zoom in far enough** and AstroDeck quietly fetches the visible region at
  **real sensor resolution** and fades it in over the enlargement. There's no
  control for this and nothing to turn on — zoom, and what you see becomes true
  pixels. If that region can't be fetched (only the newest frame or two keep
  their linear data), the enlarged view simply stays; you never get an error
  banner or a broken tile.
- **Magnifier** shows a 1:1 box at the centre of the view whatever the zoom —
  the honest focus and noise check — with a crosshair on the exact centre and
  the sensor coordinates underneath (tap them to copy). On a narrow phone stage
  there isn't room for a usable box, and the slot then says the magnifier is
  hidden rather than showing you a useless peephole.

With **Clip** on, the saturation mask sharpens the same way: the amber hatch
frame always tells you the frame contains clipped pixels, and once real sensor
pixels are in hand the individual saturated pixels inside that region are
painted, so you can tell a blown star core from a hot column. Outside that
region the frame marker still stands — nothing implies the rest is clean.

**Full-res PNG** (in **Download ▾**) is the same idea for exporting: the frame
at its full native resolution, baked at the levels you have on screen, rather
than the display-sized preview. It's honest about how exact that is — in
**Auto** stretch with brightness untouched it's the image exactly as you see it;
with a manual stretch it says it's a very close match rather than claiming a
pixel-identity it can't deliver. Like the crops, it needs the frame's linear
data, so it's offered on recent frames and dims with the reason on a
NINA-stretched one.

---

## Cooler

The **Cooler** panel appears when the camera supports cooling:

- A **cooling on / off** indicator with the current power %, plus an **at
  target** chip when the sensor has stabilised.
- Live **sensor** and **target** temperatures.
- **Target °C** field (default `-10`), a **Cool** button to start cooling to it,
  and **Warm** to turn the cooler off.

If the camera has a **dew heater**, a **dew heater** slider (0–100%) sets its
power.

---

## Filter wheel

When a filter wheel is connected, the **Filter Wheel** panel shows one button per
filter name; the active filter is highlighted. Tap a filter to move the wheel to
it.

---

## Sensor gain

The **Sensor gain** panel shows **e-/ADU** — how many electrons one count in the
file is worth — and, in words, where the number came from: *from driver*,
*measured at gain N*, or *not known yet — measure it below*. AstroDeck uses it
for the noise and SNR readouts. Most drivers report it, and then there is
nothing to do here.

If yours reports nothing, open **Advanced** and press **Measure gain (e-/ADU) at
gain N** — it takes a few flat and dark frames and works the value out for you,
for the gain currently set in the Exposure panel. Point the scope at an evenly
lit surface first; it takes about a minute, and it remembers a value per gain
setting. A gain reported by the driver always overrides a measured one. The
button is dimmed with the reason while a sequence, a capture loop, polar
alignment or another measurement owns the camera.

---

## Recording where each photo points (plate solving into the file)

**Settings → Connect → Record where each photo points** is off by default. Turn
it on and, after each light is saved, AstroDeck plate-solves it and writes the
sky position (its **WCS**) into the file, so stacking software can line your
photos up without working it out again. It costs a little time per photo, which
is why it's off by default.

It never slows capture down: the solve happens in the background, one photo at a
time, after the frame is already saved and on screen. With very short exposures
it can fall behind — the oldest untagged photos are then skipped and simply save
without a position. Three things it deliberately doesn't do:

- Only **light** frames are solved. Darks, bias and flats have no stars in them,
  so they are never queued — shooting a dark library won't start a solve.
- Photos your imaging backend (NINA) saved on its own machine are never tagged —
  the file isn't on this box to rewrite.
- A solve that fails writes nothing. You never get an invented position.

Under **Advanced**: the **solver** (*Auto*, which picks the best installed
solver, or *ASTAP*), **downsample** (higher is faster and less precise; 2× is a
good trade on a Pi), and **only tag frames with enough stars**, which skips the
solve on cloud or trailed frames that would fail anyway (0 turns it off).
Changing any of this needs `config.site_optics` — **admin** — and the panel is
read-only, with the reason stated, for anyone else.

---

## Guide-camera preview

A collapsible guide-cam preview polls the live guiding frame, so you can confirm
the guide star without leaving Capture. Full guiding — the RA/Dec graph, RMS, and
dithering — lives on the **Guide** view.

---

## Under the hood (routes)

Capture actions map to: `POST /api/capture`, `/api/capture/loop`,
`/api/capture/stop`; cooler `POST /api/camera/cooler`; dew heater `POST
/api/camera/dew-heater`; filter `POST /api/filterwheel/position`; the gain
measurement `POST /api/camera/egain/learn`. Preview frames are served from
`/api/preview/{id}...` (PNG/thumb for `view.preview`, FITS for `view.media`);
the pixel-peep crops and the full-res export are `/api/preview/{id}/crop` and
`/render.png` (`view.preview`), and both exist only while that frame's linear
data is still held. Per-frame WCS stamping is configuration rather than an
action: `POST /api/config/wcs` (`config.site_optics`) writes the master toggle
and the advanced knobs, and the solve then runs itself after each saved light.
All the control routes require `control.capture`.

---

## Related

- [Focus](focus.md) — get the stars sharp (and read the V-curve).
- [Sky Atlas](sky-atlas.md) — frame a target before you shoot it.
- [Plan & sequences](plan-and-sequences.md) — automate capture across a night.
