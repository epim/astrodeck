# Per-frame WCS solve + write-back — design

**Slug:** `per-frame-wcs`
**Date:** 2026-07-24
**Status:** design only (no implementation, no commit)
**Predecessor:** PRO-2 F-B (FITS-header-completeness), spec `2026-07-22-fits-header-completeness-design.md`, plan `2026-07-22-fits-header-completeness.md` Task 6 + Task 7.

---

## 1. What already exists (the shipped mechanism)

PRO-2 F-B did more than "the mechanism" — it already wired the naive end-to-end path.
Reading the tree confirms every piece is present and tested:

- **Config flag** — `server/astrodeck/config.py:527`
  `solve_saved_lights: bool = False` (top-level `AppConfig` field, additive).
- **WCS value type** — `server/astrodeck/solve/base.py:8-27`
  `WcsSolution` (plain-number CD/CDELT celestial WCS) + `SolveResult.wcs` field (`:38`).
- **ASTAP → WcsSolution** — `server/astrodeck/solve/astap.py:100-139` `_wcs_from_astap`
  prefers the `.wcs` headerlet, falls back to `.ini`, and **returns `None` on a scale-less
  solve** (the "bogus 1°/px" guard, `:131-136`). Populated into `SolveResult.wcs` at `:179/:194`.
- **SimSolver WCS** — `server/astrodeck/solve/simsolver.py:30-49` `_sim_wcs` builds a valid
  TAN CD matrix from the sim pointing; returned in both sim-rig and hint paths (`:89/:95`).
- **Header write-back** — `server/astrodeck/imaging/fitsio.py:146-187`
  `_apply_wcs` (shared card emitter, re-checks the scale-less guard at `:151`) + `write_wcs`
  (non-fatal reopen-and-merge in `mode="update"`).
- **Capture wiring** — `server/astrodeck/hub.py:1589-1603`: when the flag is ON and a
  local save ran, after `_publish_preview`, `pick_solver(self).solve(...)` then
  `await asyncio.to_thread(write_wcs, ...)`, all inside a bare `try/except` that logs
  and never re-raises.
- **Test** — `server/tests/test_hub_capture_precession.py:420` `test_solve_saved_lights_stamps_wcs`
  pins bool-ON → CTYPE1 present.

**So "wire it end-to-end" is not the work.** The naive wire is done. This feature is the
**three gaps that wire left open**, all of which the task statement calls out:

1. **No UI.** `solve_saved_lights` is reachable only by hand-editing config JSON. There is
   zero UI surface (confirmed: no `solve_saved_lights` reference anywhere under `ui/src`).
2. **It blocks the hot path.** The solve at `hub.py:1594` is `await`-ed *inline* before
   `capture()` returns. `start_loop._loop` (`hub.py:1938`) and the sequence engine
   (`sequence/engine.py:1059`) both `await self.hub.capture(...)` fully before the next
   sub — so a 2–10 s ASTAP solve **delays the next exposure by its whole duration**, every
   frame. The task's hard requirement ("off the capture hot-path… never blocking the next
   sub") is currently **violated by the shipped code.**
3. **No advanced controls.** The task asks for solver choice, downsample, and an
   only-on-accepted gate. Today the config is a lone bool.

This design closes all three.

---

## 2. Architecture

### 2.1 Move the solve off the hot path (the core backend change)

Replace the inline `await solver.solve(...)` in `capture()` with an **enqueue** onto a
single-consumer background worker owned by the hub. The worker drains a **bounded** queue
serially (one solve in flight at a time — ASTAP is CPU-heavy; parallel solves would thrash
a Pi), stamping each saved light in place.

```
capture()                          Hub._wcs_worker  (one long-lived task)
  ...save FITS...                    loop:
  publish preview                      job = await queue.get()      # (path, ra, dec, fov, stats)
  if flag ON and local save:           if _wcs_should_solve(job.stats, cfg):   # gate (pure)
     enqueue(path, ra, dec, ...)         res = await solver.solve(path, downsample=cfg.downsample, ...)
  return info   # <-- returns NOW        if res.success and res.wcs:
                                            await to_thread(write_wcs, path, res.wcs)
```

- **Queue is bounded** (`maxsize` small, default from config, see §5). On overflow we drop
  the **oldest** pending job (keep the most recent frames tagged) and log once — memory can
  never grow unbounded and a slow solver can never wedge capture. Dropped frames simply ship
  without WCS; downstream can always re-solve. This is the honest best-effort contract.
- **Worker is lazy + lifecycle-bound.** Created on first enqueue; cancelled in the hub's
  existing teardown (alongside `stop_loop`). A solve failure/timeout inside the worker is
  caught and logged exactly as today — never touches the capture path.
- **`write_wcs` already runs on a thread** (`to_thread`) so the FITS reopen never blocks the
  loop; unchanged.
- **File-race safety:** each job targets its own already-closed FITS path; the next capture
  writes a *different* path. No same-file contention.

This is the one change that satisfies "bounded / best-effort / never blocking the next sub."

### 2.2 The solve-gate (pure, tested helper)

New pure function — put it in `server/astrodeck/solve/gate.py` (or beside `_solve_args` in
`astap.py`) so it is unit-testable with no hub/subprocess:

```python
def wcs_should_solve(star_count: int | None, cfg: WcsStampConfig) -> bool:
    """False when advanced 'only good frames' gate rejects: too few detected stars
    means ASTAP will fail anyway (wasted CPU) and the frame is likely a cloud/trail
    reject. min_stars<=0 disables the gate (default). None star_count => allow
    (NINA frames carry no count; don't punish them)."""
```

This is the **self-contained interpretation of "only-on-accepted"** (see §6, Decision D3):
the hub does *not* couple to the sequence engine's post-capture `_check_quality` verdict.

### 2.3 Downsample + solver threading

- **Downsample** — `astap.py:79` currently hardcodes `-z 0` (auto). Add an optional
  `downsample: int = 0` kwarg to `PlateSolver.solve` (ABC `base.py:44`), threaded into
  `_solve_args` as `-z {n}`. `SimSolver.solve` accepts and ignores it. Additive, default
  `0` == today's behavior — no existing caller changes.
- **Solver choice** — "Auto" keeps `providers.pick_solver(self)` (the single owner of
  ASTAP-vs-sim precedence — do **not** re-derive it). A "force ASTAP" advanced override maps
  to `find_astap()`/`AstapSolver`; when ASTAP is absent it degrades to auto and logs. Recommend
  scoping the override to **Auto / ASTAP** only (sim is auto-picked on sim rigs; exposing "force
  sim" invites the fake-solve hazard SimSolver guards against). See Decision D4.

---

## 3. Config model

Keep the shipped `solve_saved_lights: bool` as the **master enable** (already wired, already
tested, and it *is* the novice toggle). Add a sibling nested block for the advanced knobs,
consulted **only when the bool is ON**:

```python
# config.py — additive, appended (old configs load fine)
class WcsStampConfig(BaseModel):
    solver: Literal["auto", "astap"] = "auto"   # advanced: force ASTAP vs pick_solver
    downsample: int = 0                          # 0=auto; 1/2/4 = ASTAP -z
    min_stars: int = 0                           # 0=off; else skip solve below N detected stars
    queue_max: int = 4                           # bounded backlog before oldest-drop

class AppConfig(...):
    solve_saved_lights: bool = False             # UNCHANGED — master enable / novice toggle
    wcs_stamp: WcsStampConfig = Field(default_factory=WcsStampConfig)  # NEW advanced block
```

Rationale for bool + block (vs promoting to one `enabled`-bearing block): the bool is already
persisted in live configs, wired at `hub.py:1589`, and pinned by an existing test. Reusing it
is **zero-churn and back-compatible**; the block is purely additive with all-default fields so
the bool alone fully drives the feature. (See Decision D1.)

**Store setter + route:** follow the `naming`/`rotator`/`survey` precedent rather than the
field-level `ConfigPatchBody` map. Add `ConfigStore.set_wcs_stamp(...)` (mirrors
`set_naming` at `config.py:923`) and a dedicated route:

```
POST /api/config/wcs   body: { solve_saved_lights: bool, wcs_stamp: WcsStampConfig }
  cap: config.site_optics   (capture-OUTPUT concern, same cap as naming/optics)
  echoes _config_payload(principal); bus.publish("config", ...)
```

This avoids touching `_require_config_field_caps` (`app.py:1640`) and matches every other
imaging-output config route (`app.py:1034` naming).

**Redaction:** nothing secret in the block — no `redacted()` change needed. Add the fields to
the `_config_payload` dump implicitly (they ride `redacted(cfg)` since they're plain fields).

---

## 4. UI

### 4.1 Placement

New `WcsStampPanel.tsx` in `ui/src/components/settings/`, mounted in **SettingsView → Connect
tab**, in the left column right after `<NamingPanel />` (`SettingsView.tsx:172`). Both are
capture-output concerns gated by the same `config.site_optics` cap, so they cluster naturally.
No new tab.

### 4.2 Progressive disclosure (the required UX)

**Novice (always visible, zero config):** one toggle.

> **Solve & tag each frame's sky coordinates**
> Plate-solves every saved light and writes its WCS into the FITS header — slower, but lets
> stackers and PixInsight skip re-solving. *Off by default.*

Flip-and-done. The toggle is the `solve_saved_lights` bool. When OFF, the advanced disclosure
and its copy are the only other content, collapsed.

**Advanced (`<details>`, collapsed by default)** — a single "Advanced" summary row (matching
the app's existing `<details>`-based disclosures; no new primitive) revealing:

- **Solver** — SegmentedControl `Auto | ASTAP` (default Auto). Sub-copy: "Auto picks the best
  installed solver."
- **Downsample** — SegmentedControl `Auto | 1× | 2× | 4×` (default Auto). "Higher = faster,
  less precise. 2× is a good speed/accuracy trade on a Pi."
- **Only tag frames with enough stars** — number input, 0 = off (default). "Skips the solve on
  cloud/trail frames that would fail anyway."
- Advisory line (not a control): "One frame is solved at a time in the background; if solving
  falls behind, the oldest un-tagged frames are skipped — the capture never waits."

**Honest-disabled (idiom §11.8)** for the `config.site_optics` capability gate — mirror
`NamingPanel.tsx:59-63/69-74` exactly: controls stay **non-native-disabled**, dimmed, with
`aria-disabled` + a `title` naming the missing cap, and a lock line at the bottom. Never
`disabled`.

**ASTAP-absent advisory:** if the (already-served) providers/optics readout indicates no ASTAP
and the rig is real, show an inline note under the toggle: "ASTAP not detected — real rigs need
ASTAP installed to solve; sim rigs use the built-in solver." Advisory only; the toggle stays
usable (harmless when the background solve simply fails best-effort). This reuses existing
served state — no new endpoint.

### 4.3 Plumbing

- `types.ts` — add `wcs_stamp?: WcsStampConfig` (optional, WS-hello may predate it) + keep
  `solve_saved_lights` typed as optional bool on `AppConfig` (mirror `naming?`).
- `api/backends.ts` — `setWcsStampConfig(body): Promise<AppConfig>` (mirror `setNamingConfig`
  at `:425`) → `POST /api/config/wcs`; then `useStore.getState().loadConfig()`.
- The **only** UI logic worth a test is the ASTAP-advisory + summary derivation — extract to
  `ui/src/lib/wcsStamp.ts` (`wcsStampAdvisory(cfg, opticsComputed) -> string | null`,
  `wcsStampSummary(cfg) -> string`); the `.tsx` render is a thin, DOM-untested shell.

---

## 5. Backend plan (file:line seams)

1. **`config.py`** — add `WcsStampConfig` near the other small config models; add
   `wcs_stamp` field to `AppConfig` (after `:527`); add `ConfigStore.set_wcs_stamp` mirroring
   `set_naming` (`:923`). *(Keep `solve_saved_lights` bool as-is.)*
2. **`solve/base.py:44`** — add `downsample: int = 0` to the `PlateSolver.solve` abstract sig.
3. **`solve/astap.py`** — thread `downsample` into `_solve_args` (`:74-88`, replace the fixed
   `-z 0` at `:79`); accept the kwarg in `AstapSolver.solve` (`:158`).
4. **`solve/simsolver.py:66`** — accept + ignore `downsample`.
5. **`solve/gate.py`** (new) — pure `wcs_should_solve(star_count, cfg)`.
6. **`hub.py`** — replace the inline solve block (`:1589-1602`) with `enqueue`; add
   `_wcs_worker` coroutine + `_wcs_queue`/`_wcs_task` fields; consult `cfg.wcs_stamp` for
   solver/downsample/gate; cancel `_wcs_task` in the hub teardown that already cancels
   `_loop_task` (near `stop_loop`, `:1949`).
7. **`api/app.py`** — `WcsStampBody` model + `POST /api/config/wcs` route (cap
   `config.site_optics`), mirroring the naming route at `:1034`; echo `_config_payload`.

---

## 6. Open decisions (each with a recommendation)

**D1 — Config shape: keep the bool, or promote to one block?**
*Recommend:* **keep `solve_saved_lights: bool` + additive `wcs_stamp` block.** Zero migration,
back-compat with the shipped wire + test, and the bool maps 1:1 to the novice toggle. Promoting
to a single `enabled`-bearing block is cleaner on paper but churns live configs and the existing
test for no user-visible gain.

**D2 — Overflow policy when the solver falls behind.**
*Recommend:* **bounded queue, drop-oldest, log-once.** Keeps the newest frames tagged, caps
memory, never wedges capture. Alternative "skip-if-busy" (no queue) is simpler but can starve a
just-finished frame while a stale one solves; drop-oldest is strictly better for the same
complexity.

**D3 — "Only-on-accepted": couple to the sequence quality verdict, or a self-contained gate?**
*Recommend:* **self-contained `min_stars` gate in the hub.** The sequence engine's
`_check_quality`/`_handle_reject` (`engine.py:1131-1137`) runs *after* `capture()` returns, and
live-loop/single captures have no sequence verdict at all. A star-count floor covers the real
intent ("don't waste a solve on a junk frame"), works for every capture path, and stays pure/
testable. Coupling to the engine would leak sequence state into the hub and miss non-sequence
frames. (Rename the UI label to "Only tag frames with enough stars" to match the honest mechanism.)

**D4 — Solver-choice scope.**
*Recommend:* **Auto / ASTAP only.** "Auto" = `pick_solver` (unchanged precedence authority).
Exposing "force sim" would invite the exact fake-solve-on-real-rig hazard `SimSolver` refuses
(`simsolver.py:69-77`); leave sim as the auto-selected sim-rig path.

**D5 — Also stamp non-locally-saved (NINA) frames?**
*Recommend:* **no.** The gate is `local_save_path is not None` (`hub.py:1589`); NINA saves on the
imaging host and the file isn't on this box to reopen. Out of scope; document as a known limit.

**D6 — Surface a live indicator in CaptureView?**
*Recommend:* **defer.** A small "WCS tagging on" chip is nice but adds capture-view surface; keep
v1 to the Settings toggle. Note as a follow-up.

---

## 7. Test plan (LEAN — estimated **net +6 tests**)

Reuse `SimSolver` + the existing sim-hub fixture (`tests/_simhub.py`) and the existing
`test_solve_saved_lights_stamps_wcs` fixture scaffolding — do **not** duplicate setup.

1. **(rewrite, not net-new)** `test_solve_saved_lights_stamps_wcs` — update to drain the
   background worker (await the queue/`_wcs_task`) then assert CTYPE1 present. Same intent,
   new async shape. *(net 0)*
2. **+1 non-blocking contract** — `test_wcs_solve_off_capture_hotpath`: monkeypatch a solver
   whose `solve` sleeps; assert `capture()` returns *before* the solve completes (the WCS is
   absent immediately after return, present after draining the worker). Pins the core requirement.
3. **+1 parametrized gate** — `test_wcs_should_solve` over cases:
   `(stars=200, min=0)->True`, `(stars=3, min=20)->False`, `(stars=50, min=20)->True`,
   `(stars=None, min=20)->True`. One pure test, four cases.
4. **+1 downsample threading** — extend/parametrize the existing `_solve_args` test
   (`test_solver.py`) with `downsample=2 -> "-z","2"` and `0 -> "-z","0"`. (Fold into the
   existing arg test if present; counts as +1 case, ~0 new test function.)
5. **+1 overflow best-effort** — `test_wcs_queue_drops_oldest_never_raises`: enqueue past
   `queue_max` with a slow solver; assert no exception propagates to capture and the queue
   stays bounded. Guards the safety contract.
6. **+1 config round-trip + RBAC** — `POST /api/config/wcs` persists `solve_saved_lights` +
   `wcs_stamp`, echoes them, and 403s without `config.site_optics`. Fold into the existing
   config-HTTP test module; one focused test.
7. **+1 UI lib** — `wcsStamp.test.ts`: `wcsStampAdvisory` returns the ASTAP-absent string on a
   real rig w/o ASTAP and `null` otherwise; `wcsStampSummary` reflects on/off. Pure; the
   `.tsx` render is not DOM-tested.

**Net new: ~6** (1 rewrite + 5 additions + 1 folded case). Holds the "don't explode the suite"
bar while pinning the hot-path guarantee, the two safety edges (gate + overflow), the config
contract, and the one piece of UI logic.

---

## 8. Risks

- **R1 — Worker lifecycle leaks.** A background task not cancelled on hub teardown/reconnect
  could outlive its hub or hold a file handle. *Mitigation:* cancel `_wcs_task` in the same
  teardown that cancels `_loop_task`; the worker's per-job `try/except` already swallows a
  cancel-mid-solve.
- **R2 — Backlog on short subs.** With 5 s subs and 8 s solves the queue always drops. That's
  *acceptable and honest* (best-effort tagging), but the UI advisory must say so plainly so a
  user doesn't expect every frame tagged. Downsample 2×/4× is the mitigation offered in Advanced.
- **R3 — Scale-less WCS regression.** The "bogus 1°/px" guard lives in *two* places
  (`astap.py:135`, `fitsio.py:151`). Any refactor must preserve both; the gate/worker change
  touches neither, and no test may relax them.
- **R4 — ABC signature bump.** Adding `downsample` to `PlateSolver.solve` touches every solver +
  every monkeypatched test solver. *Mitigation:* default `0`, keyword-only already; audit the
  `pick_solver`-monkeypatch tests (`test_nina.py`, `test_hub_solve.py`) — a lambda solver that
  ignores extra kwargs is unaffected, but a strict-signature fake would need the kwarg.
- **R5 — Config write races.** Two panels debounce-POSTing (naming + wcs) use different dedicated
  routes/setters, each version-bumping atomically — no shared-block clobber (unlike the merged
  `ConfigPatchBody`). Confirmed safe by using the dedicated-route pattern.

---

## 9. Out of scope / follow-ups

- CaptureView live "WCS tagging" indicator (D6).
- Stamping NINA/remote-saved frames (D5).
- Distortion (SIP) terms — ASTAP can emit them; `WcsSolution` is linear-only today. Not needed
  for stacker hand-off; note as a future enrichment.
