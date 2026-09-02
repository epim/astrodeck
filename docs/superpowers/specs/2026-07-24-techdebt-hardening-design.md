# Tech-Debt Hardening Batch — Design

Date: 2026-07-24
Status: design only (do NOT implement from this doc without the usual per-task briefs)
Author: design subagent (Fable-supervised SDD)
Branch target: continue on the active Wave-0 remainder branch (local build/test only; no pushes this month)

## 0. Summary

One cohesive backend-robustness batch plus two small "learn-loop" features. Nothing here is a
new subsystem — every item hardens an existing seam or closes a loop whose *editor* already ships.

Items, ranked by correctness value:

1. **(a3) SerialLink cancel-race** — HIGHEST VALUE. A cancelled `to_thread` exchange keeps running
   on the pyserial handle after the `asyncio.Lock` is released, so the next exchange can race it →
   framing corruption. Join the orphaned exchange before releasing the port.
2. **(b2) orchestrator `_normalize` ignores `port_path`** — two same-backend serial devices coalesce
   into one session; the second silently talks to the first's port.
3. **(a2) generic refused-in-state labeling** — `e14` is "refused in current state," not always
   "parked"; the hardcoded copy misleads (below-horizon limit, motion-in-progress, etc.).
4. **(b1) driver_type collision guard** — a plugin backend can declare `driver_type="nina"` and
   shadow the built-in in `driver_type_to_backend()`, hijacking which backend a config type opens.
5. **(a1) `format_ra` mod-24 normalization** — RA hours ≥ 24 or < 0 (or a 59.9999s carry) emit
   `24:00:00`, which the LX200 parser can reject/misread.
6. **(c1) EGAIN auto-learn loop** — mean-variance (photon-transfer) measurement for cameras whose
   driver reports no e-/ADU (Alpaca/NINA). Pure math + a thin capture loop + Advanced-only UI.
7. **(c2) per-filter AF-offset auto-learn loop** — the offset *editor* ships (FilterNamesModal); add
   the LEARN loop that runs autofocus per slot and writes offsets relative to a reference filter.

No Rust. No wheel rebuild. All Python + TypeScript. Estimated net-new tests: **~9** (several parametrized).

---

## 1. (a) LX200 codec + serial hardening

### 1.1 (a3) SerialLink cancel-race — the highest-value fix

**Seam:** `server/astrodeck/devices/serial_link.py:72-88` (`SerialLink.request`) and `:90-96` (`close`).

**The bug.** `request()` is:

```python
async with self._lock:                 # serializes whole exchanges
    def _exchange(): ...               # reset_input_buffer + write + read-to-deadline
    return await asyncio.to_thread(_exchange)
```

`asyncio.to_thread` schedules `_exchange` on a worker thread and awaits a Future. If the *awaiting
coroutine* is cancelled (or times out) mid-exchange, `CancelledError` propagates out of the `await`,
the `async with self._lock` unwinds and **releases the lock** — but the worker thread cannot be
cancelled, so `_exchange` keeps running: still holding `self._ser`, still mid `read(1)` loop (up to
`timeout` seconds), possibly mid-`write`. With the lock now free, the *next* `request()` acquires it
and starts a *second* `_exchange` on the **same pyserial handle** → concurrent `reset_input_buffer` +
`write` + `read` on one port → interleaved/garbled framing.

This is not theoretical on this driver. `ZwoAm5Telescope.slew` (`zwo_am5.py:280-288`) halts on *any*
abnormal settle exit — precisely the cancel case — by issuing `self._link.request("Q", reply="none")`
in its `except`. So a cancelled slew whose in-flight settle poll (`request("GR")`) is still running in
the orphaned thread will start the halt `request("Q")` on top of it. Same for `/api/focuser/halt`
cancelling an autofocus that drives a serial focuser, and for disconnect racing an in-flight poll.

**Related race — `close()` (`serial_link.py:90-96`).** `close` nulls `self._ser` and closes the
handle *without holding `self._lock`*. If an exchange thread is mid-`read`, `self._ser` becomes `None`
under it (`AttributeError`) or the OS handle closes under a blocking read (undefined on Windows).

**Fix (design).** Keep the lock held until the orphaned exchange actually finishes, so the port has
exactly one user at all times. Bound the wait by the exchange's own deadline (which already exists).

```python
async def request(self, cmd, *, reply="hash", timeout=1.5):
    if self._ser is None:
        raise LinkError("link not open")
    async with self._lock:
        def _exchange(): ...            # unchanged body
        task = asyncio.ensure_future(asyncio.to_thread(_exchange))
        try:
            return await asyncio.shield(task)
        finally:
            # If we're leaving abnormally (cancel / timeout / error out of shield)
            # the worker thread may STILL be using self._ser. Do not release the
            # lock — hence the port — until it returns, or the next holder races
            # the orphaned pyserial handle (framing corruption). Bounded by the
            # exchange's own deadline; suppress a second cancel so we still join.
            if not task.done():
                with contextlib.suppress(BaseException):
                    await asyncio.shield(task)
```

- Normal completion: `task.done()` is True in `finally`; no extra await; behavior byte-identical.
- Cancel/timeout: `CancelledError` (or `LinkError`) propagates from the `try`; `finally` joins the
  worker (≤ `timeout`), then the original exception continues. The lock releases only after the join.
- `close()`: acquire `self._lock` first, so teardown waits for any in-flight exchange:
  ```python
  async def close(self):
      async with self._lock:
          ser, self._ser = self._ser, None
          if ser is not None:
              try: await asyncio.to_thread(ser.close)
              except Exception: pass
  ```
  `disconnect()` (`zwo_am5.py:143-149`) already sends `:Q#` (a full request, lock-serialized) *then*
  calls `close`; both now serialize correctly.

**Tradeoff (call out to the user):** cancel latency becomes bounded by the in-flight exchange's
`timeout` (≤ 1.5 s hash reads; the ack path uses its own timeout). That is the correct price — the
serial handle is single-owner and must not be handed over mid-frame. Keep per-mode timeouts modest so
cancel stays responsive (they already are: 0.2 s pyserial read timeout, 1.5 s exchange default).

**Import:** add `import contextlib` at module top.

### 1.2 (a2) Generic refused-in-state labeling

**Seam:** three sites hardcode "parked" for the `e14` refusal:
`zwo_am5.py:106-109` (`_cmd_ack`), `:255-258` (`slew`), `:298-301` (`sync`).

`lx200.py:9,19` documents `e14` as "refused in current state," and the AM5 uses it for park *and*
for other refusals (limits, an in-progress motion, an un-set target). Telling the user to "unpark
first" when the mount is actually below a horizon limit sends them the wrong way.

**Fix (design).** One helper on `ZwoAm5Telescope` that composes an honest message, probing parked
state only on the error path (a refusal is already exceptional, so one extra `:Gps#` round-trip is
fine — and it is best-effort so a failed probe never masks the original error):

```python
async def _refused_error(self, what: str) -> DeviceError:
    parked = False
    try:
        parked = await self.is_parked()
    except Exception:
        pass
    if parked:
        return DeviceError(f"{self.name}: {what} refused — mount is parked; "
                           "unpark first (AM5 e14)")
    return DeviceError(f"{self.name}: {what} refused in current state — check "
                       "limits / that a slew isn't already running (AM5 e14)")
```

Replace the three inline branches with `raise await self._refused_error(what)`. This also de-dupes
the copy. `_cmd_ack`'s non-`e14` reject branch (`:110-111`) is unchanged.

### 1.3 (a1) `format_ra` mod-24 normalization

**Seam:** `lx200.py:56-59` (`format_ra`) → `_sex` (`:48-53`).

`_sex(hours)` computes `round(abs(value)*3600)` with no wrap. `format_ra(24.0)` → `"24:00:00"`;
`format_ra(-1.0)` → `"23:00:00"` but drops the sign meaning; and `format_ra(23.99999)` carries to
`"24:00:00"`. RA is cyclic in `[0,24)`; the mount's `:Sr#` target parser expects that range.

**Fix (design).** Normalize on the *integer seconds* count so the 59.9999s carry can't survive as 24h:

```python
def format_ra(hours: float) -> str:
    total = round((hours % 24.0) * 3600) % 86400   # wrap input AND post-carry
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
```

`hours % 24.0` handles negatives and ≥24 (Python modulo returns a non-negative result for a positive
modulus). The trailing `% 86400` collapses the `86400 → 24:00:00` carry back to `00:00:00`.
`_sex` stays as-is for `format_dec` (declination is *not* cyclic — it must clamp/keep sign, never wrap;
leave it alone). No change to `parse_ra`.

---

## 2. (b) Driver-framework registry guards

### 2.1 (b1) driver_type collision guard

**Seam:** `devices/backend.py:196-200` (`register`), `drivers.py:358-367` (`driver_type_to_backend`),
`devices/backends/_discovery.py:34-97` (`discover_plugin_backends`).

**The gap.** Discovery already protects built-in *names* (`_discovery.py:64-76`): a plugin that
overwrites `BACKENDS["nina"]` is refused. But it does **not** protect built-in *driver_types*. A
plugin can register a brand-new backend name (e.g. `evil-mount`) that declares `driver_type="nina"`.
That is an `added` (not `overwritten`) backend, so discovery accepts it. Then
`driver_type_to_backend()` builds `{getattr(b,"driver_type",""): b.name for b in BACKENDS.values()}`
in registry-insertion order — the plugin registered *after* the built-ins, so `{"nina": ...}` is
overwritten to point at `evil-mount`. Now every ConnSpec/config row of type `"nina"` opens the
plugin's backend instead of NINA. Silent hijack of a device-connection route.

**Fix (design) — two layers:**

1. **Primary (mirror the existing name-guard in discovery).** Before loading plugins, snapshot the
   built-in driver_types: `builtin_types = {getattr(b,"driver_type","") for b in BACKENDS.values()} - {""}`.
   After a plugin's `added` set is computed, if any added backend's `driver_type` is in
   `builtin_types` (or was already claimed by an earlier-registered non-built-in), treat it exactly
   like the overwrite case: drop the added backend(s), append a `status:"failed"` report row
   (`detail: "refused: driver_type 'nina' collides with a built-in"`). This matches the existing
   idiom and gives the plugin author an honest report row via `plugin_load_report()`.

2. **Defense in depth (deterministic resolver).** Make `driver_type_to_backend()` first-claimer-wins
   so a stray collision can never silently redirect even if it slips past layer 1:
   ```python
   out: dict[str, str] = {}
   for b in BACKENDS.values():                    # built-ins are inserted first
       dt = getattr(b, "driver_type", "")
       if dt and dt not in out:
           out[dt] = b.name
   return out
   ```

Layer 1 is the user-visible fix; layer 2 guarantees the invariant regardless of registration path.

### 2.2 (b2) `_normalize` ignores `port_path`

**Seam:** `devices/orchestrator.py:81-96` (`_normalize`), used by `_group` (`:117-123`), `_pick_guider`
(`:137`), `_pick_solver` (`:157`), `_pick_guide_camera` (`:312`).

**The bug.** For a non-hostless backend, `_normalize` returns `(name, conn.host, conn.port)`. A serial
ConnSpec carries its address in `port_path` (COM3 / /dev/ttyACM0) and leaves `host`/`port` at `None`.
So two *different* serial devices of the *same* backend both normalize to `(name, None, None)` and
`_group` coalesces them into ONE endpoint — only the first `port_path` is opened; the second role's
device is served from the first port's session. Today `zwo-am5` fills only `telescope` (one role), so
the concrete blast radius is small, but the framework is now the home of every native serial backend
(Wanderer, future multi-role serial rigs, two mounts on a mosaic mount-pair), and the grouping key is
simply wrong for serial transport.

**Fix (design).** Include the serial address in the key, transport-discriminated, keeping the tuple
shape (the key is opaque; `to_dict` stringifies it, sessions dict keys it):

```python
def _normalize(conn):
    name = conn.backend
    try:
        hostless = bool(getattr(get_backend(name), "hostless", False))
    except KeyError:
        hostless = False
    if hostless:
        return (name, None, None)
    if conn.transport == "serial":
        return (name, conn.port_path, None)     # COM3 distinguishes two serial units
    return (name, conn.host, conn.port)
```

`hostless` still wins first (sim/phd2 unaffected). Network specs are byte-identical
(`transport="network"` default → `(name, host, port)`). Two serial specs on distinct ports now key
distinctly → two sessions. This is the minimal, shape-preserving change; every caller uses
`_normalize` uniformly so nothing else moves.

---

## 3. (c) Learn loops

Both are "close the loop" features: the *math/editor* exists; add the orchestration that fills it.
Pure logic goes in tested lib helpers; the loop is a thin capture orchestration; the UI is
Advanced-only with a novice-safe default.

### 3.1 (c1) EGAIN auto-learn (mean-variance / photon transfer)

**Why.** `Camera.egain` (`base.py:136`) and `CameraFrame.egain_e_per_adu` (`base.py:67`) are populated
only by native adapters that read it (Player One `get_egain`, ZWO `ElecPerADU`, sim constant 0.8).
Alpaca/NINA leave it `0.0`, so on those rigs the EGAIN FITS card is omitted and photometry/read-noise
in electrons is unavailable. Auto-learn measures it from frames the user can already take.

**Pure math (new, tested):** `server/astrodeck/imaging/egain.py`.

Standard mean-variance conversion gain, pair-difference to cancel fixed-pattern noise (reusing the
exact idiom `imaging/readnoise.py:19-24` already establishes):

```
signal_mean = mean(flat1) + mean(flat2) - mean(bias1) - mean(bias2)
signal_var  = var(flat1 - flat2) - var(bias1 - bias2)        # /2 cancels in ratio below
egain[e-/ADU] = signal_mean / signal_var
```

```python
def measure_egain(flats: list[np.ndarray], biases: list[np.ndarray]) -> float:
    """e-/ADU via mean-variance. Needs >=2 flats (matched illumination) and
    >=2 bias frames. Pair-difference removes fixed-pattern noise. Raises
    ValueError on too-few frames or a non-positive/degenerate variance."""
```

Guards: `< 2` flats or biases → `ValueError`; `signal_var <= 0` (flat pair identical / saturated) →
`ValueError("degenerate variance — check illumination isn't saturated or flat")`.

**Learn loop (new, thin):** a hub coroutine `learn_egain(gain, count=4, exposure_s=...)`:
- 409 guard mirrors autofocus (`app.py:3038-3040`): refuse if `engine.running or hub.looping`.
- Capture `count` bias frames (`light=False`) then `count` flats (`light=True`) at the given `gain`,
  each under `hub.exposure_guard` (serialized against every other capture path).
- Publish progress on the event bus (reuse the pattern; a small `egain` state event:
  `{"state":"running|done|failed","step":i,"of":2*count}`), so the UI shows a live count.
- On completion call `measure_egain`, persist, and stamp `cam.egain` so subsequent frames carry it.
- Cancel-safe via the existing `_spawn`/`hub._busy` machinery (a new busy key `"egain"`).

**Persistence:** a per-profile learned-egain map keyed by gain, a small side JSON mirroring the
filter-config store (`config.py:530-559`). `load_egain_config(profile)->{gain:egain}` /
`save_egain_config(...)`. On camera connect (or in `hub.capture`'s EGAIN stamp at `hub.py:1493-1497`),
if the device reports `0.0` and a learned value exists for the capture gain, use the learned value.
Device-reported egain always wins over a learned override (honest hardware value first).

**API:** `POST /api/camera/egain/learn` (`CAP_CONTROL_CAPTURE`), body `{gain, count?, exposure_s?}`,
returns the `_spawn` task handle; result rides the bus + `status.camera.egain`. A `GET` is not needed
— the learned value surfaces through the existing `status.camera.egain`.

### 3.2 (c2) Per-filter AF-offset auto-learn loop

**Why.** `FilterNamesModal` (`ui/src/components/capture/FilterNamesModal.tsx`) already edits per-filter
focuser offsets, `set_filter_names` (`hub.py:1857-1880`) persists them, and the sequence engine applies
them on filter change (`engine.py:2088-2091`). The autofocus route already accepts a `filter` slot and
moves there before the sweep (`app.py:3047-3060`) and `run_autofocus` returns `best_position`
(`autofocus.py:196`). The missing piece is the LOOP that focuses each filter and computes the deltas.

**Pure math (new, tested):** `server/astrodeck/focus/filter_offsets.py`:

```python
def offsets_from_positions(best_by_slot: dict[int, int], ref_slot: int,
                           n_slots: int, prior: list[int]) -> list[int]:
    """Per-slot focuser offset = best[slot] - best[ref]. Slots that didn't
    produce a focus (missing key) keep their PRIOR offset (AF may fail on a
    starless narrowband slot). ref_slot missing -> ValueError."""
```

**Learn loop (new, thin):** hub coroutine `learn_filter_offsets(ref_slot, af_params)`:
- 409 guard identical to autofocus (camera-exclusive).
- For each slot `i` in the wheel: `set_position(i)` → `run_autofocus(..., expose_guard=..., hub=hub)`
  (reuses the whole provider-routed sweep, including the native engine when present — no new AF code).
  Collect `best_position` when `result.success`, else skip (leaves prior offset).
- Publish progress: reuse the `focus` bus events for each per-slot sweep (the UI already renders those
  live) plus a wrapping `filter_offsets` status event carrying `{slot, of, done_slots}`.
- On completion: `offsets = offsets_from_positions(...)`, then persist via the existing
  `set_filter_names(names, offsets)` path (same store, same event) so the modal refills.
- Cancel-safe (`hub._busy["filter_offsets"]`); a cancel restores each focuser via `run_autofocus`'s own
  shielded start-pos restore (`autofocus.py:180-189`).

**API:** `POST /api/filterwheel/learn-offsets` (`CAP_CONTROL_CAPTURE`), body
`{ref_slot, exposure_s?, gain?, step?, steps_each_side?, binning?}` → `_spawn` handle.

**Reference-slot default:** slot with the *shortest* filter name that is L/Lum/Clear if present, else
the wheel's current position. (Recommendation below.)

---

## 4. UX — progressive disclosure

Backend-only items (a1/a2/a3, b1, b2) have **no UI surface** — they harden existing paths; the only
user-visible change is a more accurate refusal message (a2) and correct multi-device behavior (b2).

### 4.1 EGAIN auto-learn

- **Novice default (zero config):** nothing changes. EGAIN "just works" from the driver when reported;
  when not, the card is simply omitted (today's behavior). A novice never has to know what e-/ADU is.
- **Advanced disclosure:** a collapsed **"Advanced"** sub-section in the Camera/Cooler area of
  CaptureView (matching the existing `Advanced` disclosures already used across settings panels —
  `NamingPanel`, `AlertsPanel`, etc.). Inside: one button **"Measure gain (e-/ADU)"** with plain copy:
  *"Takes a few flat and dark frames to measure your camera's true gain — improves the noise and SNR
  readouts. Takes about a minute."* Below it, the current value (device-reported or learned) as a raw
  number for experts, with provenance (`from driver` vs `measured 2026-07-24`).
- **Honest-disabled (§11.8):** when no camera, or read-only caller, or a capture loop/sequence is
  running, the button is dimmed + `aria-disabled` + `title` explaining why — never native `disabled`,
  never hidden (via the existing `Gated`/`useCan` idiom, `mode="disable"` for the group).
- **Expert reward:** the Advanced section also shows the learned per-gain table (raw), and a note that
  a driver-reported value always overrides a measured one.

### 4.2 Per-filter AF-offset learn

- **Novice default:** the offset editor already works by hand; offsets default to `0` and *everything
  still images correctly* with zero offsets. A novice can ignore the feature entirely.
- **Advanced disclosure inside FilterNamesModal:** a collapsed **"Learn offsets automatically"** area
  below the manual grid. Plain copy: *"Focuses each filter for you and fills in the offsets. Point at a
  star field first. Takes a few minutes."* A reference-filter picker (defaults per §5) with a one-line
  explanation: *"Offsets are measured relative to this filter (it stays at 0)."* A **Start** button.
- **While running:** an inline progress line (*"Focusing Ha (3 of 7)…"*) driven by the bus events; the
  manual grid stays visible. The novice path (manual Save) is untouched and still available.
- **Expert reward:** on completion the computed deltas fill the offset inputs *as editable drafts* —
  the expert can hand-tweak any slot before the final Save, and slots where AF failed keep their prior
  value (surfaced with a small "kept" hint) rather than snapping to 0.
- **Honest-disabled (§11.8):** Start is dimmed + `aria-disabled` + `title` when there's no focuser, no
  filter wheel, read-only, or a capture is in progress.

---

## 5. Open decisions (each with a recommendation)

1. **Where to enforce the driver_type collision guard — `register()` vs discovery vs resolver?**
   *Recommendation:* discovery (layer 1, mirrors the existing built-in-name guard and yields an honest
   `plugin_load_report` row) **plus** the first-claimer-wins resolver (layer 2, cheap invariant). Do
   **not** add a raise to `register()` — built-ins re-register (e.g. `register_all` called twice in
   tests) and an over-eager raise there is fragile. Recorded as a decision because it's tempting to
   "just guard register."

2. **SerialLink cancel join — bounded-wait vs shield-and-forget?**
   *Recommendation:* bounded join (§1.1). Correctness (single-owner port) beats sub-second cancel
   latency; the wait is already capped by the exchange timeout. Rejected alt: releasing the lock and
   letting the orphan run (the current bug) — it corrupts framing.

3. **EGAIN learned-value precedence — driver vs measured.**
   *Recommendation:* driver-reported always wins; a measured value is used only when the driver reports
   `0.0`. Never override a real hardware number with an estimate.

4. **EGAIN persistence granularity — per-gain vs single.**
   *Recommendation:* per-gain map (conversion gain varies with the gain setting, especially across a
   camera's HCG transition). Keyed by exact gain integer; applied only on an exact match (no
   interpolation in v1 — lean).

5. **AF-offset reference slot default.**
   *Recommendation:* prefer an L/Lum/Clear-named slot (case-insensitive), else the wheel's current
   position. Always user-overridable in the picker. The reference is pinned to offset 0.

6. **AF-offset learn: what to do with a slot AF fails on (starless narrowband).**
   *Recommendation:* keep the slot's prior offset and mark it "kept" in the result; never write a bogus
   0 that would defocus that filter. `offsets_from_positions` already encodes this via `prior`.

7. **Should the two learn loops publish new bus event types or reuse existing?**
   *Recommendation:* reuse `focus` events for the actual sweeps (the UI renders them already) and add
   one small wrapping status event per loop (`egain`, `filter_offsets`) carrying only step/of counts.
   Minimizes new client-side surface.

---

## 6. LEAN test plan

Reuse existing fixtures (sim rig, fake serial link seam `zwo_am5._make_link`, the lx200 codec test
module, the orchestrator grouping tests). Pure logic → tested lib helpers; loops → one integration
test each; renders → thin, untested-by-DOM.

| # | Area | Test (parametrized where noted) | Kind |
|---|------|--------------------------------|------|
| 1 | **a3 cancel-race** | Fake blocking serial: cancel a `request` mid-exchange, assert the lock is not released until the worker returns AND a subsequent `request` reads clean framing (no interleave). One test, 2 cases (cancel during `hash` read; during `ack` read). | unit |
| 2 | **a3 close race** | `close()` while an exchange is in flight waits for it (no `AttributeError`/None deref). Fold into #1's fixture as a 3rd case. | unit (fold) |
| 3 | **a1 format_ra** | Add cases to the EXISTING lx200 codec test: `24.0→00:00:00`, `25.5`, `-1.0→23:00:00`, `23.999999→00:00:00`. Net **0 new functions** (extra params on the existing test). | unit (fold) |
| 4 | **a2 refused labeling** | `_refused_error`: parked→"parked; unpark", not-parked→generic, probe-raises→still generic. One parametrized test (3 cases) with a fake link. | unit |
| 5 | **b1 driver_type collision** | Discovery refuses a plugin whose `driver_type` collides with a built-in (report row + built-in still owns the type); AND `driver_type_to_backend()` is first-claimer-wins. One test, 2 asserts. | unit |
| 6 | **b2 normalize port_path** | Two `zwo-am5` serial ConnSpecs on `COM3`/`COM4` → 2 distinct endpoint keys / 2 sessions; a network spec is unchanged. Fold into the existing orchestrator grouping test if present, else 1 new. | unit |
| 7 | **c1 measure_egain** | Synthetic Poisson flats+bias recover a known gain within tolerance; degenerate variance and <2 frames raise. One parametrized test. | unit |
| 8 | **c1 learn loop + persistence** | Sim/fake camera: `learn_egain` captures, injects known frames (monkeypatch `measure_egain` inputs or a scripted fake camera), stores per-gain, applies when device egain is 0; 409 when looping. One integration test. | integration |
| 9 | **c2 offsets_from_positions** | Deltas relative to ref; missing slot keeps prior; missing ref raises. One parametrized test. | unit |
| 10 | **c2 learn-offsets loop** | Sim rig, 3 slots: loop runs AF per slot, computes ref-relative offsets, persists via `set_filter_names`; a failed slot keeps prior; 409 when looping. One integration test. | integration |

**Net new test functions: ~9** (#3 folds into an existing test; #2/#6 fold where possible). Several
are parametrized so the assertion count is higher than the function count while the suite grows
minimally — consistent with the ~1901-test suite and the ~1-2%-redundant audit.

UI: the two Advanced surfaces are thin shells over existing store/api calls; any pure formatting
(e.g. egain provenance label, "kept" hint) goes in a lib helper only if it's non-trivial — otherwise
no new tsx tests (the render is a shell). Expected UI net-new tests: **0-1**.

---

## 7. Risks

- **Cancel latency (a3).** Bounded by exchange timeout (≤1.5 s). Acceptable and documented; mitigated by
  the already-small pyserial read timeout. If a future path needs snappier cancel, shorten that mode's
  timeout — do not reintroduce the orphan release.
- **b2 key widening.** Any code that constructs endpoint keys *by hand* (rather than via `_normalize`)
  would break. Grep confirms all keys flow through `_normalize`; `to_dict` stringifies keys generically.
  Low risk, but the per-task brief must re-grep for literal `(name, host, port)` tuple construction.
- **EGAIN measurement validity.** Mean-variance needs matched, unsaturated flat illumination; a user
  who runs it on a bad flat gets a garbage number. Mitigations: the degenerate-variance guard, the
  driver-wins precedence, and plain copy telling them what to point at. It's an Advanced, opt-in tool.
- **AF-offset loop duration.** N filters × a full sweep each is minutes long and camera-exclusive; a
  cancel must restore every focuser (relies on `run_autofocus`'s shielded restore — verified present).
  The 409 guard prevents overlap with sequences/loops.
- **Plugin collision guard behavior change.** A third-party plugin that (wrongly) reused a built-in
  driver_type will now be refused with a report row instead of silently hijacking — this is the intended
  correction, but note it in release notes in case any real plugin depended on the old behavior.
- **Test-count creep.** Guarded by folding codec/normalize cases into existing tests and parametrizing.

---

## 8. Files touched (design inventory)

Backend:
- `server/astrodeck/devices/serial_link.py` — cancel-join in `request`, lock in `close` (a3).
- `server/astrodeck/devices/lx200.py` — `format_ra` mod-24 (a1).
- `server/astrodeck/devices/backends/zwo_am5.py` — `_refused_error` helper + 3 call sites (a2).
- `server/astrodeck/devices/backends/_discovery.py` — driver_type collision guard (b1 layer 1).
- `server/astrodeck/drivers.py` — first-claimer-wins `driver_type_to_backend` (b1 layer 2).
- `server/astrodeck/devices/orchestrator.py` — serial-aware `_normalize` (b2).
- `server/astrodeck/imaging/egain.py` — NEW pure `measure_egain` (c1).
- `server/astrodeck/focus/filter_offsets.py` — NEW pure `offsets_from_positions` (c2).
- `server/astrodeck/hub.py` — `learn_egain`, `learn_filter_offsets`; egain-apply-on-connect (c1/c2).
- `server/astrodeck/config.py` — `load/save_egain_config` (c1).
- `server/astrodeck/api/app.py` — `POST /api/camera/egain/learn`, `POST /api/filterwheel/learn-offsets`.

UI:
- `ui/src/views/CaptureView.tsx` — Advanced "Measure gain" trigger + FilterNamesModal wiring.
- `ui/src/components/capture/FilterNamesModal.tsx` — Advanced "Learn offsets" area + progress line.
- `ui/src/store.ts` — `egain`/`filter_offsets` progress event handling; `learnEgain`/`learnFilterOffsets`
  actions.
- `ui/src/types.ts` — additive event/status fields.

No Rust, no `astrodeck_native` change, no wheel rebuild.
