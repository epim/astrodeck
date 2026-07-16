# Monitor

The **Monitor** view is the glanceable live dashboard: what's running, how
it's going, and whether anything needs your attention — the screen to leave up
on a second tablet, or to open from the run banner when you're not sure
things are still healthy. It's read-only except for **Pause / Resume /
Abort**; a viewer sees the whole dashboard but not those controls.

---

## The header strip

At the top: a state badge (**RUNNING / PAUSED / COMPLETE / ERROR / ABORTED /
IDLE**, plus **NINA is driving** when NINA owns the sequence), a **LIVE**
chip when a frame has landed in the last few seconds, the current target or
plan name, the active filter, and — while running or paused — a large finish-
clock countdown.

**Controls** (Pause/Resume + Abort) appear only while a run is active
(running or paused) and only for an **operator or admin** account. Abort is a
hold-to-confirm control: it shows a persistent **"HOLD TO ABORT"** hint and
fills as you hold, so it can't fire on a stray tap; it still works over plain
HTTP if the WebSocket link is down.

> **A note on who can actually press these.** The button row here is shown to
> anyone holding `control.capture` (viewer excluded, operator included). The
> server, however, requires `control.mount` on every one of
> `/api/sequence/pause`, `/resume`, and `/abort` — and only **admin** holds
> that capability (see
> [remote-access-and-roles.md](remote-access-and-roles.md)). In today's build
> an operator account can see and press these buttons but gets a permission
> error back — the same is true of the **≡ Run Sequence** button on
> [Plan & sequences](plan-and-sequences.md). Treat sequence run-control as
> **admin-only** in practice until this is reconciled.

---

## Progress

While idle, the Progress panel invites you to plan a session. Once a
sequence has run, it shows:

- A **progress bar** (frames done / total, with a percent).
- A **sub-frame bar** for the exposure currently in flight — client-side
  interpolated between frame-boundary updates from the engine, so it advances
  smoothly between them; it **freezes and greys out** the moment a stall is
  suspected (see below) rather than keep animating a guess.
- **elapsed** time, and the plan/step detail line the engine is reporting.
- A **flagged** count when frames were kept but didn't clear a quality gate
  (HFR/cloud check) — flagged frames are never silently discarded.
- An **HFR-trend mini-sparkline** once a few frames have measurable stars —
  an early warning for dew or focus drift.
- On failure, the panel renders inline: **"Sequence failed"** or
  **"Sequence aborted"**, a human-readable reason, and the last few
  error/warning log lines — no drawer to dig through.

### Stall detection — what you actually see

The engine only reports progress at **frame boundaries**, so the Monitor
infers a stall from how long the *current* frame has been running versus its
own exposure length:

- Past **2×** the exposure time with no new frame: the stall line turns
  amber and the sub-frame bar freezes/greys.
- Past **3× exposure + a fixed margin**: the line turns red and reads
  **"CAPTURE STALLED? last frame N ago"**, and the device vibrates once
  (mobile).

This is advisory, not a verdict — a slow plate-solve or filter change can
look like a stall for a few seconds. It clears itself the moment the next
frame lands; it does not pause or abort anything on its own.

### Pause reads honestly here too

If the sequence is **paused**, the header badge reads **PAUSED**, not
running — and because pause only takes effect at the next **frame boundary**
(see [plan-and-sequences.md](plan-and-sequences.md#running-a-sequence)), a
paused run can still show a fresh "last frame N ago" tick right after you hit
Pause, and can still trip the stall warning above if that last in-flight
frame is itself running long. A paused run is not a stuck run — but the
Monitor won't hide a genuine stall just because you asked it to pause.

The persistent run banner (header, any view) mirrors the same state: it reads
**SEQUENCE PAUSED** while paused instead of blinking **SEQUENCE RUNNING**
against a Plan card that already knows better.

---

## Countdowns

- **Meridian flip** — a countdown ring once a flip is scheduled, warning
  inside 5 minutes; or a static note (**flip disabled**, **no flip needed
  (fork mount)**, **flip n/a (mount doesn't report)**) when it doesn't apply.
- **Cooling** — shown while the camera is actively cooling toward a target
  (current → target °C), or an **AT °C** confirmation once it settles.

## Last frame, Guiding, Thermal

- **Last frame** — the live thumbnail, dimmed for night viewing with its own
  persisted brightness slider; a stale badge when no fresh frame has arrived.
- **Guiding** — the RA/Dec error sparkline, an RMS verdict, and a **guider
  stale (frozen reading)** note if the guider stopped reporting.
- **Thermal** — sensor/target temperature and the cooler power bar, or
  **no cooler** when the camera doesn't support it.

## Weather (admins only)

Admins (`view.site_precise`) additionally see the **Sky Conditions** forecast
chart and, once weather is enabled, the **Radar** map — both documented in
full in [weather.md](weather.md). They render nothing for operators or
viewers; the data never crosses the wire to them.

---

## Recovering an interrupted run

The Monitor dashboard itself holds no state of its own to recover — it just
starts reading whatever the engine reports the moment you're back. The
actual recovery affordances live one hop over:

- On **[Plan & sequences](plan-and-sequences.md)**, if the engine still has
  an interrupted run on record (`/api/sequence/recoverable`), a **recover**
  banner names it and offers **Resume from frame N**.
- The underlying **[Session](sessions-multi-night.md#what-survives-a-reboot)**
  is never silently lost either way: any session still marked **active** at
  boot gets swept to **dormant**, ready for a manual or auto-resume, and
  progress is always counted from its ledger rather than trusted to memory.

---

## Related

- [Plan & sequences](plan-and-sequences.md) — building and running what
  Monitor watches.
- [Sessions & multi-night imaging](sessions-multi-night.md) — recovery after
  a reboot.
- [Weather](weather.md) — the Sky Conditions and Radar panels shown here to
  admins.
- [Troubleshooting](troubleshooting.md) — a stall/recovery checklist.
