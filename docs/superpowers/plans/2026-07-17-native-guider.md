> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan. Dispatch each task to a fresh implementer subagent; each task is independently testable and reviewable. Implementers see ONLY their own task's Files/Interfaces/Steps — every signature a later task depends on is restated in that later task. Follow strict TDD: write the failing test exactly as given, run it and see the expected failure, implement, run and see the expected pass, then commit with the exact `git add` listed. NEVER `git add -A` (the repo root carries reviewer scratch — e.g. `drive_hero_flows.mjs`, `AstroDeck-review3-codex.md`).

# Native Guider (in-house PHD2 replacement) — Implementation Plan

**Goal.** Ship AstroDeck's in-process, cross-platform native guider — a pure-Rust guiding engine (`astro-guide`) driven by a Python I/O loop (`guide/native.py`) — that guides an Alpaca/native/sim rig all night with RMS comparable to PHD2 defaults, calibrates, dithers+settles under the sequence engine's existing contract, survives star-lost, handles meridian flips, persists calibration, and implements the FULL algorithm suite (hysteresis+lowpass RA, resist-switch Dec, Z-filter, calibration, dither/settle, star-lost recovery, multi-star tracking, GP/PPEC), with PHD2 remaining a per-profile selectable fallback.

**Architecture.** Approach A (spec §2, decision D1): a new pure `astro-guide` crate is a synchronous, I/O-free state machine — exactly the proven `astro-focus`/`focus/native.py` shape (`native/crates/astro-focus/src/sweep.rs` `FocusSweep` is the template). The crate ingests a guide frame's measured star offsets and emits abstract corrections (`Action`); Python (`server/astrodeck/guide/native.py`, following `server/astrodeck/focus/native.py`) owns camera exposure, mount pulse-guide, asyncio timing, bus events, cancellation, and persistence. The engine degrades cleanly when the wheel is absent (the `NATIVE_AVAILABLE` guarded-import idiom, `providers.py:50-54`). A closed-loop simulator (`SimGuideCamera` coupled to `SimTelescope`) is the e2e acceptance harness for every algorithm.

**Tech stack.** Rust (workspace `native/`, `edition=2021`, `rust-version=1.85`, `license=MPL-2.0`); `nalgebra` for the GP linear algebra (Eigen does not travel — licensing report §3); PyO3 0.22 abi3-py311 + `numpy` 0.22 for the wheel (`native/crates/astrodeck-native`); Python 3.11 FastAPI backend (`server/astrodeck`); React/TypeScript UI (`ui/`). Built via `maturin develop --release` into `server/.venv`.

## Global Constraints (spec §0/§1 binding requirements — verbatim intent)

These bind every task; copy them into each implementer's context:

- **Full-suite v1 scope** (spec §0 USER-DECIDED): hysteresis + lowpass (RA), resist-switch (Dec), Z-filter, calibration, dither+settle, star-lost recovery, MULTI-STAR tracking, and the predictive PEC (Gaussian-process) algorithm.
- **Native-first, PHD2 fallback** (spec §0): the native guider becomes the default provider; the existing `guide/phd2.py` bridge stays selectable per-profile during maturing, then retires (the NINA pattern). NINA-mode rigs keep guiding through `NinaGuider`/PHD2 unchanged (D5) — the native guider targets Alpaca/native/sim rigs where AstroDeck owns the devices.
- **Pure-Rust engine / Python owns I/O** (D1): the crate is synchronous, I/O-free, no async, no globals, no GUI-toolkit dependencies, no external processes, no camera-loop machinery in Rust. Python owns ALL I/O.
- **Same GuideStats bus channel** (spec §3.2/§3.5): `NativeGuider` publishes the SAME `GuideStats` shape on the SAME `"guide"` bus channel so MonitorView/Sparkline/SessionsPanel/GuideView work unchanged.
- **Pulse-guide-only output** (D2): correction output v1 = mount pulse-guide only (Alpaca `PulseGuide`, sim nudge). ST-4-through-guide-camera is deferred; the engine emits abstract corrections either way.
- **Static-BLC-only** (D4): static backlash compensation ships in v1 (dossier §7/§10.1); the ADAPTIVE BLC controller (dossier §10.2) stays off/deferred until a backlash-measurement tool exists.
- **Alpaca/native/sim rigs only** (D5): a NINA rig lets NINA own its guider; mixing AstroDeck-native guiding with NINA-owned imaging is out of scope.
- **guide_camera = Camera device role** (D6): the guide camera is any `Camera` device assigned to a new `guide_camera` RigSpec role. No new device class; the existing `Camera` ABC (`expose → CameraFrame`) is sufficient at guide cadences (0.5–5 s).
- **Apache-2.0 crate headers + BSD-3 derivation notes** (spec §0 license resolution + §3.1/§3.6, AMENDED 2026-07-17 after the user chose Apache-2.0 as the project license): every `astro-guide` source file carries the Apache-2.0 header (`SPDX-License-Identifier: Apache-2.0`); every file whose logic derives from PHD2 additionally names its PHD2 source file + dossier § in the header. `astro-guide`'s Cargo.toml sets `license = "Apache-2.0"` (per-crate override of the workspace field). MPL-2.0 remains ONLY on the existing NINA/Hocus-Focus-derived files in astro-star/astro-focus/astro-tppa — do not touch those. The root LICENSE (Apache-2.0) exists; P0-T1's THIRD-PARTY-NOTICES obligations are unchanged, and any plan step below that says "MPL-2.0 header" for a NEW astro-guide file must be read as "Apache-2.0 header".
- **THIRD-PARTY-NOTICES content per spec §3.6**: full BSD-3-Clause text + per-component copyright table (PHD2 main-tree team + named holders; GP/PPEC Max Planck Society + Klenske paper citation); MPL-2.0 statement for `native/`; NINA/Hocus-Focus attribution relocated in.
- **Cross-platform Win/Linux/Mac**: no GUI deps, no external processes; the Windows dev box runs the suites, CI covers Linux (platform-conditional notes called out per task).

**Naming that MUST be spelled identically across all tasks** (self-review pinned these):
- Crate package `astro-guide`, lib crate `astro_guide`.
- Engine: `GuideEngine`; per-frame `GuideEngine::ingest(&mut self, meta: &FrameMeta, measured: &[MeasuredStar]) -> Action`.
- `Action` enum variants EXACTLY: `Idle`, `Pulse { axis: Axis, dir: Direction, ms: u32 }`, `PulsePair { ra: Option<AxisPulse>, dec: Option<AxisPulse> }`, `CalStep { leg: CalLeg, dir: Direction, ms: u32 }`, `Settle`, `LockLost`.
- `Axis { Ra, Dec }`; `Direction { North, South, East, West }`; `AxisPulse { dir: Direction, ms: u32 }`.
- `GuideAlgorithm` trait: `fn result(&mut self, input: f64) -> f64; fn reset(&mut self); fn deduce_result(&mut self) -> f64 { 0.0 }; fn min_move(&self) -> f64;`.
- PyO3 class `GuideEngine`, method `process(frame, timestamp_s, exposure_s) -> dict`; PyO3 fn `guide_star_find(frame, params=None) -> (list, dict)`.

**Build/test commands** (run foreground; long commands may auto-background — verify or re-run foreground, never park waiting):
- Rust: `cd native && cargo test -p astro-guide` (expect the new crate's suites green; `cargo test --workspace` for cross-crate).
- Wheel: `maturin develop --release -m native/crates/astrodeck-native/Cargo.toml` (installs `astrodeck_native` into `server/.venv`).
- Server: `cd server && ./.venv/Scripts/python.exe -m pytest -q`. **Baseline at plan time: 1127 passed + 1 known local-only failure (`test_polar_native`, a GPU/native-fixture skip on this box).** Each task states its expected delta (`1127 + N passed`, same 1 known failure).
- UI pure test: `npx tsx ui/src/lib/__tests__/<name>.test.ts` (run from `ui/`); UI build: `cd ui && npm run build`.

---

## Phase P0 — Licensing infra + crate skeleton + mount gap fixes

### P0-T1 — Licensing infrastructure (THIRD-PARTY-NOTICES + header policy)

**Files**
- Create `C:\Users\bear\astro\THIRD-PARTY-NOTICES.md`
- Create `C:\Users\bear\astro\docs\native-parity\rust-header-policy.md`
- Modify `C:\Users\bear\astro\native\README.md` (relocate the "PHD2 (BSD-3) reserved for a future guiding crate" note at :24-30 into a pointer to THIRD-PARTY-NOTICES; move any inline NINA/Hocus-Focus attribution prose here per spec §3.6)

**Interfaces**
- Produces: a repo-root notices file the later Rust-port tasks reference in their file headers; a header-policy doc every `astro-guide` file follows.
- Consumes: the licensing report (`.superpowers/sdd/guider-licensing-report.md`) verbatim text; the dossier provenance note (dossier §16, commit `4a13cf24`).

**Steps** (documentation task — no automated test; the "test" is a content checklist verified by `grep`)

- [ ] Write `THIRD-PARTY-NOTICES.md` with this EXACT content:

```markdown
# Third-Party Notices

AstroDeck includes components derived from or bundled with third-party
open-source software. The notices below are mandatory attributions preserved
per each component's license terms.

## AstroDeck native crates (`native/crates/*`)

The `native/` Rust workspace (`astro-star`, `astro-focus`, `astro-tppa`,
`astro-guide`, `astrodeck-native`) is licensed under the Mozilla Public
License, v. 2.0 (MPL-2.0). A copy of the MPL-2.0 is available at
https://mozilla.org/MPL/2.0/. Each source file carries the MPL-2.0 header.

## PHD2 — guiding algorithms (BSD-3-Clause)

The `astro-guide` crate is a clean-room Rust reimplementation of PHD2's guiding
stack, produced from the source-mapped algorithm dossier
`docs/native-parity/algorithms/phd2-guiding.md` (extracted from PHD2 at commit
4a13cf245d7e485e79533697f87b032b304df952). Because the port derives from PHD2's
expressed logic, PHD2's BSD-3-Clause notice is preserved here (licensing report
§4, Path (a)):

```
This software includes code derived from PHD2
(https://github.com/OpenPHDGuiding/phd2), used under the following license:

Copyright (c) 2013-2019, Open PHD Guiding development team
Copyright (c) 2014-2015, Max Planck Society
Copyright (c) 2012, Bret McKee            [hysteresis, resist-switch, multi-star guider, mount/calibration]
Copyright (c) 2006-2010, Craig Stark      [star centroid, multi-star guider base]
Copyright (c) 2018, Ken Self              [Z-filter guide algorithm]
Copyright (c) 2020, Bruce Waddington      [multi-star guider extensions]
Copyright (c) 2023, Bruce Waddington      [calibration assistant / backlash tool]
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software without
   specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
OF THE POSSIBILITY OF SUCH DAMAGE.
```

### Predictive PEC / Gaussian-process guider (BSD-3-Clause, additionally)

The GP/PPEC algorithm (`astro-guide/src/algorithms/gaussian_process.rs`) is
ported from PHD2's `contributions/MPI_IS_gaussian_process` (Max Planck Institute
for Intelligent Systems, Tübingen). Its BSD-3-Clause notice is preserved:

```
Copyright 2014-2017, Max Planck Society.
Authors: Edgar D. Klenske, Stephan Wenninger, Raffi Enficiaud
All rights reserved.

[... the identical BSD-3-Clause conditions + AS-IS disclaimer as above ...]
```

Algorithm reference (academic citation, per licensing report §4 Path (b)):
Edgar D. Klenske, Melanie N. Zeilinger, Bernhard Schölkopf, Philipp Hennig,
"Gaussian Process Based Predictive Control for Periodic Error Correction,"
IEEE Transactions on Control Systems Technology, vol. 24, no. 1, pp. 110-121, 2016.

The GP port uses `nalgebra` for linear algebra; PHD2's C++ used Eigen (MPL-2.0),
which is a separable build dependency of PHD2 and does NOT travel to this port
(licensing report §3).

## NINA / Hocus Focus — autofocus + star detection (Mozilla Public License 2.0)

`astro-star` and `astro-focus` are clean-room reimplementations from the audited
dossiers `docs/native-parity/algorithms/nina-autofocus.md` and
`hocusfocus-autofocus-tilt.md`. No code was copied from NINA or Hocus Focus; the
upstream projects are MPL-2.0 and this reimplementation is likewise MPL-2.0.
```

- [ ] Fill the two `[... identical BSD-3-Clause ...]` placeholders in the GP block with the full clause text (copy the 3-clause + AS-IS paragraph verbatim from the PHD2 block above; do NOT leave the bracketed shorthand in the shipped file).
- [ ] Write `docs/native-parity/rust-header-policy.md` stating the mandatory header for every `astro-guide` file:

```
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this
// file, You can obtain one at https://mozilla.org/MPL/2.0/.
//
// Provenance: clean-room Rust reimplementation from the audited algorithm
// dossier docs/native-parity/algorithms/phd2-guiding.md (<DOSSIER SECTIONS>).
// Derived from PHD2 <phd2 src file:lines> (BSD-3-Clause; see THIRD-PARTY-NOTICES.md).
// No code copied from PHD2.
```
State that `<DOSSIER SECTIONS>` and `<phd2 src file:lines>` come from the dossier §16 source map, and that non-derived files (e.g. the sim harness) use the `astro-focus` header form without the PHD2 line.
- [ ] Edit `native/README.md:24-30` to replace the reserved-crate sentence with: "The `astro-guide` crate reimplements PHD2's guiding stack (BSD-3-Clause); see the repo-root `THIRD-PARTY-NOTICES.md` for attributions." Move any NINA/Hocus-Focus attribution prose out of README into THIRD-PARTY-NOTICES.
- [ ] Verify content: `grep -c "BSD-3-Clause" THIRD-PARTY-NOTICES.md` ≥ 2, `grep "Klenske" THIRD-PARTY-NOTICES.md`, `grep "Max Planck Society" THIRD-PARTY-NOTICES.md`, `grep "MPL-2.0" THIRD-PARTY-NOTICES.md`. Confirm no `[... ` bracket placeholder remains: `grep -n "\[\.\.\." THIRD-PARTY-NOTICES.md` returns nothing.
- [ ] Commit:
```
git add THIRD-PARTY-NOTICES.md docs/native-parity/rust-header-policy.md native/README.md
git commit -m "docs(licensing): THIRD-PARTY-NOTICES (PHD2/MPI-IS BSD-3 + MPL-2.0) + Rust header policy

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P0-T2 — `astro-guide` crate skeleton + workspace wiring

**Files**
- Create `C:\Users\bear\astro\native\crates\astro-guide\Cargo.toml`
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\lib.rs`
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\types.rs`
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\smoke.rs`
- Modify `C:\Users\bear\astro\native\Cargo.toml` (add member + `nalgebra` to a workspace deps table)

**Interfaces**
- Produces: the crate compiling with the shared `Axis`/`Direction`/`AxisPulse`/`Action`/`FrameMeta`/`MeasuredStar` types every later P1–P4 task imports.
- Consumes: `astro-star` primitives (`GrayFrame`, `Rect`, stats) by path dependency (`native/crates/astro-star`).

**Steps**

- [ ] Add to `native/Cargo.toml` `[workspace] members` the line `"crates/astro-guide",` (after `astro-focus`). Add a `[workspace.dependencies]` table with `nalgebra = "0.33"` (the GP task consumes it; declaring here keeps versions pinned workspace-wide).
- [ ] Write the failing smoke test `native/crates/astro-guide/tests/smoke.rs`:
```rust
// MPL-2.0 header (see docs/native-parity/rust-header-policy.md).
use astro_guide::types::{Action, Axis, AxisPulse, Direction, FrameMeta, MeasuredStar};

#[test]
fn types_construct_and_match() {
    let a = Action::PulsePair {
        ra: Some(AxisPulse { dir: Direction::West, ms: 120 }),
        dec: None,
    };
    match a {
        Action::PulsePair { ra: Some(p), dec: None } => {
            assert_eq!(p.ms, 120);
            assert!(matches!(p.dir, Direction::West));
        }
        _ => panic!("wrong variant"),
    }
    let _ = Action::Idle;
    let _ = Action::LockLost;
    let _ = Action::Settle;
    let _ = Action::Pulse { axis: Axis::Ra, dir: Direction::East, ms: 5 };
    let m = MeasuredStar { x: 1.0, y: 2.0, snr: 10.0, mass: 500.0, hfd: 3.0, found: true };
    let f = FrameMeta { timestamp_s: 100.0, exposure_s: 2.0 };
    assert_eq!(m.x + f.exposure_s, 3.0);
}
```
- [ ] Run `cd native && cargo test -p astro-guide` → EXPECT failure: `error: no matching package named 'astro-guide'` / unresolved `astro_guide`.
- [ ] Write `native/crates/astro-guide/Cargo.toml`:
```toml
[package]
name = "astro-guide"
version = "0.1.0"
edition.workspace = true
license.workspace = true
rust-version.workspace = true

[dependencies]
astro-star = { path = "../astro-star" }
nalgebra = { workspace = true }
```
- [ ] Write `native/crates/astro-guide/src/types.rs` (MPL-2.0 header; dossier §5/§6/§7 provenance) with:
```rust
/// Mount axis. `Ra` == PHD2 x (RA), `Dec` == PHD2 y (Dec). Dossier §units.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Axis { Ra, Dec }

/// Guide-pulse direction. UP==NORTH(Dec+), DOWN==SOUTH(Dec-),
/// RIGHT==EAST(RA-), LEFT==WEST(RA+). Dossier §units.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Direction { North, South, East, West }

/// One axis's issued pulse.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AxisPulse { pub dir: Direction, pub ms: u32 }

/// Which calibration leg a `CalStep` belongs to (dossier §8.2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CalLeg { GoWest, GoEast, ClearBacklash, GoNorth, GoSouth, NudgeSouth }

/// The engine's per-frame decision. The host performs exactly one of these.
#[derive(Debug, Clone, PartialEq)]
pub enum Action {
    /// No correction (deadband/vetoed both axes, or paused).
    Idle,
    /// Single-axis pulse (calibration re-center / one-axis guide).
    Pulse { axis: Axis, dir: Direction, ms: u32 },
    /// A guide frame's corrections: up to one pulse per axis (dossier §7).
    PulsePair { ra: Option<AxisPulse>, dec: Option<AxisPulse> },
    /// A calibration state-machine step (dossier §8.2).
    CalStep { leg: CalLeg, dir: Direction, ms: u32 },
    /// Within a start/dither settle window; the host waits (dossier §12).
    Settle,
    /// Guide star lost and recovery exhausted this frame (dossier §3.3).
    LockLost,
}

/// Per-frame metadata the engine needs (clock injected — dossier §17 deviation).
#[derive(Debug, Clone, Copy)]
pub struct FrameMeta { pub timestamp_s: f64, pub exposure_s: f64 }

/// A star measured this frame (positions in binned pixels; dossier §1).
#[derive(Debug, Clone, Copy)]
pub struct MeasuredStar {
    pub x: f64, pub y: f64,
    pub snr: f64, pub mass: f64, pub hfd: f64,
    pub found: bool,
}
```
- [ ] Write `native/crates/astro-guide/src/lib.rs` (MPL-2.0 header; crate doc) with `pub mod types;` and a crate-level `//!` doc mirroring `astro-focus/src/lib.rs`'s framing (pure algorithm crate, no async/I/O/globals; positions in binned pixels; pulses in ms; rates px/ms).
- [ ] Run `cd native && cargo test -p astro-guide` → EXPECT pass (`test types_construct_and_match ... ok`).
- [ ] Run `cd native && cargo build --workspace` → EXPECT clean (member wired, nalgebra resolves).
- [ ] Commit:
```
git add native/Cargo.toml native/crates/astro-guide/Cargo.toml native/crates/astro-guide/src/lib.rs native/crates/astro-guide/src/types.rs native/crates/astro-guide/tests/smoke.rs
git commit -m "feat(astro-guide): crate skeleton + shared Action/Axis/MeasuredStar types (spec §3.1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P0-T3 — Mount gap fixes: `CanPulseGuide` probe + guide-rate reads

**Files**
- Modify `C:\Users\bear\astro\server\astrodeck\devices\base.py` (Telescope ABC: add `can_pulse_guide: bool = False` capability flag + async `guide_rates()` default at :187-192 region)
- Modify `C:\Users\bear\astro\server\astrodeck\devices\alpaca.py` (`AlpacaTelescope`: probe `CanPulseGuide` in `connect` at :478-489; add `guide_rates()` reading `GuideRateRightAscension/Declination` near `pulse_guide` at :531-532)
- Modify `C:\Users\bear\astro\server\astrodeck\devices\sim.py` (`SimTelescope`: set `can_pulse_guide = True`; `guide_rates()` returns the sim's fixed rate near :530)
- Create `C:\Users\bear\astro\server\tests\test_mount_guide_probe.py`

**Interfaces**
- Produces: `Telescope.can_pulse_guide: bool` (capability flag, default False); `async Telescope.guide_rates() -> tuple[float, float] | None` returning `(ra_deg_per_s, dec_deg_per_s)` at 1× guide speed, or `None` when the mount does not report them (calibration then uses advisories per dossier §17).
- Consumes: `AlpacaTelescope._get` (`alpaca.py:283`); the connect probe pattern from `destination_pier_side` (`alpaca.py:541-552`).

**Steps**

- [ ] Write the failing test `server/tests/test_mount_guide_probe.py`:
```python
import pytest
from astrodeck.devices.sim import build_sim_rig

@pytest.mark.asyncio
async def test_sim_mount_reports_pulse_guide_and_rates():
    rig = build_sim_rig()
    tel = rig["telescope"]
    await tel.connect()
    assert tel.can_pulse_guide is True
    rates = await tel.guide_rates()
    assert rates is not None
    ra, dec = rates
    assert ra > 0 and dec > 0

def test_base_telescope_defaults():
    from astrodeck.devices.base import Telescope
    assert Telescope.can_pulse_guide is False
```
- [ ] Run `cd server && ./.venv/Scripts/python.exe -m pytest -q server/tests/test_mount_guide_probe.py` → EXPECT failure (`AttributeError: ... can_pulse_guide` / `guide_rates`).
- [ ] In `devices/base.py` `Telescope`, add after `reports_destination_pier_side` (:152): `can_pulse_guide: bool = False` with a docstring noting it gates whether the native guider will start (spec §4: pulse rejection → refuse to start with actionable error). Add after `pulse_guide` (:188):
```python
    async def guide_rates(self) -> tuple[float, float] | None:
        """(ra_deg_per_s, dec_deg_per_s) at 1x guide speed, or None when the
        mount doesn't report them. Calibration uses actual rates when present,
        else falls back to advisories (dossier §17)."""
        return None
```
- [ ] In `alpaca.py` `AlpacaTelescope.connect` (after the destination-pier probe, :485-489), add a best-effort probe: `try: self.can_pulse_guide = bool(await self._get("canpulseguide")) except Exception: pass`. Add a `guide_rates` override that reads `guideraterightascension`/`guideratedeclination` (ASCOM deg/s) and returns them, returning `None` on any `DeviceError`/transport error (mirroring `pier_side`'s try/except at :534-539).
- [ ] In `sim.py` `SimTelescope.__init__` (near :436) set `self.can_pulse_guide = True`; add `async def guide_rates(self)` returning the sim's fixed 0.5× sidereal rate as `(15.0/3600*0.5, 15.0/3600*0.5)` deg/s (matches the sim `pulse_guide` nudge scale, keeps the closed-loop sim self-consistent for P1-T8).
- [ ] Run the test → EXPECT pass.
- [ ] Run `cd server && ./.venv/Scripts/python.exe -m pytest -q` → EXPECT `1127 + 2 passed` + 1 known failure (`test_polar_native`).
- [ ] Commit:
```
git add server/astrodeck/devices/base.py server/astrodeck/devices/alpaca.py server/astrodeck/devices/sim.py server/tests/test_mount_guide_probe.py
git commit -m "feat(devices): Telescope.can_pulse_guide probe + guide_rates reads (spec §3.3)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

## Phase P1 — First guidable milestone (star-find → track → calibrate → guide → sim e2e)

### P1-T1 — `starfind.rs` (`Star::Find` parity)

**Files**
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\starfind.rs`
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\lib.rs` (`pub mod starfind;`)
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\starfind_golden.rs`

**Interfaces**
- Produces:
  - `pub struct StarFindResult { pub x: f64, pub y: f64, pub mass: f64, pub snr: f64, pub hfd: f64, pub peak_val: u16, pub result: FindResult }`
  - `pub enum FindResult { StarOk, StarSaturated, StarLowSnr, StarLowMass, StarLowHfd, StarHiHfd, StarTooNearEdge, StarMassChange, StarError }` with `pub fn was_found(r: FindResult) -> bool`
  - `pub struct FindParams { pub search_region: i32, pub min_hfd: f64, pub max_hfd: f64, pub max_adu: u32, pub pedestal: u16, pub bits_per_pixel: u32 }` with a `Default` = dossier §1.1 defaults (search_region 15, min_hfd 1.5, max_hfd 20.0, max_adu 0, pedestal 0, bpp 16).
  - `pub fn star_find(frame: &astro_star::GrayFrame, base_x: f64, base_y: f64, p: &FindParams) -> StarFindResult` (FindCentroid mode).
- Consumes: `astro_star::GrayFrame` (indexing `frame` by `(x, y) -> u16`; the astro-star primitive from `image.rs`).

**Steps** (LARGE PORT — uses the spec's port exception: signatures + transcription source + full golden vectors + acceptance behavior)

- [ ] **Transcription source (pin exactly):** dossier §1.3 (FindCentroid algorithm steps 1–10), §1.4 (`hfr`); upstream `references/phd2/src/star.cpp:126-483` (`Star::Find`), `star.cpp:83-124` (`hfr`), `star.cpp:41-70` (state). Header per P0-T1 policy naming these. Module layout: `star_find` (public), private `annulus_background(frame, peak, bounds) -> (mean_bg, sigma_bg, sigma2_bg, nbg)`, `centroid_over_disk(...)`, `hfr(pixels, cx, cy, mass) -> f64`. Constants exactly as dossier §1.3: annulus A=7, B=12; 9-iteration 2σ clip with `nbg < 10` break and `|Δmean| < 0.5` convergence; threshold `mean_bg + 3σ + 0.5`; Simonetti SNR with `GAIN = 0.5`; `LOW_SNR = 3.0`; mass floor 10.0; saturation flat-top heuristic (8-bit `d*191 < mx`, else `d*65535 < 32*mx`).
- [ ] Write the golden test `native/crates/astro-guide/tests/starfind_golden.rs`. Fixtures — a synthetic frame builder plus these cases derived by hand-trace from dossier §1.3/§1.4 (each with a provenance comment):

```rust
// Provenance: fixtures hand-derived from dossier §1.3 (FindCentroid) / §1.4 (hfr).
// A synthetic Gaussian star on a flat background is a direct parity target:
// the 3x3-smoothed peak, 2-sigma-clipped annulus background, and mass-weighted
// centroid recover the injected sub-pixel center to <0.05 px (dossier §17:
// "unit test against synthetic Gaussians + hot pixels").
use astro_guide::starfind::{star_find, FindParams, FindResult, was_found};

/// Build a WxH u16 frame: flat background `bg`, one Gaussian star at (cx,cy)
/// with peak amplitude `amp` and sigma `sg` (background-additive).
fn gaussian_frame(w: usize, h: usize, bg: u16, cx: f64, cy: f64, amp: f64, sg: f64) -> Vec<u16> {
    let mut v = vec![bg; w * h];
    for y in 0..h { for x in 0..w {
        let dx = x as f64 - cx; let dy = y as f64 - cy;
        let g = amp * (-(dx*dx + dy*dy) / (2.0*sg*sg)).exp();
        v[y*w + x] = (bg as f64 + g).min(65535.0) as u16;
    }}
    v
}

#[test]
fn centroid_recovers_subpixel_gaussian() {
    // 41x41, bg=100, star at (20.30, 19.70), amp=4000, sigma=1.6.
    let (w, h) = (41usize, 41usize);
    let px = gaussian_frame(w, h, 100, 20.30, 19.70, 4000.0, 1.6);
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let r = star_find(&gf, 20.0, 20.0, &FindParams::default());
    assert!(was_found(r.result), "result was {:?}", r.result);
    assert!((r.x - 20.30).abs() < 0.05, "x={}", r.x);
    assert!((r.y - 19.70).abs() < 0.05, "y={}", r.y);
    assert!(r.snr > 10.0, "snr={}", r.snr);
    // HFD of a sigma=1.6 Gaussian ~ 2*sigma*1.1774 ~ 3.77 px (half-flux diameter).
    assert!(r.hfd > 3.0 && r.hfd < 4.5, "hfd={}", r.hfd);
}

#[test]
fn hot_pixel_rejected_low_hfd() {
    // Single bright pixel on flat bg: hfr()==0.25 -> hfd==0.5 < min_hfd 1.5.
    let (w, h) = (31usize, 31usize);
    let mut px = vec![100u16; w*h];
    px[15*w + 15] = 60000;
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let r = star_find(&gf, 15.0, 15.0, &FindParams::default());
    assert!(matches!(r.result, FindResult::StarLowHfd), "result={:?}", r.result);
}

#[test]
fn empty_field_low_mass_or_snr() {
    let (w, h) = (31usize, 31usize);
    let px = vec![100u16; w*h];
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let r = star_find(&gf, 15.0, 15.0, &FindParams::default());
    assert!(!was_found(r.result));
    assert!(matches!(r.result, FindResult::StarLowMass | FindResult::StarLowSnr));
}

#[test]
fn saturated_star_still_found() {
    // Flat-topped star: many pixels at 65535 -> StarSaturated (still "found").
    let (w, h) = (41usize, 41usize);
    let mut px = gaussian_frame(w, h, 100, 20.0, 20.0, 200000.0, 2.2); // clips flat
    // ensure a broad flat top
    for y in 18..23 { for x in 18..23 { px[y*w+x] = 65535; } }
    let gf = astro_star::GrayFrame::new(&px, w, h);
    let mut p = FindParams::default(); p.max_adu = 65535;
    let r = star_find(&gf, 20.0, 20.0, &p);
    assert!(matches!(r.result, FindResult::StarSaturated));
    assert!(was_found(r.result));
}
```
- [ ] Run `cd native && cargo test -p astro-guide --test starfind_golden` → EXPECT failure (unresolved `astro_guide::starfind`).
- [ ] Implement `starfind.rs` per the transcription source. Acceptance behavior to satisfy: the four golden cases above; sub-pixel centroid within 0.05 px; hot-pixel → `StarLowHfd`; empty → not-found; flat-top → `StarSaturated`.
- [ ] Run `cd native && cargo test -p astro-guide --test starfind_golden` → EXPECT pass (4 tests).
- [ ] Run `cd native && cargo test -p astro-guide` → EXPECT all green.
- [ ] Commit:
```
git add native/crates/astro-guide/src/starfind.rs native/crates/astro-guide/src/lib.rs native/crates/astro-guide/tests/starfind_golden.rs
git commit -m "feat(astro-guide): Star::Find parity — annulus bg, Simonetti SNR, HFD (dossier §1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P1-T2 — `transforms.rs` (camera ⇄ mount)

**Files**
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\transforms.rs`
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\lib.rs` (`pub mod transforms;`)
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\transforms_golden.rs`

**Interfaces**
- Produces:
  - `pub fn norm_angle(a: f64) -> f64` (wrap to (−π, π]).
  - `pub struct Cal { pub x_rate: f64, pub y_rate: f64, pub x_angle: f64, pub y_angle: f64, pub y_angle_error: f64, pub declination: f64, pub pier_side: PierSide, pub ra_parity: Parity, pub dec_parity: Parity, pub rotator_angle: f64, pub binning: u16, pub is_valid: bool }` with `pub fn y_angle_error_from(x_angle: f64, y_angle: f64) -> f64 { norm_angle(x_angle - y_angle + PI/2) }` (dossier §5).
  - `pub enum PierSide { East, West, Unknown }`; `pub enum Parity { Even, Odd, Unknown }`.
  - `pub fn camera_to_mount(cam: (f64, f64), cal: &Cal) -> (f64, f64)` and `pub fn mount_to_camera(mnt: (f64, f64), cal: &Cal) -> (f64, f64)` per dossier §5.
- Consumes: nothing (pure trig).

**Steps**

- [ ] Write the golden test `native/crates/astro-guide/tests/transforms_golden.rs`:
```rust
// Provenance: exact vectors hand-derived from dossier §5 (camera<->mount).
use astro_guide::transforms::{camera_to_mount, mount_to_camera, norm_angle, Cal};
use std::f64::consts::PI;

fn cal(x_angle: f64, y_angle_error: f64) -> Cal {
    Cal { x_rate: 1.0, y_rate: 1.0, x_angle, y_angle: 0.0, y_angle_error,
          declination: 0.0, pier_side: astro_guide::transforms::PierSide::West,
          ra_parity: astro_guide::transforms::Parity::Unknown,
          dec_parity: astro_guide::transforms::Parity::Unknown,
          rotator_angle: 0.0, binning: 1, is_valid: true }
}

#[test]
fn identity_when_xangle0_yerr0() {
    // x_angle=0, y_angle_error=0 => cam==mount. cam=(3,4)->mount=(3,4).
    let c = cal(0.0, 0.0);
    let (mx, my) = camera_to_mount((3.0, 4.0), &c);
    assert!((mx - 3.0).abs() < 1e-9, "mx={}", mx);
    assert!((my - 4.0).abs() < 1e-9, "my={}", my);
}

#[test]
fn rotated_ninety_degrees() {
    // x_angle=PI/2, y_angle_error=0. cam=(3,4)->mount=(4,-3).
    let c = cal(PI/2.0, 0.0);
    let (mx, my) = camera_to_mount((3.0, 4.0), &c);
    assert!((mx - 4.0).abs() < 1e-9, "mx={}", mx);
    assert!((my + 3.0).abs() < 1e-9, "my={}", my);
}

#[test]
fn mount_to_camera_inverts_identity() {
    let c = cal(0.0, 0.0);
    let (cx, cy) = mount_to_camera((3.0, 4.0), &c);
    assert!((cx - 3.0).abs() < 1e-9 && (cy - 4.0).abs() < 1e-9);
}

#[test]
fn norm_angle_wraps() {
    assert!((norm_angle(3.0*PI) - PI).abs() < 1e-9);
    assert!((norm_angle(-3.0*PI) - PI).abs() < 1e-9);
    assert!(norm_angle(0.0).abs() < 1e-9);
}
```
- [ ] Run `cd native && cargo test -p astro-guide --test transforms_golden` → EXPECT failure (unresolved module).
- [ ] Implement `transforms.rs` exactly per dossier §5 (`camera_to_mount`: `theta = atan2(y,x)`, `x_angle = theta - cal.x_angle`, `y_angle = theta - (cal.x_angle + cal.y_angle_error)`, `mount = (cos(x_angle)*hyp, sin(y_angle)*hyp)`; `mount_to_camera` with the `|y_angle_error| > PI/2` theta-negation branch). Provenance header names `references/phd2/src/mount.cpp:1135-1223` + dossier §5.
- [ ] Run `cd native && cargo test -p astro-guide --test transforms_golden` → EXPECT pass (4 tests).
- [ ] Commit:
```
git add native/crates/astro-guide/src/transforms.rs native/crates/astro-guide/src/lib.rs native/crates/astro-guide/tests/transforms_golden.rs
git commit -m "feat(astro-guide): camera<->mount transforms + Cal model (dossier §5)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P1-T3 — `select.rs` (auto-find + single-star primary selection)

**Files**
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\select.rs`
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\lib.rs`
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\select_golden.rs`

**Interfaces**
- Produces:
  - `pub struct Candidate { pub x: f64, pub y: f64, pub snr: f64, pub mass: f64, pub hfd: f64, pub peak_val: u16 }`
  - `pub struct SelectParams { pub search_region: i32, pub af_min_snr: f64, pub extra_edge_allowance: i32, pub max_stars: usize }` (defaults: search_region 15, af_min_snr 6.0, extra_edge_allowance 0, max_stars 1 for P1).
  - `pub fn auto_find(frame: &astro_star::GrayFrame, p: &SelectParams) -> Vec<Candidate>` — the §2 pipeline (PSF convolution, local-max scan, merge <5px, search-box conflict, edge drop, candidate measure via `star_find`), returning candidates sorted brightest-first.
  - `pub fn select_primary(cands: &[Candidate], sat_thresh: u16, af_min_snr: f64) -> Option<usize>` — the §2.7 three-pass selection returning the chosen index.
- Consumes: `starfind::{star_find, FindParams, was_found}`; `astro_star::GrayFrame`.

**Steps** (LARGE PORT — port exception)

- [ ] **Transcription source:** dossier §2.1–§2.7; upstream `references/phd2/src/star.cpp:718-1154` (AutoFind), PSF kernel `star.cpp:574-656`, downsample `star.cpp:658-680`, stats `star.cpp:515-544`. Implement the 9×9 PSF matched-filter (dossier §2.2 ring sums + coefficients EXACTLY), local-max `h ≥ 0.1` significance (§2.3), TOP_N=100, merge `d²<25` (§2.4), search-box conflict (§2.4), edge drop (§2.4), saturation inference (§2.5), and the three-pass primary selection (§2.7). For P1 `max_stars = 1`: the candidate list is single-star; multi-star candidate management (§2.6) is deferred to P3-T1.
- [ ] Write `native/crates/astro-guide/tests/select_golden.rs`. Fixtures — a multi-star synthetic frame with three Gaussians of known brightness/position; assert `auto_find` returns them brightest-first, merges a planted 3-px duplicate, drops an edge star, and `select_primary` returns the brightest non-saturated. Provenance comment: "hand-derived from dossier §2 pipeline; a 3-Gaussian field with one edge star + one 3px duplicate is a direct parity target for merge/edge/selection." Include a `gaussian_frame`-style builder (reuse the pattern from P1-T1). Assert with tolerances (positions <0.5 px of injected; ordering by brightness).
- [ ] Run `cd native && cargo test -p astro-guide --test select_golden` → EXPECT failure.
- [ ] Implement `select.rs`. Acceptance behavior: three planted stars returned brightest-first; duplicate within 5 px merged (dimmer dropped); star within `search_region+extra_edge_allowance` of an edge dropped; `select_primary` returns brightest passing Pass-1.
- [ ] Run the golden test → EXPECT pass. Run `cargo test -p astro-guide` → all green.
- [ ] Commit:
```
git add native/crates/astro-guide/src/select.rs native/crates/astro-guide/src/lib.rs native/crates/astro-guide/tests/select_golden.rs
git commit -m "feat(astro-guide): AutoFind PSF pipeline + 3-pass primary selection (dossier §2)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P1-T4 — `track.rs` (MassChecker, DistanceChecker, per-frame tracking)

**Files**
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\track.rs`
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\lib.rs`
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\track_golden.rs`

**Interfaces**
- Produces:
  - `pub struct MassChecker { ... }` with `pub fn new(window_ms: f64) -> Self`, `pub fn check(&self, mass: f64, threshold: f64) -> bool` (true = accept), `pub fn append(&mut self, t_ms: f64, mass: f64)`, `pub fn reset(&mut self)` — dossier §3.1 (default window 22500 ms stored ×2 = 45 s drop; `history.len() < 5` → accept; high/low water; `lim0/lim2/lim3`).
  - `pub enum TrackState { Guiding, Waiting, Recovering }`; `pub struct DistanceChecker { ... }` with `pub fn new() -> Self`, `pub fn activate(&mut self, now_s: f64)`, `pub fn check_distance(&mut self, now_s: f64, distance: f64, err_smoothed: f64, small_context: bool) -> bool`, `pub fn state(&self) -> TrackState` — dossier §3.2 (`WAIT_INTERVAL_MS=5000`, forced tolerance 2.0 on activate, tolerate-jumps default off/threshold 4.0).
  - `pub struct AvgDist { ... }` implementing dossier §13 (fast EMA α=0.3, slow EMA seed-then-α=0.045, `current_error()` returns 100.0 when no star > 20 s).
- Consumes: `transforms::camera_to_mount`, `MeasuredStar`, `FrameMeta`.

**Steps** (port exception for the state machines; explicit golden traces below)

- [ ] **Transcription source:** dossier §3.1 (`guider_multistar.cpp:52-180`), §3.2 (`guider_multistar.cpp:601-704`), §13 (`guider.cpp:1065-1139`).
- [ ] Write `native/crates/astro-guide/tests/track_golden.rs` with these hand-traced golden cases (provenance comments citing the dossier §):
```rust
// Provenance: hand-traced from dossier §3.1/§3.2/§13.
use astro_guide::track::{MassChecker, DistanceChecker, TrackState, AvgDist};

#[test]
fn mass_checker_accepts_until_five_then_gates() {
    // §3.1: history<5 always accepts; threshold 0.5.
    let mut mc = MassChecker::new(22500.0);
    for i in 0..4 { assert!(mc.check(1000.0, 0.5)); mc.append(i as f64 * 1000.0, 1000.0); }
    // 5th sample present; steady mass ~ median 1000 -> a 40% drop (600) still
    // within lim0 = low_water*(1-0.5)=500 -> accepted; a 60% drop (350) < 500 -> rejected.
    mc.append(4000.0, 1000.0);
    assert!(mc.check(600.0, 0.5), "40% dip accepted");
    assert!(!mc.check(350.0, 0.5), "65% dip rejected");
}

#[test]
fn distance_checker_waiting_then_recovering() {
    // §3.2: a large jump moves Guiding->Waiting (reject); still-large & expired
    // (>5s) -> Recovering (accept).
    let mut dc = DistanceChecker::new();
    assert_eq!(dc.state(), TrackState::Guiding);
    // small_context=false so tolerance applies; err_smoothed=1.0, tol default 4.0
    // (tolerate-jumps OFF => tolerance 9e99 normally, but activate() forced 2.0).
    dc.activate(100.0); // star lost -> Waiting, forced tolerance 2.0, expiry 105s
    assert_eq!(dc.state(), TrackState::Waiting);
    assert!(!dc.check_distance(101.0, 10.0, 1.0, false), "large & not expired -> reject");
    assert!(dc.check_distance(106.0, 10.0, 1.0, false), "large & expired -> Recovering accept");
    assert_eq!(dc.state(), TrackState::Recovering);
    assert!(dc.check_distance(107.0, 0.5, 1.0, false), "small -> back to Guiding");
    assert_eq!(dc.state(), TrackState::Guiding);
}

#[test]
fn avg_dist_large_distance_when_stale() {
    // §13: current_error()==100.0 when no star found for >20s.
    let mut a = AvgDist::new();
    a.update(0.0, 0.8, 0.5);
    assert!((a.current_error(1.0, true) - 0.8).abs() < 0.3); // fast EMA near sample
    assert_eq!(a.current_error(30.0, true), 100.0); // >20s stale
}
```
- [ ] Run `cd native && cargo test -p astro-guide --test track_golden` → EXPECT failure.
- [ ] Implement `track.rs`. The public `AvgDist::update(now_s, dist, dist_ra)`, `current_error(now_s, dec_guiding) -> f64`. Provenance headers per policy.
- [ ] Run the golden test → EXPECT pass (3 tests). Run `cargo test -p astro-guide` → green.
- [ ] Commit:
```
git add native/crates/astro-guide/src/track.rs native/crates/astro-guide/src/lib.rs native/crates/astro-guide/tests/track_golden.rs
git commit -m "feat(astro-guide): MassChecker + DistanceChecker + avgDistance EMAs (dossier §3/§13)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P1-T5 — Algorithms: `GuideAlgorithm` trait + Hysteresis + ResistSwitch

**Files**
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\mod.rs`
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\hysteresis.rs`
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\resist_switch.rs`
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\lib.rs` (`pub mod algorithms;`)
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\algorithms_golden.rs`

**Interfaces**
- Produces:
  - `pub trait GuideAlgorithm { fn result(&mut self, input: f64) -> f64; fn reset(&mut self); fn deduce_result(&mut self) -> f64 { 0.0 }; fn min_move(&self) -> f64; }`
  - `pub struct Hysteresis { pub hysteresis: f64, pub aggression: f64, pub min_move: f64, last_move: f64 }` with `pub fn new(hysteresis: f64, aggression: f64, min_move: f64) -> Self` and `Default` = dossier §6.1 (0.1 / 0.7 / 0.2).
  - `pub struct ResistSwitch { pub min_move: f64, pub aggression: f64, pub fast_switch: bool, ... }` with `Default` = §6.2 (0.2 / 1.0 / true), `HISTORY_SIZE = 10`.
- Consumes: nothing.

**Steps** (real code — small algorithms transcribed in full from dossier §6.1/§6.2)

- [ ] Write `native/crates/astro-guide/tests/algorithms_golden.rs` with the EXACT hand-computed step responses (provenance comments show the arithmetic):
```rust
// Provenance: step responses hand-computed from dossier §6.1 (Hysteresis) and
// §6.2 (ResistSwitch), defaults from §15.
use astro_guide::algorithms::{GuideAlgorithm, Hysteresis, ResistSwitch};

#[test]
fn hysteresis_default_step_response() {
    // h=0.1, aggression=0.7, min_move=0.2. last_move stores the aggression-scaled
    // output (§6.1). Input [1,1,1,0.1,-0.5]:
    //  1.0 -> (0.9*1 + 0.1*0)*0.7 = 0.63
    //  1.0 -> (0.9*1 + 0.1*0.63)*0.7 = 0.6741
    //  1.0 -> (0.9*1 + 0.1*0.6741)*0.7 = 0.677187
    //  0.1 -> |0.1|<0.2 -> 0.0 (last_move decays to 0)
    // -0.5 -> (0.9*-0.5 + 0.1*0)*0.7 = -0.315
    let mut h = Hysteresis::new(0.1, 0.7, 0.2);
    let ins = [1.0, 1.0, 1.0, 0.1, -0.5];
    let exp = [0.63, 0.6741, 0.677187, 0.0, -0.315];
    for (i, e) in ins.iter().zip(exp.iter()) {
        let got = h.result(*i);
        assert!((got - e).abs() < 1e-9, "in={} got={} exp={}", i, got, e);
    }
}

#[test]
fn resist_switch_requires_three_and_worsening() {
    // min_move=0.2, aggression=1.0, fast_switch=true, thresh=0.6. Constant 0.3:
    // frame1: dec_history=+1 (<3) -> veto 0.0
    // frame2: dec_history=+2 (<3) -> veto 0.0
    // frame3: dec_history=+3, newest3(0.9) worsening vs oldest3(0) -> side=+1 -> 0.3
    // frame4: side +1 == sign(input) -> 0.3
    // frame5: 0.3
    let mut r = ResistSwitch::default();
    let exp = [0.0, 0.0, 0.3, 0.3, 0.3];
    for e in exp.iter() {
        let got = r.result(0.3);
        assert!((got - e).abs() < 1e-9, "got={} exp={}", got, e);
    }
}

#[test]
fn resist_switch_deadband_and_reset() {
    let mut r = ResistSwitch::default();
    assert_eq!(r.result(0.1), 0.0); // below min_move 0.2
    r.reset();
    assert_eq!(r.result(0.1), 0.0);
}
```
- [ ] Run `cd native && cargo test -p astro-guide --test algorithms_golden` → EXPECT failure.
- [ ] Implement `algorithms/mod.rs` (trait + `pub use`), `hysteresis.rs`, `resist_switch.rs` transcribing dossier §6.1/§6.2 verbatim (min-move tested on INPUT; hysteresis `last_move = r` stores the aggression-scaled result and vetoed moves store 0; resist-switch ring of 10, fast-switch bypass at 3×min_move, vote/worsening gate, overshoot veto). `min_move()` returns the configured min_move (Identity would return −1.0 but Identity is not in P1). Headers name `guide_algorithm_hysteresis.cpp:42-91` / `guide_algorithm_resistswitch.cpp:42-177` + dossier §.
- [ ] Run the golden test → EXPECT pass (3 tests). Run `cargo test -p astro-guide` → green.
- [ ] Commit:
```
git add native/crates/astro-guide/src/algorithms native/crates/astro-guide/src/lib.rs native/crates/astro-guide/tests/algorithms_golden.rs
git commit -m "feat(astro-guide): GuideAlgorithm trait + Hysteresis + ResistSwitch (dossier §6.1/§6.2)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P1-T6 — `calibration.rs` (calibration state machine)

**Files**
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\calibration.rs`
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\lib.rs`
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\calibration_golden.rs`

**Interfaces**
- Produces:
  - `pub struct CalConfig { pub calibration_distance: f64, pub calibration_duration_ms: u32, pub max_steps: u32, pub dec_guide_mode: DecMode, pub assume_orthogonal: bool }` (defaults dossier §8.1/§15: distance = `max(25, ceil(20.0/image_scale))`, duration 750 ms, max 60, dec mode Auto, assume_orthogonal false).
  - `pub enum DecMode { Off, Auto, North, South }`.
  - `pub struct Calibrator { ... }` with `pub fn new(cfg: CalConfig, start: (f64, f64)) -> Self`, `pub fn step(&mut self, current: (f64, f64)) -> CalOutcome`, where `pub enum CalOutcome { Pulse { leg: CalLeg, dir: Direction, ms: u32 }, Done(Cal), Failed(String) }`. One state-machine step per frame (dossier §8.2 GO_WEST→GO_EAST→CLEAR_BACKLASH→GO_NORTH→GO_SOUTH→NUDGE_SOUTH→COMPLETE).
  - `pub fn sanity_advisories(cal: &Cal, ra_steps: u32, dec_steps: u32) -> Vec<String>` (dossier §8.3: min-steps 4, orthogonality 12.5°, rate ratios 0.20 — as advisory strings, spec §4/§17).
- Consumes: `transforms::{Cal, camera_to_mount, norm_angle, PierSide, Parity}`; `types::{CalLeg, Direction}`.

**Steps** (LARGE PORT — port exception)

- [ ] **Transcription source:** dossier §8.1 (`calstep_dialog.cpp:206-241`), §8.2 (`scope.cpp:1202-1784`; constants `scope.cpp:44-69`), §8.3 (`scope.cpp:868-985`), §8.4 (`mount.cpp:1544-1648`). Implement GO_WEST (measure `x_angle`/`x_rate` until distance ≥ calibration_distance, 60-step fail), GO_EAST (re-center), CLEAR_BACKLASH (3 consecutive ≥ expected-step north moves; `BL_MIN_CLEARING_DISTANCE 3` proceed; skipped when `dec_guide_mode == Off` with `y_rate = 1.0` sentinel), GO_NORTH (measure), GO_SOUTH (re-center + advisory), NUDGE_SOUTH (≤3 nudges to within 2.0 px), COMPLETE (stamp `y_angle_error = norm(x_angle - y_angle + π/2)`, declination, pier, binning). Clocks/steps injected (no wall clock).
- [ ] Write `native/crates/astro-guide/tests/calibration_golden.rs`. Because the dossier gives no literal end-to-end vector, DERIVE the fixture by driving the calibrator against a synthetic mount model in the test: a closure `move_star(dir, ms)` that advances a simulated star by a known rate/angle (e.g. WEST moves star +x at 0.02 px/ms, NORTH moves +y at 0.018 px/ms, with a small orthogonality tilt). Provenance comment: "fixture generated by driving the §8.2 machine against a known linear mount model; recovered x_rate/y_rate/x_angle/y_angle match the injected rates/angles within tolerance (this is the by-hand-trace the port exception prescribes)." Assert:
```rust
// recovered rates within 5% of injected; x_angle within 0.02 rad of 0;
// y_angle within 0.02 rad of PI/2; is_valid true; y_rate != 1.0 (dec calibrated).
```
Include a second test: `dec_guide_mode = Off` → completes with `y_rate == 1.0` sentinel and `y_angle == norm(x_angle + PI/2)`. Include a third: a mount whose WEST leg never moves the star (rate 0) → `CalOutcome::Failed` after 60 steps with "did not move enough".
- [ ] Run `cd native && cargo test -p astro-guide --test calibration_golden` → EXPECT failure.
- [ ] Implement `calibration.rs`. Acceptance behavior: the three fixtures above; sanity advisories fire on <4 steps / >12.5° non-orthogonality.
- [ ] Run the golden test → EXPECT pass. Run `cargo test -p astro-guide` → green.
- [ ] Commit:
```
git add native/crates/astro-guide/src/calibration.rs native/crates/astro-guide/src/lib.rs native/crates/astro-guide/tests/calibration_golden.rs
git commit -m "feat(astro-guide): calibration state machine + sanity advisories (dossier §8)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P1-T7 — `engine.rs` (`GuideEngine`) + `settle.rs` + move/pulse limiting

**Files**
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\settle.rs`
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\engine.rs`
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\lib.rs`
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\engine_scenarios.rs`

**Interfaces**
- Produces (the engine every later task and the PyO3 layer drives):
  - `pub struct EngineConfig { pub cal: calibration::CalConfig, pub find: starfind::FindParams, pub max_ra_duration_ms: u32, pub max_dec_duration_ms: u32, pub dec_guide_mode: calibration::DecMode, pub ra_algorithm: AlgoKind, pub dec_algorithm: AlgoKind, pub blc_pulse_ms: u32 }` with `AlgoKind { Hysteresis, ResistSwitch, Lowpass, Lowpass2, ZFilter, Ppec }` (P1 constructs only Hysteresis/ResistSwitch; the enum is complete so later tasks slot in without changing the ctor signature).
  - `pub struct GuideEngine { ... }` with:
    - `pub fn new(cfg: EngineConfig) -> Self`
    - `pub fn begin_calibration(&mut self, primary: (f64, f64))`
    - `pub fn begin_guiding(&mut self)` (requires a valid `Cal`)
    - `pub fn ingest(&mut self, meta: &FrameMeta, measured: &[MeasuredStar]) -> Action` — the per-frame decision. `measured[0]` is the primary (secondaries follow in P3). Runs tracking (jump reject), computes `offset.mount = camera_to_mount(star - lock)`, feeds each axis algorithm, applies static BLC (dossier §10.1) + `MoveAxis` clamps (dossier §7: max_ra/dec_duration, dec-mode gating), converts px→ms via the axis rate (RA dec-compensated), emits `PulsePair`/`CalStep`/`Settle`/`LockLost`/`Idle`.
    - `pub fn measure(&self, frame: &astro_star::GrayFrame) -> Vec<MeasuredStar>` — runs `star_find` at the current lock (or `auto_find`+`select_primary` when unlocked). Pure; the PyO3 `process` calls this then `ingest`.
    - `pub fn dither(&mut self, dx_px: f64, dy_px: f64)` (implemented fully in P2; P1 stub sets a settle window)
    - `pub fn stats(&self) -> GuideStatsSnapshot` (RMS windows matching `GuideStats`: `{ guiding: bool, rms_ra: f64, rms_dec: f64, rms_total: f64, snr: f64, recent: Vec<(f64,f64,f64)> }` = (t, ra, dec))
    - `pub fn flip_calibration(&mut self, requires_dec_flip: bool) -> bool` (P2)
    - `pub fn calibration(&self) -> Option<transforms::Cal>` / `pub fn set_calibration(&mut self, cal: transforms::Cal)` (serializable state for persistence)
  - `pub struct Settle { ... }` (dossier §12: tolerance_px, settle_time_sec, timeout_sec, per-frame `evaluate(err, locked, now_s) -> SettleState { Settling, Done, Failed(String) }`).
- Consumes: `starfind`, `select`, `track`, `transforms`, `algorithms`, `calibration`, `types`.

**Steps**

- [ ] Write `native/crates/astro-guide/tests/engine_scenarios.rs`. Because the engine's per-frame output depends on the composed algorithms already unit-tested, this test drives it with SCRIPTED `MeasuredStar` sequences (matching spec §5 "scripted fake engine → Action sequences asserted") and pre-set calibration:
```rust
// Provenance: engine composition pins from dossier §3/§5/§6/§7. Calibration is
// injected (set_calibration) so the offset->pulse path is deterministic.
use astro_guide::engine::{GuideEngine, EngineConfig, AlgoKind};
use astro_guide::types::{Action, FrameMeta, MeasuredStar, Direction};
use astro_guide::transforms::{Cal, PierSide, Parity};

fn ident_cal() -> Cal {
    // x_angle=0, y_angle_error=0 => camera==mount; rate 0.01 px/ms both axes.
    Cal { x_rate: 0.01, y_rate: 0.01, x_angle: 0.0, y_angle: std::f64::consts::FRAC_PI_2,
          y_angle_error: 0.0, declination: 0.0, pier_side: PierSide::West,
          ra_parity: Parity::Even, dec_parity: Parity::Even, rotator_angle: 0.0,
          binning: 1, is_valid: true }
}
fn star(x: f64, y: f64) -> MeasuredStar { MeasuredStar { x, y, snr: 30.0, mass: 800.0, hfd: 3.0, found: true } }

#[test]
fn steady_offset_pulses_correct_axis_and_direction() {
    let mut cfg = EngineConfig::default();
    cfg.ra_algorithm = AlgoKind::Hysteresis;
    cfg.dec_algorithm = AlgoKind::ResistSwitch;
    let mut e = GuideEngine::new(cfg);
    e.set_calibration(ident_cal());
    e.begin_guiding();
    // Lock at (100,100); star drifts +x (mount +x => WEST pulse per dossier §units).
    let meta = FrameMeta { timestamp_s: 0.0, exposure_s: 2.0 };
    let _ = e.ingest(&meta, &[star(100.0, 100.0)]); // establishes lock
    // Now star at (105,100): mount error x=+5 => hysteresis first move
    // (0.9*5+0.1*0)*0.7=3.15 px; /0.01 px/ms = 315 ms WEST. Dec ~0 => no dec pulse.
    let a = e.ingest(&FrameMeta{timestamp_s:2.0,exposure_s:2.0}, &[star(105.0,100.0)]);
    match a {
        Action::PulsePair { ra: Some(p), dec } => {
            assert!(matches!(p.dir, Direction::West), "dir={:?}", p.dir);
            assert!((p.ms as i64 - 315).abs() <= 1, "ms={}", p.ms);
            assert!(dec.is_none() || dec.unwrap().ms == 0);
        }
        other => panic!("expected PulsePair, got {:?}", other),
    }
}

#[test]
fn deadband_yields_idle() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let m = FrameMeta{timestamp_s:0.0,exposure_s:2.0};
    let _ = e.ingest(&m, &[star(100.0,100.0)]);
    // 0.1 px error < min_move 0.2 on both axes => Idle.
    let a = e.ingest(&FrameMeta{timestamp_s:2.0,exposure_s:2.0}, &[star(100.1,100.05)]);
    assert!(matches!(a, Action::Idle), "got {:?}", a);
}

#[test]
fn ra_pulse_clamped_to_max_duration() {
    let mut cfg = EngineConfig::default();
    cfg.max_ra_duration_ms = 2500;
    let mut e = GuideEngine::new(cfg);
    let mut c = ident_cal(); c.x_rate = 0.001; // slow => long ms
    e.set_calibration(c);
    e.begin_guiding();
    let m = FrameMeta{timestamp_s:0.0,exposure_s:2.0};
    let _ = e.ingest(&m, &[star(100.0,100.0)]);
    let a = e.ingest(&FrameMeta{timestamp_s:2.0,exposure_s:2.0}, &[star(160.0,100.0)]);
    if let Action::PulsePair { ra: Some(p), .. } = a { assert_eq!(p.ms, 2500); }
    else { panic!("expected clamped RA pulse, got {:?}", a); }
}

#[test]
fn lost_star_returns_lock_lost() {
    let mut e = GuideEngine::new(EngineConfig::default());
    e.set_calibration(ident_cal());
    e.begin_guiding();
    let m = FrameMeta{timestamp_s:0.0,exposure_s:2.0};
    let _ = e.ingest(&m, &[star(100.0,100.0)]);
    // A not-found star for long enough exhausts recovery -> LockLost.
    let mut a = Action::Idle;
    for i in 1..30 {
        a = e.ingest(&FrameMeta{timestamp_s:i as f64 *2.0,exposure_s:2.0},
                     &[MeasuredStar{x:100.0,y:100.0,snr:0.0,mass:0.0,hfd:0.0,found:false}]);
    }
    assert!(matches!(a, Action::LockLost), "got {:?}", a);
}
```
- [ ] Run `cd native && cargo test -p astro-guide --test engine_scenarios` → EXPECT failure.
- [ ] Implement `settle.rs` (dossier §12) and `engine.rs`. Key composition per dossier §7 move pipeline: `xdir = if xd>0 {West} else {East}`, `ydir = if yd>0 {South} else {North}`; `x_ms = round(|xd/x_rate|)` with RA dec-compensation `x_rate = cal.x_rate * cos(dec_now)/cos(cal.declination)` (dossier §9.6; when dec unknown use `cal.x_rate`); dec-mode gating (§7); static BLC extra pulse on dec direction reversal (§10.1, `blc_pulse_ms`, adaptive controller OFF per D4); clamps to max_ra/dec_duration. `begin_calibration` drives the `Calibrator`; `ingest` returns `CalStep` while calibrating and transitions to guiding on `CalOutcome::Done`. `EngineConfig::default()` = dossier §15 defaults.
- [ ] Run the engine scenarios → EXPECT pass (4 tests). Run `cargo test -p astro-guide` → all green.
- [ ] Commit:
```
git add native/crates/astro-guide/src/engine.rs native/crates/astro-guide/src/settle.rs native/crates/astro-guide/src/lib.rs native/crates/astro-guide/tests/engine_scenarios.rs
git commit -m "feat(astro-guide): GuideEngine compose + move/pulse limiting + settle (dossier §7/§12)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P1-T8 — PyO3 surface: `GuideEngine` class + `guide_star_find`

**Files**
- Modify `C:\Users\bear\astro\native\crates\astrodeck-native\Cargo.toml` (add `astro-guide = { path = "../astro-guide" }`)
- Modify `C:\Users\bear\astro\native\crates\astrodeck-native\src\lib.rs` (add `GuideEngine` pyclass, `guide_star_find` pyfunction, register in `astrodeck_native` module)
- Create `C:\Users\bear\astro\server\tests\test_native_guide_surface.py`

**Interfaces**
- Produces (Python-visible, via the wheel):
  - `astrodeck_native.guide_star_find(frame, params=None) -> (stars, meta)` where `frame` is numpy `uint16` `(H,W)`; each star is `{x, y, snr, mass, hfd, peak}`; `meta` is `{sat_thresh}`. Mirrors `detect_and_measure`'s calling convention (GIL released for the heavy scan).
  - `astrodeck_native.GuideEngine(config)` pyclass with:
    - `.begin_calibration(x, y)`, `.begin_guiding()`
    - `.process(frame, timestamp_s, exposure_s) -> dict` — runs `measure` + `ingest`, returns the Action serialized: `{"action": "idle"|"pulse"|"pulse_pair"|"cal_step"|"settle"|"lock_lost", ...}`. For `pulse_pair`: `{"action":"pulse_pair","ra":{"dir":"west","ms":123}|null,"dec":{...}|null}`. For `cal_step`: `{"action":"cal_step","leg":"go_west","dir":"west","ms":750}`.
    - `.stats() -> dict` = `{guiding, rms_ra, rms_dec, rms_total, snr, recent:[[t,ra,dec],...]}`
    - `.dither(dx, dy)`, `.flip_calibration(requires_dec_flip) -> bool`
    - `.dump_calibration() -> dict | None`, `.load_calibration(dict)`
  - `config` dict mirrors `EngineConfig` (snake_case; missing keys take dossier defaults) with `ra_algorithm`/`dec_algorithm` string values (`"hysteresis"`, `"resist_switch"`, ...), `dec_guide_mode` (`"auto"`/`"off"`/`"north"`/`"south"`), `image_scale_arcsec`, `max_ra_duration_ms`, `max_dec_duration_ms`, `blc_pulse_ms`.
- Consumes: `astro_guide::{engine::GuideEngine, ...}`; the existing `build_params`/dict-helper idioms in `astrodeck-native/src/lib.rs` (`get_opt`, `get_req`, `override_field!`, GIL release via `py.allow_threads`).

**Steps** (bake in the PyO3 degrade-via-NATIVE_AVAILABLE watch-out at the Python test layer)

- [ ] Write the failing Python test `server/tests/test_native_guide_surface.py`:
```python
import math
import numpy as np
import pytest

native = pytest.importorskip("astrodeck_native")  # skip cleanly when wheel absent

def _gaussian_frame(w, h, cx, cy, amp=4000.0, sg=1.6, bg=100):
    yy, xx = np.mgrid[0:h, 0:w]
    g = amp * np.exp(-(((xx-cx)**2 + (yy-cy)**2) / (2*sg*sg)))
    return np.clip(bg + g, 0, 65535).astype(np.uint16)

def test_guide_star_find_returns_candidates():
    frame = _gaussian_frame(64, 64, 32.4, 30.6)
    stars, meta = native.guide_star_find(frame)
    assert len(stars) >= 1
    s = stars[0]
    assert abs(s["x"] - 32.4) < 0.6 and abs(s["y"] - 30.6) < 0.6
    assert s["snr"] > 5.0
    assert "sat_thresh" in meta

def test_engine_process_pulse_pair_shape():
    cfg = {"image_scale_arcsec": 2.0, "ra_algorithm": "hysteresis",
           "dec_algorithm": "resist_switch", "dec_guide_mode": "auto"}
    e = native.GuideEngine(cfg)
    # inject a calibration so the offset->pulse path is deterministic
    e.load_calibration({"x_rate": 0.01, "y_rate": 0.01, "x_angle": 0.0,
                        "y_angle": math.pi/2, "y_angle_error": 0.0,
                        "declination": 0.0, "pier_side": "west",
                        "ra_parity": "even", "dec_parity": "even",
                        "rotator_angle": 0.0, "binning": 1, "is_valid": True})
    e.begin_guiding()
    f0 = _gaussian_frame(64, 64, 32.0, 32.0)
    a0 = e.process(f0, 0.0, 2.0)  # establishes lock
    assert a0["action"] in ("idle", "pulse_pair", "settle")
    f1 = _gaussian_frame(64, 64, 37.0, 32.0)  # +5px in x
    a1 = e.process(f1, 2.0, 2.0)
    assert a1["action"] == "pulse_pair"
    assert a1["ra"] is not None and a1["ra"]["dir"] == "west"
    assert a1["ra"]["ms"] > 0

def test_calibration_roundtrips():
    e = native.GuideEngine({"image_scale_arcsec": 2.0})
    cal = {"x_rate": 0.02, "y_rate": 0.018, "x_angle": 0.1, "y_angle": 1.6,
           "y_angle_error": 0.0, "declination": 0.2, "pier_side": "east",
           "ra_parity": "odd", "dec_parity": "even", "rotator_angle": 0.0,
           "binning": 1, "is_valid": True}
    e.load_calibration(cal)
    out = e.dump_calibration()
    assert out is not None
    assert abs(out["x_rate"] - 0.02) < 1e-9 and out["pier_side"] == "east"
```
- [ ] Run `cd server && ./.venv/Scripts/python.exe -m pytest -q server/tests/test_native_guide_surface.py` → EXPECT failure (`AttributeError: module 'astrodeck_native' has no attribute 'GuideEngine'`) — the wheel currently lacks the surface.
- [ ] Add `astro-guide` to `astrodeck-native/Cargo.toml` deps. Implement the `GuideEngine` pyclass and `guide_star_find` pyfunction in `astrodeck-native/src/lib.rs` (follow the `FocusSweep` pyclass + `detect_and_measure` patterns already there: `get_opt`/`get_req`/`override_field!`, `py.allow_threads`, `PyValueError` for engine errors). Register both in the `#[pymodule] fn astrodeck_native`. Action→dict and Cal↔dict serializers as specified.
- [ ] Rebuild the wheel: `maturin develop --release -m native/crates/astrodeck-native/Cargo.toml` (into `server/.venv`). (Long command — run foreground; if it auto-backgrounds, re-run and confirm `Built wheel` / `Installed astrodeck_native`.)
- [ ] Run the Python test → EXPECT pass (3 tests).
- [ ] Run `cd native && cargo test --workspace` → EXPECT green (no crate regressions).
- [ ] Commit:
```
git add native/crates/astrodeck-native/Cargo.toml native/crates/astrodeck-native/src/lib.rs server/tests/test_native_guide_surface.py
git commit -m "feat(native): PyO3 GuideEngine + guide_star_find surface (spec §3.1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P1-T9 — Closed-loop `SimGuideCamera` + `SimTelescope` coupling (REQUIRED test infra)

**Files**
- Modify `C:\Users\bear\astro\server\astrodeck\devices\sim.py` (add `SimGuideCamera(Camera)` coupled to `SimRig` guide-star model; add periodic-error/drift/seeing state to `SimRig`; `SimTelescope.pulse_guide` already nudges `rig.ra_hours`/`rig.dec_deg` at :530-539 — extend `SimRig` with a guide-star pixel model the camera renders)
- Modify `C:\Users\bear\astro\server\astrodeck\devices\sim.py` `build_sim_rig` (:746-751) to render `guide_camera` via `SimGuideCamera` (keep the existing `"guide_camera"` key)
- Create `C:\Users\bear\astro\server\tests\test_sim_guide_camera.py`

**Interfaces**
- Produces:
  - `SimGuideCamera(Camera)` rendering a synthetic star field whose pixel positions respond to `SimTelescope` pointing (`rig.ra_hours`/`rig.dec_deg`) plus injected disturbances (drift rate, periodic error at a configurable worm period, seeing jitter). Config via device `extra` / `SimRig` fields: `guide_drift_px_s`, `guide_pe_amplitude_px`, `guide_pe_period_s`, `guide_seeing_px`.
  - `SimRig.guide_star_px(now_s) -> (x, y)` — the ground-truth guide-star pixel position: base center + integrated `pulse_guide` corrections (mount motion) + drift + PE(now) + seeing. This is the closed loop: `SimTelescope.pulse_guide` moves the model; `SimGuideCamera.expose` renders the star there.
- Consumes: the existing `SimCamera._render`/`_render_stars` Gaussian rendering (reuse for the guide star); `SimTelescope.pulse_guide` (:530-539) coupling into `SimRig`.

**Steps**

- [ ] Write the failing test `server/tests/test_sim_guide_camera.py`:
```python
import numpy as np
import pytest
from astrodeck.devices.sim import build_sim_rig

@pytest.mark.asyncio
async def test_guide_camera_star_moves_with_pulse():
    rig = build_sim_rig()
    cam = rig["guide_camera"]; tel = rig["telescope"]
    await cam.connect(); await tel.connect()
    frame0 = await cam.expose(1.0, 100, 30, binning=1)
    # locate brightest pixel
    a0 = np.asarray(frame0.data)
    y0, x0 = np.unravel_index(int(np.argmax(a0)), a0.shape)
    # a WEST pulse must shift the rendered star (closed loop)
    await tel.pulse_guide("west", 800)
    frame1 = await cam.expose(1.0, 100, 30, binning=1)
    a1 = np.asarray(frame1.data)
    y1, x1 = np.unravel_index(int(np.argmax(a1)), a1.shape)
    assert (abs(int(x1)-int(x0)) + abs(int(y1)-int(y0))) >= 2, "star did not respond to pulse"

@pytest.mark.asyncio
async def test_guide_camera_has_single_bright_star():
    rig = build_sim_rig()
    cam = rig["guide_camera"]
    await cam.connect()
    frame = await cam.expose(1.0, 100, 30, binning=1)
    a = np.asarray(frame.data)
    assert a.dtype == np.uint16
    assert a.max() > a.mean() + 500  # a clear star above background
```
- [ ] Run `cd server && ./.venv/Scripts/python.exe -m pytest -q server/tests/test_sim_guide_camera.py` → EXPECT failure (guide_camera is a plain `SimCamera`; a pulse does not measurably move the peak / no dedicated guide star).
- [ ] Add to `SimRig.__init__` the guide-star model fields (base center, `guide_drift_px_s=0.0`, `guide_pe_amplitude_px`, `guide_pe_period_s`, `guide_seeing_px`, an accumulated `_guide_offset_px` mutated by `pulse_guide`) and `guide_star_px(now_s)`. Extend `SimTelescope.pulse_guide` to also update `rig._guide_offset_px` by the same nudge (so mount motion moves the star in the guide frame; keep the RA/Dec nudge). Implement `SimGuideCamera(Camera)` rendering one Gaussian at `rig.guide_star_px(time.time())` on a flat noisy background (reuse the `SimCamera` Gaussian helper). Wire `build_sim_rig` to construct `SimGuideCamera` for the `"guide_camera"` key.
- [ ] Run the test → EXPECT pass (2 tests).
- [ ] Run `cd server && ./.venv/Scripts/python.exe -m pytest -q` → EXPECT `1127 + N passed` + 1 known failure. (If any existing sim test asserted the guide_camera was a `SimCamera` instance, update it — note it in the commit.)
- [ ] Commit:
```
git add server/astrodeck/devices/sim.py server/tests/test_sim_guide_camera.py
git commit -m "feat(sim): SimGuideCamera closed-loop star model coupled to SimTelescope (spec §3.4)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P1-T10 — `NativeGuider` loop + minimal provider/backend wiring + **P1 e2e gate (converge)**

**Files**
- Create `C:\Users\bear\astro\server\astrodeck\guide\native.py` (`NativeGuider(Guider)`)
- Modify `C:\Users\bear\astro\server\astrodeck\guide\__init__.py` (export `NativeGuider`)
- Modify `C:\Users\bear\astro\server\astrodeck\devices\backends\sim_backend.py` (`SimSession.native_guider`: return a `NativeGuider` over the sim guide camera + telescope when the wheel is present, else the existing `SimGuider`)
- Create `C:\Users\bear\astro\server\tests\test_native_guider_e2e.py`

**Interfaces**
- Produces: `NativeGuider(Guider)` implementing the full `Guider` ABC (`guide/base.py:17-77`): `connect`/`disconnect`/`start_guiding`/`stop_guiding`/`dither`/`stats`/`is_active`/`flip_calibration`/`guide_frame`; `can_flip_calibration = True`. Constructor `NativeGuider(guide_camera: Camera, telescope: Telescope, *, config: dict, profile_id: str | None = None)`. Owns the exposure→`process`→`pulse_guide`→`bus.publish("guide", ...)` loop (the `focus/native.py` pattern), publishing the SAME `GuideStats` shape. Cancellation honors `GUIDE_OP_TIMEOUT_S` (the sequence engine already waits on `start_guiding`/`dither`).
- Consumes: `astrodeck_native.GuideEngine` (guarded via `providers.NATIVE_AVAILABLE` + the local `try: import astrodeck_native` idiom from `focus/native.py:40-43`); `Camera.expose`, `Telescope.pulse_guide`/`guide_rates`/`can_pulse_guide`; `events.bus`; `GuideStats` from `guide/base.py`.

**Steps**

- [ ] Write the failing e2e test `server/tests/test_native_guider_e2e.py`:
```python
import numpy as np
import pytest
from astrodeck.devices.sim import build_sim_rig
from astrodeck.providers import NATIVE_AVAILABLE

pytestmark = pytest.mark.skipif(not NATIVE_AVAILABLE, reason="native wheel absent")

@pytest.mark.asyncio
async def test_native_guider_converges_on_sim():
    from astrodeck.guide.native import NativeGuider
    rig = build_sim_rig()
    cam = rig["guide_camera"]; tel = rig["telescope"]
    await cam.connect(); await tel.connect()
    # inject a modest drift the guider must cancel
    rig["_rig"].guide_drift_px_s = 0.15
    g = NativeGuider(cam, tel, config={"image_scale_arcsec": 2.0,
                                       "exposure_s": 0.2}, profile_id="test")
    await g.connect()
    await g.start_guiding()          # calibrate + settle
    assert await g.is_active()
    # run a while and assert RMS converges below a threshold
    import asyncio
    await asyncio.sleep(6.0)
    st = g.stats()
    assert st.guiding
    assert st.rms_total < 2.0, f"rms_total={st.rms_total}"
    await g.stop_guiding()
    await g.disconnect()
```
- [ ] Run `cd server && ./.venv/Scripts/python.exe -m pytest -q server/tests/test_native_guider_e2e.py` → EXPECT failure (`ModuleNotFoundError: astrodeck.guide.native`).
- [ ] Implement `guide/native.py`. Structure (mirroring `focus/native.py`): guarded `import astrodeck_native as _native`; `connect` verifies `telescope.can_pulse_guide` (spec §4: refuse to start with an actionable `DeviceError` when False); `start_guiding` builds the `GuideEngine` (image scale, algorithms, guide rates from `telescope.guide_rates()`), runs a calibration pass (loop: expose → `engine.process` returns `cal_step` → `telescope.pulse_guide(dir, ms)`) then transitions to a guide loop task that per frame: expose → `engine.process(frame.data, ts, exp)` → dispatch the Action (`pulse_pair` → up to two `pulse_guide` calls; `settle` → keep looping; `lock_lost` → bounded reacquire before reporting inactive) → `bus.publish("guide", **stats().__dict__)`. `stats()` maps `engine.stats()` → `GuideStats`. `dither`/`flip_calibration`/`guide_frame` per ABC (dither delegates to `engine.dither` + settle wait — full behavior lands in P2; P1 provides a working `dither` that shifts the lock and waits for settle). Persist calibration to `CONFIG_DIR/guider/<profile-id>.json` on calibrate (full reuse logic in P2).
- [ ] In `sim_backend.py` `SimSession.native_guider`: when `providers.NATIVE_AVAILABLE`, construct `NativeGuider(self._rig.get("guide_camera"), self._rig.get("telescope"), config={...}, profile_id=...)`; else keep returning `SimGuider`. Deferred import to keep module load light.
- [ ] Run the e2e test → EXPECT pass (RMS converges below 2.0 px).
- [ ] Run `cd server && ./.venv/Scripts/python.exe -m pytest -q` → EXPECT `1127 + N passed` + 1 known failure.
- [ ] Commit:
```
git add server/astrodeck/guide/native.py server/astrodeck/guide/__init__.py server/astrodeck/devices/backends/sim_backend.py server/tests/test_native_guider_e2e.py
git commit -m "feat(guide): NativeGuider loop + sim wiring — P1 gate: sim e2e converges (spec §3.2/§5)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

## Phase P2 — Sequence-engine parity on sim rig (dither/settle, recovery, flip, persistence, wiring, UI)

### P2-T1 — Dither + settle + fast-recenter (engine + NativeGuider.dither)

**Files**
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\engine.rs` (full `dither(dx,dy)`: lock-position shift + algorithm `reset()` + fast-recenter steps per dossier §11; settle inflation per §13; `GuidingDitherSettleDone` handling)
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\settle.rs` (if needed for dither settle params)
- Modify `C:\Users\bear\astro\native\crates\astrodeck-native\src\lib.rs` (`.dither(dx,dy)` + settle-state exposure in `process`/`stats`)
- Modify `C:\Users\bear\astro\server\astrodeck\guide\native.py` (`dither(pixels)`: `engine.dither` + wait for settle, matching PHD2Guider's settle-wait contract)
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\dither_scenarios.rs`
- Create `C:\Users\bear\astro\server\tests\test_native_guider_dither.py`

**Interfaces**
- Produces: `GuideEngine::dither(dx_px, dy_px)` shifts the lock position (mount-frame delta → camera via `mount_to_camera`, the 4-sign validity search of dossier §11.2), resets the axis algorithms (`GuidingDithered`), inflates the avg-distance stats by the dither distance, and arms a settle window; per-frame fast-recenter (dossier §11.2, `FastRecenter` default true, `0.7*max_move_px` steps). `process` reports `{"action":"settle"}` while settling and `stats()["settling"]` boolean.
- Consumes: P1 engine internals; `settle::Settle`.

**Steps**

- [ ] Write `native/crates/astro-guide/tests/dither_scenarios.rs`: after a `dither(5.0, 0.0)`, the lock shifts, algorithms reset (`last_move`==0), the next few frames emit recenter pulses, and `evaluate` reports `Settle` until the star is within tolerance for the settle time, then guiding resumes. Provenance: dossier §11/§12. Assert the settle transitions with an injected clock.
- [ ] Run `cd native && cargo test -p astro-guide --test dither_scenarios` → EXPECT failure.
- [ ] Implement the engine dither/settle/fast-recenter. Run the Rust test → EXPECT pass.
- [ ] Rebuild the wheel (`maturin develop --release ...`, foreground).
- [ ] Write `server/tests/test_native_guider_dither.py`: start guiding on the sim, call `await g.dither(3.0)`, assert it returns after settle and `stats().guiding` is True and recent samples show the dither displacement then reconvergence.
- [ ] Run the Python test → EXPECT pass. Implement `NativeGuider.dither` (call `engine.dither`, then poll until `stats()["settling"]` clears or `GUIDE_OP_TIMEOUT_S`, mirroring PHD2Guider's settle-wait shape at `guide/phd2.py:353-359`).
- [ ] Run the Python test → EXPECT pass. Run full `pytest -q` → `1127 + N` + 1 known failure.
- [ ] Commit (exact `git add` of the 6 files):
```
git add native/crates/astro-guide/src/engine.rs native/crates/astro-guide/src/settle.rs native/crates/astrodeck-native/src/lib.rs server/astrodeck/guide/native.py native/crates/astro-guide/tests/dither_scenarios.rs server/tests/test_native_guider_dither.py
git commit -m "feat(guide): dither + settle + fast-recenter (dossier §11/§12)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P2-T2 — Star-lost recovery + `flip_calibration` + calibration persistence + **P2 gate (recovery, flip, dither+settle e2e)**

**Files**
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\engine.rs` (`flip_calibration(requires_dec_flip)` per dossier §9.4; bounded LockLost→auto-reselect)
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\transforms.rs` (calibration flip math, if not already in engine)
- Modify `C:\Users\bear\astro\server\astrodeck\guide\native.py` (persistence: dump/load `CONFIG_DIR/guider/<profile-id>.json`; star-lost → bounded reacquire then report inactive so `_maybe_recover_guiding` keeps its semantics; `flip_calibration` delegates to the engine)
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\flip_golden.rs`
- Create `C:\Users\bear\astro\server\tests\test_native_guider_recovery.py`

**Interfaces**
- Produces: `GuideEngine::flip_calibration(requires_dec_flip: bool) -> bool` (`x_angle += π`; `y_angle += π` only when `requires_dec_flip`; dec parity flips unless dec-flip required; RA parity unchanged — dossier §9.4). `NativeGuider` persistence: on successful calibration write the serialized `Cal` (pier side, declination, rates, angles, binning) to `CONFIG_DIR/guider/<profile-id>.json`; on `start_guiding` reuse it if compatible (dossier §8 rules: same binning; declination present) else recalibrate. Star-lost: engine `LockLost` → NativeGuider attempts bounded auto-reselect (a few frames of `auto_find`) before flipping `stats().guiding` False.
- Consumes: `hub.meridian_flip` already calls `guider.flip_calibration` (`hub.py:1853-1863`); `_maybe_recover_guiding` (`sequence/engine.py:1908-1924`) already re-derives guiding from `plan.guide`.

**Steps**

- [ ] Write `native/crates/astro-guide/tests/flip_golden.rs`: an East-side calibration flipped to West adds π to `x_angle` (normalized), leaves `y_angle` unchanged when `requires_dec_flip=false` (adds π when true), flips dec parity (unless dec-flip), leaves RA parity. Exact vectors: `x_angle=0.3 → norm(0.3+π)`; assert with 1e-9. Provenance: dossier §9.4.
- [ ] Run `cd native && cargo test -p astro-guide --test flip_golden` → EXPECT failure. Implement. Run → EXPECT pass. Rebuild wheel (foreground).
- [ ] Write `server/tests/test_native_guider_recovery.py` (the **P2 e2e gate**): three sim scenarios —
  1. **star-lost recovery**: inject a cloud (blank the guide star for a few frames via `rig._rig.guide_star_hidden=True`), assert the guider reports not-active then recovers when the star returns and `stats().guiding` becomes True again.
  2. **meridian flip**: after `start_guiding`, call `await g.flip_calibration()` (returns True) and assert guiding resumes without a runaway (RMS stays bounded on continued frames).
  3. **calibration persistence round-trip**: after `start_guiding` calibrates, a NEW `NativeGuider` with the same `profile_id` reuses the persisted calibration (skips recalibration — assert via a spy/flag that no `cal_step` was issued on the second start).
- [ ] Run the Python test → EXPECT failure (recovery/persistence not implemented).
- [ ] Implement `NativeGuider` recovery + persistence + `flip_calibration`. Add the `SimRig.guide_star_hidden` hook to `sim.py` if needed for scenario 1 (the guide camera renders no star when hidden).
- [ ] Run the Python test → EXPECT pass (3 scenarios). Run full `pytest -q` → `1127 + N` + 1 known failure.
- [ ] Commit:
```
git add native/crates/astro-guide/src/engine.rs native/crates/astro-guide/src/transforms.rs server/astrodeck/guide/native.py server/astrodeck/devices/sim.py native/crates/astro-guide/tests/flip_golden.rs server/tests/test_native_guider_recovery.py
git commit -m "feat(guide): star-lost recovery + flip_calibration + cal persistence — P2 gate (dossier §8/§9)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P2-T3 — Provider/backend/UI wiring (`_resolve_guide`, native_backend guider role, GuideView)

**Files**
- Modify `C:\Users\bear\astro\server\astrodeck\config.py` (`ProvidersConfig`: add `guide: str = "auto"` at :278-281)
- Modify `C:\Users\bear\astro\server\astrodeck\providers.py` (add `"guide"` to `Capability` at :57; add `_resolve_guide`; register in `_RESOLVERS`; include `"guide"` in `resolve_all`'s loop at :298)
- Modify `C:\Users\bear\astro\server\astrodeck\devices\backend.py` (`ROLES`: add `"guide_camera"` at :28-37)
- Modify `C:\Users\bear\astro\server\astrodeck\devices\backends\native_backend.py` (add `"guider"` + `"guide_camera"` roles; `native_guider()` returns a `NativeGuider` over the assigned guide camera + telescope when the wheel is present and both connected)
- Modify `C:\Users\bear\astro\server\astrodeck\api\app.py` (add `/api/guide/calibrate`, `/api/guide/calibration` DELETE (clear), `/api/guide/settings` GET/PUT — with MODULE-SCOPE Pydantic body models)
- Modify `C:\Users\bear\astro\ui\src\views\GuideView.tsx` (calibration state/action, algorithm settings drawer, lock-star overlay, provider indicator)
- Create `C:\Users\bear\astro\ui\src\lib\guideSettings.ts` (pure helper: algorithm param schema + defaults + validation)
- Create `C:\Users\bear\astro\ui\src\lib\__tests__\guideSettings.test.ts`
- Create `C:\Users\bear\astro\server\tests\test_resolve_guide.py`

**Interfaces**
- Produces:
  - `providers.resolve("guide", hub) -> ProviderChoice` — `auto` → `astrodeck` when a `guide_camera` + `telescope` are connected and `NATIVE_AVAILABLE`; `backend` → the NINA/PHD2 guider (a NINA rig or a configured PHD2 driver); `sim` on sim rigs; falls back to `backend` (PHD2) when the wheel is absent (spec §3.3/§4 — never a crash). Mirrors `_resolve_autofocus` (`providers.py:173-206`).
  - `native_backend` fills `"guider"` (a `NativeGuider`) and `"guide_camera"` (an Alpaca camera by dev_num); `guide_camera()` returns the assigned camera.
  - UI: `guideSettings.ts` exports `defaultGuideSettings()`, `validateGuideSettings(s)`, `GUIDE_ALGORITHMS` (per-axis pick lists) with the dossier §15 PHD2-default parameter set.
- Consumes: `resolve_all` (`providers.py:293-307`) so `poll_status` badges the guide panel; `NativeGuider`; the existing GuideView store slice (`ui/src/store.ts:403`).

**Steps** (bake in: FastAPI body models MODULE-SCOPE — PEP-563 degrades function-local models to query params; UI pure tests use the guarded `process.exit`-via-`globalThis` idiom, no `@types/node`)

- [ ] Write `server/tests/test_resolve_guide.py`: a resolution matrix (sim rig → `sim`; native rig with guide_camera+mount+wheel → `astrodeck`; NINA rig → `backend`; wheel absent → `backend`/PHD2; explicit `backend` override honored; explicit `astrodeck` override with prerequisites absent degrades to auto). Follow the existing `_resolve_autofocus` test style.
- [ ] Run `cd server && ./.venv/Scripts/python.exe -m pytest -q server/tests/test_resolve_guide.py` → EXPECT failure (`guide` not a capability).
- [ ] Implement `ProvidersConfig.guide`, `providers._resolve_guide` + registration + `resolve_all` loop entry, `backend.py` `ROLES += ("guide_camera",)`, `native_backend.py` guider + guide_camera roles. Run → EXPECT pass.
- [ ] Write the UI pure test `ui/src/lib/__tests__/guideSettings.test.ts` (copy the header/`test`/`assert`/`process.exit` idiom EXACTLY from `ui/src/lib/__tests__/apiError.test.ts`): assert `defaultGuideSettings()` yields RA=Hysteresis(0.7/0.1/0.2), Dec=ResistSwitch(1.0), `validateGuideSettings` clamps out-of-range (aggression ≤ 2.0, hysteresis ≤ 0.99) and rejects unknown algorithm names.
- [ ] Run `cd ui && npx tsx src/lib/__tests__/guideSettings.test.ts` → EXPECT failure (module missing).
- [ ] Implement `ui/src/lib/guideSettings.ts` (pure, no React import). Run the pure test → EXPECT pass (`N passed, 0 failed`).
- [ ] Wire `GuideView.tsx`: Calibrate button + progress states + clear-calibration, algorithm settings drawer (per-axis pick + params from `guideSettings.ts`), lock-star overlay markers on the existing `GuideFramePreview`, provider indicator from `status.providers.guide`. Add the API endpoints in `app.py` with **module-scope** body models `class GuideSettingsBody(BaseModel): ...` (NOT defined inside the route function). Guard with `CAP_CONTROL_GUIDE`.
- [ ] Run `cd ui && npm run build` → EXPECT clean build.
- [ ] Run full `pytest -q` → `1127 + N` + 1 known failure.
- [ ] Commit:
```
git add server/astrodeck/config.py server/astrodeck/providers.py server/astrodeck/devices/backend.py server/astrodeck/devices/backends/native_backend.py server/astrodeck/api/app.py ui/src/views/GuideView.tsx ui/src/lib/guideSettings.ts ui/src/lib/__tests__/guideSettings.test.ts server/tests/test_resolve_guide.py
git commit -m "feat(guide): _resolve_guide + native guider role + GuideView calibration/settings UI (spec §3.3/§3.5)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

## Phase P3 — Multi-star tracking; lowpass; Z-filter

### P3-T1 — Multi-star tracking (`RefineOffset` + candidate management)

**Files**
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\select.rs` (multi-star candidate list §2.6, `max_stars` up to 12)
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\refine.rs` (`RefineOffset` §4)
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\engine.rs` (`ingest` uses secondaries: `measure` finds primary+secondaries, `RefineOffset` may replace `offset.camera`)
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\lib.rs`
- Modify `C:\Users\bear\astro\native\crates\astrodeck-native\src\lib.rs` (`process` accepts multi-star; `guide_star_find` returns the secondaries for the UI overlay)
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\refine_golden.rs`

**Interfaces**
- Produces: `pub struct SecondaryStar { pub reference_point: (f64,f64), pub was_lost: bool, pub zero_count: u32, pub miss_count: u32, ... }`; `pub fn refine_offset(primary_offset: (f64,f64), secondaries: &mut [SecondaryStar], measured: &[MeasuredStar], primary_snr: f64, primary_sigma: f64, lock_moved: bool) -> Option<(f64,f64)>` (dossier §4: stabilization gate at 5×/2× sigma, zero-count DZ at 5, miss-count re-baseline at 10, 2.5×sigma excursion, SNR-relative weights, only accept if it SHRINKS the offset). `select::auto_find` gains `max_stars` up to 12 (candidate list, 25-px dedup, `af_min_snr` 6.0).
- Consumes: P1 `select`/`track`/`engine`; `MeasuredStar`.

**Steps** (LARGE PORT — port exception; `guider_multistar.cpp:706-921`, dossier §4)

- [ ] Write `native/crates/astro-guide/tests/refine_golden.rs`: a primary + 3 secondaries with known per-star offsets; assert the SNR-weighted median shrinks the offset (returns `Some`), a secondary that jumps 2.5×sigma is skipped (miss_count++), a zero-on-both-axes secondary is erased (hot pixel), and the stabilization gate returns `None` until >5 samples collected. Provenance comments cite dossier §4 line-by-line. Because the dossier has no literal vector, DERIVE by hand-trace with a fixed sigma and 3 offsets; record the expected weighted average as a literal.
- [ ] Run `cd native && cargo test -p astro-guide --test refine_golden` → EXPECT failure. Implement `refine.rs` + multi-star `select`. Run → EXPECT pass.
- [ ] Update `engine.rs` `measure`/`ingest` to track secondaries and apply `refine_offset` (guarded: any panic path permanently drops to single-star for the session — dossier §4). Update the PyO3 `process`/`guide_star_find` to surface secondaries (`stats()`/overlay). Rebuild wheel (foreground).
- [ ] Add a Python smoke `server/tests/test_native_guide_multistar.py`: `guide_star_find` on a multi-star frame returns ≥2 candidates; a `GuideEngine` guiding with multi-star enabled still converges on the sim (extend the e2e). Run → EXPECT pass. Run full `pytest -q` → `1127 + N` + 1 known failure.
- [ ] Commit:
```
git add native/crates/astro-guide/src/refine.rs native/crates/astro-guide/src/select.rs native/crates/astro-guide/src/engine.rs native/crates/astro-guide/src/lib.rs native/crates/astrodeck-native/src/lib.rs native/crates/astro-guide/tests/refine_golden.rs server/tests/test_native_guide_multistar.py
git commit -m "feat(astro-guide): multi-star RefineOffset + candidate management (dossier §2.6/§4)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P3-T2 — Lowpass + Lowpass2 + ZFilter algorithms + **P3 gate**

**Files**
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\lowpass.rs` (Lowpass §6.3 + Lowpass2 §6.4)
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\zfilter.rs` (ZFilter §6.5 + mkfilter Bessel/Butterworth synthesis)
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\mod.rs` (register)
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\engine.rs` (`AlgoKind::{Lowpass, Lowpass2, ZFilter}` construction)
- Modify `C:\Users\bear\astro\native\crates\astrodeck-native\src\lib.rs` (accept the new algorithm strings)
- Modify `C:\Users\bear\astro\ui\src\lib\guideSettings.ts` (add lowpass/lowpass2/zfilter to the pick lists + params)
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\lowpass_zfilter_golden.rs`

**Interfaces**
- Produces: `Lowpass`, `Lowpass2`, `ZFilter` implementing `GuideAlgorithm`. `ZFilter::build(design, order, corner_period)` porting A.J. Fisher's mkfilter (dossier §6.5 Bessel pole table + Butterworth fallback when `corner<6.0`, bilinear transform, DC-gain normalization; min-move on OUTPUT). Defaults dossier §15.
- Consumes: `algorithms::GuideAlgorithm`; the Bessel pole table (dossier §6.5, transcribe verbatim).

**Steps** (LARGE PORT for ZFilter — port exception; `zfilterfactory.cpp:50-260`)

- [ ] Write `native/crates/astro-guide/tests/lowpass_zfilter_golden.rs`:
  - Lowpass2 step response (hand-computed from §6.4: warm-up `n<4` → `input*att`; the `>3` rejects reset quirk — dossier §17 "replicate code not comment"). Provide the exact literal outputs for a scripted input with `aggressiveness=80`, `min_move=0.2`.
  - ZFilter: assert the Butterworth-4 corner-4.0 filter (exp_factor 1.0) has DC gain 1.0 (a constant input eventually yields the same steady output through `result` reconstruction), and that a Bessel-4 corner-8.0 (exp_factor 2.0) coefficient set matches the transcribed poles (assert `xcoeffs`/`ycoeffs` first coefficients against literals computed by-hand-trace from the pole table — record with a provenance comment). Min-move on OUTPUT (§6.5): a below-min-move filtered result yields 0 and leaves `sum_corr` unchanged.
- [ ] Run `cd native && cargo test -p astro-guide --test lowpass_zfilter_golden` → EXPECT failure. Implement `lowpass.rs`, `zfilter.rs` (transcribe the Bessel table + mkfilter steps 1–6 verbatim; DO NOT implement the Chebyshev branch — dossier §6.5/§17 "dead/buggy"). Run → EXPECT pass.
- [ ] Register in `engine.rs` + PyO3 strings + UI settings. Rebuild wheel (foreground).
- [ ] **P3 gate** — extend the sim e2e (`server/tests/test_native_guider_e2e.py` or a new `test_native_guider_algorithms.py`): guide the sim with Dec=Lowpass2 and with RA=ZFilter, assert convergence below threshold for each (proves the algorithms are wired end-to-end and stable). Run → EXPECT pass.
- [ ] Run `cd ui && npm run build` → clean. Run full `pytest -q` → `1127 + N` + 1 known failure.
- [ ] Commit:
```
git add native/crates/astro-guide/src/algorithms/lowpass.rs native/crates/astro-guide/src/algorithms/zfilter.rs native/crates/astro-guide/src/algorithms/mod.rs native/crates/astro-guide/src/engine.rs native/crates/astrodeck-native/src/lib.rs ui/src/lib/guideSettings.ts native/crates/astro-guide/tests/lowpass_zfilter_golden.rs server/tests/test_native_guider_algorithms.py
git commit -m "feat(astro-guide): Lowpass/Lowpass2 + ZFilter (mkfilter Bessel/Butterworth) — P3 gate (dossier §6.3-6.5)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

## Phase P4 — GP/PPEC + static BLC

### P4-T1 — Gaussian-process predictive PEC port

**Files**
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\gaussian_process.rs`
- Create `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\gp_math.rs` (covariance kernels + regularizer + FFT period ID)
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\algorithms\mod.rs`
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\engine.rs` (`AlgoKind::Ppec`, RA-only per dossier §6.8; `deduce_result` wiring for dark reckoning)
- Modify `C:\Users\bear\astro\native\crates\astro-guide\Cargo.toml` (ensure `nalgebra` present; add an FFT crate `rustfft` to workspace deps if the plain DFT is too slow — otherwise a direct DFT is acceptable at ≤4096 points)
- Modify `C:\Users\bear\astro\native\crates\astrodeck-native\src\lib.rs` (`"ppec"`/`"gaussian_process"` algorithm string, RA-only validation)
- Modify `C:\Users\bear\astro\ui\src\lib\guideSettings.ts` (PPEC as an RA algorithm with the §6.8.1 parameter set)
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\gp_golden.rs`

**Interfaces**
- Produces:
  - `pub struct GpParams { ... }` — the dossier §6.8.1 defaults (control_gain 0.6, prediction_gain 0.5, min_move 0.2, SE0 700/20, periodic 10/200/20, SE1 25/10, min-periods 2.0/2.0, points 100, compute_period true, retain 40%).
  - `pub struct GaussianProcessGuider { ... }` implementing `GuideAlgorithm` with the §6.8.3 `result(input, snr, time_step)` (the trait's `result(input)` uses stored snr/dt; the engine passes snr/dt via a wider `result_with(input, snr, dt)` — add that method to the trait as `fn result_with(&mut self, input: f64, snr: f64, dt: f64) -> f64 { self.result(input) }` default so only PPEC overrides it), `reset`, and `deduce_result` (dark reckoning §6.8.3).
  - `gp_math`: `covariance(...)` (PeriodicSquareExponential2 inference kernel + PeriodicSquareExponential projection kernel, §6.8.4), `regularize(...)` (5-s grid, §6.8.4 step 2), `estimate_period(...)` (FFT + Hamming + 3-point quadratic interpolation, §6.8.5), GP inference (subset-of-data, LDLT, explicit linear trend).
- Consumes: `nalgebra` (LDLT solves, matrix algebra — the Eigen replacement); `algorithms::GuideAlgorithm`.

**Steps** (LARGEST PORT — port exception; upstream `contributions/MPI_IS_gaussian_process/src/*`, dossier §6.8)

- [ ] **Transcription source (pin exactly):** dossier §6.8.1–§6.8.6; upstream `contributions/MPI_IS_gaussian_process/src/gaussian_process_guider.cpp`, `gaussian_process.cpp`, `covariance_functions.cpp`, `tools/math_tools.cpp`; wrapper `src/guide_algorithm_gaussian_process.cpp`. Module layout: `gp_math.rs` (pure kernel/regularize/FFT/GP-inference; nalgebra) and `gaussian_process.rs` (the controller: circular buffer 8192, regularized 2048, grid 5.0 s, learning rate 0.01, hybrid hysteresis 0.1, the §6.8.3 control loop with warm-up ≤10 measurements, min-move zeroing only the reactive+hysteresis terms while the GP prediction is always applied, the smooth hysteresis→GP blend over the first N periods). Header per policy naming the two source files + dossier §6.8.
- [ ] Write `native/crates/astro-guide/tests/gp_golden.rs`. Because the dossier lacks literal GP vectors, DERIVE fixtures by exercising sub-pieces against known-analytic inputs (record with provenance comments — this is the by-hand-trace the port exception prescribes):
  - **Kernel**: `covariance` at `d=0` equals `σ_SE0² + σ_P² + σ_SE1²` (with defaults: `20² + 20² + 10² = 900`); at `d=P` (one period) the periodic term returns to its peak. Assert exact literals.
  - **variance_from_snr** (§6.8.2): `snr=10 → sd = 2.1752/(10-3.3)+0.5 = 0.8247...`, `variance = sd²`. Assert the exact literal.
  - **Regularizer**: a linear ramp on a 5-s grid integrates to the trapezoidal mean per cell — assert the recovered grid values.
  - **FFT period ID**: a pure sinusoid of period 200 s sampled on the 5-s grid recovers `P ≈ 200` within the interpolation tolerance (±5 s). Assert.
  - **End-to-end warm-up**: with `n_measurements ≤ 10`, `result_with(input, snr, dt)` returns `control_gain * input` (0.6·input) with min-move; below min_move → 0 for the reactive term.
  - **Prediction below deadband**: after >10 measurements on a strong periodic error, a below-min-move frame still returns a NON-zero correction (the GP prediction is always applied — dossier §6.8.3). Assert non-zero.
- [ ] Run `cd native && cargo test -p astro-guide --test gp_golden` → EXPECT failure. Implement `gp_math.rs` then `gaussian_process.rs`. Add `result_with` to the `GuideAlgorithm` trait (default delegates to `result`); PPEC overrides it. Run → EXPECT pass.
- [ ] Register `AlgoKind::Ppec` (RA-only — reject a Dec PPEC config with a clear error, dossier §6.8). Rebuild wheel (foreground). Add `"ppec"` to the PyO3 algorithm strings + UI RA pick list.
- [ ] Run `cd native && cargo test -p astro-guide` → all green. Run `cd ui && npm run build` → clean.
- [ ] Commit:
```
git add native/crates/astro-guide/src/algorithms/gaussian_process.rs native/crates/astro-guide/src/algorithms/gp_math.rs native/crates/astro-guide/src/algorithms/mod.rs native/crates/astro-guide/src/engine.rs native/crates/astro-guide/Cargo.toml native/crates/astrodeck-native/src/lib.rs ui/src/lib/guideSettings.ts native/crates/astro-guide/tests/gp_golden.rs native/Cargo.toml
git commit -m "feat(astro-guide): Gaussian-process PPEC port (nalgebra) (dossier §6.8; MPI-IS BSD-3)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

### P4-T2 — Static BLC + **P4 gate (PPEC beats hysteresis on injected PE)**

**Files**
- Modify `C:\Users\bear\astro\native\crates\astro-guide\src\engine.rs` (confirm static BLC apply on dec reversal is wired through `blc_pulse_ms`; adaptive controller stays OFF per D4 — assert this explicitly in code comments + a test)
- Modify `C:\Users\bear\astro\server\astrodeck\guide\native.py` (expose `blc_pulse_ms` via config; default 0 = disabled, like PHD2)
- Modify `C:\Users\bear\astro\ui\src\lib\guideSettings.ts` (static BLC pulse field, default 0)
- Create `C:\Users\bear\astro\native\crates\astro-guide\tests\blc_golden.rs`
- Create `C:\Users\bear\astro\server\tests\test_native_guider_ppec.py`

**Interfaces**
- Produces: static BLC — on a dec direction reversal, add `blc_pulse_ms` to the dec pulse (dossier §10.1); adaptive size controller (§10.2) explicitly not implemented (D4). `NativeGuider` config `blc_pulse_ms` (default 0).
- Consumes: P1 engine dec-pulse path; the sim PE injection (`SimRig.guide_pe_amplitude_px`/`guide_pe_period_s`).

**Steps**

- [ ] Write `native/crates/astro-guide/tests/blc_golden.rs`: with `blc_pulse_ms=200`, a dec correction that reverses direction (South after North) has 200 ms added to its `ms`; a same-direction correction does not; `blc_pulse_ms=0` never adds. Provenance: dossier §10.1. Assert exact ms.
- [ ] Run `cd native && cargo test -p astro-guide --test blc_golden` → EXPECT failure (if not already satisfied by P1). Implement/confirm static BLC. Run → EXPECT pass. Rebuild wheel (foreground).
- [ ] Write the **P4 gate** `server/tests/test_native_guider_ppec.py`: inject a strong periodic error (`guide_pe_amplitude_px=4.0`, `guide_pe_period_s=120`) on the sim, guide once with RA=Hysteresis and once with RA=PPEC over enough frames for PPEC to learn the period, and assert `rms_ra(ppec) < rms_ra(hysteresis)` (spec §5: "PPEC beats hysteresis on strong injected PE"). Allow a generous margin and enough sim time.
- [ ] Run the Python test → EXPECT failure until PPEC is wired through `NativeGuider`. Wire `blc_pulse_ms` + `ppec` config through `native.py`. Run → EXPECT pass.
- [ ] Run full `pytest -q` → `1127 + N` + 1 known failure. Run `cd ui && npm run build` → clean.
- [ ] Commit:
```
git add native/crates/astro-guide/src/engine.rs server/astrodeck/guide/native.py ui/src/lib/guideSettings.ts native/crates/astro-guide/tests/blc_golden.rs server/tests/test_native_guider_ppec.py
git commit -m "feat(guide): static BLC + PPEC wiring — P4 gate: PPEC beats hysteresis on injected PE (dossier §10.1/§6.8; D4)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

## Phase P5 — Docs + PHD2-fallback polish + head-to-head RMS affordance

### P5-T1 — Docs + provider-switch polish + same-night RMS comparison

**Files**
- Modify `C:\Users\bear\astro\docs\guide.md` (native guider: how it works, calibration, algorithm settings, PHD2 fallback selection) — create if absent; otherwise update the guiding section
- Modify `C:\Users\bear\astro\docs\native-parity\` (add a `native-guider.md` provider doc: architecture, phase status, parity notes, deferred items D2/D4)
- Modify `C:\Users\bear\astro\ui\src\views\GuideView.tsx` (provider-switch affordance: pick `astrodeck`/`backend`(PHD2) per-profile without leaving the view; a same-night head-to-head RMS readout comparing the last window under each provider)
- Create `C:\Users\bear\astro\ui\src\lib\rmsCompare.ts` (pure helper: given two labelled RMS windows, compute the comparison verdict)
- Create `C:\Users\bear\astro\ui\src\lib\__tests__\rmsCompare.test.ts`
- Modify `C:\Users\bear\astro\server\astrodeck\api\app.py` (if a small endpoint is needed to record/return the per-provider RMS window — MODULE-SCOPE body model)

**Interfaces**
- Produces: user + developer docs; a UI affordance to switch guide provider per-profile and see a same-night RMS comparison; `rmsCompare.ts` pure helper.
- Consumes: `providers.resolve("guide")` + the existing per-profile provider override (`Profile.providers`); the `guide` bus channel RMS history already in the store.

**Steps**

- [ ] Write the UI pure test `ui/src/lib/__tests__/rmsCompare.test.ts` (copy the `apiError.test.ts` idiom exactly, incl. the `globalThis` `process.exit` guard): `rmsCompare` returns "native better"/"PHD2 better"/"comparable" for representative RMS pairs and handles empty/insufficient windows (returns "insufficient data").
- [ ] Run `cd ui && npx tsx src/lib/__tests__/rmsCompare.test.ts` → EXPECT failure. Implement `rmsCompare.ts`. Run → EXPECT pass.
- [ ] Write `docs/guide.md` guiding section + `docs/native-parity/native-guider.md` (architecture, the P0–P4 parity coverage, deferred items with their decision refs: ST-4 output D2, adaptive BLC D4, NINA-rig native guiding D5). No code — a content checklist verified by `grep` (mentions "PHD2 fallback", "calibration persistence", "multi-star", "PPEC", "D2", "D4").
- [ ] Wire the GuideView provider-switch + RMS comparison affordance using `rmsCompare.ts`. Add the endpoint only if needed (module-scope body model).
- [ ] Run `cd ui && npm run build` → clean. Run full `pytest -q` → `1127 + N` + 1 known failure.
- [ ] Commit:
```
git add docs/guide.md docs/native-parity/native-guider.md ui/src/views/GuideView.tsx ui/src/lib/rmsCompare.ts ui/src/lib/__tests__/rmsCompare.test.ts server/astrodeck/api/app.py
git commit -m "docs+feat(guide): native-guider docs + PHD2-fallback switch + same-night RMS compare (spec §6 P5)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01M6wy4Lkm8yAoZWJWiyv6FL"
```

---

## Per-phase acceptance gates (spec §5 e2e sim scenarios)

- **P1 gate** (P1-T10): closed-loop sim — calibrate, guide, RMS converges below threshold. REQUIRES the P1-T9 `SimGuideCamera`/`SimTelescope` coupling first.
- **P2 gate** (P2-T2): dither+settle (P2-T1), star-lost recovery, meridian-flip calibration flip, calibration-persistence round-trip on the sim.
- **P3 gate** (P3-T2): sim converges with multi-star tracking active and with Lowpass2/ZFilter as the axis algorithms.
- **P4 gate** (P4-T2): PPEC beats hysteresis on strong injected periodic error (`rms_ra(ppec) < rms_ra(hysteresis)`).
- **P5**: user + dev docs, PHD2-fallback selection, same-night head-to-head RMS affordance.

## Self-review notes (performed while writing)

- **Spec coverage walk**: §0 decisions D1–D6 all mapped (D1 pure engine → P0-T2/P1-T7; D2 pulse-only → engine Action + NativeGuider; D3 core-first sequencing → P1-P3 before P4 PPEC; D4 static-BLC-only → P4-T2 with adaptive OFF; D5 NINA untouched → `_resolve_guide` `backend` branch, P2-T3; D6 guide_camera role → P2-T3 + P0-T3). §1 goals → P1-P4 gates. §3.1 crate modules all present (starfind/select/track/transforms/algorithms/calibration/engine/settle/refine/gaussian_process; PyO3 surface P1-T8). §3.2 NativeGuider → P1-T10/P2. §3.3 provider+backend+alpaca gap → P0-T3/P2-T3. §3.4 closed-loop sim → P1-T9. §3.5 UI → P2-T3/P5-T1. §3.6 licensing → P0-T1. §4 error handling → NativeGuider (P1-T10/P2-T2) + `_resolve_guide` fallback. §5 testing → golden vectors per task + sim e2e gates. §6 phasing P0-P5 → the six phases here.
- **Placeholder scan**: no TBD/TODO/"similar to task N"; the only bracketed shorthand is inside the THIRD-PARTY-NOTICES GP block, and P0-T1 explicitly instructs filling it with the verbatim clause text before commit (and a `grep` check that no bracket remains).
- **Type/signature consistency**: `Action` variants (`Idle`/`Pulse`/`PulsePair`/`CalStep`/`Settle`/`LockLost`), `Axis`/`Direction`/`AxisPulse`, `GuideEngine::ingest(&FrameMeta, &[MeasuredStar]) -> Action`, `GuideAlgorithm` trait, and the PyO3 `GuideEngine.process`/`guide_star_find` names are pinned in Global Constraints and used identically across P0-T2 → P1-T7 → P1-T8 → P2 → P3 → P4.
- **Phase gates present**: one explicit gate task/step per phase (above).
