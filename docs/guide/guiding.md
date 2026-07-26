# Guiding

AstroDeck ships its **own** autoguider — a Rust engine that ports PHD2's
guiding algorithms (star find, calibration, per-axis correction, backlash
compensation, multi-star tracking, predictive PEC) so an Alpaca/native/sim rig
gets NINA/PHD2-parity guiding with **no NINA and no PHD2 process required**.
It is the **default** guide provider on the simulator rig and on any rig where
AstroDeck owns the guide camera + mount; the PHD2 bridge and NINA's own
guiding stay available as a fallback (see [Provider](#provider--phd2-fallback)
below). Everything here lives on the **Guide** view.

---

## Guide Error, Scatter, and the live graph

- **Guide Error · arcsec** — the RA/Dec error graph, plus **RMS RA**, **RMS
  Dec**, **RMS Total** (color-coded: good under 1″, warn under 2″, bad above),
  and **SNR**. A **● guiding** badge shows while actively correcting.
- **Scatter** — the same samples plotted as a 2D scatter, useful for spotting
  a systematic drift vs. random seeing noise at a glance.
- **Guide-camera preview** — a collapsible live thumbnail with a lock-region
  reticle overlaid on the locked star (the same preview Capture's
  guide-camera panel shows — see [Capture](capture.md#guide-camera-preview)).

---

## Control

- **Start Guiding** — calibrates (if no compatible calibration is persisted;
  see below) then guides continuously.
- **Stop** — stops guiding; the current calibration is left in place.
- **Force Recalibrate** — stops guiding, discards the persisted calibration
  for the active profile, and restarts, so the engine walks a fresh
  calibration instead of reusing the old one.
- **Dither** — nudges the lock position by the given pixel amount and waits
  for the guider to settle back within tolerance before the next exposure
  (used automatically between sequence frames when "dither during sequence"
  is on — see [Plan & sequences](plan-and-sequences.md)).

Calibration, once it succeeds, is **persisted per profile** and reused on the
next start as long as it's still compatible (same pier side, same image
scale/binning within tolerance, RA known at both stop and start) — so you
don't re-walk a ~20+ second calibration dance every time you start guiding. A
trained **Predictive PEC** model is persisted alongside it (per profile) and
restored on the same calibration-reuse path, subject to a stop/start downtime
gate (see the Guide Tuning panel, below). **Clear Calibration** (in the
Guide Tuning panel, below) discards both the calibration and the persisted
PPEC model explicitly.

All controls require `control.guide` (operator or admin); a viewer sees the
graph and RMS numbers live but every button is disabled.

---

## Guiding Assistant

If you don't know what your guide settings should be, run this first. The
**Guiding Assistant** panel measures your mount for a few minutes, then
recommends settings in plain language and can apply them for you.

**It drives the mount.** Press **Run Guiding Assistant** and the first phase
watches a guide star drift; the second phase deliberately pulses the mount
north and south a number of times to measure how much slack (Dec **backlash** —
the dead play in the gears before a direction reversal actually moves the
scope) it has. The whole run takes **2–4 minutes** and the scope really does
move, so check first that it can move freely and nothing will snag. The
progress bar names the phase it's in, and **Stop** cancels at any point.

If you'd rather it didn't move the scope, turn off **Also measure mount slack
(moves the scope)** before pressing Run. That leaves the watching phase only
(about two minutes, mount tracking normally, never driven) — the backlash
figure is then simply not measured.

**What has to be true first.** The assistant needs the AstroDeck native guider
(it needs raw pulse and star access the PHD2/NINA bridges don't expose), a
connected guider, `control.guide`, and **guiding stopped**. Whichever of those
is missing is stated on the panel where the Run button is. It also refuses
while the mount is parked or slewing, and it holds the mount for its whole run:
a sequence that tries to start guiding meanwhile is refused rather than
calibrating on top of the assistant's pulses.

**Reading the result.** You get one sentence: how fast the mount drifts (with a
polar-alignment verdict — *excellent*, *good*, *fair — a quick polar tweak
would help*, or *consider re-doing polar alignment*), how much Dec backlash it
found, and that recommended settings are ready. **Apply recommended settings**
saves them; like every other guide setting they take effect at the **next**
guiding start. **Start over** discards the measurements and returns you to the
Run screen — it does not re-measure.

**Applying never discards your calibration on its own.** If the recommendation
changes a guide algorithm the panel says so above the button, and your saved
calibration is kept so guiding still starts straight away. Only the advanced
per-setting path offers to clear it, in a dialog — and dismissing that dialog
**keeps** the calibration.

**If the slack measurement didn't happen** — the star was lost, the run was cut
short, or the walk stopped early to keep the star on the sensor — the panel
says so and the recommendation is your **current** backlash pulse, so Apply
changes nothing. A value you tuned by hand is never zeroed off a measurement
that never happened.

If a run fails outright (no guide star, a device error, you cancelled it) the
panel says what stopped it, confirms nothing was changed, and offers **Try
again**.

### Advanced — measurements and per-setting apply

The **Advanced · measurements and per-setting apply** disclosure holds the raw
drift graph and scatter plot, the numbers behind the verdict (RMS RA/Dec/total,
drift per minute, periodic-error peak-to-peak and its period, seeing jitter,
and the backlash figure ± its spread), and a table of every recommendation as
*current → recommended* with the reason for it.

Each row has a tick and **Apply selected** writes only what's ticked. Two rows
can target the same setting — *RA algorithm → Hysteresis* versus *RA algorithm →
Predictive PEC* — and those are either/or: ticking one unticks the other.
Suggestions marked *advanced* (Predictive PEC, Lowpass2) are never part of the
one-tap set; the default recommendations stay deliberately conservative.
**Open in tuning editor** scrolls to the Guide Tuning panel below and fills it
in without saving anything — you still press **Save** there.

---

## Guide Tuning

An **Edit** toggle exposes the per-axis algorithm selection:

- **RA algorithm** — Hysteresis (default), Lowpass, Lowpass 2, Resist Switch,
  **Predictive PEC (Gaussian Process)**, or Z Filter.
- **Dec algorithm** — Hysteresis, Lowpass, Lowpass 2, Resist Switch (default),
  or Z Filter. (PPEC is RA-only — periodic error lives in the worm gear that
  drives RA tracking, so a Dec predictor has nothing periodic to learn.)

Each algorithm's parameters start at its PHD2-default set and are editable
underneath the picker (swapping the algorithm resets them to that default). The
panel also carries **Dec guide direction** — *Auto (both directions)*, *North
only*, *South only*, or *Off (no Dec guiding)* — and a **Dec backlash pulse
(ms)**, the fixed seed pulse added on a Dec direction reversal (0 = off; the
[Guiding Assistant](#guiding-assistant) can measure a value for it). **Save**
writes the lot, and everything here applies on the next guiding start. A viewer
sees the same fields read-only.

**Predictive PEC** learns your mount's periodic error over a
few worm cycles and blends in a feed-forward prediction once it has enough
data — it converges faster and holds tighter than reactive Hysteresis on a
mount with real periodic error, at the cost of a warm-up window before the
prediction kicks in. A **dither** no longer throws that learning away: PPEC
compensates for the gear-time gap the dither introduces and keeps its trained
model (it does not reset), so the prediction recovers as soon as the star
settles. The trained model is also **retained across a guiding stop/start**:
if you stop and restart on the same calibration within a fraction of a worm
period, PPEC restores its learned model instead of warming up from zero (a
longer gap starts fresh). PPEC stays **opt-in** — you pick it as the RA
algorithm; Hysteresis remains the default.

**Clear Calibration** here discards the persisted calibration (see Control,
above) — do this after a real backlash/optics change, or if a fresh
calibration would help isolate a guiding problem.

The Dec backlash pulse above is *static*: it's a fixed number you (or the
Guiding Assistant) set, engine-level rather than per-axis, and off by default.
The *adaptive* backlash controller — one that measures and re-adjusts the pulse
by itself while guiding — is not implemented; see
[native-guider.md](../native-parity/native-guider.md#known-deferred-items).

---

## Provider — PHD2 fallback

The **Guide Provider** panel picks *who* guides, independent of the algorithm
choice above:

- **Auto (best available)** — AstroDeck decides: NINA owns guiding on a NINA
  rig (guiding is never split between NINA and AstroDeck on the same rig);
  otherwise the native engine runs if a guide camera + mount are both
  connected (badged **AstroDeck native** on real hardware, or **Simulator** on
  the sim rig — same engine, different badge); otherwise it falls back to the
  **PHD2** bridge.
- **AstroDeck native** — force the native engine. Offered only when it can
  actually run on this rig (a guide camera + mount connected). If you force it
  where it can't run, guiding *degrades* to whatever is wired instead of
  failing — and the badge then shows what is **actually** guiding, never the
  option you picked.
- **PHD2 / NINA bridge** — force the legacy bridge (NINA's own guiding on a
  NINA rig, or a standalone PHD2 socket otherwise). Offered only when a bridge
  guider is available on the connected rig.

The dropdown lists **only the providers that apply to the connected rig** — the
same "only offer what's eligible" rule the Equipment tab's task routing uses.

A switch takes effect at the **next guiding start**: it never swaps a guider
that's already running. Change the provider, then stop and (re)start guiding
for it to take hold. The line under the dropdown always explains *why* it
resolved the way it did (e.g. *"the native guider is running (guide camera +
mount)"*, *"NINA is guiding this rig"*), and the badge at the top of the Guide
Error panel always reflects the guider that is **really** serving right now —
so the head-to-head comparison below can never compare a guider against itself
under two names. Changing the provider needs `config.backend` (admin) — it's a
backend/connect-shape decision, the same capability the Equipment tab's task
routing (autofocus/polar align/plate solve) uses, not `control.guide`.

The override is saved into the **profile** you activate, exactly like the
other task-provider overrides: save a profile while the override is set, and
loading/activating that profile restores it.

### Same-night RMS: native vs. PHD2

Once you've guided under **both** providers in the same session — switch the
provider, then stop and restart guiding, and do a run under each — the panel
shows a head-to-head verdict: which one guided tighter, "comparable" (within
roughly 10% of each other — ordinary seeing noise, not a real difference), or
"insufficient data" if one side hasn't guided long enough yet. Because each
window is tagged by the guider that *actually* served it (not the option you
selected), the verdict compares real like-for-like data. This is a same-night
comparison only: it resets when you reload the page, and it does not track
history across different nights.

---

## Under the hood (routes)

`POST /api/guide/start`, `/api/guide/stop`, `/api/guide/dither`,
`/api/guide/calibrate` (force-recalibrate), `DELETE /api/guide/calibration`
(clear persisted calibration) — all `control.guide`-gated. `GET`/`PUT
/api/guide/settings` reads/writes the per-axis tuning (`control.guide`), and is
also what the Guiding Assistant's Apply uses. The assistant itself is
`POST /api/guide/assistant/start` and `/stop` (`control.guide`) plus
`GET /api/guide/assistant/report` (`view.status`), which returns the last run's
measurements and recommendations — or `null` when it hasn't run this session.
`GET /api/guide/frame.png` serves the guide-camera preview
(`view.preview`). The provider override rides the same
`POST /api/config/providers` route the Equipment tab's Tasks panel uses
(`config.backend`) — see
[Equipment & profiles](equipment-and-profiles.md#task-providers-autofocus-polar-align-plate-solve).

## Related

- [Capture](capture.md#guide-camera-preview) — the guide-cam glance without
  leaving Capture.
- [Monitor](monitor.md#last-frame-guiding-thermal) — the live RMS sparkline
  and stale-guider note.
- [Equipment & profiles](equipment-and-profiles.md) — connecting a guide
  camera + mount, task providers, and profiles.
- [native-guider.md](../native-parity/native-guider.md) — the engine's
  architecture, algorithm coverage, and known deferred items (developer doc).
