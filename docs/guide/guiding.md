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
don't re-walk a ~20+ second calibration dance every time you start guiding.
**Clear Calibration** (in the Guide Algorithm panel, below) discards it
explicitly.

All controls require `control.guide` (operator or admin); a viewer sees the
graph and RMS numbers live but every button is disabled.

---

## Guide Algorithm

An **Edit** toggle exposes the per-axis algorithm selection:

- **RA algorithm** — Hysteresis (default), Lowpass, Lowpass 2, Resist Switch,
  **Predictive PEC (Gaussian Process)**, or Z Filter.
- **Dec algorithm** — Hysteresis, Lowpass, Lowpass 2, Resist Switch (default),
  or Z Filter. (PPEC is RA-only — periodic error lives in the worm gear that
  drives RA tracking, so a Dec predictor has nothing periodic to learn.)

Each algorithm's dossier-default parameters are shown read-only for reference;
only the algorithm *kind* is editable today. The pick applies on the next
guiding start. **Predictive PEC** learns your mount's periodic error over a
few worm cycles and blends in a feed-forward prediction once it has enough
data — it converges faster and holds tighter than reactive Hysteresis on a
mount with real periodic error, at the cost of a warm-up window before the
prediction kicks in.

**Clear Calibration** here discards the persisted calibration (see Control,
above) — do this after a real backlash/optics change, or if a fresh
calibration would help isolate a guiding problem.

Static Dec backlash compensation (a fixed seed pulse added on a Dec direction
reversal) is engine-level, not per-axis; it defaults to disabled and has no
editor yet (the *adaptive* backlash controller — which measures and adjusts
the pulse automatically — is not implemented; see
[native-guider.md](../native-parity/native-guider.md#known-deferred-items)).

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
/api/guide/settings` reads/writes the per-axis algorithm selection
(`control.guide`). `GET /api/guide/frame.png` serves the guide-camera preview
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
