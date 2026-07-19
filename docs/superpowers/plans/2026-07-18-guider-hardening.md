> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan. Dispatch each task to a fresh implementer subagent; each task is independently testable and reviewable. Implementers see ONLY their own task's Files/Interfaces/Steps — every signature a later task depends on is restated in that later task's Interfaces. Follow strict TDD: write the failing test exactly as given, run it and see the expected failure, implement, run and see the expected pass, then commit with the exact `git add` listed. NEVER `git add -A` (the repo root carries reviewer scratch — e.g. `drive_hero_flows.mjs`).

# Guider Hardening (Parity Wave, Sub-Project A) — Implementation Plan

**Goal.** Close the five ledgered native-guider follow-ups (spec `docs/superpowers/specs/2026-07-18-guider-hardening-design.md`) so the native guider can be trusted UNATTENDED on real hardware: a transient guide-camera exposure fault is absorbed by bounded retry (a persistent one dies loudly through the existing honest-death path); PPEC survives dithers (gear-time compensation, not model reset), reads a real wall-clock, and retains its trained window across sessions; a responsive-but-stuck Alpaca camera cannot hang the guide loop; and real rigs get true arcsec guide statistics from a guide-optics focal length. PPEC does NOT become a default in this sub-project (spec §1 non-goal).

**Architecture.** Unchanged from the native-guider program (spec §2): a pure, I/O-free Rust engine (`astro-guide`, `GuideEngine`) drives per-frame decisions; the Python host (`server/astrodeck/guide/native.py`, `NativeGuider`) owns camera exposure, mount pulse-guide, asyncio timing, bus events, cancellation, and persistence; PyO3 (`native/crates/astrodeck-native`) marshals the engine across the boundary. The guide-error stats stay on the SAME `GuideStats` bus channel. This wave adds: a Python retry envelope + Alpaca poll deadline (A1/A3, host-only); a Rust `dither_notify` trait hook + settle dead-reckoning + wall-clock clock source (A2, engine-internal, no PyO3 surface change); a Rust GP window dump/restore surface with PyO3 mirrors + Python persistence sidecar (A5); and a config field + backend scale computation + one UI field (A4).

**Tech stack.** Rust (workspace `native/`, `edition=2021`); `astro-guide` is Apache-2.0 (per-crate override; `docs/native-parity/rust-header-policy.md`), the rest of `native/` is MPL-2.0; PyO3 0.22 abi3-py311 + `numpy` 0.22 for the wheel; Python 3.12 FastAPI backend (`server/astrodeck`); React/TypeScript UI (`ui/`). Wheel built via `maturin develop --release -m native/crates/astrodeck-native/Cargo.toml` into `server/.venv`.

## Global Constraints

These bind EVERY task; copy them into each implementer's context.

- **Origin-map citation rule (spec §0, binding).** Each task's code comments cite its §0 origin finding by name, verbatim: A1 ← `final-branch-review I2`; A2 ← `P4-T1 review rulings A + C + finding M6`; A3 ← `final-branch-review tonight-risk #5`; A4 ← `P2-T3 review F2`; A5 ← `P4-T1 review concern ruling B`.
- **Upstream-literal rule + dual citations (Rust parity code).** Every Rust change that ports PHD2/MPI-IS-expressed logic is upstream-literal and carries DUAL citations in the code comment — the upstream `file:lines` AND the dossier `§` — matching the whole program's convention. The specific upstream sources this wave uses:
  - `GuidingDithered` gear-time compensation: `references/phd2/contributions/MPI_IS_gaussian_process/src/gaussian_process_guider.cpp:427-434` + dossier `docs/native-parity/algorithms/phd2-guiding.md §6.8.6`.
  - `reset()` (what dither does NOT do): same file `:408-425`.
  - `deduceResult` dead reckoning: same file `:372-406` + dossier §3.3 / §6.8.3.
  - `GuidingStarted` cross-session retention: **`references/phd2/src/guide_algorithm_gaussian_process.cpp:1017-1087`** + dossier §6.8.6. (Ambiguity resolved — see the note at the top of A-T5: the spec's input list attributed `GuidingStarted` to the MPI-IS `gaussian_process_guider.cpp`, but `GuidingStarted`/`noreset_max_pct_period` live in the PHD2 wrapper, not the MPI-IS contribution.)
- **Apache-2.0 SPDX headers (`docs/native-parity/rust-header-policy.md`).** Every `astro-guide` source file already carries the Apache-2.0 DERIVED header form (`SPDX-License-Identifier: Apache-2.0` + provenance paragraph naming the dossier §§ and PHD2 `file:lines`, `No code copied from PHD2.`). This wave EDITS existing `astro-guide` files (`algorithms/mod.rs`, `algorithms/gaussian_process.rs`, `engine.rs`) — do NOT rewrite their headers; only extend the provenance paragraph where a new upstream source (the GP wrapper `guide_algorithm_gaussian_process.cpp`) is first cited (A-T5). All new Rust tests land IN-MODULE in the files' existing `#[cfg(test)] mod tests` blocks, inheriting those headers — no new Rust files, no new headers. `astrodeck-native/src/lib.rs` is MPL-2.0 (PyO3 layer) — unchanged header.
- **Zero `unsafe`.** No `unsafe` anywhere in this wave (the `Send` supertrait already lets boxed algorithm objects cross `py.allow_threads` without it).
- **Four Rust gates (run all, from `native/`, for every task that touches Rust).** `cargo fmt --all` then `cargo fmt --all -- --check`; `cargo clippy --workspace --all-targets -- -D warnings`; `cargo test -p astro-guide` (**baseline 164 tests**; each Rust task states its expected delta); `cargo build --workspace`.
- **Wheel rebuild.** After ANY change to a crate the wheel bundles (this wave: A-T4 and A-T5 change `astro-guide` and A-T5 changes the PyO3 surface), rebuild before the Python tests: `maturin develop --release -m native/crates/astrodeck-native/Cargo.toml`.
- **Server suite baseline: 1184 passed / 0 failed** under default xdist (`-n auto`): `cd server && ./.venv/Scripts/python.exe -m pytest -q`. Each task states its expected delta. Native-wheel-gated test files (`pytestmark = skipif(not NATIVE_AVAILABLE)`) skip in a wheel-less run and only add to `passed` when the wheel is installed — each such task notes it.
- **UI pure-test idiom (for any UI test).** Manual harness (`test`/`assert`/`passed`/`failed`), run via `npx tsx src/lib/__tests__/<name>.test.ts` from `ui/`, ending with the `globalThis` `process.exit` guard — no `@types/node`:
  ```ts
  console.log(`<name>.test.ts: ${passed} passed, ${failed} failed`);
  if (failed) {
    failures.forEach((f) => console.error(f));
    (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
  }
  ```
- **Never `git add -A`.** Every commit lists exact files. Every commit message ends with a blank line then EXACTLY these two trailers:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL
  ```

**Task order (spec §6):** A-T1 (A3 timeout) → A-T2 (A1 retry, consumes A3's `DeviceError`) → A-T3 (A4 optics) → A-T4 (A2 dither/settle/clock bundle) → A-T5 (A5 retention) → A-T6 (acceptance gate + CI + docs).

---

### A-T1 — Alpaca imageready timeout (A3)

Origin: `final-branch-review tonight-risk #5` — the pre-existing `AlpacaCamera.expose` imageready poll has no overall timeout, so a responsive-but-stuck camera (driver answers `imageready=false` forever) hangs the guide loop indefinitely. Python only.

**Files**
- Modify `C:\Users\bear\astro\server\astrodeck\devices\alpaca.py` — add a module-level constant near the other Alpaca constants (`DISCOVERY_PORT`, ~:36); add the deadline to `AlpacaCamera.expose`'s imageready poll (:368-376).
- Create `C:\Users\bear\astro\server\tests\test_alpaca_imageready_timeout.py`.

**Interfaces**
- Produces (consumed by A-T2): `AlpacaCamera.expose` raises `astrodeck.devices.base.DeviceError("imageready timeout")` when the poll exceeds `exposure_s + _IMAGEREADY_POLL_MARGIN_S` (30 s). No behavior change on the happy path; the existing `asyncio.CancelledError` → `abort_exposure` path is preserved. Imaging contexts surface this via the existing `DeviceError` path; A-T2's guiding retry envelope catches it.
- Consumes: existing `DeviceError` (already imported in `alpaca.py` from `.base`), `time` (already imported).

**Steps**

- [ ] Write the failing test `C:\Users\bear\astro\server\tests\test_alpaca_imageready_timeout.py`:
  ```python
  """A3 (final-branch-review tonight-risk #5): AlpacaCamera.expose enforces an
  overall imageready poll deadline (exposure_s + 30 s) so a responsive-but-stuck
  camera cannot hang the guide loop forever."""
  import asyncio

  import pytest

  import astrodeck.devices.alpaca as alpaca
  from astrodeck.devices.alpaca import AlpacaCamera, AlpacaConnection
  from astrodeck.devices.base import DeviceError


  def _stub_camera() -> AlpacaCamera:
      cam = AlpacaCamera(AlpacaConnection("127.0.0.1", 11111), 0, "stub")
      cam.sensor_width = 640
      cam.sensor_height = 480
      cam.max_gain = 0
      return cam


  @pytest.mark.asyncio
  async def test_imageready_never_true_times_out(monkeypatch):
      # Zero margin so the deadline is exposure_s past start; a never-ready poll
      # must raise DeviceError("imageready timeout"), not loop forever.
      monkeypatch.setattr(alpaca, "_IMAGEREADY_POLL_MARGIN_S", 0.0)
      cam = _stub_camera()

      async def _put(method, **params):
          return None

      async def _get(method, **params):
          return False  # imageready is never true

      monkeypatch.setattr(cam, "_put", _put)
      monkeypatch.setattr(cam, "_get", _get)

      with pytest.raises(DeviceError, match="imageready timeout"):
          await cam.expose(0.01, 100, 30, binning=1)
      assert cam._exposing is False  # the finally: cleared it


  @pytest.mark.asyncio
  async def test_imageready_true_first_poll_no_timeout(monkeypatch):
      # Happy path unaffected: imageready true on the first poll returns a frame.
      monkeypatch.setattr(alpaca, "_IMAGEREADY_POLL_MARGIN_S", 30.0)
      cam = _stub_camera()

      async def _put(method, **params):
          return None

      async def _get(method, **params):
          return True

      async def _download_image():
          import numpy as np
          return np.zeros((480, 640), dtype=np.uint16)

      async def _temp():
          return -10.0

      monkeypatch.setattr(cam, "_put", _put)
      monkeypatch.setattr(cam, "_get", _get)
      monkeypatch.setattr(cam, "_download_image", _download_image)
      monkeypatch.setattr(cam, "get_temperature", _temp)

      frame = await cam.expose(0.01, 100, 30, binning=1)
      assert frame.data.shape == (480, 640)
      assert cam._exposing is False
  ```
- [ ] Run it, see it fail: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_alpaca_imageready_timeout.py -q` — expected failure: `AttributeError: module 'astrodeck.devices.alpaca' has no attribute '_IMAGEREADY_POLL_MARGIN_S'` (the constant does not exist yet).
- [ ] Implement. Add the constant next to the other module constants in `alpaca.py` (after `DISCOVERY_MSG = b"alpacadiscovery1"`, ~:37):
  ```python
  # A3 (final-branch-review tonight-risk #5): overall deadline for the
  # AlpacaCamera.expose imageready poll = exposure_s + this margin. Guards a
  # responsive-but-stuck camera (driver answers imageready=false forever) that
  # would otherwise hang the guide loop indefinitely; expiry raises
  # DeviceError("imageready timeout"), which the native guider's retry envelope
  # then handles in guiding contexts (imaging surfaces the DeviceError path).
  _IMAGEREADY_POLL_MARGIN_S = 30.0
  ```
  Current `expose` poll (`alpaca.py:368-376`):
  ```python
          self._exposing = True
          try:
              while not await self._get("imageready"):
                  await asyncio.sleep(min(0.5, max(0.1, seconds / 20)))
          except asyncio.CancelledError:
              await self.abort_exposure()
              raise
          finally:
              self._exposing = False
  ```
  Replace with:
  ```python
          self._exposing = True
          deadline = time.monotonic() + seconds + _IMAGEREADY_POLL_MARGIN_S
          try:
              while not await self._get("imageready"):
                  if time.monotonic() > deadline:
                      raise DeviceError("imageready timeout")
                  await asyncio.sleep(min(0.5, max(0.1, seconds / 20)))
          except asyncio.CancelledError:
              await self.abort_exposure()
              raise
          finally:
              self._exposing = False
  ```
- [ ] Run it, see it pass: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_alpaca_imageready_timeout.py -q` — 2 passed.
- [ ] Full suite (delta +2): `cd server && ./.venv/Scripts/python.exe -m pytest -q` → **1186 passed / 0 failed**.
- [ ] Commit:
  ```
  git add server/astrodeck/devices/alpaca.py server/tests/test_alpaca_imageready_timeout.py
  git commit -m "fix(devices/alpaca): A3 imageready poll deadline (exposure_s + 30s)

  final-branch-review tonight-risk #5: a responsive-but-stuck Alpaca camera can
  no longer hang the guide loop — expose() raises DeviceError(\"imageready
  timeout\") past the deadline, which the native guider's retry envelope handles.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

### A-T2 — Exposure retry/backoff envelope (A1)

Origin: `final-branch-review I2` — spec §4 was under-implemented: the first guide-camera hiccup was fatal (ranked tonight-risk #1). A transient exposure fault mid-guiding or mid-calibration must be absorbed without human intervention (bounded retry); a persistent fault must die loudly through the existing honest-death path (bus error, `guiding=False`, `is_active` False). Python only.

**Files**
- Modify `C:\Users\bear\astro\server\astrodeck\guide\native.py` — add module-level constants near `_REACQUIRE_BUDGET` (:90); a `_fault_frames` counter in `__init__` (:150-153) and its reset in `start_guiding` (:216-218); the retry loop inside `_expose` (:540-544); the fault-budget accounting in `_guide_loop` (:413-421).
- Modify `C:\Users\bear\astro\server\astrodeck\devices\sim.py` — import `DeviceError` (:17-29); add the `guide_expose_fail_next_n` knob to `SimGuideCamera` (:508-522) and the fault-injection check at the top of `SimGuideCamera.expose` (:537-540).
- Create `C:\Users\bear\astro\server\tests\test_native_guider_expose_retry.py`.

**Interfaces**
- Consumes (from A-T1): `AlpacaCamera.expose` may raise `DeviceError("imageready timeout")`; the retry envelope catches all `DeviceError`.
- Produces:
  - `NativeGuider._expose()` retries a failing `cam.expose` up to `_EXPOSE_RETRIES = 3` times with `_EXPOSE_BACKOFF_S = (0.5, 1.0, 2.0)` s between attempts, catching ONLY `DeviceError` (`asyncio.CancelledError` propagates immediately); on exhaustion it raises `DeviceError`.
  - `NativeGuider._guide_loop`: a fully-exhausted exposure counts one lost frame against `_fault_frames`; `_fault_frames >= _FAULT_FRAME_BUDGET = 5` (consecutive) stops the loop via the existing honest-death path (bus error names the device fault, `_lost=True`, `_active=False`, `_stop.set()`). A successful exposure resets `_fault_frames = 0`. The engine's own `_REACQUIRE_BUDGET` (8) for genuine star losses is UNCHANGED.
  - `NativeGuider._calibrate`: exposures also go through the retrying `_expose`; exhausted retries mid-leg raise `DeviceError` (never a raw camera exception), which propagates through `start_guiding` exactly like the existing calibration `DeviceError`s — the session survives.
  - `SimGuideCamera.guide_expose_fail_next_n: int = 0` — the next N `expose` calls raise `DeviceError("sim guide camera: injected exposure fault")`.

**Steps**

- [ ] Write the failing test `C:\Users\bear\astro\server\tests\test_native_guider_expose_retry.py`:
  ```python
  """A1 (final-branch-review I2): the native guider absorbs a transient
  guide-camera exposure fault via bounded retry+backoff, and dies loudly (honest
  death: _lost, not guiding) on a persistent fault. Drives the sim's
  guide_expose_fail_next_n knob; needs no native wheel (NativeGuider._expose /
  _guide_loop / _calibrate are pure Python over the sim camera)."""
  import pytest

  import astrodeck.guide.native as nativemod
  from astrodeck.devices.base import DeviceError
  from astrodeck.devices.sim import build_sim_rig
  from astrodeck.guide.native import NativeGuider


  def _guider(monkeypatch):
      monkeypatch.setattr(nativemod, "_EXPOSE_BACKOFF_S", (0.0, 0.0, 0.0))
      rig = build_sim_rig()
      cam, tel = rig["guide_camera"], rig["telescope"]
      return NativeGuider(cam, tel, config={"exposure_s": 0.05}, profile_id=None), cam, tel


  @pytest.mark.asyncio
  async def test_two_frame_fault_absorbed_by_retry(monkeypatch):
      guider, cam, tel = _guider(monkeypatch)
      await cam.connect()
      cam.guide_expose_fail_next_n = 2  # 2 faults then success -> absorbed in one _expose
      frame = await guider._expose()
      assert frame is not None
      assert cam.guide_expose_fail_next_n == 0  # fully consumed


  @pytest.mark.asyncio
  async def test_persistent_fault_exhausts_retries(monkeypatch):
      guider, cam, tel = _guider(monkeypatch)
      await cam.connect()
      cam.guide_expose_fail_next_n = 10_000  # every attempt faults
      with pytest.raises(DeviceError):
          await guider._expose()


  @pytest.mark.asyncio
  async def test_persistent_fault_honest_death(monkeypatch):
      guider, cam, tel = _guider(monkeypatch)
      await cam.connect()
      cam.guide_expose_fail_next_n = 10_000  # camera wedged: every exposure faults
      guider._active = True
      guider._stop.clear()
      await guider._guide_loop()  # returns when the fault budget trips honest death
      assert guider._lost is True
      assert guider._active is False
      assert guider.stats().guiding is False


  @pytest.mark.asyncio
  async def test_calibration_fault_aborts_with_deviceerror(monkeypatch):
      guider, cam, tel = _guider(monkeypatch)
      await cam.connect()
      await tel.connect()
      cam.guide_expose_fail_next_n = 10_000
      # _calibrate's first line is `await self._expose()`; an exhausted retry
      # raises DeviceError (a handled channel), never a raw camera exception —
      # so start_guiding's caller survives the calibration abort.
      with pytest.raises(DeviceError):
          await guider._calibrate()
  ```
- [ ] Run it, see it fail: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_native_guider_expose_retry.py -q` — expected failure: `AttributeError: module 'astrodeck.guide.native' has no attribute '_EXPOSE_BACKOFF_S'`.
- [ ] Implement the sim knob. In `sim.py`, add `DeviceError` to the `.base` import (:17):
  ```python
  from .base import (
      Camera,
      CameraFrame,
      DeviceError,
      FilterWheel,
      Focuser,
      PierSide,
      Rotator,
      SafetyMonitor,
      SafetyReading,
      Switch,
      SwitchPort,
      Telescope,
  )
  ```
  In `SimGuideCamera.__init__` (after `self.full_well = 65535`, ~:518) add:
  ```python
          # A1 (final-branch-review I2) fault-injection knob: the next N expose
          # calls raise DeviceError, driving the native guider's retry envelope
          # and honest-death budget in tests. 0 = no injected faults.
          self.guide_expose_fail_next_n = 0
  ```
  At the very top of `SimGuideCamera.expose` (before `self._abort.clear()`, :540) add:
  ```python
          if self.guide_expose_fail_next_n > 0:
              self.guide_expose_fail_next_n -= 1
              raise DeviceError("sim guide camera: injected exposure fault")
  ```
- [ ] Implement the retry envelope in `native.py`. Add constants after `_REACQUIRE_BUDGET = 8` (:90):
  ```python
  # A1 (final-branch-review I2): a transient guide-camera exposure fault is
  # absorbed by bounded retry+backoff around every guide/cal exposure; a
  # persistent one dies loudly through the existing honest-death path. Retries
  # after the first attempt, backoff seconds between attempts (one per retry),
  # and the consecutive-exhausted-frame budget that trips honest death.
  _EXPOSE_RETRIES = 3
  _EXPOSE_BACKOFF_S = (0.5, 1.0, 2.0)
  _FAULT_FRAME_BUDGET = 5
  ```
  In `__init__`, next to `self._reacquire = 0` (:153) add:
  ```python
          self._fault_frames = 0
  ```
  In `start_guiding`, next to `self._reacquire = 0` (:217) add:
  ```python
              self._fault_frames = 0
  ```
  Replace `_expose` (`native.py:540-544`):
  ```python
      async def _expose(self):
          frame = await self.cam.expose(
              self._exposure_s, self._gain, self._offset, binning=self._binning)
          self._last_frame = frame.data
          return frame
  ```
  with:
  ```python
      async def _expose(self):
          """Expose one guide frame, absorbing a transient camera fault (A1;
          final-branch-review I2): on DeviceError (incl. the A3 imageready
          timeout) retry up to _EXPOSE_RETRIES times with _EXPOSE_BACKOFF_S
          backoff. asyncio.CancelledError (a stop mid-exposure) is NOT retried —
          it propagates immediately. Exhausted retries raise DeviceError, which
          the guide loop counts as one lost frame and the calibration path
          surfaces as a clean calibration abort."""
          last_err: DeviceError | None = None
          for attempt in range(_EXPOSE_RETRIES + 1):
              try:
                  frame = await self.cam.expose(
                      self._exposure_s, self._gain, self._offset,
                      binning=self._binning)
                  self._last_frame = frame.data
                  return frame
              except DeviceError as e:
                  last_err = e
                  if attempt < _EXPOSE_RETRIES:
                      bus.log("warning",
                              f"native guider: guide exposure failed ({e}); "
                              f"retry {attempt + 1}/{_EXPOSE_RETRIES}", "guide")
                      await asyncio.sleep(_EXPOSE_BACKOFF_S[attempt])
          raise DeviceError(
              f"native guider: guide exposure failed after {_EXPOSE_RETRIES} "
              f"retries: {last_err}")
  ```
  Replace the `_guide_loop` `while` body's first two lines (`native.py:414-421`):
  ```python
              while not self._stop.is_set():
                  frame = await self._expose()
                  action = self._engine.process(
                      frame.data, frame.timestamp, self._exposure_s)
                  await self._dispatch(action)
                  self._sync_settle_window(action)
                  self._last_stats = self.stats()
                  bus.publish("guide", **self._last_stats.__dict__)
  ```
  with (the exposure now guarded by the fault budget):
  ```python
              while not self._stop.is_set():
                  try:
                      frame = await self._expose()
                  except DeviceError as e:
                      # A1 (final-branch-review I2): a fully-exhausted exposure is
                      # ONE lost frame. Consecutive exhaustions past the budget mean
                      # a wedged camera — die loudly through the existing honest-death
                      # path (bus error names the device fault; guiding=False;
                      # is_active goes false so the sequence engine's recovery sees
                      # it). The engine's own star-lost _REACQUIRE_BUDGET is unchanged.
                      self._fault_frames += 1
                      if self._fault_frames >= _FAULT_FRAME_BUDGET:
                          bus.log("error",
                                  f"native guider: guide camera fault — stopping "
                                  f"({e})", "guide")
                          self._lost = True
                          self._active = False
                          self._stop.set()
                          bus.publish("guide", **self.stats().__dict__)
                          break
                      bus.log("warning",
                              f"native guider: lost frame to camera fault "
                              f"({self._fault_frames}/{_FAULT_FRAME_BUDGET})", "guide")
                      continue
                  self._fault_frames = 0
                  action = self._engine.process(
                      frame.data, frame.timestamp, self._exposure_s)
                  await self._dispatch(action)
                  self._sync_settle_window(action)
                  self._last_stats = self.stats()
                  bus.publish("guide", **self._last_stats.__dict__)
  ```
- [ ] Run it, see it pass: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_native_guider_expose_retry.py -q` — 4 passed.
- [ ] Full suite (delta +4): `cd server && ./.venv/Scripts/python.exe -m pytest -q` → **1190 passed / 0 failed**.
- [ ] Commit:
  ```
  git add server/astrodeck/guide/native.py server/astrodeck/devices/sim.py server/tests/test_native_guider_expose_retry.py
  git commit -m "feat(guide/native): A1 exposure retry envelope + fault budget

  final-branch-review I2: a transient guide-camera exposure fault is absorbed by
  bounded retry (3x, 0.5/1/2s backoff); a persistent fault (5 consecutive
  exhausted frames) dies loudly via the existing honest-death path. Calibration
  exposures share the envelope and abort cleanly on exhaustion. Sim gains a
  guide_expose_fail_next_n fault-injection knob.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

### A-T3 — Guide optics scale (A4)

Origin: `P2-T3 review F2` — there is no principled `image_scale_arcsec` source on a real rig, so published RMS/error stats default-scale at 1.0 "/px (the arcsec BADGE is wrong; guiding correctness is unaffected — calibration measures px/ms empirically). Add a guide-optics focal length config field, compute the real scale in the native backend, and expose one UI field. Python + UI.

**Files**
- Modify `C:\Users\bear\astro\server\astrodeck\config.py` — add `guide_focal_length_mm` to the `Optics` model (:66-71).
- Modify `C:\Users\bear\astro\server\astrodeck\devices\backends\native_backend.py` — replace the F2 KNOWN GAP comment + construction with the real computation (:135-147).
- Create `C:\Users\bear\astro\server\tests\test_native_guide_optics_scale.py`.
- Modify `C:\Users\bear\astro\ui\src\types.ts` — add the optional field to the `Optics` interface (:466-472).
- Modify `C:\Users\bear\astro\ui\src\views\AtlasView.tsx` — add one numeric guide-scope field to the optics section, committed through the existing `commitOpticsPatch` (:294-310) pattern (mirror `commitPixel` at :315-324).
- Create `C:\Users\bear\astro\ui\src\lib\__tests__\guideOptics.test.ts`.

**Interfaces**
- Produces: `Optics.guide_focal_length_mm: float | None = None` (validated `> 0`, `<= 20000` when present). `NativeSession.native_guider()` passes `image_scale_arcsec = 206.265 * pixel_size_um / guide_focal_length_mm * binning` into the `NativeGuider` config when both the guide camera's `pixel_size_um` and the configured `guide_focal_length_mm` are positive; else the current 1.0 default. The guide loop is constructed at bin 1 (`binning = 1`).
- Consumes: `astrodeck.config.config_store.cfg().optics.guide_focal_length_mm`; the guide-camera device's `pixel_size_um` attribute. The UI reuses `ui/src/lib/optics.ts` `scale(fl, px, bin)` (already `206.265 * px * bin / fl`) for the live hint.

**Steps**

- [ ] Write the failing server test `C:\Users\bear\astro\server\tests\test_native_guide_optics_scale.py`:
  ```python
  """A4 (P2-T3 review F2): native_guider() computes a real image_scale_arcsec
  from Optics.guide_focal_length_mm + the guide camera's pixel_size_um, instead
  of the 1.0 default that mis-scales the arcsec badge on a real rig. Needs no
  native wheel (NATIVE_AVAILABLE is monkeypatched; NativeGuider constructs in
  pure Python)."""
  from types import SimpleNamespace

  import pytest

  import astrodeck.config as configmod
  import astrodeck.devices.backends.native_backend as nb
  from astrodeck.config import Optics


  class _FakeCam:
      pixel_size_um = 4.0


  class _FakeTel:
      pass


  def _session_with_optics(monkeypatch, guide_fl):
      monkeypatch.setattr(nb, "NATIVE_AVAILABLE", True, raising=False)
      cfg = SimpleNamespace(optics=Optics(focal_length_mm=530.0,
                                          guide_focal_length_mm=guide_fl))
      monkeypatch.setattr(configmod.config_store, "cfg", lambda: cfg)
      # native_guider reads NATIVE_AVAILABLE via `from ...providers import ...`;
      # also patch the source of truth so the import inside native_guider sees True.
      import astrodeck.providers as providers
      monkeypatch.setattr(providers, "NATIVE_AVAILABLE", True, raising=False)
      sess = nb.NativeSession("127.0.0.1")
      sess._devices["guide_camera"] = _FakeCam()
      sess._devices["telescope"] = _FakeTel()
      return sess


  def test_guide_scale_computed_from_focal_length(monkeypatch):
      sess = _session_with_optics(monkeypatch, guide_fl=200.0)
      guider = sess.native_guider()
      assert guider is not None
      # 206.265 * 4.0 / 200.0 * 1 (bin 1) = 4.1253 arcsec/px
      assert guider._image_scale == pytest.approx(206.265 * 4.0 / 200.0)


  def test_guide_scale_falls_back_to_default_when_unset(monkeypatch):
      sess = _session_with_optics(monkeypatch, guide_fl=None)
      guider = sess.native_guider()
      assert guider is not None
      assert guider._image_scale == 1.0  # the documented default when no guide FL
  ```
- [ ] Run it, see it fail: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_native_guide_optics_scale.py -q` — expected failure: `pydantic ... unexpected keyword argument 'guide_focal_length_mm'` (the `Optics` field does not exist yet) / `assert 1.0 == approx(4.1253)`.
- [ ] Implement the config field. In `config.py`, extend `Optics` (:66-71):
  ```python
  class Optics(BaseModel):
      focal_length_mm: float = Field(530.0, gt=0, le=20000)
      pixel_size_um: float = Field(0.0, ge=0, le=50)   # 0 = use camera
      sensor_width_px: int = Field(0, ge=0)            # 0 = use camera
      sensor_height_px: int = Field(0, ge=0)           # 0 = use camera
      auto_from_camera: bool = True
      # A4 (P2-T3 review F2): the GUIDE scope's focal length (mm), optional and
      # independent of the main imaging train's focal_length_mm above. When set
      # (with the guide camera's pixel_size_um) it gives the native guider a real
      # image_scale_arcsec so on-sky RMS is reported in true arcsec instead of the
      # 1.0 "/px badge default. Validated > 0 when present.
      guide_focal_length_mm: float | None = Field(default=None, gt=0, le=20000)
  ```
- [ ] Implement the backend computation. In `native_backend.py`, replace the F2 KNOWN GAP block + construction (:135-148):
  ```python
          # KNOWN GAP (P2-T3 fix round F2, ledgered): no ``image_scale_arcsec``
          # is passed, so published RMS/error stats default-scale at 1.0 "/px on
          # a real rig. ... (deleted) ...
          self._guider = NativeGuider(
              gcam, tel,
              config={"exposure_s": 2.0, **guide_algo_config()},
              profile_id=None)
          return self._guider
  ```
  with (the real computation; F2 CLOSED):
  ```python
          # A4 (P2-T3 review F2, CLOSED): compute a real image_scale_arcsec from
          # the configured guide-scope focal length + the guide camera's pixel
          # size, so on-sky RMS is reported in true arcsec. Falls back to 1.0
          # (the documented default, guiding correctness unaffected — calibration
          # measures px/ms empirically) when either input is missing. The guide
          # loop runs at bin 1, so binning = 1 here.
          image_scale = 1.0
          try:
              from ...config import config_store
              guide_fl = config_store.cfg().optics.guide_focal_length_mm
              px = getattr(gcam, "pixel_size_um", None)
              binning = 1
              if guide_fl and guide_fl > 0 and px and px > 0:
                  image_scale = 206.265 * float(px) / float(guide_fl) * binning
          except Exception:  # pragma: no cover - defensive; scale stays 1.0
              image_scale = 1.0
          self._guider = NativeGuider(
              gcam, tel,
              config={"exposure_s": 2.0, "image_scale_arcsec": image_scale,
                      **guide_algo_config()},
              profile_id=None)
          return self._guider
  ```
- [ ] Run the server test, see it pass: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_native_guide_optics_scale.py -q` — 2 passed.
- [ ] Write the failing UI test `C:\Users\bear\astro\ui\src\lib\__tests__\guideOptics.test.ts`:
  ```ts
  // guideOptics.test.ts — the guide-scope plate scale hint reuses lib/optics.ts
  // scale(fl, px, bin) = 206.265 * px * bin / fl (A4; the SAME arcsec-per-px
  // formula the native backend uses). Run with:
  //   npx tsx src/lib/__tests__/guideOptics.test.ts   (from ui/)
  import { scale } from "../optics";

  let passed = 0;
  let failed = 0;
  const failures: string[] = [];
  function test(name: string, fn: () => void): void {
    try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  }
  function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

  test("4um / 200mm guide scope -> 4.1253 arcsec/px", () => {
    assert(Math.abs(scale(200, 4.0) - 4.1253) < 1e-3, `got ${scale(200, 4.0)}`);
  });
  test("binning 2 doubles the guide scale", () => {
    assert(Math.abs(scale(200, 4.0, 2) - 8.2506) < 1e-3, `got ${scale(200, 4.0, 2)}`);
  });
  test("no focal length -> 0 (no hint)", () => {
    assert(scale(0, 4.0) === 0, "fl<=0 guards to 0");
  });

  console.log(`guideOptics.test.ts: ${passed} passed, ${failed} failed`);
  if (failed) {
    failures.forEach((f) => console.error(f));
    (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
  }
  ```
- [ ] Run it, see it pass immediately (it exercises the EXISTING `scale()` — this is the regression anchor for the guide-scale hint): `cd ui && npx tsx src/lib/__tests__/guideOptics.test.ts` → `3 passed, 0 failed`.
- [ ] Implement the UI field. In `ui/src/types.ts`, add to the `Optics` interface (:466-472):
  ```ts
  export interface Optics {
    focal_length_mm: number;
    pixel_size_um: number;
    sensor_width_px: number;
    sensor_height_px: number;
    auto_from_camera: boolean;
    guide_focal_length_mm?: number | null; // A4: optional guide-scope focal length (mm)
  }
  ```
  In `ui/src/views/AtlasView.tsx`, add — following the existing `commitPixel`/`commitOpticsPatch` pattern (:294-324) — a guide-scope focal-length control in the optics section. Local draft state initialized from `optics?.guide_focal_length_mm`, a blur/Enter committer, and a numeric input with a live hint from `scale(guideFl, guidePx)`:
  ```tsx
  // A4 (P2-T3 review F2): optional guide-scope focal length. Empty clears it
  // (server stores null); a value commits through the shared optics PUT.
  const [guideFocalDraft, setGuideFocalDraft] = useState(
    String(optics?.guide_focal_length_mm || ""),
  );
  useEffect(() => {
    setGuideFocalDraft(String(optics?.guide_focal_length_mm || ""));
  }, [optics?.guide_focal_length_mm]);
  const commitGuideFocal = useCallback(() => {
    const raw = guideFocalDraft.trim();
    const n = raw === "" ? null : Number(raw);
    if (n !== null && (!Number.isFinite(n) || n <= 0)) {
      setGuideFocalDraft(String(optics?.guide_focal_length_mm || ""));
      return;
    }
    if (n === (optics?.guide_focal_length_mm ?? null)) return;
    void commitOpticsPatch({ guide_focal_length_mm: n });
  }, [guideFocalDraft, optics?.guide_focal_length_mm, commitOpticsPatch]);
  ```
  and, next to the pixel-size field in the optics form JSX:
  ```tsx
  <label className="optics-field">
    <span>Guide scope focal length (mm)</span>
    <input
      type="number"
      inputMode="decimal"
      min="0"
      value={guideFocalDraft}
      placeholder="optional"
      onChange={(e) => setGuideFocalDraft(e.target.value)}
      onBlur={commitGuideFocal}
      onKeyDown={(e) => { if (e.key === "Enter") commitGuideFocal(); }}
    />
  </label>
  ```
- [ ] UI build check: `cd ui && npm run build` — clean.
- [ ] Full server suite (delta +2): `cd server && ./.venv/Scripts/python.exe -m pytest -q` → **1192 passed / 0 failed**.
- [ ] Commit:
  ```
  git add server/astrodeck/config.py server/astrodeck/devices/backends/native_backend.py server/tests/test_native_guide_optics_scale.py ui/src/types.ts ui/src/views/AtlasView.tsx ui/src/lib/__tests__/guideOptics.test.ts
  git commit -m "feat(config/ui): A4 guide-scope focal length -> real image_scale_arcsec

  P2-T3 review F2 CLOSED: Optics.guide_focal_length_mm (optional, >0) feeds
  native_guider() a real image_scale_arcsec = 206.265*px/fl*bin, so on-sky RMS is
  reported in true arcsec instead of the 1.0\"/px badge default. One optics-form
  field; falls back to 1.0 when unset (guiding correctness unaffected).

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

### A-T4 — PPEC dither compensation + settle dead-reckoning + wall-clock (A2)

Origin: `P4-T1 review rulings A + C + finding M6` — the bundle the reviewer mandated; the `dither_notify` hook is the reviewer's own minimal wiring. Today the engine RESETS PPEC on every dither (holding it in the reactive Hysteresis-blend fallback), settle-window dead-reckoning ownership is unresolved, and the GP clock is synthesized from accumulated exposure rather than the real frame timestamp. Rust engine-internal (no PyO3 surface change); wheel rebuild required.

**Files**
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\mod.rs` — add the defaulted `dither_notify` trait method after `result_with` (:50-53).
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\gaussian_process.rs` — override `dither_notify` in `impl GuideAlgorithm` (after `reset`, :565-567); update the two module-doc notes (:42-46, "not called by GuideEngine today") and the clock-model note (:29-37, "synthesized from … exposure `dt`") to reflect A2; add in-module parity tests.
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\engine.rs` — add the `gp_clock_dt` free fn + `last_gp_ts` field (struct :349, `new` :413, `reset_guiding_state` :495-515); wire `dither_notify` into `dither` (:1418-1420); make the settle dropped-frame path dead-reckon (`settle_monitor_dropped_frame` :900-915 + its two call sites :666-670, :726-728); feed `gp_clock_dt` to the two `compute_move` calls (:871, :883); add in-module tests.

**Interfaces**
- Produces (engine-internal; consumed by A-T5 and A-T6):
  - Trait method `fn dither_notify(&mut self, ra_amt_px: f64, ra_rate: f64) -> bool { let _ = (ra_amt_px, ra_rate); false }` (default: reactive algorithms; only `GaussianProcessGuider` overrides → applies `GuidingDithered` gear-time compensation and returns `true`).
  - `GuideEngine::dither` now skips the RA algorithm `reset()` when `dither_notify` returns `true` (Dec reset unchanged); `cal.x_rate` is the calibration-derived RA rate passed as `ra_rate`.
  - Lost-star frames during an open settle window run `deduce_result` (PPEC dead reckoning) exactly like ordinary lost frames.
  - The GP gear clock derives from frame `timestamp_s` deltas (guarded: non-monotone or `> 10× exposure_s` deltas fall back to `exposure_s`).
  - `process(frame, timestamp_s, exposure_s)` PyO3 signature UNCHANGED (`timestamp_s` was already threaded).
- Consumes: the existing `GaussianProcessGuider::guiding_dithered(amt, rate)` (:532-536) and `guiding_dither_settle_done` (:541-545); `Cal.x_rate`; `FrameMeta.timestamp_s` / `exposure_s`.

**Steps**

- [ ] Write the failing Rust tests. In `algorithms/gaussian_process.rs`, add to the existing `#[cfg(test)] mod tests` block (:588+):
  ```rust
      /// A2 (P4-T1 rulings A+C / M6; upstream GuidingDithered
      /// gaussian_process_guider.cpp:427-434, dossier §6.8.6): dither_notify
      /// applies the gear-time compensation (dither_offset += amt/rate) and
      /// PRESERVES the trained buffer — the whole point of A2 (no reset). It
      /// returns true so the engine skips ra_algo.reset().
      #[test]
      fn dither_notify_shifts_gear_time_and_preserves_buffer() {
          let mut gp = GaussianProcessGuider::new(GpParams::default());
          for _ in 0..15 {
              gp.result_with(1.0, 20.0, 5.0);
          }
          let n_before = gp.buffer.len();
          assert!(n_before > 10, "buffer trained: {n_before}");
          let off_before = gp.dither_offset;

          let handled = gp.dither_notify(3.0, 2.0); // amt=3px, rate=2px/s
          assert!(handled, "PPEC handles the dither (engine must skip reset)");
          assert!(
              (gp.dither_offset - (off_before + 3.0 / 2.0)).abs() < 1e-12,
              "gear time shifted by amt/rate"
          );
          assert!(gp.dithering_active, "dark-guiding window opened");
          assert_eq!(gp.dither_steps, MAX_DITHER_STEPS);
          assert_eq!(
              gp.buffer.len(),
              n_before,
              "dither_notify must NOT reset the trained buffer"
          );
      }

      /// A2: a reactive algorithm keeps the defaulted dither_notify (false, no
      /// state change) so the engine resets it on dither as before.
      #[test]
      fn default_dither_notify_is_false_for_reactive_algo() {
          let mut h = Hysteresis::default();
          assert!(!h.dither_notify(3.0, 2.0));
      }
  ```
  In `engine.rs`, add to its `#[cfg(test)] mod tests` block (:1579+):
  ```rust
      /// A2/M6 (finding M6): the GP gear clock advances on the real frame
      /// timestamp delta, guarding host clock jumps (non-monotone or > 10x
      /// exposure) by falling back to the exposure for that frame.
      #[test]
      fn gp_clock_dt_guards_host_clock_jumps() {
          // First frame (no previous timestamp) uses the exposure.
          assert_eq!(gp_clock_dt(None, 100.0, 5.0), 5.0);
          // A normal forward delta is used verbatim.
          assert_eq!(gp_clock_dt(Some(100.0), 105.0, 5.0), 5.0);
          // Non-monotone (clock went backwards) -> exposure fallback.
          assert_eq!(gp_clock_dt(Some(105.0), 100.0, 5.0), 5.0);
          // Absurd jump (> 10x exposure) -> exposure fallback.
          assert_eq!(gp_clock_dt(Some(100.0), 160.0, 5.0), 5.0);
          // At exactly 10x it is still accepted (boundary).
          assert_eq!(gp_clock_dt(Some(100.0), 150.0, 5.0), 50.0);
      }
  ```
- [ ] Run them, see them fail: `cd native && cargo test -p astro-guide dither_notify gp_clock_dt` — expected: `no method named 'dither_notify'` and `cannot find function 'gp_clock_dt' in this scope`.
- [ ] Implement the trait method. In `algorithms/mod.rs`, after `result_with` (:50-53) add:
  ```rust
      /// Notify a predictive algorithm of a dither so it compensates the
      /// gear-time gap IN PLACE (keeping its trained model) instead of being
      /// reset (A2; P4-T1 rulings A+C). `ra_amt_px` is the RA dither magnitude
      /// (px); `ra_rate` is the calibration-derived RA rate the engine holds.
      /// Returns `true` if the algorithm handled it and the engine must SKIP
      /// its `reset()`. Defaulted `false` (no-op) for every reactive algorithm;
      /// only the Gaussian-process predictor overrides it (upstream
      /// `GuidingDithered`, gaussian_process_guider.cpp:427-434, dossier §6.8.6).
      fn dither_notify(&mut self, ra_amt_px: f64, ra_rate: f64) -> bool {
          let _ = (ra_amt_px, ra_rate);
          false
      }
  ```
- [ ] Implement the GP override. In `gaussian_process.rs`, inside `impl GuideAlgorithm for GaussianProcessGuider`, after `reset` (:565-567) add:
  ```rust
      /// A2 (P4-T1 rulings A+C; upstream GuidingDithered
      /// gaussian_process_guider.cpp:427-434, dossier §6.8.6): compensate the
      /// dither's gear-time gap and KEEP the trained model. Returns true so the
      /// engine skips `reset()` on the RA axis.
      fn dither_notify(&mut self, ra_amt_px: f64, ra_rate: f64) -> bool {
          self.guiding_dithered(ra_amt_px, ra_rate);
          true
      }
  ```
  Also update the two module-doc notes so they no longer claim the wiring is absent: change the "**Engine integration scope**" paragraph (:42-46) so `guiding_dithered` is described as "called by [`crate::engine::GuideEngine::dither`] via [`GuideAlgorithm::dither_notify`] (A2)"; and the "**Clock model**" paragraph (:29-37) to note "the `time_step`/`dt` the engine passes is the guarded real frame-timestamp delta (finding M6), not accumulated exposure — the algorithm advances its clock by whatever `dt` it receives."
- [ ] Implement the engine changes in `engine.rs`.
  Add the free fn near the other module-level helpers (after `flip_parity`, :385):
  ```rust
  /// GP gear-clock dt (finding M6): the real inter-frame wall-clock delta from
  /// `timestamp_s`, guarding host clock jumps — a non-monotone or absurd
  /// (> 10x exposure) delta falls back to the exposure for that frame, so a host
  /// clock jump can't corrupt the synthesized GP clock. Only PPEC consumes this;
  /// reactive algorithms ignore the `dt` argument.
  fn gp_clock_dt(prev_ts: Option<f64>, now: f64, exposure_s: f64) -> f64 {
      match prev_ts {
          Some(p) if now - p > 0.0 && now - p <= 10.0 * exposure_s => now - p,
          _ => exposure_s,
      }
  }
  ```
  Add the field to `GuideEngine` (after `last_snr: f64,` :318, or alongside `secondaries`):
  ```rust
      /// Timestamp of the last accepted frame that advanced the GP gear clock
      /// (finding M6); `None` at a session boundary. Feeds `gp_clock_dt`.
      last_gp_ts: Option<f64>,
  ```
  Init in `new` (after `last_snr: 0.0,` :413):
  ```rust
              last_gp_ts: None,
  ```
  Reset in `reset_guiding_state` (after `self.last_snr = 0.0;` :507):
  ```rust
          self.last_gp_ts = None;
  ```
  Wire `dither_notify` into `dither`. Replace (:1418-1420):
  ```rust
          // GuidingDithered -> reset() (dossier §11.1/§11.2).
          self.ra_algo.reset();
          self.dec_algo.reset();
  ```
  with:
  ```rust
          // GuidingDithered (dossier §11.1/§11.2/§6.8.6): a predictive RA
          // algorithm (PPEC) compensates the gear-time gap IN PLACE and keeps
          // its trained model (dither_notify -> true, upstream
          // gaussian_process_guider.cpp:427-434); every reactive algorithm
          // returns false and is reset as before (upstream reset(), :408-425).
          // Dec is always reset. `cal.x_rate` is the calibration-derived RA rate
          // the engine already holds (A2; P4-T1 rulings A+C).
          if !self.ra_algo.dither_notify(dx_px, cal.x_rate) {
              self.ra_algo.reset();
          }
          self.dec_algo.reset();
  ```
  Make the settle dropped-frame path dead-reckon. Replace `settle_monitor_dropped_frame` (:900-915) — add a `dead_reckon: bool` param and run `deduce_move` on the non-failure branch when it is `true`:
  ```rust
      fn settle_monitor_dropped_frame(
          &mut self,
          now: f64,
          dec_guiding: bool,
          dead_reckon: bool,
      ) -> Action {
          let err = self.avg_dist.current_error(now, dec_guiding);
          let state = self
              .settle
              .as_mut()
              .expect("caller verified self.settle is Some")
              .evaluate(err, false, now);
          match state {
              SettleState::Failed(_) => {
                  self.settle = None;
                  self.recenter = None;
                  Action::LockLost
              }
              _ => {
                  // A2 (P4-T1 ruling C; upstream deduceResult
                  // gaussian_process_guider.cpp:372-406, dossier §3.3/§6.8.3):
                  // a lost-STAR frame during the settle window dead-reckons
                  // exactly like an ordinary lost frame, so PPEC keeps
                  // correcting periodic error through the settle. Non-predictive
                  // algorithms deduce 0.0 -> Idle -> the Settle wait signal
                  // (prior behavior). A mass-REJECT frame (dead_reckon == false)
                  // does not deduce: the measurement was rejected, not missing.
                  if dead_reckon {
                      let mv = self.deduce_move();
                      if !matches!(mv, Action::Idle) {
                          return mv;
                      }
                  }
                  Action::Settle
              }
          }
      }
  ```
  Update the `!found` settling call site (:668-670) to `dead_reckon = true`:
  ```rust
              if settling {
                  return self.settle_monitor_dropped_frame(now, dec_guiding, true);
              }
  ```
  Update the mass-reject settling call site (:726-728) to `dead_reckon = false`:
  ```rust
              if settling {
                  return self.settle_monitor_dropped_frame(now, dec_guiding, false);
              }
  ```
  Feed `gp_clock_dt` to the move pipeline. After `self.push_recent(now, mount.0, mount.1);` (:828) add:
  ```rust
          // M6 (finding M6): advance PPEC's gear clock on the real frame
          // timestamp delta, not accumulated exposure, guarding host clock jumps.
          let gp_dt = gp_clock_dt(self.last_gp_ts, now, meta.exposure_s);
          self.last_gp_ts = Some(now);
  ```
  In the settle dwell (step 6, :871), change `let a = self.compute_move(mount, s.snr, meta.exposure_s);` to `let a = self.compute_move(mount, s.snr, gp_dt);`. In the normal move (step 7, :883), change `self.compute_move(mount, s.snr, meta.exposure_s)` to `self.compute_move(mount, s.snr, gp_dt)`.
- [ ] Run the Rust tests, see them pass: `cd native && cargo test -p astro-guide dither_notify gp_clock_dt` — 3 passed.
- [ ] Run the four Rust gates: `cd native && cargo fmt --all && cargo fmt --all -- --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test -p astro-guide && cargo build --workspace`. **Expected `cargo test -p astro-guide`: 164 + 3 = 167 passed.**
- [ ] Rebuild the wheel: `maturin develop --release -m native/crates/astrodeck-native/Cargo.toml`.
- [ ] Write the failing Python gate third arm — append to `C:\Users\bear\astro\server\tests\test_native_guider_ppec.py` (reusing its `_VirtualClock`, `_calibrate`, `_guide_arm`, and gate constants):
  ```python
  async def _guide_arm_with_dither(cal: dict, clock, dither_at: int) -> float:
      """Guide a PPEC arm that DITHERS mid-run, returning the post-dither RA RMS
      (px) over the measurement window. Proves A2: the model SURVIVES the dither
      (RA=PPEC is not reset), so post-dither RMS still beats the reactive arm."""
      import astrodeck_native as native
      from astrodeck.devices.sim import build_sim_rig
      from astrodeck.guide.native import NativeGuider

      clock.vt = _VT_BASE
      rig = build_sim_rig()
      r = rig["_rig"]
      cam, tel = rig["guide_camera"], rig["telescope"]
      r.guide_scale_arcsec_px = _GUIDE_SCALE
      r.guide_pe_amplitude_px = _PE_AMPLITUDE_PX
      r.guide_pe_period_s = _PE_PERIOD_S
      r.guide_seeing_px = _SEEING_PX
      r.guide_drift_px_s = 0.0
      await cam.connect()
      await tel.connect()

      guider = NativeGuider(cam, tel, config={
          "ra_algorithm": "ppec", "image_scale_arcsec": _GUIDE_SCALE,
          "exposure_s": _RENDER_EXP_S}, profile_id=None)
      rates = await tel.guide_rates()
      eng = native.GuideEngine(guider._build_engine_config(rates))
      eng.load_calibration({k: v for k, v in cal.items()
                            if k != "image_scale_arcsec"})
      eng.begin_guiding()

      errs: list[float] = []
      dithered = False
      window_start = _LEARN_FRAMES + _MAX_DITHER_SETTLE
      for i in range(_LEARN_FRAMES + _MAX_DITHER_SETTLE + _MEASURE_FRAMES):
          clock.vt += _LOGICAL_DT_S
          frame = await cam.expose(_RENDER_EXP_S, 100, 30, binning=1)
          if i == dither_at and not dithered:
              pre = eng.dump_gp_window() if hasattr(eng, "dump_gp_window") else None
              eng.dither(3.0, 0.0)  # RA-axis dither; PPEC must NOT reset
              dithered = True
          a = eng.process(frame.data, frame.timestamp, _LOGICAL_DT_S)
          k = a["action"]
          if k == "pulse":
              await tel.pulse_guide(a["dir"], int(a["ms"]))
          elif k == "pulse_pair":
              if a.get("ra"):
                  await tel.pulse_guide(a["ra"]["dir"], int(a["ra"]["ms"]))
              if a.get("dec"):
                  await tel.pulse_guide(a["dec"]["dir"], int(a["dec"]["ms"]))
          rec = eng.stats().get("recent", [])
          if rec and i >= window_start:
              errs.append(float(rec[-1][1]))
      assert not eng.stats()["settling"], "settle window closed before the window"
      assert errs, "no post-dither measurement frames recorded"
      return math.sqrt(sum(e * e for e in errs) / len(errs))


  @pytest.mark.asyncio
  async def test_ppec_survives_mid_run_dither(monkeypatch):
      """A2 GATE third arm (spec §3 A2 / §5): PPEC dithered mid-run recovers to
      beat reactive Hysteresis on the SAME injected PE — proving the model was
      compensated, not reset. Deterministic (virtual clock); <= ~40 s added."""
      import astrodeck.devices.sim as simmod
      from astrodeck.devices.sim import build_sim_rig

      clock = _VirtualClock()
      monkeypatch.setattr(simmod, "time", clock)

      # Shared quiet-sky calibration (PE off), reused by both arms.
      clock.vt = _VT_BASE
      rig = build_sim_rig()
      r = rig["_rig"]
      cam, tel = rig["guide_camera"], rig["telescope"]
      r.guide_scale_arcsec_px = _GUIDE_SCALE
      r.guide_pe_amplitude_px = 0.0
      r.guide_seeing_px = 0.05
      r.guide_drift_px_s = 0.0
      await cam.connect()
      await tel.connect()
      cal = await _calibrate(
          cam, tel,
          {"image_scale_arcsec": _GUIDE_SCALE, "exposure_s": _RENDER_EXP_S,
           "ra_algorithm": "ppec"},
          clock,
      )

      hyst_rms = await _guide_arm("hysteresis", cal, clock)
      ppec_dither_rms = await _guide_arm_with_dither(cal, clock,
                                                     dither_at=_LEARN_FRAMES // 2)

      assert ppec_dither_rms < 0.8 * hyst_rms, (
          f"post-dither PPEC must still beat hysteresis (model survived the "
          f"dither): ppec={ppec_dither_rms:.4f}px hyst={hyst_rms:.4f}px "
          f"ratio={ppec_dither_rms / hyst_rms:.3f}")
      assert ppec_dither_rms < 1.5, f"ppec arm did not reconverge: {ppec_dither_rms:.4f}px"
  ```
  And add the two new constants next to the existing gate constants (:67-76):
  ```python
  _MAX_DITHER_SETTLE = 20     # frames (~100 engine-s) for the dither to settle out
  ```
- [ ] Run it, see it pass (wheel installed): `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_native_guider_ppec.py -q` — 4 passed (the 3 existing + the new third arm). Confirm determinism by running twice: identical result.
- [ ] Verify the M6 change did NOT perturb the existing gate: `test_ppec_beats_hysteresis_on_injected_pe` still passes (the virtual clock's `timestamp_s` advances by exactly `_LOGICAL_DT_S` each frame, so `gp_clock_dt` == the exposure, byte-identical to the prior exposure-accumulation). Run the whole existing native guider suite: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_native_guider_e2e.py tests/test_native_guider_dither.py tests/test_native_guider_recovery.py tests/test_native_guider_algorithms.py -q` — all pass unchanged (non-PPEC paths are byte-identical: `dither_notify` returns false → reset as before; settle deduce yields Idle → Settle as before).
- [ ] Full server suite (delta +1 with wheel; native-gated file already in the suite): `cd server && ./.venv/Scripts/python.exe -m pytest -q` → **1193 passed / 0 failed** (with the wheel; the new arm is inside the already-gated `test_native_guider_ppec.py`).
- [ ] Commit:
  ```
  git add native/crates/astro-guide/src/algorithms/mod.rs native/crates/astro-guide/src/algorithms/gaussian_process.rs native/crates/astro-guide/src/engine.rs server/tests/test_native_guider_ppec.py
  git commit -m "feat(astro-guide): A2 PPEC dither compensation + settle deduce + wall-clock

  P4-T1 rulings A+C / finding M6: dither_notify hook lets PPEC compensate the
  gear-time gap (GuidingDithered) instead of resetting on dither; lost-star
  settle frames dead-reckon (deduceResult) like ordinary lost frames; the GP
  gear clock now advances on guarded real frame-timestamp deltas, not
  accumulated exposure. Gate gains a mid-run-dither third arm proving the model
  survives. Reactive algorithms and the existing gate are byte-identical.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

### A-T5 — GP cross-session retention (A5)

Origin: `P4-T1 review concern ruling B` — §6.8.6 `GuidingStarted` retention, deferred as out-of-brief then, in-scope now. Upstream PHD2 retains a trained PPEC model across a guiding stop/start; AstroDeck retrains from zero every session. Rust (dump/restore + retention) + PyO3 mirrors + Python persistence sidecar. Wheel rebuild required.

**Ambiguity resolved (record in the commit + code comments).** The spec's input list attributed `GuidingStarted`/retention to `references/phd2/contributions/MPI_IS_gaussian_process/src/gaussian_process_guider.cpp`. That file has NO `GuidingStarted` — the retention logic and `noreset_max_pct_period` (default 40) live in the PHD2 WRAPPER `references/phd2/src/guide_algorithm_gaussian_process.cpp:1017-1087` (`GuidingStarted`: retain iff same pier side and `|worm_offset| < retain_pct/100·P`). The cross-session, on-disk context has no live pier/RA offset to read, so the upstream check is ADAPTED (DERIVED, not upstream-literal) to a buffer-trim rule the spec pins: **keep the newest points spanning at most `retain_max_pct_period` (40%) of one period of gear time**, and gate restoration in Python on the calibration reuse-gate result (same profile + same calibration) in place of upstream's pier-side check. Cite the wrapper `:1017-1087` + dossier §6.8.6 for the retain-pct constant and reset-vs-retain intent; mark the buffer-trim as the DERIVED cross-session adaptation.

**Ambiguity resolved (dump tuple shape).** The spec sketched `dump_gp_window() -> Vec<(t, y, w)>`-style. A faithful round-trip of the `DataPoint` buffer that `update_gp` reconstructs from needs the CONTROL too (the cumulative-control sum builds the gear error, `gaussian_process.rs update_gp`), so this implements a 4-tuple `(timestamp, measurement, variance, control)` — the "-style" sketch amplified to the four fields the buffer actually carries. Documented in the method doc.

**Files**
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\mod.rs` — add defaulted `dump_gp_window`/`restore_gp_window` trait methods (after A-T4's `dither_notify`).
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\gaussian_process.rs` — add inherent `dump_window`/`restore_window` on `GaussianProcessGuider`; override the two trait methods; extend the provenance header to cite the GP wrapper; add in-module tests.
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\engine.rs` — add `dump_gp_window`/`restore_gp_window` passthroughs near `calibration()` (:1549).
- Modify `C:\Users\bear\astro\native\crates\astrodeck-native\src\lib.rs` — add `dump_gp_window`/`restore_gp_window` `#[pymethods]` on the `GuideEngine` pyclass (after `load_calibration`, :1460-1464).
- Modify `C:\Users\bear\astro\server\astrodeck\guide\native.py` — add `_GP_RETAIN_PCT_PERIOD` constant; `_persist_gp_window`/`_load_gp_window`/`_restore_gp_window`; call persist in `stop_guiding` (:296-310); call restore in `start_guiding`'s reuse block (after `reused = True`, ~:263); extend `clear_calibration` (:711-733) to remove BOTH files.
- Create `C:\Users\bear\astro\server\tests\test_native_guider_gp_retention.py` (Python file-round-trip; no wheel — uses a fake engine stub).

**Interfaces**
- Consumes (from A-T4): the `GuideAlgorithm` trait already carries `dither_notify`; PPEC is trained through `result_with`.
- Produces (consumed by A-T6):
  - Trait: `fn dump_gp_window(&self) -> Vec<(f64, f64, f64, f64)> { Vec::new() }` and `fn restore_gp_window(&mut self, points: &[(f64, f64, f64, f64)], retain_pct: f64) { let _ = (points, retain_pct); }` (defaults: non-predictive; only PPEC overrides).
  - `GuideEngine::dump_gp_window(&self) -> Vec<(f64,f64,f64,f64)>`; `GuideEngine::restore_gp_window(&mut self, &[(f64,f64,f64,f64)], f64)`.
  - PyO3 `GuideEngine.dump_gp_window() -> list[[t, m, v, c]]` (empty for a non-PPEC RA algorithm or an untrained model); `GuideEngine.restore_gp_window(points, retain_pct=40.0)`.
  - Python `NativeGuider`: persists the trained window to `CONFIG_DIR/guider/<profile-id>-gp.json` on `stop_guiding`; restores it on `start_guiding` ONLY on the calibration-reuse path (same profile + same calibration); `clear_calibration()` removes both `<profile-id>.json` AND `<profile-id>-gp.json` (one clear = both files). A corrupt `-gp.json` logs a warning and starts fresh. `_GP_RETAIN_PCT_PERIOD = 40.0`.

**Steps**

- [ ] Write the failing Rust tests. In `gaussian_process.rs` `mod tests`:
  ```rust
      /// A5 (P4-T1 ruling B; upstream GuidingStarted
      /// guide_algorithm_gaussian_process.cpp:1017-1087, dossier §6.8.6):
      /// dump_window round-trips the trained buffer; restore_window rebuilds it
      /// with 100% retention (a full period is far wider than the trained span).
      #[test]
      fn dump_window_roundtrips_the_trained_buffer() {
          let mut gp = GaussianProcessGuider::new(GpParams::default());
          for _ in 0..20 {
              gp.result_with(1.0, 20.0, 5.0);
          }
          let dumped = gp.dump_window();
          assert_eq!(dumped.len(), gp.buffer.len());

          let mut gp2 = GaussianProcessGuider::new(GpParams::default());
          gp2.restore_window(&dumped, 100.0);
          assert_eq!(gp2.buffer.len(), dumped.len(), "full retention keeps all");
          let (t0, m0, v0, c0) = dumped[0];
          assert!((gp2.buffer[0].timestamp - t0).abs() < 1e-12);
          assert!((gp2.buffer[0].measurement - m0).abs() < 1e-12);
          assert!((gp2.buffer[0].variance - v0).abs() < 1e-12);
          assert!((gp2.buffer[0].control - c0).abs() < 1e-12);
      }

      /// A5: 40% retention keeps only the NEWEST points within 0.40*period of
      /// gear time; the oldest are discarded (upstream retain_max_pct_period=40).
      #[test]
      fn restore_window_retains_newest_pct_of_period() {
          // Hand-built window spanning 300 s at the default period P=200 s.
          // horizon = 0.40 * 200 = 80 s; cutoff = 300 - 80 = 220 s. Points at
          // 0,20,...,300 (16 points): kept are t in [220, 300] -> 220..300 step
          // 20 = {220,240,260,280,300} = 5 points.
          let window: Vec<(f64, f64, f64, f64)> = (0..=15)
              .map(|i| (i as f64 * 20.0, 0.1, 1.0, 0.0))
              .collect();
          let mut gp = GaussianProcessGuider::new(GpParams::default());
          gp.restore_window(&window, 40.0);
          assert_eq!(gp.buffer.len(), 5, "kept newest 80s (0.4*200) of 300s");
          assert!((gp.buffer.front().unwrap().timestamp - 220.0).abs() < 1e-12);
          assert!((gp.buffer.back().unwrap().timestamp - 300.0).abs() < 1e-12);
      }

      /// A5: an empty or seed-only window leaves a fresh model (nothing to trust).
      #[test]
      fn restore_empty_window_is_a_noop() {
          let mut gp = GaussianProcessGuider::new(GpParams::default());
          let n0 = gp.buffer.len();
          gp.restore_window(&[], 40.0);
          assert_eq!(gp.buffer.len(), n0);
          gp.restore_window(&[(0.0, 0.0, 0.0, 0.0)], 40.0); // single point < 2
          assert_eq!(gp.buffer.len(), n0);
      }

      /// A5: a reactive algorithm dumps an empty window (no model to persist).
      #[test]
      fn default_dump_window_is_empty_for_reactive_algo() {
          let h = Hysteresis::default();
          assert!(h.dump_gp_window().is_empty());
      }
  ```
- [ ] Run them, see them fail: `cd native && cargo test -p astro-guide dump_window restore_window default_dump_window` — expected: `no method named 'dump_window'`, etc.
- [ ] Implement the trait defaults in `algorithms/mod.rs` (after A-T4's `dither_notify`):
  ```rust
      /// Serialize the trained predictive-model window for cross-session
      /// persistence (A5; dossier §6.8.6). Each row is
      /// `(timestamp, measurement, variance, control)`. Non-predictive
      /// algorithms hold no model — default empty; only PPEC overrides.
      fn dump_gp_window(&self) -> Vec<(f64, f64, f64, f64)> {
          Vec::new()
      }

      /// Restore a persisted predictive-model window with `GuidingStarted`
      /// retention (A5; keep the newest `retain_pct`% of one period). Default
      /// no-op; only PPEC overrides.
      fn restore_gp_window(&mut self, points: &[(f64, f64, f64, f64)], retain_pct: f64) {
          let _ = (points, retain_pct);
      }
  ```
- [ ] Implement the GP inherent methods + overrides in `gaussian_process.rs`. Extend the file header's provenance paragraph to add the wrapper source for the retention logic, e.g. append to the "Derived from PHD2 …" list: `and src/guide_algorithm_gaussian_process.cpp:1017-1087 (GuidingStarted cross-session model retention, dossier §6.8.6)`. Add the inherent methods in `impl GaussianProcessGuider` (near `guiding_dither_settle_done`, :541-545):
  ```rust
      /// Serialize the trained window for cross-session persistence (A5; P4-T1
      /// ruling B; upstream model retention around GuidingStarted,
      /// guide_algorithm_gaussian_process.cpp:1017-1087, dossier §6.8.6). Each
      /// row is `(timestamp, measurement, variance, control)` — the four
      /// DataPoint fields `update_gp` reconstructs the GP from (control is
      /// required for the cumulative gear-error sum; the spec's `(t,y,w)` sketch
      /// is amplified to these four). Includes the seed; an untrained guider
      /// dumps just it.
      pub fn dump_window(&self) -> Vec<(f64, f64, f64, f64)> {
          self.buffer
              .iter()
              .map(|p| (p.timestamp, p.measurement, p.variance, p.control))
              .collect()
      }

      /// Restore a persisted window from `dump_window`, applying retention (A5;
      /// dossier §6.8.6): keep only the NEWEST points spanning at most
      /// `retain_pct`% of one period of gear time — the DERIVED cross-session
      /// adaptation of upstream's `|worm_offset| < retain_pct/100·P` retain rule
      /// (the on-disk context has no live pier/RA offset; Python gates
      /// restoration on the calibration reuse-gate instead). An empty or
      /// seed-only window (or one that trims below 2 points) leaves a fresh
      /// model. Continues the synthesized clock from the newest retained point.
      pub fn restore_window(&mut self, points: &[(f64, f64, f64, f64)], retain_pct: f64) {
          if points.len() < 2 {
              return;
          }
          let t_max = points.last().map(|p| p.0).unwrap_or(0.0);
          let horizon = (retain_pct / 100.0).max(0.0) * self.period_length();
          let cutoff = t_max - horizon;
          let kept: VecDeque<DataPoint> = points
              .iter()
              .filter(|p| p.0 >= cutoff)
              .map(|&(timestamp, measurement, variance, control)| DataPoint {
                  timestamp,
                  measurement,
                  variance,
                  control,
              })
              .collect();
          if kept.len() < 2 {
              return;
          }
          self.buffer = kept;
          // Continue the synthesized clock from the newest retained point so the
          // next set_timestamp advances coherently (module clock model). Dither
          // state and the prediction anchor reset; the learned period (kernel)
          // and gear-error periodicity are what retention preserves.
          self.wall = t_max;
          self.start_wall = 0.0;
          self.last_wall = t_max;
          self.first_since_start = false;
          self.last_prediction_end = -1.0;
          self.dither_offset = 0.0;
          self.dither_steps = 0;
          self.dithering_active = false;
      }
  ```
  Override the trait methods in `impl GuideAlgorithm for GaussianProcessGuider` (after A-T4's `dither_notify`):
  ```rust
      fn dump_gp_window(&self) -> Vec<(f64, f64, f64, f64)> {
          self.dump_window()
      }

      fn restore_gp_window(&mut self, points: &[(f64, f64, f64, f64)], retain_pct: f64) {
          self.restore_window(points, retain_pct);
      }
  ```
- [ ] Implement the engine passthroughs in `engine.rs` (near `calibration()`, :1549):
  ```rust
      /// Dump the RA algorithm's trained GP window for cross-session persistence
      /// (A5; dossier §6.8.6). Empty for every non-PPEC RA algorithm.
      pub fn dump_gp_window(&self) -> Vec<(f64, f64, f64, f64)> {
          self.ra_algo.dump_gp_window()
      }

      /// Restore a persisted GP window into the RA algorithm with retention
      /// (A5; dossier §6.8.6). No-op for non-PPEC.
      pub fn restore_gp_window(&mut self, points: &[(f64, f64, f64, f64)], retain_pct: f64) {
          self.ra_algo.restore_gp_window(points, retain_pct);
      }
  ```
- [ ] Run the four Rust gates: `cd native && cargo fmt --all && cargo fmt --all -- --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test -p astro-guide && cargo build --workspace`. **Expected `cargo test -p astro-guide`: 167 + 4 = 171 passed.**
- [ ] Implement the PyO3 surface in `astrodeck-native/src/lib.rs`, `#[pymethods] impl GuideEngine`, after `load_calibration` (:1460-1464):
  ```rust
      /// Dump the RA algorithm's trained GP window (A5, dossier §6.8.6) for
      /// cross-session persistence: a list of `[timestamp, measurement,
      /// variance, control]` rows (empty for a non-PPEC RA algorithm or an
      /// untrained model). Serializable beside the calibration.
      fn dump_gp_window<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
          let out = PyList::empty_bound(py);
          for (t, m, v, c) in self.inner.dump_gp_window() {
              out.append(PyList::new_bound(py, [t, m, v, c]))?;
          }
          Ok(out)
      }

      /// Restore a persisted GP window (from `dump_gp_window`) into the RA
      /// algorithm, applying `GuidingStarted` retention (keep the newest
      /// `retain_pct`% of one period; A5, dossier §6.8.6). No-op for a non-PPEC
      /// RA algorithm.
      #[pyo3(signature = (points, retain_pct=40.0))]
      fn restore_gp_window(&mut self, points: Vec<(f64, f64, f64, f64)>, retain_pct: f64) {
          self.inner.restore_gp_window(&points, retain_pct);
      }
  ```
- [ ] Rebuild the wheel: `maturin develop --release -m native/crates/astrodeck-native/Cargo.toml`. Smoke-check the surface: `cd server && ./.venv/Scripts/python.exe -c "import astrodeck_native as n; e=n.GuideEngine({'ra_algorithm':'ppec','image_scale_arcsec':0.5}); print(e.dump_gp_window()); e.restore_gp_window([], 40.0); print('ok')"`.
- [ ] Implement the Python persistence in `native.py`. Add the constant near the other module constants (after `_UNKNOWN_DECLINATION`, :100):
  ```python
  # A5 (P4-T1 ruling B; dossier §6.8.6): retain the newest 40% of one period of
  # the trained PPEC gear-time model across a stop/start, per profile.
  _GP_RETAIN_PCT_PERIOD = 40.0
  ```
  In `stop_guiding`, before the final `bus.publish`/`bus.log` (after the settle-waiter unblock, ~:309):
  ```python
          self._persist_gp_window()  # A5: save the trained PPEC model on stop
  ```
  In `start_guiding`'s reuse block, after `reused = True` and its `bus.log` (~:263-265), add:
  ```python
                      # A5 (P4-T1 ruling B): restore the persisted PPEC model
                      # window ONLY on the calibration-REUSE path (same profile +
                      # same calibration). A fresh calibration means the geometry
                      # changed, so the trained gear-time model no longer applies.
                      # No-op for a non-PPEC RA algorithm.
                      self._restore_gp_window()
  ```
  Add the three methods (near the persistence block, after `_load_persisted_calibration`, :764):
  ```python
      def _persist_gp_window(self) -> None:
          """Persist the trained PPEC gear-time model to
          ``CONFIG_DIR/guider/<profile>-gp.json`` on guiding stop (A5, dossier
          §6.8.6). Best-effort; nothing to save for a non-PPEC RA algorithm or an
          untrained model (dump returns empty / seed-only)."""
          if not self.profile_id or self._engine is None:
              return
          try:
              window = self._engine.dump_gp_window()
              if not window or len(window) < 2:
                  return
              from ..config import CONFIG_DIR
              d = CONFIG_DIR / "guider"
              d.mkdir(parents=True, exist_ok=True)
              (d / f"{self.profile_id}-gp.json").write_text(
                  json.dumps(window), encoding="utf-8")
              bus.log("info",
                      f"native guider: saved PPEC model for profile "
                      f"{self.profile_id}", "guide")
          except Exception as e:  # pragma: no cover - best effort
              bus.log("warning",
                      f"native guider: could not persist PPEC model: {e}", "guide")

      def _load_gp_window(self) -> list | None:
          """Read this profile's persisted GP window
          (``CONFIG_DIR/guider/<profile>-gp.json``) as a list of
          ``(t, measurement, variance, control)`` tuples, or None when absent /
          corrupt. Never raises — a corrupt file logs and yields None (fresh
          model), mirroring the calibration-persistence hardening."""
          if not self.profile_id:
              return None
          try:
              from ..config import CONFIG_DIR
              p = CONFIG_DIR / "guider" / f"{self.profile_id}-gp.json"
              if not p.exists():
                  return None
              data = json.loads(p.read_text(encoding="utf-8"))
              if not isinstance(data, list):
                  bus.log("warning",
                          f"native guider: persisted GP window for profile "
                          f"{self.profile_id} is not a JSON array; ignoring",
                          "guide")
                  return None
              return [(float(t), float(m), float(v), float(c))
                      for t, m, v, c in data]
          except Exception as e:  # pragma: no cover - defensive
              bus.log("warning",
                      f"native guider: could not read persisted GP window "
                      f"({e}); starting fresh", "guide")
              return None

      def _restore_gp_window(self) -> None:
          """Restore the persisted PPEC model into the live engine on the
          calibration-reuse path (A5). No-op for a non-PPEC RA algorithm or when
          there is no persisted window."""
          if not self.profile_id or self._engine is None:
              return
          points = self._load_gp_window()
          if not points:
              return
          try:
              self._engine.restore_gp_window(points, _GP_RETAIN_PCT_PERIOD)
              bus.log("info",
                      f"native guider: restored PPEC model for profile "
                      f"{self.profile_id}", "guide")
          except Exception as e:  # pragma: no cover - defensive
              bus.log("warning",
                      f"native guider: could not restore PPEC model ({e}); "
                      f"starting fresh", "guide")
  ```
  Extend `clear_calibration` (:711-733) to remove both files (one clear = both):
  ```python
      def clear_calibration(self) -> bool:
          """Delete this profile's persisted calibration AND its persisted PPEC
          model (``<profile>.json`` + ``<profile>-gp.json``) so the NEXT
          ``start_guiding`` drives a fresh calibration walk and a fresh model
          (dossier §8.4/§6.8.6). Best-effort and non-fatal (used by
          ``DELETE /api/guide/calibration``); returns True when a file was
          removed. Does not disturb an in-flight guide loop."""
          if not self.profile_id:
              return False
          removed = False
          try:
              from ..config import CONFIG_DIR
              d = CONFIG_DIR / "guider"
              for name in (f"{self.profile_id}.json", f"{self.profile_id}-gp.json"):
                  p = d / name
                  if p.exists():
                      p.unlink()
                      removed = True
              if removed:
                  bus.log("info",
                          f"native guider: cleared persisted calibration + PPEC "
                          f"model for profile {self.profile_id}", "guide")
          except Exception as e:  # pragma: no cover - best effort
              bus.log("warning",
                      f"native guider: could not clear calibration: {e}", "guide")
          return removed
  ```
- [ ] Write the failing Python file-round-trip test `C:\Users\bear\astro\server\tests\test_native_guider_gp_retention.py` (no wheel — a fake engine stub; the Rust retention math is covered by the Rust goldens above, and the full-integration restore→prediction quality is the A-T6 e2e):
  ```python
  """A5 (P4-T1 ruling B): NativeGuider persists/loads the PPEC model window
  beside the calibration, and clear_calibration removes BOTH files. File-level
  round-trip with a fake engine stub (the Rust dump/restore math is covered by
  astro-guide goldens; the real engine restore->prediction e2e is the unattended
  night gate)."""
  import json

  import astrodeck.config as configmod
  from astrodeck.guide.native import NativeGuider


  class _FakeEngine:
      def __init__(self, window):
          self._window = window
          self.restored = None

      def dump_gp_window(self):
          return self._window

      def restore_gp_window(self, points, retain_pct):
          self.restored = (points, retain_pct)


  def _guider(tmp_path, monkeypatch, window):
      monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)
      g = NativeGuider.__new__(NativeGuider)  # bypass __init__ (no devices needed)
      g.profile_id = "prof1"
      g._engine = _FakeEngine(window)
      return g


  def test_persist_then_load_roundtrip(tmp_path, monkeypatch):
      window = [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, -0.05]]
      g = _guider(tmp_path, monkeypatch, window)
      g._persist_gp_window()
      p = tmp_path / "guider" / "prof1-gp.json"
      assert p.exists()
      assert json.loads(p.read_text()) == window
      loaded = g._load_gp_window()
      assert loaded == [(0.0, 0.1, 1.0, 0.0), (5.0, 0.2, 1.0, -0.05)]


  def test_restore_calls_engine_with_retain_pct(tmp_path, monkeypatch):
      window = [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, -0.05]]
      g = _guider(tmp_path, monkeypatch, window)
      g._persist_gp_window()
      g._restore_gp_window()
      assert g._engine.restored is not None
      points, pct = g._engine.restored
      assert pct == 40.0
      assert points[0] == (0.0, 0.1, 1.0, 0.0)


  def test_untrained_or_empty_window_not_persisted(tmp_path, monkeypatch):
      g = _guider(tmp_path, monkeypatch, [[0.0, 0.0, 0.0, 0.0]])  # seed-only (<2)
      g._persist_gp_window()
      assert not (tmp_path / "guider" / "prof1-gp.json").exists()


  def test_corrupt_gp_file_is_ignored(tmp_path, monkeypatch):
      g = _guider(tmp_path, monkeypatch, [])
      d = tmp_path / "guider"
      d.mkdir(parents=True, exist_ok=True)
      (d / "prof1-gp.json").write_text("{not json", encoding="utf-8")
      assert g._load_gp_window() is None  # logged + fresh model, never raises


  def test_clear_calibration_removes_both_files(tmp_path, monkeypatch):
      g = _guider(tmp_path, monkeypatch, [[0.0, 0.1, 1.0, 0.0], [5.0, 0.2, 1.0, 0.0]])
      d = tmp_path / "guider"
      d.mkdir(parents=True, exist_ok=True)
      (d / "prof1.json").write_text("{}", encoding="utf-8")
      g._persist_gp_window()
      assert (d / "prof1-gp.json").exists()
      assert g.clear_calibration() is True
      assert not (d / "prof1.json").exists()
      assert not (d / "prof1-gp.json").exists()
  ```
- [ ] Run it, see it pass: `cd server && ./.venv/Scripts/python.exe -m pytest tests/test_native_guider_gp_retention.py -q` — 5 passed.
- [ ] Full server suite (delta +5): `cd server && ./.venv/Scripts/python.exe -m pytest -q` → **1198 passed / 0 failed** (with the wheel; the retention Python test is wheel-free).
- [ ] Commit:
  ```
  git add native/crates/astro-guide/src/algorithms/mod.rs native/crates/astro-guide/src/algorithms/gaussian_process.rs native/crates/astro-guide/src/engine.rs native/crates/astrodeck-native/src/lib.rs server/astrodeck/guide/native.py server/tests/test_native_guider_gp_retention.py
  git commit -m "feat(astro-guide+guide/native): A5 cross-session PPEC model retention

  P4-T1 ruling B: GuideEngine.dump_gp_window/restore_gp_window (PyO3-mirrored)
  serialize the trained GP window with GuidingStarted retention (keep the newest
  40% of one period). NativeGuider persists it to <profile>-gp.json on stop,
  restores it on start ONLY on the calibration-reuse path, and one Clear removes
  both files. Corrupt window -> log + fresh model. GuidingStarted lives in the
  PHD2 wrapper guide_algorithm_gaussian_process.cpp (spec cite corrected).

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

### A-T6 — Acceptance gate + CI wiring + docs

Origin: spec §5 (CI-wired "unattended night" acceptance gate) + §6 (docs touch). One deterministic e2e proves the whole sub-project on the closed-loop sim; the CI native job runs it; the two docs' deferred-item lines flip to closed.

**Files**
- Create `C:\Users\bear\astro\server\tests\test_native_guider_unattended_night.py`.
- Modify `C:\Users\bear\astro\.github\workflows\ci.yml` — add the new file to the native job's single pytest line (:84).
- Modify `C:\Users\bear\astro\docs\native-parity\native-guider.md` — flip the three deferred items (PPEC dither-compensation, cross-session retention, guide-optics scale) to closed (:157-182).
- Modify `C:\Users\bear\astro\docs\guide\guiding.md` — update the PPEC paragraph + retention sentence (:40-43, :64-68).

**Interfaces**
- Consumes: A-T2's retry envelope (`NativeGuider._expose`, `guide_expose_fail_next_n`); A-T4's `GuideEngine.dither` (PPEC non-reset) + wall-clock; A-T5's `GuideEngine.dump_gp_window`/`restore_gp_window` + `NativeGuider._persist_gp_window`/`_restore_gp_window`; the sim's virtual-clock idiom from `test_native_guider_ppec.py`.
- Produces: the CI-wired sub-project acceptance gate.

**Per-phase acceptance gate — the "unattended night" scenario (spec §5).** On the closed-loop sim, deterministic under a virtual clock: (1) PPEC guides and TRAINS; (2) an injected 2-exposure camera fault mid-run is ABSORBED by the retry envelope (guiding continues; no honest death); (3) a mid-run RA dither does NOT reset PPEC (the trained window survives — `dump_gp_window` stays populated, `stats()["settling"]` opens then closes); (4) a STOP persists the model to disk and a fresh START on the SAME calibration RESTORES it (retention proven by immediate post-restart RA RMS beating a from-scratch PPEC over the same early window). Assertions: fault fully consumed; post-dither window count > 10 (non-reset); `-gp.json` exists after stop; restored window > 10 points; `restored_rms < scratch_rms`.

**Steps**

- [ ] Write the failing e2e `C:\Users\bear\astro\server\tests\test_native_guider_unattended_night.py`:
  ```python
  """Sub-project A acceptance gate (spec §5): the UNATTENDED NIGHT scenario on
  the closed-loop sim, deterministic under a virtual clock. Exercises A1 (fault
  absorption via NativeGuider._expose), A2 (PPEC survives a mid-run dither), and
  A5 (stop persists + start restores the trained model). CI-wired (native job)."""
  import math

  import pytest

  import astrodeck.config as configmod
  import astrodeck.devices.sim as simmod
  import astrodeck.guide.native as nativemod
  from astrodeck.devices.sim import build_sim_rig
  from astrodeck.guide.native import NativeGuider
  from astrodeck.providers import NATIVE_AVAILABLE

  pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")

  # Mirror the PPEC gate's time-compression (test_native_guider_ppec.py).
  import time as _realtime

  _LOGICAL_DT_S = 5.0
  _PE_PERIOD_S = 200.0
  _PE_AMPLITUDE_PX = 4.0
  _SEEING_PX = 0.1
  _GUIDE_SCALE = 0.5
  _RENDER_EXP_S = 0.1
  _LEARN_FRAMES = 100
  _SETTLE_FRAMES = 20
  _MEASURE_FRAMES = 40
  _VT_BASE = 1000.0


  class _VirtualClock:
      def __init__(self):
          self.vt = _VT_BASE

      def time(self):
          return self.vt

      def __getattr__(self, name):
          return getattr(_realtime, name)


  def _rms(errs):
      return math.sqrt(sum(e * e for e in errs) / len(errs)) if errs else 0.0


  async def _calibrate(cam, tel, engine_cfg, clock):
      import astrodeck_native as native
      eng = native.GuideEngine(engine_cfg)
      clock.vt += _LOGICAL_DT_S
      frame = await cam.expose(_RENDER_EXP_S, 100, 30, binning=1)
      stars, _ = native.guide_star_find(frame.data)
      assert stars
      eng.begin_calibration(float(stars[0]["x"]), float(stars[0]["y"]))
      _ra, dec_deg = await tel.get_position()
      pier = (await tel.pier_side()).value
      eng.set_scope_pointing(math.radians(float(dec_deg)), pier, "unknown",
                             "unknown", 0.0, 1)
      for _ in range(4000):
          clock.vt += _LOGICAL_DT_S
          frame = await cam.expose(_RENDER_EXP_S, 100, 30, binning=1)
          a = eng.process(frame.data, frame.timestamp, _LOGICAL_DT_S)
          if a["action"] == "cal_step":
              await tel.pulse_guide(a["dir"], int(a["ms"]))
              continue
          cal = eng.dump_calibration()
          if cal and cal.get("is_valid"):
              return cal
      raise AssertionError("calibration did not complete")


  async def _new_ppec_guider(clock, cal, tmp_path, profile_id):
      import astrodeck_native as native
      rig = build_sim_rig()
      r = rig["_rig"]
      cam, tel = rig["guide_camera"], rig["telescope"]
      r.guide_scale_arcsec_px = _GUIDE_SCALE
      r.guide_pe_amplitude_px = _PE_AMPLITUDE_PX
      r.guide_pe_period_s = _PE_PERIOD_S
      r.guide_seeing_px = _SEEING_PX
      r.guide_drift_px_s = 0.0
      await cam.connect()
      await tel.connect()
      g = NativeGuider(cam, tel, config={
          "ra_algorithm": "ppec", "image_scale_arcsec": _GUIDE_SCALE,
          "exposure_s": _RENDER_EXP_S}, profile_id=profile_id)
      rates = await tel.guide_rates()
      g._engine = native.GuideEngine(g._build_engine_config(rates))
      g._engine.load_calibration({k: v for k, v in cal.items()
                                  if k != "image_scale_arcsec"})
      g._engine.begin_guiding()
      g._active = True
      return g, cam, tel


  async def _drive(g, cam, tel, clock, n, *, fault_at=None, dither_at=None,
                   record_from=None):
      errs = []
      for i in range(n):
          clock.vt += _LOGICAL_DT_S
          if fault_at is not None and i == fault_at:
              cam.guide_expose_fail_next_n = 2  # A1: transient 2-exposure fault
          if dither_at is not None and i == dither_at:
              g._engine.dither(3.0, 0.0)  # A2: RA dither, PPEC must NOT reset
          frame = await g._expose()  # A1 retry envelope absorbs the fault
          a = g._engine.process(frame.data, frame.timestamp, _LOGICAL_DT_S)
          k = a["action"]
          if k == "pulse":
              await tel.pulse_guide(a["dir"], int(a["ms"]))
          elif k == "pulse_pair":
              if a.get("ra"):
                  await tel.pulse_guide(a["ra"]["dir"], int(a["ra"]["ms"]))
              if a.get("dec"):
                  await tel.pulse_guide(a["dec"]["dir"], int(a["dec"]["ms"]))
          rec = g._engine.stats().get("recent", [])
          if rec and (record_from is None or i >= record_from):
              errs.append(float(rec[-1][1]))
      return errs


  @pytest.mark.asyncio
  async def test_unattended_night(monkeypatch, tmp_path):
      clock = _VirtualClock()
      monkeypatch.setattr(simmod, "time", clock)
      monkeypatch.setattr(nativemod, "_EXPOSE_BACKOFF_S", (0.0, 0.0, 0.0))
      monkeypatch.setattr(configmod, "CONFIG_DIR", tmp_path)

      # Shared quiet-sky calibration (PE off), reused by every run below.
      clock.vt = _VT_BASE
      rig = build_sim_rig()
      r = rig["_rig"]
      cam0, tel0 = rig["guide_camera"], rig["telescope"]
      r.guide_scale_arcsec_px = _GUIDE_SCALE
      r.guide_pe_amplitude_px = 0.0
      r.guide_seeing_px = 0.05
      r.guide_drift_px_s = 0.0
      await cam0.connect()
      await tel0.connect()
      cal = await _calibrate(
          cam0, tel0,
          {"image_scale_arcsec": _GUIDE_SCALE, "exposure_s": _RENDER_EXP_S,
           "ra_algorithm": "ppec"},
          clock)

      # --- Run 1: train + absorb a camera fault + dither (no reset) ---
      clock.vt = _VT_BASE
      g1, cam1, tel1 = await _new_ppec_guider(clock, cal, tmp_path, "unattended")
      await _drive(g1, cam1, tel1, clock, _LEARN_FRAMES,
                   fault_at=_LEARN_FRAMES // 3)
      assert cam1.guide_expose_fail_next_n == 0, "A1: 2-exposure fault absorbed"
      assert g1._lost is False and g1._active is True, "guiding survived the fault"

      pre = g1._engine.dump_gp_window()
      assert len(pre) > 10, "PPEC trained before the dither"
      await _drive(g1, cam1, tel1, clock, _SETTLE_FRAMES, dither_at=0)
      post = g1._engine.dump_gp_window()
      assert len(post) > 10, "A2: dither did NOT reset the trained model"
      assert g1._engine.stats()["settling"] is False, "settle window closed"

      # --- Stop: persist the trained model to disk ---
      await g1.stop_guiding()
      gp_file = tmp_path / "guider" / "unattended-gp.json"
      assert gp_file.exists(), "A5: PPEC model persisted on stop"

      # --- Run 2: fresh engine, SAME calibration, restore the model ---
      clock.vt = _VT_BASE
      g2, cam2, tel2 = await _new_ppec_guider(clock, cal, tmp_path, "unattended")
      g2._restore_gp_window()
      assert len(g2._engine.dump_gp_window()) > 10, "A5: retention restored a window"
      restored_errs = await _drive(g2, cam2, tel2, clock, _MEASURE_FRAMES)
      restored_rms = _rms(restored_errs)

      # --- Baseline: fresh PPEC, NO retention, same early window ---
      clock.vt = _VT_BASE
      g3, cam3, tel3 = await _new_ppec_guider(clock, cal, tmp_path, "scratch")
      scratch_errs = await _drive(g3, cam3, tel3, clock, _MEASURE_FRAMES)
      scratch_rms = _rms(scratch_errs)

      assert restored_rms < scratch_rms, (
          f"A5: retention must give immediate post-restart prediction quality "
          f"(restored={restored_rms:.4f}px vs scratch={scratch_rms:.4f}px)")
  ```
- [ ] Rebuild the wheel if not current, then run it: `maturin develop --release -m native/crates/astrodeck-native/Cargo.toml && cd server && ./.venv/Scripts/python.exe -m pytest tests/test_native_guider_unattended_night.py -q` — 1 passed. Run twice to confirm determinism (identical result; no run-to-run variance). If the retention margin is tight on this box, note the observed `restored_rms`/`scratch_rms` in the commit body (the assertion is a strict inequality; the virtual clock makes it reproducible).
- [ ] Wire CI. In `.github/workflows/ci.yml`, the native job's single pytest line (:84) is currently:
  ```
          cd server && pytest tests/test_native_module.py tests/test_autofocus_native.py tests/test_polar_native.py tests/test_native_guide_surface.py tests/test_native_guider_e2e.py tests/test_native_guider_dither.py tests/test_native_guider_recovery.py tests/test_native_guider_algorithms.py tests/test_native_guider_ppec.py -q
  ```
  Edit it to append the new gate (the A-T5 retention Python test is wheel-free and runs in the `server` job, so it needs no native-job entry):
  ```
          cd server && pytest tests/test_native_module.py tests/test_autofocus_native.py tests/test_polar_native.py tests/test_native_guide_surface.py tests/test_native_guider_e2e.py tests/test_native_guider_dither.py tests/test_native_guider_recovery.py tests/test_native_guider_algorithms.py tests/test_native_guider_ppec.py tests/test_native_guider_unattended_night.py -q
  ```
- [ ] Update the docs. In `docs/native-parity/native-guider.md`, the three deferred items (:157-182) flip to closed:
  - Replace the "**PPEC dither-compensation wiring.**" bullet (:157-170) with a closed note: the engine now compensates the gear-time gap on dither (`GuidingDithered` via `dither_notify`) instead of resetting, settle-window lost-star frames dead-reckon (`deduceResult`), and the GP gear clock reads guarded real frame-timestamp deltas (finding M6) — closed in sub-project A (parity wave); PPEC remaining opt-in is a separate default-on decision requiring on-sky evidence.
  - Replace the "**Cross-session GP model retention …**" bullet (:171-175) with a closed note: the trained model persists to `<profile>-gp.json` on stop and restores on start on the calibration-reuse path with `GuidingStarted` 40%-of-period retention — closed in sub-project A.
  - Replace the "**Real-rig `image_scale_arcsec` needs an `Optics.guide_focal_length_mm` config field.**" bullet (:176-182) with a closed note: `Optics.guide_focal_length_mm` now feeds a real `image_scale_arcsec`; closed in sub-project A. (Leave the sim pier-flip / ST-4 / adaptive-BLC / NINA-rig items untouched — spec §1 non-goals.)
  In `docs/guide/guiding.md`, update the PPEC paragraph (:64-68) to note PPEC now survives dithers (gear-time compensation, not reset) and retains its trained model across a stop/start on a compatible calibration; and confirm the retention sentence at :40-43 still reads correctly (calibration AND, now, the GP model persist per profile).
- [ ] Full server suite: `cd server && ./.venv/Scripts/python.exe -m pytest -q` → **1199 passed / 0 failed** (with the wheel; the e2e is native-gated). Sanity: `grep -n unattended_night .github/workflows/ci.yml` shows the file in the native list.
- [ ] Commit:
  ```
  git add server/tests/test_native_guider_unattended_night.py .github/workflows/ci.yml docs/native-parity/native-guider.md docs/guide/guiding.md
  git commit -m "test(guide): A6 unattended-night acceptance gate + CI wiring + docs

  spec §5/§6: one deterministic closed-loop e2e proves the sub-project — PPEC
  trains, absorbs an injected 2-exposure camera fault (A1), survives a mid-run
  dither without model reset (A2), and a stop/start restores the trained model
  (A5) with immediate post-restart prediction quality. Added to the CI native
  job. native-guider.md's three deferred items (PPEC dither, retention, guide
  scale) and guiding.md's PPEC notes flip to closed.

  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
  ```

---

## Self-review (performed before commit; fixes folded in above)

1. **Spec coverage.** A1 → A-T2; A2 (dither compensation + settle dead-reckoning + wall-clock M6 + gate third arm) → A-T4; A3 → A-T1; A4 (config + backend + UI) → A-T3; A5 (Rust dump/restore + retention + Python sidecar + clear-both + corrupt→fresh) → A-T5; §5 acceptance gate + CI + §6 docs → A-T6. Every §3 item and the §5 gate maps to a task.
2. **Placeholder scan.** No TBD/TODO/"handle edge cases"/"similar to task N"; every code step carries the code and every constant is named with its value (`_IMAGEREADY_POLL_MARGIN_S=30.0`, `_EXPOSE_RETRIES=3`, `_EXPOSE_BACKOFF_S=(0.5,1.0,2.0)`, `_FAULT_FRAME_BUDGET=5`, `guide_expose_fail_next_n=0`, `_GP_RETAIN_PCT_PERIOD=40.0`, `retain_pct=40.0`).
3. **Type/signature consistency.** `dither_notify(&mut self, ra_amt_px: f64, ra_rate: f64) -> bool` identical in the trait (mod.rs) and the GP override; `dump_gp_window(&self) -> Vec<(f64,f64,f64,f64)>` / `restore_gp_window(&mut self, &[(f64,f64,f64,f64)], f64)` identical in trait, GP, engine, and PyO3 (as `list[[t,m,v,c]]` / `(points, retain_pct=40.0)`); config field `guide_focal_length_mm` identical in `config.py`, `native_backend.py`, `types.ts`, and the tests; persistence file `CONFIG_DIR/guider/<profile-id>-gp.json` identical in persist/load/clear and the gate.
4. **Quoted current code verified** against the real files read this session (`native.py` `_expose`/`_guide_loop`/`clear_calibration`; `alpaca.py` expose poll; `engine.rs` `dither`/`settle_monitor_dropped_frame`/`compute_move` call sites; `gaussian_process.rs` `guiding_dithered`; `lib.rs` `load_calibration`; `config.py` `Optics`; `sim.py` `SimGuideCamera`; `ci.yml` native line).
