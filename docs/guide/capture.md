# Capture

The **Capture** view is where you take exposures and watch them live. It works
identically against the simulator and real gear. Imaging controls need
**operator or admin** access (`control.capture`); a viewer sees a read-only
preview with a **Read-only** badge.

---

## Exposure controls

The **Exposure** panel:

- **Exposure (s)** — exposure time, default `2`.
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
- **Stop** — end the loop and abort the current exposure.

You can't capture while polar alignment is running — stop alignment first.

---

## Reading the live preview

The **Live Preview** panel double-buffers so it never flashes between frames.

**Toolbar:**

- Zoom: `−` / `+`, a **Fit** button, and **100%** (1:1 pixels). The current zoom
  shows as a percentage.
- Overlay toggles:
  - **Stars** — a per-star HFR overlay. Each detected star is circled; sharp
    stars are thin solid rings, soft ones thicker, and bad ones dashed. (HFR =
    *half-flux radius*: the radius containing half a star's light — **lower is
    sharper**.)
  - **Clip** — a saturation mask, an amber hatch over pixels at full well.
    Available only for linear (un-stretched) data with a known full well.
  - **Reticle** — a full crosshair + rings.
  - **Center** — a small centre mark.
- **Download ▾** — save the frame as **Stretched PNG**, **Lossless PNG** (when
  available), or **FITS** (raw science, needs `view.media`). FITS saved on a
  NINA host isn't downloadable here and shows as *FITS (on host)*.

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

## Guide-camera preview

A collapsible guide-cam preview polls the live guiding frame, so you can confirm
the guide star without leaving Capture. Full guiding — the RA/Dec graph, RMS, and
dithering — lives on the **Guide** view.

---

## Under the hood (routes)

Capture actions map to: `POST /api/capture`, `/api/capture/loop`,
`/api/capture/stop`; cooler `POST /api/camera/cooler`; dew heater `POST
/api/camera/dew-heater`; filter `POST /api/filterwheel/position`. Preview frames
are served from `/api/preview/{id}...` (PNG/thumb for `view.preview`, FITS for
`view.media`). All the control routes require `control.capture`.

---

## Related

- [Focus](focus.md) — get the stars sharp (and read the V-curve).
- [Sky Atlas](sky-atlas.md) — frame a target before you shoot it.
- [Plan & sequences](plan-and-sequences.md) — automate capture across a night.
