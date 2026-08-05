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
  between targets and needs `control.mount` — held by **operator and admin**
  (operators are meant to run sequences); the **≡ Run Sequence** button (and
  Monitor's Pause/Resume/Abort) stays visible but **disabled** for a
  **viewer**, with a lock note naming the required access ("Running a
  sequence needs operator or admin access.").

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

## Instructions (when-this-then-that rules)

Everything above is a fixed plan. **Instructions** are optional rules laid on
top of it — *when this happens, do that*. A plan with no rules runs exactly as
it always did.

The **Instructions** panel sits with the plan builder; **+ Add rule** adds one.
A rule is a single line: a **trigger**, its value, and an **action**.

Triggers:

- **Stars look bloated (HFR above)** — an HFR threshold (typical good focus is
  2–3 px).
- **Guiding is wandering (guide error above)** — a guide-RMS threshold (under 1
  is good guiding). The unit follows the guider — arcseconds when the guide
  scope's focal length is known, pixels otherwise — and the rule text says
  which.
- **A frame is rejected** — the quality gates rejected the frame just taken.
- **A target finishes**.
- **The clock reaches** — a local `HH:MM` time. It fires once, at the first
  frame boundary at or after that time.

Actions come in two groups. *Keep imaging*: **Notify me** (with your message
and a severity), **Pause**, **Refocus**, **Dither**. *Give up on something*:
**Drop a target from tonight**, **Stop this target and switch to…**, **Stop the
whole session**. The second group changes what actually gets shot, so choosing
one prints a sentence saying what it costs you.

Rules are evaluated at **frame boundaries**, never mid-exposure. The two
threshold triggers are *edge*-triggered: a rule fires on the crossing and won't
fire again until the value comes back down to or below its threshold, so a
stuck-high HFR can't fire the same rule on every sub.

Each rule's **advanced** toggle adds **once per run**, a **cooldown** in seconds
(minimum gap between fires), and **only target** — a gate, so the rule is live
only while that target is the one being shot.

Editing rules needs `control.mount` (operator or admin); a viewer sees the
panel read-only with a note saying so.

### Preview what these rules do

**Preview what these rules do** opens a dry run. Set a pretend HFR, guide error,
frame-rejected / target-complete state, clock and active target, and every rule
says whether it *would fire* on such a frame or what it's waiting for; a rule
that ends or abandons work is flagged as such rather than reading like a
harmless notification. It writes nothing and is open to every role. It's honest
about its limits, too: it assumes each rule is ready to fire, while a real run
also applies the edge, once and cooldown timing, so the live result can differ.

### Jumping between targets

Two actions change the running order. Both need at least **two** targets in the
plan — with fewer they aren't offered at all, and the panel says why.

- **Drop a target from tonight** — pick the target to drop. Aimed at a target
  *later* in the night, it's simply removed when its turn comes and **the target
  you're shooting now keeps shooting**. Aimed at the target being shot right
  now, that one is abandoned.
- **Stop this target and switch to…** — pick where to go. The target being shot
  is abandoned and the named one moves to the front of the queue. If the name
  doesn't match a target in this plan, or names the target that's already
  running, **nothing happens** — the run carries on exactly where it was (a
  typo can't cost you the target you're shooting).

Picking either action switches **once per run** on for you so a rule can't
loop; you can turn that off under advanced. There's also a hard ceiling of 64
executed jumps per run: past that, further jumps are logged as a warning and
the night continues with normal scheduling — so two rules that jump at each
other degrade rather than hang. Frame counts are kept per step for the whole
run, so a target that comes round again resumes where it left off rather than
starting over.

### Combining conditions

Under **advanced**, **+ Combine conditions (AND/OR)** turns a rule's single
trigger into an **all** (AND) or **any** (OR) over 2 to 8 conditions drawn from
the same list. There's exactly one level — no nesting inside nesting. The
combined expression is edge-triggered as a whole: it fires when it becomes
true and re-arms once it's decisively false. If one of its terms can't be read
on a given frame and it's still needed to decide the answer, the rule doesn't
fire and stays armed — an unreadable metric never counts as "false".

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
- **if missed** — **wait** (run it whenever its window is open, even if the start
  passed while another target was shooting) or **skip** (drop it for the night
  once its start is more than 5 minutes past). A window that has already *closed*
  — stop time, dawn, or max run — is skipped under both settings; a closed window
  cannot be waited for. A **Now** start has nothing to miss, so **skip** does not
  apply to it (use **stop** / **max run** to bound a run-now target).

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
`GET /api/sequence/state`, `GET`/`POST /api/sequence/preflight`. Scheduling and
instructions are embedded in the plan itself (there is no separate schedule or
instructions route);
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
