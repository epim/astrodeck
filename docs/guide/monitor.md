# Monitor

The **Monitor** view is the glanceable live dashboard: what's running, how
it's going, and whether anything needs your attention — the screen to leave up
on a second tablet, or to open from the run banner when you're not sure
things are still healthy. It's read-only except for **Pause / Resume /
Abort**, which need `control.mount` — held by **operator and admin** accounts
(operators are meant to run sequences); a **viewer** sees the same dashboard
with the controls in place but **disabled**, next to a lock note naming the
required access.

---

## The header strip

At the top: a state badge (**RUNNING / PAUSED / COMPLETE / ERROR / ABORTED /
IDLE**, plus **NINA is driving** when NINA owns the sequence), a **LIVE**
chip when a frame has landed in the last few seconds, the current target or
plan name, the active filter, and — while running or paused — a large finish-
clock countdown.

**Controls** (Pause/Resume + Abort) appear only while a run is active
(running or paused). They're enabled for an account holding `control.mount`
— **operator and admin**, since a running sequence slews the mount to each
target; a **viewer** sees them in the same position, disabled, with a shared
lock note ("View only — pausing, resuming or aborting this run needs
operator or admin access."). Abort is a hold-to-confirm
control: it shows a persistent
**"HOLD TO ABORT"** hint and fills as you hold, so it can't fire on a stray
tap; it still works over plain HTTP if the WebSocket link is down.

> **A note on who can actually press these.** The server requires
> `control.mount` on every one of `/api/sequence/pause`, `/resume`, and
> `/abort` (a running sequence slews the mount between targets) — the same
> capability that gates mount motion and the **≡ Run Sequence** button on
> [Plan & sequences](plan-and-sequences.md). Operators are meant to run
> sequences (the intended use of the role — the eventual basis for a
> telescope-rental interface), so operator's capability set **includes**
> `control.mount` and gets the same enabled controls as admin (see
> [remote-access-and-roles.md](remote-access-and-roles.md)). Only **viewer**
> sees the controls row in its normal position but **disabled**, with a lock
> note naming the required access. Screen anatomy stays stable across roles;
> permissions change enabled state, not what exists. No role sees an enabled
> button that then fails.

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
own exposure length — but **only while the sequence state is RUNNING**. A
dim, neutral **"last frame N ago"** line is shown any time a frame has
landed, regardless of state; it only escalates while running:

- Past **2×** the exposure time with no new frame **while running**: the
  line turns amber and the sub-frame bar freezes/greys.
- Past **3× exposure + a fixed margin**, still **while running**: the line
  turns red and reads **"CAPTURE STALLED? last frame N ago"**, and the
  device vibrates once (mobile).

Paused, complete, aborted, error, and idle never escalate — the line stays
dim "last frame N ago" with no colour change and no "CAPTURE STALLED?" text.
A stopped or waiting run's frame gap is explained by the state itself, not a
stall, so it no longer gets treated as one. This is advisory, not a verdict
even while running — a slow plate-solve or filter change can look like a
stall for a few seconds. It clears itself the moment the next frame lands
(or the state changes); it does not pause or abort anything on its own.

### Pause reads honestly here too

If the sequence is **paused**, the header badge reads **PAUSED**, not
running, and the stall warning above **cannot fire while paused** — it's
gated to the running state alone, so a paused run's frame gap never gets
mistaken for a stall. There's a brief, honest window right when you press
Pause: pause only takes effect at the next **frame boundary** (see
[plan-and-sequences.md](plan-and-sequences.md#running-a-sequence)), so for
that last in-flight frame the state (and the stall detector) still genuinely
reads **running** — a real stall can still surface in that narrow window,
which is correct, not a bug. The moment the badge actually flips to
**PAUSED**, the warning clears and cannot retrigger until you resume.

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

## Weather (operator + admin)

Operators and admins (`view.weather`, 2026-07-17 decisions wave I2)
additionally see the **Sky Conditions** forecast chart and, once weather is
enabled, the **Radar** map — both documented in full in
[weather.md](weather.md). They render nothing for viewers; the data never
crosses the wire to them.

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
