# Plan & sequences

The **Plan** view builds an autonomous imaging run: one or more targets, each
with per-filter exposure steps, plus the automation that ties it together (slew,
centre, autofocus, guide, dither, meridian flip, wind-down).

**Access.** The plan *builder* on screen — targets, steps, automation
toggles, schedule — is not access-gated at all: it lives only in this
browser tab's memory and `localStorage`, so even a viewer can experiment
with it. What *is* gated:

- **Saving, saving-as, importing, or deleting** a plan in the library needs
  `control.capture` — operator and admin both have it; viewer doesn't get
  the Save / Save as… / Import buttons at all (loading and exporting a
  saved plan stay open to everyone, since neither writes to the rig).
- **Starting, pausing, resuming, or aborting a sequence** slews the mount
  between targets and needs `control.mount` — by default that's **admin
  only**; operator holds `control.capture` but not `control.mount`, so the
  **≡ Run Sequence** button (and Monitor's Pause/Resume/Abort) is hidden for
  operator too, replaced by the same passive "View only" note a viewer gets.

See [remote-access-and-roles.md](remote-access-and-roles.md) for the full
matrix.

---

## Building a plan

### Targets

Add a target with the search box (*"+ add target — search catalog"*), or send
one from the [Sky Atlas](sky-atlas.md). **Order by tonight** sorts targets by
visibility. Each target row has toggles:

- **center** — plate-solve and centre after slewing.
- **AF** — autofocus at the start of the target.
- **Cal** — calibration target (darks/bias) — skips slew, focus, and guiding.

A **PA** chip shows the target's camera angle (with *· auto* when a rotator will
set it).

### Steps (per filter)

Each target holds exposure **steps**, one grid row each, with columns: **filter**
(or *no filter*), **exp s**, **gain**, **bin**, and **count**. A default step is
120 s, gain 100, offset 30, bin 1, count 10, Light frames. Add as many steps as
you have filters/exposure combos.

The **Plan** panel (right) names the plan and totals the **frames** and
**integration** time.

---

## Automation

The automation section carries the run-wide behaviour (defaults shown):

- **guide during sequence** (on) · **recover guiding if lost** (on)
- **dither every N frames** (3) · **dither size (pixels)** (3.0)
- **refocus every N frames** (0 = only at target start) · **refocus on temp Δ°C**
  (0 = off) · **apply filter focus offsets** (on)
- **meridian flip (German mount)** (on) · **meridian warn lead (min)** (15)
- **safety monitor gate** (on)
- **cool sensor to °C** (blank = off)
- **flag HFR spikes (× median, 0 = off)**
- **park mount when done** (off) · **warm camera when done** (off)

### The target lifecycle

For each target the engine runs: **slew → (centre) → (autofocus) → start guiding
→ for each step: set filter (+ focus offset) → expose × count**, with dithering,
periodic/thermal refocus, meridian-flip handling, and guiding-loss recovery. It
grades each frame's quality before recording, honours the mount-altitude floor
and the fail-closed safety gate, and on completion parks/warms per your toggles.
Calibration targets skip slew/centre/focus/guiding. Pause, resume, and abort are
safe at frame boundaries, and progress is persisted for resume.

---

## Count modes and quotas (multi-night)

By default a plan runs in **attempts** mode: `count` = frames to shoot. Toggle
**count = accepted frames** and each step's count becomes a quota of *accepted*
frames — rejected frames don't count and the step keeps shooting, across nights,
until the quota is met. Related quality/guard fields:

- **min stars per frame** (0 = off) · **max guide RMS (arcsec)** (0 = off) — the
  accept/reject gates.
- **skip step after N rejects** (10) · **end night after N rejects** (20) — the
  loop guards (accepted-mode only).

If you disable *both* reject guards and leave a non-calibration target with no
stop boundary, the plan can run forever — AstroDeck flags this as "unbounded" and
auto-resume will refuse it. Multi-night accumulation is managed as a **Session**
— see [sessions-multi-night.md](sessions-multi-night.md).

---

## Scheduling (per target)

Each target has a collapsible **schedule** controlling *when* it runs:

- **start** — **Now**, **Dusk**, **Dawn**, or a **Time** (dusk/dawn take a ±
  minute offset).
- **min alt** — a per-target start gate: this target begins only once **it**
  climbs above this altitude (separate from the global mount-altitude safety
  floor).
- **stop** — **None**, **Dawn**, or a **Time**; plus a **max run** cap.
- **if missed** — **wait** (hold for the window to open) or **skip** (move on if
  the window is already missed).

The engine freezes each window at start and runs targets in a visibility-sorted,
skip-ahead order. A per-target altitude **spark** and runtime chips
(*"Waiting — … · starts HH:MM"*, *"low all night"*) show status at a glance.
(Dusk/dawn use the twilight angle from your safety config.)

---

## Running a sequence

Press **≡ Run Sequence** to open the **preflight** modal (it checks readiness).
While running you get **Pause**, **Resume**, and **Abort**. Abort is
hold-to-confirm — it shows a persistent **"HOLD TO ABORT"** hint and fills as
you hold, so a stray tap can't fire it. The
state badge reads **RUNNING / PAUSED / COMPLETE / ERROR / ABORTED**. After a
failure you can **Re-run**, **Resume from frame N**, or view the log. If a run was
interrupted (e.g. power cut), a **recover** banner offers to resume it.

**Pause is honest about when it actually takes effect.** Pause, resume, and
abort are all safe only at **frame boundaries** — mid-exposure, the current
frame finishes (or the current step of the target lifecycle completes)
before the engine actually stops. So right after pressing Pause you can still
briefly see the run behaving as if it's running: the last frame lands, the
header banner switches to **SEQUENCE PAUSED** (it no longer misreports
**SEQUENCE RUNNING** while paused), but a paused run *still counts as owning
the camera* — manual Single/Loop on [Capture](capture.md) stay blocked with
*"Sequence paused — camera reserved"* until you resume or abort. Once the
badge actually reads **PAUSED**, the
[Monitor's stall warning](monitor.md#stall-detection--what-you-actually-see)
is gated off entirely — it only ever escalates while the state is
**running**, so a paused run's frame gap is never mistaken for a stall.

Live progress, ETA, and telemetry — plus the stall warning and recovery
banner in full — are on the [Monitor](monitor.md) view.

---

## The Plan panel — identity, library, save/import/export

The Plan panel and the plan library used to be two separate boxes; they're
now **one panel** (titled **Plan**) so "which plan am I editing, is it
saved, and what else is saved?" reads as one system:

- **Header identity.** A **Plan name** field, with a saved/unsaved cue next
  to it that always tells the truth: **"Unsaved changes"** (warn-coloured,
  for operator/admin — the editor has diverged since the last save/load),
  **"Saved"** (matches the loaded library plan), or **"Not saved yet"** (a
  fresh local draft never written to the library). A viewer sees the same
  cue, just dimmed, since they have no Save button to act on it with.
- **Save / Save as… / Import** — a row under the name field, visible only
  to `control.capture` holders (operator, admin):
  - **Save** upserts the *loaded* plan in place (no new copy). If nothing is
    loaded, it mints a new one.
  - **Save as…** opens an inline name field (prefilled from the current
    plan name); **Save copy** forks a new, separately-saved plan without
    touching what's currently loaded, **Cancel** backs out. A name collision
    prompts *"A plan named '…' already exists — Save anyway as a second
    copy with the same name?"*.
  - **Import** loads a plan JSON from disk (a plan exported by a *newer*
    AstroDeck is rejected).
- **frames** / **integration** — the plan's headline totals, same numbers as
  the builder above.
- **Saved plans (N)** — a collapsible list (up to 500 plans) of two-line
  rows so a name is never truncated: the **name** on its own full-width
  line (wraps to two lines rather than ellipsizing), then a metadata chip
  (*"6t · 300f · 480m"*) plus **load** / **export** / **delete** on the line
  below. The currently-loaded row is highlighted and tagged **loaded**
  (**loaded · edited** once you've changed anything). **load** and
  **export** are open to every role — neither writes to the rig; **delete**
  needs `control.capture`, so it's hidden for a viewer.

---

## Routes

Plans: `GET/POST /api/plans`, `GET /api/plans/{id}`, `DELETE /api/plans/{id}`,
`GET /api/plans/{id}/export`, `POST /api/plans/import`. Sequence:
`POST /api/sequence/start`, `/pause`, `/resume`, `/abort`, `/recover`,
`GET /api/sequence/state`, `GET`/`POST /api/sequence/preflight`. Scheduling is
embedded per target in the plan (there is no separate schedule route);
visibility comes from `GET /api/visibility`. Plan-library writes (save/
delete/import) require `control.capture`; sequence control
(start/pause/resume/abort/recover) requires `control.mount` — see the
**Access** note at the top of this page for who actually holds each one.

---

## Related

- [Monitor](monitor.md) — live progress, ETA, stall detection, and recovery.
- [Sessions & multi-night imaging](sessions-multi-night.md) — accepted-frame
  quotas and auto-resume.
- [Safety & automation](safety-and-automation.md) — the guards a run honours.
- [Sky Atlas](sky-atlas.md) — send framed targets (and mosaics) into a plan.
