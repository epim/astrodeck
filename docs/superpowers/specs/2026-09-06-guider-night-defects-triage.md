# Guider-night defects: triage and closure backlog (2026-09-06)

Source of truth for the ralph-loop closing out what the NGC 604 first night
(2026-09-05/06) exposed. Every item was verified on real frames, the night log,
or the API before it was written down; the evidence is banked as fixtures under
`server/tests/fixtures/ngc604_20260906/` (512x512 crops of last night's subs,
each with the human `VERDICT` in its header) and narrated in the memory note
`astrodeck-guider-runaway-ngc604`.

Every item resolves to exactly one terminal disposition:

- FIXED: code change landed, a test that FAILS on the old code and passes on
  the new one (prove it by reverting once), mapped CI job green locally.
- ACCEPTED: needs the sky or hardware to settle; document what was measured,
  the mitigation shipped, and what tonight's auto-resume run must show.

Ground rules, in force for every iteration:

1. All coding by subagents (opus for the guider/engine items, sonnet for the
   rest); the supervising session reviews every diff adversarially before
   commit and re-reads any diff attributed to a downgraded model.
2. TDD against the REAL fixtures first. A test that only exercises a double's
   flags does not count; the trailed fixtures must be rejected by the same
   code path that accepted them last night.
3. Sabotage-verify every FIXED item: revert the fix (stash), run the new test,
   confirm it fails, restore. Record the failing assertion in the commit body.
4. Commit locally per item with explicit pathspecs (`git commit -- <paths>`).
   No push, no deploy inside the loop; both are the supervisor's step after
   the loop, gated on the full local CI dry-run and `scripts/rig_precheck.py`.
5. Rust changes rebuild the wheel (`native` CI job locally) before the Python
   tests that depend on them are declared green.
6. The rig is parked and idle by day; nothing in the loop touches astrotown.
   The one at-scope validation is tonight's auto-resume of session
   `185864c1` (dormant, auto_resume=True, 140 owed), which re-arms at dusk.

## Status legend

`TODO` not started, `WIP` in progress, `DONE` terminal.

## Backlog

| ID | Sev | Disposition | Status | Acceptance criteria | CI job(s) |
|----|-----|-------------|--------|---------------------|-----------|
| GN-01 | High | FIXED | WIP | After a pier change the native guider RECALIBRATES instead of reusing a flipped calibration: `start_guiding` runs the calibration walk when the persisted/in-memory cal's `pier_side` differs from the mount's, and `flip_calibration` is never applied to a reused cal (config `guide.recalibrate_after_pier_change`, default True; the flip path stays available behind it for mounts that are known to need it). The engine's meridian-flip step must not persist a flipped cal that the restart then flips again (last night's 03:30 double flip). Tests: fake telescope reporting a changed pier -> calibrate called, flip not called, persisted file carries the NEW pier stamp; the pre-flip/post-flip sequence in the engine ends with exactly one calibration on the new side. Also: `DELETE /api/guide/calibration` must survive a stop and a flip (the 02:14 clear was overwritten). | server |
| GN-02 | High | FIXED | WIP | `zwo_am5.pulse_guide`'s stop command cannot be delayed by an event-loop stall: the stop is scheduled on a thread timer (or the serial writer thread) at pulse start, the coroutine still awaits the nominal duration, and a cap (`_PULSE_MAX_MS`, 1000) bounds any single move to <= 15 arcsec. Test: a fake transport records command timestamps; block the loop for 2 s while a 200 ms east pulse is in flight; the `:Te#` must arrive within 300 ms of `:Td#`. Same for `Qn`/`Qs`/`Qw`. Sabotage: revert -> stop arrives after ~2.2 s. | server |
| GN-03 | High | FIXED | TODO | `recover_guiding` re-locks are counted and surfaced: each re-lock logs a warning with the displacement, `GuideStats` carries `relocks` and `relock_arcsec_total`, and the sequence engine treats N re-locks (default 3) inside M minutes (default 10) as a guiding failure: pause capture, re-centre by plate solve, recalibrate, resume (the HOLD/RESUME checklist), instead of shooting a walking field. Tests: fake engine emitting star_lost + relock events -> stats increment, engine hold path entered; below the threshold nothing changes. | server |
| GN-04 | High | FIXED | TODO | Trailed subs are rejected. The engine's eccentricity gate (`_check_quality`, `max_eccentricity` imaging standard) is measured against the fixtures with the ENGINE's own metric (`frame_eccentricity(star_marks(...))`): pick a default that rejects `staircase_G60`, `doubleblob_L60`, `pedrift_L60` and accepts `clean_R60` and `jump_R60` (a single jump-then-settle is not an eccentricity signature; see the whole-frame table at the end), using the median ceiling plus the elongated-fraction companion rule from that table (report the numbers in the commit). Ship that default (0 stays "off" only when the operator sets it), make the flow compiler carry it into the run, and add a doctor warning when the standard is off. Tests: each fixture through the real grade+gate path. | server |
| GN-05 | High | FIXED | WIP | The autofocus size metric agrees with the grader's HFR at focus and is not fooled by resolved structure: when the fine-scale (level 1) star population is large enough, `star_size` votes with the same flux-weighted estimator over the same bright-star population `detect_stars` uses; coarse pyramid sources are used only when no stars resolve (real donuts). Tests on fixtures: `clean_R60` -> |star_size.radius - median_hfr| < 15%; `m33core_G60` -> no source at scale >= 8 while level-1 stars exist; `donut_L60` -> a size is still returned and exceeds the clean one by > 2x. Update the code comment that claimed agreement. Also: the fitted vertex is refused when it measures worse than a sampled point (already shipped, keep the test). | server |
| GN-06 | Med | FIXED | WIP | Calibration validity understands a reversed Dec axis: `y_angle_error` is folded PHD2-style (|e| > 90 deg -> e - 180, dec parity reversed) for the report, advisories and any sanity check; the transform (`camera_to_mount`) is unchanged and gets a regression test with a left-handed cal. The report shows ~2-5 deg for last night's calibrations instead of 172-178, and an actual > 15 deg error becomes an advisory. Tests: Rust unit tests on `Cal::y_angle_error_from` + fold; Python report test. | native, server |
| GN-07 | Med | FIXED | WIP | Sub headers stop lying: `OBJCTRA/OBJCTDEC` are written from the last plate-solved pointing (or the target when `pointing.verified`), the raw mount report goes to `MOUNTRA/MOUNTDEC`, and a `PNTGSRC` card says which. Test: fitsio writer with a mount reporting 50 arcmin off a solved pointing. | server |
| GN-08 | Med | FIXED | WIP | The HFR watchdog can be relative: `hfr_above` accepts a threshold expressed as a factor of the post-focus baseline (the HFR measured on the first accepted frame after each autofocus), the CONDITION node offers "HFR above (x focus)" and the flow wizard/templates default to 1.3x rather than 3.2 px, so a rig whose L runs at 3.5 px does not refocus every pass. Tests: instructions eval with baseline, to_plan mapping, wizard default. | server, ui |
| GN-09 | Low | FIXED | TODO | Doctor rule: a flow with no GUIDE node on a rig whose active mount driver declares `needs_guiding` (the AM5 harmonic drive: unguided 60 s subs trailed 15 px last night) warns "this mount needs guiding for subs over N s". Driver capability flag on the AM5 backend + sim; test on the doctor. | server |
| GN-10 | Low | ACCEPTED | DONE | The mount's reported RA/Dec walks during a run (up to 50 arcmin last night while the field held to a dither). Root cause is in the mount/driver, not this loop; GN-07 removes the damage from the data and the flip is already scheduled from the target and clock. Record the measurements and the at-scope checks still owed (pier_side semantics of the AM5 report, which GN-01 depends on for its "changed" test). | n/a (docs) |

## Per-item notes (evidence, seams, the failing test)

**GN-01 pier change.** Evidence: 00:43 (August cal at dec 60, stamped east,
flipped to west) and 03:30 (the engine's flip step flipped+persisted the
fresh west cal, then `start_guiding` reused it and flipped it again because
the mount still reported "west") both ran the field away within minutes;
fresh calibrations at 01:05 and 03:42 guided; the 02:47 one ran away within
10 min (B_0025, a staircase under DEFAULT loop params), which is the GN-02
class (pulse overrun on loop stalls) and not a flip. The 03:42 cal ran with
the damped params (RA aggr 0.4, Dec 0.5, min_move 0.4 px) that are still
live on the rig. Seams:
`server/astrodeck/guide/native.py` `start_guiding` (reuse branch ~555-612),
`_maybe_flip_for_pier` (864-885), the meridian-flip method that calls
`flip_calibration` and `_persist_calibration` (~1455-1475), `_persist_calibration`
(1559), `clear_calibration` (1588); `sequence/engine.py` `_maybe_meridian_flip`
(4497) and the post-flip guiding restart. Failing test first: fake telescope
whose `pier_side()` returns "east" then "west"; persisted cal stamped "east";
assert `_calibrate` called once, `flip_calibration` never, persisted stamp "west".

**GN-02 pulse stop.** Evidence: guider `recent` list +83" at 02:11:40 (S
readout) and +128"/+35" at 02:12:20 (thumbnail + wheel move), each decaying
over 12-30 s; `tail_Ha300` fixture. Seam: `devices/backends/zwo_am5.py`
`pulse_guide` (756-781), module notes at 66-85. The east pulse suspends
tracking (`:Td#`/`:Te#`), west is `R2`+`Mw`/`Qw`, dec is `R1`+`Mn|Ms`/`Qn|Qs`.
Failing test: fake `_request` that timestamps commands; `asyncio.get_running_loop().call_soon(time.sleep, 2.0)` right after the pulse starts; assert the stop's
timestamp - start < 0.3 s.

**GN-03 re-locks.** Evidence: RMS 2.3" reported while the field walked 40'
(headers and star matching). Seams: `guide/native.py` `_lost`/`_reacquire`
(312-317, 523-533), `stats()`; `sequence/engine.py` `_maybe_recover_guiding`
(~1908-1924) and the HOLD/RESUME checklist in `flows/nodes.py` holdresume.

**GN-04 eccentricity.** Evidence: `staircase_G60` passed at HFR 3.10, ecc
0.61 by `detect_stars`; the engine's own `frame_eccentricity` was never
consulted because the standard defaults to 0. Seams: `sequence/engine.py`
`_check_quality` (5505-5560), `sequence/policy.py` (`max_eccentricity`,
"standards" tier), `hub.py` 3354 (`frame_eccentricity(marks)`), the imaging
standards panel (#239). Measure the engine metric on every fixture FIRST and
put the table in the commit message.

**GN-05 autofocus metric.** Evidence table (same pixels, thirds of a frame):
L 4.07/3.94/3.84 grader vs 8.02/8.84/13.14 size; R 2.95/2.98/3.09 vs
4.06/4.39/6.74; G 3.14/3.21/3.22 vs 4.42/6.36/3.89, voting with 4-24 sources
at pyramid scales 2-16. Seams: `imaging/stars.py` `star_size` (763-870),
`_measure_source` (680-731, the comment claiming agreement), `_is_resolved`
(731); `focus/autofocus.py` `sweep_metric` (528), `_size_point`.

**GN-06 validity.** Evidence: three fresh calibrations reported ortho
174.9-178.2 deg, `is_valid` True, advisories only about rates. Seams:
`native/crates/astro-guide/src/transforms.rs` `y_angle_error_from` (111),
`camera_to_mount`/`mount_to_camera` (the |e| > pi/2 branch), `calibration.rs`
`sanity_advisories`; `guide/native.py` `calibration_report` (~1529-1550).

**GN-07 headers.** Evidence: `OBJCTDEC` +30 47 -> +31 51 across subs whose
star fields match within a dither. Seams: `imaging/fitsio.py` 122
(`OBJCTRA`), `hub.py` pointing state (`mount.pointing.verified`, `write_wcs`
at 2892).

**GN-08 relative watchdog.** Evidence: L at 3.5 px triggered an 8-minute
refocus every pass at the template's 3.2 px. Seams: `sequence/instructions.py`
`_eval_predicate` (160-175), `flows/to_plan.py` (~419), `flows/nodes.py`
condition params, `flows/wizard.py` defaults, the UI inspector for the node.

**GN-09 doctor.** Seams: `flows/doctor.py`, driver capability declarations in
`devices/backends/zwo_am5.py` and `devices/sim.py`.

## Per-iteration playbook (for the loop)

1. Pick the highest row whose Status is not DONE (High first, then Med, then
   Low; within a severity in table order). Set it WIP.
2. Dispatch a subagent with the row, its notes, the fixture list and the
   ground rules; it writes the failing test against the fixtures, implements
   the minimal change, runs the mapped CI job(s) locally against `server/.venv`
   (and rebuilds the wheel for `native` rows), and reports the diff.
3. Review the diff adversarially (does the test observe real behaviour? does
   the fix leave a dead branch reachable? any new claim nothing keeps?).
   Sabotage-verify. Then commit with explicit pathspecs and the failing
   assertion quoted in the body.
4. Set the row DONE and append the outcome to the memory note's "code owed"
   list (strike the item).
5. When every row is DONE, run the full local CI dry-run
   (`scratchpad/ci-local/run_ci_local.sh`); green means the loop is complete.

## After the loop (supervisor, not the loop)

Bump to 0.3.25, push, watch CI, cut the release (Windows binary by hand as
before), deploy to astrotown with `scripts/deploy_0325.ps1` derived from
`deploy_0324.ps1` (rig_precheck gate), before dusk. Tonight's auto-resume of
session 185864c1 is the field test: expect a fresh calibration after the flip,
no re-lock warnings, trailed subs rejected, headers carrying solved
coordinates. Read `captures/logs/<night>.jsonl` and the sub verdicts in the
morning.

## Loop invocation

```
bash ~/.claude/plugins/cache/claude-plugins-official/ralph-loop/1.0.0/scripts/setup-ralph-loop.sh \
  "Work docs/superpowers/specs/2026-09-06-guider-night-defects-triage.md: take the highest non-DONE row, dispatch a subagent to TDD it against the real fixtures in server/tests/fixtures/ngc604_20260906, review the diff adversarially, sabotage-verify, commit with explicit pathspecs, mark the row DONE. No push, no deploy, never touch astrotown. When every row is DONE and the full local CI dry-run is green, output the promise." \
  --max-iterations 30 --completion-promise "GUIDER NIGHT DEFECTS SHIPPED"
```

## Measured baselines on the fixtures (2026-09-06 08:30, repo HEAD)

Same code path the engine uses: `median_hfr` (grader), `frame_eccentricity(star_marks(detect_stars(...)))` (the `max_eccentricity` gate reads this), `star_size` (the autofocus sweep metric).

| fixture | verdict | grader HFR | engine ecc | autofocus size (n, scale) |
|---|---|---|---|---|
| clean_R60 | clean | 2.61 | 0.44 | 3.05 (10, 2) |
| jump_R60 | trailed (single jump) | 2.95 | 0.49 | 4.29 (1, 2) |
| tail_Ha300 | tailed | 2.96 | 0.56 | 3.43 (4, 4) |
| m33core_G60 | galaxy (no trail) | 3.21 | 0.74 | 4.75 (12, 2) |
| donut_L60 | defocused | 3.94 | 0.76 | 8.77 (1, 4) |
| doubleblob_L60 | trailed | 3.82 | 0.81 | 6.48 (1, 4) |
| pedrift_L60 | trailed | 4.24 | 0.81 | 4.75 (2, 2) |
| staircase_G60 | trailed | 3.43 | 0.86 | 19.23 (4, 16) |

Read-outs for the loop: a ceiling near 0.65 on the ENGINE ecc separates staircase / double-blob / PE-drift / donut from clean, but a galaxy-dominated crop reads 0.74 on its own, so GN-04 must grade on the mid-bright star population of a full frame (star_marks already gates on flux) and must show the full-frame number for the M33 field before choosing the default; a single jump-then-settle (0.49) is NOT caught by median eccentricity, so GN-03 (re-lock surfacing) and GN-02 (pulse cap) are what stop that class. The size metric is 17% high on the clean field and explodes on trails and coarse-scale structure, which is GN-05.

## Whole-frame eccentricity, every frame still on disk (2026-09-06 11:20, repo HEAD)

The engine grades FULL frames (6248x4176), and the crop numbers above overstate
the staircase: G_0003 reads 0.86 in its crop and 0.61 whole-frame, because the
box-truncated fainter stars of a staircase read as one round lobe. Measured with
the engine's own path (`frame_eccentricity(star_marks(detect_stars(data)))`)
plus the distribution behind it: median, the median over the 30 brightest,
and the fraction of mid-bright stars above 0.7 / 0.8. Verdicts by eye on the
3x zooms (scratchpad ngc604/<frame>_zoom_1.png).

| frame | verdict (eye) | HFR | ecc median | top-30 median | frac > 0.7 | frac > 0.8 |
|---|---|---|---|---|---|---|
| R_0007 | clean | 2.73 | 0.51 | 0.51 | 0.03 | 0.01 |
| R_0030 | clean | 2.94 | 0.56 | 0.54 | 0.04 | 0.00 |
| S_0005 | clean | 2.80 | 0.51 | 0.50 | 0.01 | 0.00 |
| R_0002 | jump then settle | 2.98 | 0.51 | 0.52 | 0.04 | 0.02 |
| S_0017 | jump with a hook tail | 2.95 | 0.50 | 0.52 | 0.10 | 0.10 |
| Ha_0018 | faint tail (300 s) | 2.83 | 0.62 | 0.63 | 0.17 | 0.12 |
| B_0032 | mild smear | 3.69 | 0.65 | 0.65 | 0.30 | 0.01 |
| G_0003 | staircase | 3.10 | 0.61 | 0.51 | 0.42 | 0.37 |
| G_0031 | staircase | 3.78 | 0.67 | 0.65 | 0.43 | 0.26 |
| B_0025 | staircase | 4.07 | 0.74 | 0.70 | 0.61 | 0.27 |
| B_0004 | staircase | 3.38 | 0.77 | 0.78 | 0.74 | 0.38 |
| L_0006 | double blob | 4.05 | 0.84 | 0.83 | 0.99 | 0.84 |
| L_0026 | PE drift, unguided | 4.13 | 0.87 | 0.83 | 0.95 | 0.93 |
| L_0001 | donut | 4.06 | 0.86 | 0.85 | 0.97 | 0.84 |

Read-out for GN-04: no single median ceiling has margin on both sides (clean
tops out at 0.56, the staircase G_0003 sits at 0.61). Two statistics tied to
ONE dial do: reject when the median exceeds `max_eccentricity` (default 0.65,
clean margin 0.09) OR when more than 25% of the mid-bright stars exceed
`max_eccentricity + 0.15` (0.80 at the default; clean tops out at 10%, the
mildest staircase reads 26%). That catches every staircase and blob, accepts
every clean frame, and knowingly passes the jump-then-settle class (R_0002,
S_0017, Ha_0018) and the mild smear B_0032, which GN-02 and GN-03 address at
the source. The crop fixture `jump_R60` therefore CANNOT be a must-reject for
GN-04; its row is corrected below. `m33core_G60` is a crop of the staircase
frame G_0003 and is rightly rejected.
