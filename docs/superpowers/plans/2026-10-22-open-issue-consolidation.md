# AstroDeck open-issue consolidation plan (2026-10-22)

Written for a Sonnet 5.5 coordinator running Sonnet 5.5 coders and verifiers. Every step below is a command or a brief a Sonnet agent can execute without inventing architecture. Where a decision is the owner's, the plan names the default and goes on.

## Goal

Resolve the 168 issues open on 2026-10-22 (#16 to #1106). On that day they were read, classified (scratchpad `plan/classified*.jsonl`, not in the repo) and grouped. 117 of them share a design with at least one neighbour and were combined into 23 new issues, #1107 to #1129, each of which names its members, the one solution, and what done means. The remaining 51 are close-outs already fixed on main, rig-gated measurements, owner-gated rulings, the mosaic feature set under #189, the release tracker #1073, and two large features.

The combined issues are the work. This plan orders them into waves, splits the large ones into Sonnet-sized work packages, and says how each wave runs.

## State on 2026-10-22

- `origin/main` = 3c865bbf (release 0.3.46, published with a signed source tarball). Main CI green.
- The working tree `C:\Users\bear\astro` is on `feat/photosphere-production`, fully merged and 369 commits behind main, with a large dirty untracked set. Never build on it. Every worktree is cut from `origin/main`.
- 65 worktrees are registered, most with missing directories: run `git worktree prune` first; remove finished scratchpad worktrees (junction inside `ui/node_modules` first, with `cmd //c rmdir <path>`, then `git worktree remove`).
- The rig (astrotown) is powered off until the owner turns it on; it runs 0.3.45. Self-update to 0.3.46 needs the release-signing public key pinned in Settings > Updates. Rig-gated items wait.
- The auto-mode classifier refuses some scripted GitHub writes (`gh issue create` from a Python loop was refused; the same command typed directly was allowed). Make GitHub writes as direct `gh` commands, one per call, not from scripts.

## The combined issues (C-nn is the plan's short name; the number is the GitHub issue)

| C | Issue | Title | Members | Size |
|---|---|---|---|---|
| C-01 | #1107 | Guide quality owns star identity, settle and the per-exposure verdict | #1083 #1096 #1095 #1085 #1089 #1078 #1066 | XL, split |
| C-02 | #1108 | One classifier for a starless frame | #1093 #599 #721 #1077 #817 #308 | XL, split |
| C-03 | #1109 | Recovery ladder when the mount is lost or halted | #1098 #857 #880 #144 | L |
| C-04 | #1110 | Angle-keyed flats end to end | #176 #783 #784 #825 #785 #827 | L, split |
| C-05 | #1111 | Calibration stages the quick sheet promises | #603 #843 #845 #818 #820 | L+, split |
| C-06 | #1112 | Tonight and campaign notes derive from the compiled plan | #819 #775 #772 #773 #777 #778 #730 #809 #830 | M |
| C-07 | #1113 | Mosaic live stacks: budget, picker, assembled preview | #172 #779 #780 #781 #782 | L, split |
| C-08 | #1114 | Mosaic identity through the frame ledger and reports | #188 #795 #796 #797 | M |
| C-09 | #1115 | Compiler fidelity: the structure the editor draws reaches the engine | #1074 #774 #183 | L+ |
| C-10 | #1116 | Rotator-corrected groups: tolerance, labels, fallback | #787 #828 #788 #790 #806 | M |
| C-11 | #1117 | Photosphere scanner publishes only what it measured | #900 #1058 #1059 #904 #903 #902 #905 #1060 | XL, split |
| C-12 | #1118 | Panorama simulator integrity | #901 #953 #954 #975 #1062 | M |
| C-13 | #1119 | Tests that fail only under parallel full-suite load | #1099 #755 #756 #909 #916 #1079 #1081 #700 #442 #754 #914 | L, split |
| C-14 | #1120 | Date replay: the Sun cone, file ages and the config guard | #1069 #1070 #807 | M |
| C-15 | #1121 | Test strength ratchets | #978 #805 #1019 #720 | L |
| C-16 | #1122 | Test tooling hygiene | #740 #741 #802 #776 #753 #715 #716 #757 #808 | M |
| C-17 | #1123 | The container image ships the native engine | #655 #799 #800 #798 | L |
| C-18 | #1124 | Flow editor input: modifier keys and undo on every surface | #769 #771 #829 | M |
| C-19 | #1125 | Live stage status has one writer | #464 #742 | M |
| C-20 | #1126 | Observatory envelope: open, bind the dome, close safely | #601 #602 #893 #657 #192 | L |
| C-21 | #1127 | Site-derived moments reach only cleared principals | #748 #1067 | M |
| C-22 | #1128 | Rig availability: Windows Update, off-box evidence | #1076 #606 #125 (#605 closed, D-J) | M |
| C-23 | #1129 | Hardening leftovers | #831 #794 #1068 | S |

Each combined issue's body holds the design and the done criteria. A coder's brief points at the combined issue and lists which members its WP covers; the coder reads the members for the evidence.

## How a wave runs (the coordinator's loop)

1. `git -C C:/Users/bear/astro fetch origin` and record BASE = `origin/main`.
2. For each WP: `git -C C:/Users/bear/astro worktree add -b wave<N>/<WP> <scratchpad>/wt/w<N>-<WP> origin/main` (absolute path; a relative path lands inside the repo). UI WPs get `ui/node_modules` as a junction to an installed copy (`cmd //c mklink /J <wt>/ui/node_modules <installed>/ui/node_modules`).
3. Dispatch one Sonnet coder per WP (`model: sonnet`, set explicitly) with the brief: the combined issue number, the member numbers it covers, the owned files, the named mutant it must show red, the test commands, and the report path. Coders never commit, push, deploy, ssh or write to GitHub. Coders never write the site's latitude, longitude or label, nor any mount alt/az, into anything.
4. Dispatch one Sonnet verifier per finished WP: it rereads the issues, reruns the mutant from a byte backup, reruns the owned-area tests, and checks `git status` shows only owned files changed.
5. Merge the verified WPs one after the other onto an integration worktree cut from BASE. After each merge that touches `engine.py`, `app.py` or `hub.py`, rerun the server suite before the next merge (the relaxation rule below).
6. On the integrated tree run once: `server/.venv/Scripts/python.exe -m pytest tests -n auto -q -p no:cacheprovider` (from `<wt>/server`, with `-o pythonpath=<wt>/server`), `node ui/run-tests.mjs`, `npm --prefix ui run typecheck`, `npm --prefix ui run build`, the relay suite, `python tools/docs/check_docs.py`, and `python tools/gen_credits.py --check` when credits inputs changed. Fix integration defects in the integration worktree and file each as an issue.
7. Scans over every changed file: `grep -l -F -f C:/Users/bear/.astrodeck/privacy-needles.txt <files>` (file names only, and assert the file list is non-empty), a BOM scan, an emoji scan, a non-ASCII scan on new files.
8. Commit each WP separately with explicit pathspecs: `git commit -F <msg> -- <paths>`. Push the integration branch, `gh pr create` to main, wait for the PR's checks, `gh pr merge --merge` when CLEAN (owner's standing rule: merge your own passing PRs).
9. Close each member issue with the commit hash and the proving tests; close the combined issue when every non-gated member is closed; leave rig-gated and owner-gated members open with a comment saying what remains.
10. File every new defect found (check `gh issue list --search` first).

Relaxation rule for `engine.py`, `app.py` and `hub.py`: one WP per wave owns each file. A second WP may edit the same file only if the functions or routes it names are disjoint from the owner's, and the coordinator merges the two serially with a server-suite run between. The wave tables below mark the owner with (owner) and the sharers with (shared: <functions>).

Rules every WP follows (unchanged from the 2026-09-30 plan, section "Rules for every work package"): tests first, a named mutant shown red, owned files only, no commits by coders, made-up site in tests, UTF-8 no BOM, no emojis, the two-line copyright header on new files, mutation runs from a byte backup inside the worktree.

Model rule: coders and verifiers are Sonnet 5.5; Haiku may run the scans and the close-out commands. Never Opus.

Cost rule: full suites run once per wave on the integrated tree, never per WP. Wave 17's lean verifier (reread, mutant, owned tests, status check; no second full suite) is the verifier shape.

## Wave 0 (coordinator alone, no code)

1. Prune worktrees (see State). Create `<scratchpad>/wt/` fresh.
2. Close-outs, each after running the evidence command and reading the issue once:
   - #632, #636, #637: `git show origin/main:packaging/distribution-policy.json` shows `playerone`, `astrospheric` and `artwork` as `redistribute` with the 2026-10-20 ruling comment URLs. Close with that line and 8890a438.
   - #767: `git show origin/main:tools/licence/release_provenance.py | grep -n resolve` shows root, base and path resolved. Close with the lines.
   - #824: `tools/licence/audit_artifact.py` on main has `content_sha256` (CRLF to LF for text payloads) and the `ngc_extras.tsv` registry row was regenerated for 0.3.46; the source gate reports 0 blockers. Close with 8890a438 and the gate line from the v0.3.46 run.
   - #868: `docs/hardware/zwo-am5-lx200-protocol.md` on main says :GAT# is the tracking flag. Close.
   - #869: `zwo_am5.py` on main has `GOTO_UNKNOWN_REFUSAL_REASON`. Close.
   - #875: `FlowsScreen.tsx` on main guards `loadLibrary` on `libraryLoading`. Close; note the durable single-owner load as a C-19 follow-up if the verifier sees a second load.
   - #876: `hub.py` on main has `_JNOW_REPROBE_HOLDOFF_S = 60.0` and definite-answer caching. Close.
   - #810: fixed under #729 (shared helper in `GroupRun`). Close.
   - #730: the owner's 2026-10-08 comment names both seams fixed (6a8e558f, ebfd6068); the remaining wording is a C-06 member. Close #730 itself and leave the wording to #1112.
   - #175: WP-124 built per-panel convergence end to end; CROTA2 sense confirmed 2026-10-07. Close.
   - #254: D-01 tooling (gate_run.py, worktree tooling) is on main. Close.
3. Comment on #1073 that its remaining parts are: frozen-binary provenance backlog (its own body), #638 (owner), #1091 and #801 (wave 1, WP-1.8), and the WCSLIB data filter in `packaging/astrodeck.spec` (`a.datas` filter after Analysis, recorded in `release-reviewed-inputs.json`).
4. Done 2026-10-22: the decision list was posted on PR #1130 and the owner answered the same day; the rulings below are folded in. Before each wave, read PR #1130 and the three pending issues (#638, #718, #826) for the owner's replies to the explanations.
5. Install `py-spy` into a scratch venv (not the shared `.venv`) for WP-1.3's hang diagnosis.

## Owner rulings (answered on PR #1130, 2026-10-22; RULED lines are binding, the rest proceed on the default in bold)

- D-A (#880, C-03) RULED: a manual STOP or a manual move during a run **pauses the run until the operator resumes it**. While paused nothing automatic happens: no re-centre, no next target, no auto-resume restart. WP 3.1 builds a `paused_by_operator` ladder state whose only exit is an operator resume.
- D-B (#893, C-20) RULED: **the roof never closes unless the mount is confirmed parked, whatever the link state**. With the link down, `close_observatory` reopens the link once and reads the park state; if it cannot confirm, it refuses, leaves the roof open and pages. No park-record shortcut and no close-anyway path: a collision costs more than rain. WP 4.4.
- D-C (#526) RULED: the 2026-09-28 symptom was the camera loose in the rotator. **#526 closed as that cause; `ROTATOR_BACKLASH_DEG` unchanged, no code.**
- D-D (#1094) RULED: two ways to run "this, then that": **one flow with both targets in series, or two flows queued back to back**. Each target gets a bound of **frame count or maximum time, whichever comes first**. A planned ending (the time bound, stop time, `max_run_min`, window end) promotes the queued flow; an abort or a fault does not, and the log names the queued session when a run ends without promoting it. New WP 3.10.
- D-E (#180 Part B) RULED: a failed panel is **skipped for the next panel, then revisited; if it fails again, the next panel that still needs frames, then back, and so on. Never dropped.** WP 6.2.
- D-F (#638): explanation posted on PR #1130, awaiting the owner. Default: `astropy.wcs` is out of the runtime (#1103) and out of the build (`packaging/astrodeck.spec` excludes `astropy/wcs/`), so **WCSLIB is not shipped and the pending record resolves to not-shipped**; the GPL-with-GCC-exception runtime inside OpenBLAS gets its notice text in THIRD-PARTY-NOTICES.md. Open sub-question: the executables on releases v0.3.23 through v0.3.28 did contain WCSLIB; leave them (recommended) or delete those assets.
- D-G (#718, XL): explanation posted, awaiting the owner. Default: **deferred past wave 6**. Recommended alternative: a small WP that adds the two missing questions (how the night ends, auto-resume) to NEW FLOW's own sheet.
- D-H (#748, #1067, C-21) RULED: **split**. Each dawn-park or sun-watch event writes a night-file line carrying the site-computed moment, flagged `site_derived` and withheld from uncleared principals, and a separate alert-safe wording without the moment that the alert sinks forward. #1067's flip-hold edges stay gated as planned. WP 2.9.
- D-I (#826): explanation posted, awaiting the owner. Default: `solve_saved_lights` **defaults on when a plate solver is configured**, and `rotator_bin_deg` gets a Settings control. WP 3.9.
- D-J (#605) RULED: out of scope and not a GitHub matter. **#605 closed as not planned and removed from C-22.**
- D-K (#823) RULED: the owner cancels the stuck Pages job; the workflow still gets `cancel-in-progress: true` on its concurrency group (WP 1.10).
- D-L (#766, XL) RULED: proceed. **WP 6.3 produces a spec and a fixture set, no code until the owner reads it.**

## Wave 1 (foundations; engine owner C-01)

| WP | Issue | Covers | Owned files | Size | Mutant |
|---|---|---|---|---|---|
| 1.1 | #1107 C-01a | #1085 #1089 #1083 | `native/crates/astro-guide/src/{engine,track,types}.rs` (stats export: HFD, flux, lock centroid), `server/astrodeck/guide/native.py` (star identity after a loss, runaway detector reads it), `server/astrodeck/guide/flight_recorder.py` (hfr, flux, timestamped mount position, model rates), tests `test_w1_guide_identity*.py`, Rust golden tests | L | drop the identity check: the injected star swap is accepted |
| 1.2 | #1107 C-01b | #1096 #1095 #1078 #1066, plus #831's lazy engine attribute | `server/astrodeck/sequence/engine.py` (owner: GuideVerdict over the exposure window, settle after calibration, `_record_session_frame` eccentricity and reject_reason, guide_rms None when idle, `_maybe_recover_guiding` wording, the attribute initialised in `__init__`), `server/astrodeck/sequence/models.py` (FrameRecord fields), `server/astrodeck/hub.py` (owner: `rotate_to_pa` warning by guider type) | L | widen the verdict window back to the rolling 150 s: the over-ceiling frame passes |
| 1.3 | #1119 C-13a | #755 #756 #1081 #442 #909 #916 #700 #1079 | `server/tests/_group_harness.py`, `server/tests/conftest.py` (owner), `server/pytest.ini` or `pyproject` pytest section (basetemp per worker), the listed test files, `server/tests/_native_fixtures.py` (new; explicit close of NativeGuider) | L | remove the contention factor: the spin case fails under 40 burners |
| 1.4 | #1119 C-13b | #1099 | `server/astrodeck/sequence/report.py` (await the write), `server/tests/test_s7_sim_*.py`, `server/tests/test_1071_privacy.py` (leak mutant under load) | M | drop the await: the count mismatch returns |
| 1.5 | #1122 C-16 + the UI half of C-13 | #740 #741 #802 #776 #753 #754 #914 #715 #716 #757 #808 | `scripts/mutate.py`, `tools/licence/mutate_gates.py`, `server/tests/test_clean_tree_gate.py`, `ui/run-tests.mjs`, `ui/tools/r7parity*`, the bridge constant's module, `server/tests/_flow_night.py` (Night records to close), `server/tests/test_w1_thread_platform_ratchet.py` (new) | M | one per member, listed in the brief |
| 1.6 | #1129 C-23 | #831 (all but the engine line) #794 #1068 | `server/astrodeck/api/app.py` (owner: rotator reverse route 409 during a run), `server/astrodeck/config.py` (deadman_url validation), `server/astrodeck/imaging/fitsio.py` (degenerate CD refused), the audit module (`record(category=...)`), `server/astrodeck/factory_reset.py` (`*.bak`) | S | reset leaves the .bak: the directory listing test fails |
| 1.7 | #1124 C-18 | #769 #771 #829 (keys and undo only) | `ui/src/lib/keys.ts` (new), `ui/src/store.ts` (history), the flow editor key bindings, the phone FLOW tab and the #/next stage sheet, DOM tests | M | drop the modifier check: Ctrl+F is swallowed |
| 1.8 | #1123 C-17 + #1073 parts | #655 #799 #800 #798 #1091 #801 | `Dockerfile`, `docker-compose*.yml`, `.github/workflows/release.yml` (candidate tag cleanup, image gate step), `scripts/build_release.py` (LF manifest and NOTICE), `tools/licence/audit_artifact.py --image`, `docs/install/*docker*`, `tools/docs/setup-claims.json`, `tools/licence/release-reviewed-inputs.json` | L | build without the wheel succeeds silently: the compose test fails |
| 1.9 | #1118 C-12 | #901 #953 #954 #975 #1062 | `tools/photosphere_sim/**`, its tests, `CONTRACT.md`, the two fixtures regenerated | M | ignore the seed: two seeds give one draw |
| 1.10 | #1106, #823 | relay pip-audit 503; Pages concurrency | `.github/workflows/relay*.yml` (retry pip-audit three times, then warn and pass on a 5xx from the index, fail on a finding), `.github/workflows/pages*.yml` (`cancel-in-progress: true`) | S | treat a 503 as a finding: the fixture run fails |
| 1.11 | #804 | OpenNGC snapshot pin | the catalog build script under `tools/`, `server/astrodeck/catalog/data/ngc*.tsv` (regenerated from the pinned commit), `tools/licence/artifact-registry.json` rows for the two files (exact hashes), `tools/licence/release-gate-review.md` (a delta note) | M | drop the pin: the build reads HEAD and the row-count test fails |

Release checkpoint after wave 1 is not needed; wave 1 is foundations.

## Wave 2 (engine owner C-02; conftest owner C-14; app.py owner C-04)

WP-2.1 runs first and merges before the rest of the wave starts (the others import it).

| WP | Issue | Covers | Owned files | Size | Mutant |
|---|---|---|---|---|---|
| 2.1 | #1108 C-02a | the classifier | `server/astrodeck/imaging/frame_class.py` (new: `classify(frame, context) -> FrameClass`), `server/tests/fixtures/frame_class/` (synthetic frames: stars, no light, obstructed edge, cloud), `tools/no_light_audit.py` (class column), `test_w2_frame_class.py` | M | swap the obstruction edge test: the obstructed fixture reads no_light |
| 2.2 | #1108 C-02b | #1093 #721 #1077 #817 | `server/astrodeck/sequence/engine.py` (owner: centring hold and no-light hold call the classifier; `solver_missing` ends the hold as a fault; the autofocus sweep judge keeps a slope whose failed points are no_light or obstructed and reports a found minimum as found; `_return_wheel_after_solve` message), `server/tests/test_w2_holds_classified.py`, the autofocus scenario test | L | skip the classifier in the hold: the obstruction night blames the sky |
| 2.3 | #1120 C-14 | #1069 #1070 #807 | `server/tests/_sky.py` (new target chooser), the 18 files that aim at RA 5 h Dec +10 (list them from `git grep -l "5h\|RA 5\|ra=75" server/tests`), `server/tests/conftest.py` (owner: shift_clock mtime shift; config guard as a session-level autouse), `.github/workflows/ci.yml` (weekly replay run at 2026-06-15) | M | drop the stat shift: the write-then-age test fails under replay |
| 2.4 | #1110 C-04a | #784 #825 #785 #827 #176 (callers) | `server/astrodeck/calibration/{keys,matcher}.py`, `server/astrodeck/sequence/bundle.py`, `server/astrodeck/flows/calibration_health.py` (cache per flow revision), `server/astrodeck/api/app.py` (owner: light-solve apply path passes rotmech; health route), UI health matrix copy, `test_w2_angle_flats_callers.py` | M | drop the angle from LightNeed: the two-angle bundle pairs the wrong flat |
| 2.5 | #1112 C-06 | #819 #775 #772 #773 #777 #778 #830 (wording) | `server/astrodeck/flows/{tonight,compile,examples}.py`, `server/astrodeck/sequence/session.py` (`owes_more`), `ui/src/.../runCopy`, the spec doc section 5.9, `test_w2_notes_from_plan.py` (table over every example) | M | re-introduce one graph read in the note: the table test fails |
| 2.6 | #1117 C-11a | #900 #1058 #904 #903 #902 | `ui/src/next/hubs/sky/sheets/photosphere.ts`, `horizon.tsx`, their `__tests__`, the saved horizon schema (per-bin status), `tools/photosphere_sim` scorer's interpolation import (shared function) | L | paint unknown-overlap frames: the chart-yard variant publishes a Measured bin away from a step |
| 2.7 | #1116 C-10 | #787 #828 #788 #790 #806 | `server/astrodeck/sequence/angle_check.py` (`allowed_error`), `server/astrodeck/sequence/models.py` (comment), the Sky card in `ui/src/next` (`commandedPa`), the sky-angle row writer (if it is in `engine.py`, that item moves to WP-3.1 as a shared function) | M | convergence 0 in the fallback: the 4x1 high-Dec case fails |
| 2.8 | #1121 C-15 | #978 #805 #1019 #720 | `server/tests/_ratchet.py` (new), `server/tests/test_w2_ratchets.py`, ten source pins replaced (the issue lists candidates), the UI callers test, `ui/src/testing/driveTo.ts` (new), the wizard DOM tests | L | add one silent handler: the ratchet fails |
| 2.9 | #1127 C-21 | #748 #1067 | `server/astrodeck/{logfmt,events}.py` (site_derived marker), `dawn_park.py`, `sun_watch.py` (D-H: each event writes a flagged night-file line with the moment and a separate alert-safe wording without it), `api/redact.py` (drop marked lines and flip-hold edges for uncleared principals), `alerting.py` (forwards only the alert-safe wording), privacy tests | M | drop the marker in dawn_park: the viewer test sees the line; forward the flagged line: the alert-sink test fails |

Release checkpoint: 0.3.47 after wave 2 (C-01, C-02 and C-14 change what a night does). Steps: `python tools/licence/version_bump_evidence.py 0.3.47` in the release commit, tag `v0.3.47`, verify `gh release view v0.3.47` lists the tarball, sha256, sig and SBOM; then self-update on the rig (needs the pinned key) or the staged ssh deploy.

## Wave 3 (engine owner C-03; app.py owner C-08)

| WP | Issue | Covers | Owned files | Size | Mutant |
|---|---|---|---|---|---|
| 3.1 | #1109 C-03 | #1098 #857 #880 (#144 rig verify) | `server/astrodeck/sequence/recovery.py` (new: RecoveryLadder states and transitions), `server/astrodeck/sequence/engine.py` (owner: calls the ladder at failed centring, off-field solve, STOP and deadman; D-A: STOP and a manual move enter `paused_by_operator`, whose only exit is an operator resume), `resume_arm.py` (ResumeArm never restarts a paused run), `test_w3_recovery_ladder.py` | L | skip the home: the off-field night holds in place; auto-resume a paused run: the D-A test fails |
| 3.2 | #1114 C-08 | #188 #795 #796 #797 | `server/astrodeck/sequence/engine.py` (shared: `_record_session_frame` stamps mosaic_id, panel_id, labels), `server/astrodeck/sequence/{report,bundle}.py` (ids as keys), `server/astrodeck/api/app.py` (owner: CSV columns from `FrameRecord.model_fields`), `tools/gen_report_types.py` (new), `ui/src/types.report.generated.ts` (new), the gallery mosaic chip, `test_w3_mosaic_identity.py` | M | label as key: two same-named groups merge |
| 3.3 | #1113 C-07a | #780 #781 #782 | `server/astrodeck/imaging/sessionstack.py` (per-panel budget, least-recently-visited eviction, add after accept), `server/astrodeck/api/app.py` (shared: `panel` query on the stack routes), `ui/src/components/PanelPicker.tsx` (new) used by the three surfaces, the WS stack event | M | restore least-recently-added: the four-panel rotating night never builds a stack |
| 3.4 | #1117 C-11b | #905 #1060 #1059 | `ui/src/next/hubs/sky/sheets/pano/poseTrack.ts`, the tracer, the alignment report (north confidence), `tools/photosphere_sim` case files (a ruling row per gate for treeline), the scorer | L | ignore the measured gyro scale: overlay_moving fails at 2 percent |
| 3.5 | #1108 C-02c | #599 (#308 stays rig-gated) | `server/astrodeck/site_horizon.py` (new: per-night (alt, az bin) refusals from obstructed frames), `server/astrodeck/api/app.py` (shared: suggestion route and accept route), the Site page offer in the UI, `test_w3_horizon_suggestion.py` | M | apply the mask without the press: the D-07 test fails |
| 3.6 | #1112 C-06 rider | #809 | `server/astrodeck/api/app.py` (shared: retry-set-aside resolves against the same compile as the progress card), test | S | resolve against a fresh compile: the re-keyed group 404s |
| 3.7 | #1128 C-22 | #1076 #606 (#125 closes on the rig; #605 closed, D-J) | `server/astrodeck/power_guard.py` or `bootcause.py` (pending reboot, active hours), `rig_beacon.py` (last log id, boot cause, pending flag), the preflight (`scripts/rig_precheck.py` and the arm gate in `hub.py`, owner), the relay's rig page (last ten pings), tests | M | ignore the pending flag: the arm test passes when it must refuse |
| 3.8 | #871 | docstring and abort path | `server/astrodeck/devices/backends/asiair_backend.py` (an abort does not wait behind a FITS download: cancel the download first), its test | S | remove the cancel: the abort test times out |
| 3.9 | #178, #181, #826 (D-I) | mosaic UI wiring | `ui/src/.../TargetFramingSheet.tsx` (panelCsv prop), `server/astrodeck/catalog/region.py` (shape fields on region rows), the SUGGEST GRID ellipse use, the `rotator_bin_deg` Settings control and `solve_saved_lights` default | M | drop the shape fields: the region row test fails |
| 3.10 | #1094 (D-D) | per-target time bound; queue hand-over on planned endings | `server/astrodeck/sequence/models.py` (`Target.max_time_min` beside `count`, 0 = none: the target is done when either is met), `server/astrodeck/sequence/engine.py` (shared with 3.1 on disjoint functions: the target-complete predicate and the run-end promote call; serial merge after 3.1 with a suite run between), `server/astrodeck/sequence/resume_arm.py` (promote the queued session on `WindowClosed`, `max_run_min`, the stop time and the per-target bound; never on abort, unsafe, error, quality stop or cooling skip; log "'<name>' is queued behind this session and will not start" when a run ends without promoting), the stepped Send to Flow Wizard and NEW FLOW option for the bound, `flows/doctor.py` (a target with neither bound and an unbounded stop gets the existing note), `test_w3_target_time_bound.py` | M | promote on abort too: the abort test fails; drop the time bound: the 60-minute sim target runs to its count |

## Wave 4 (engine owner C-05; C-19 shares)

| WP | Issue | Covers | Owned files | Size | Mutant |
|---|---|---|---|---|---|
| 4.1a | #1111 C-05 (sheet) | #843 #818 #820 #845 | `server/astrodeck/flows/nodes.py` (capability table), `flows/doctor.py` (incapable choice is an error), `flows/tonight.py` (flats duration), `ui/src/components/flows/QuickFlow.tsx`, six end-to-end chip tests `test_w4_quick_chips.py` | M | offer the lens cap: the doctor test fails |
| 4.1b | #1111 C-05 + #1110 C-04b (engine) | #603 job C, #783 | `server/astrodeck/sequence/engine.py` (owner: `end_of_night` action for darks-after; twilight-sky flats metered; one flat set per distinct panel angle with ROTMECH), `flows/to_plan.py` (binds darks-after to end_of_night), sim night tests | L | skip the per-angle loop: the two-angle night shoots one set |
| 4.2 | #1125 C-19 | #464 #742 | `server/astrodeck/sequence/engine.py` (shared: publish `flows.statuses` at stage transitions), `events.py`, `ui/src/store.ts` (one selector), both canvases' pills, LEDs and wires, the spec text, a recorded-WS DOM test | M | skip the publish: the pill never changes |
| 4.3 | #1113 C-07b | #172 part B, #779 | `server/astrodeck/imaging/mosaic_preview.py` (new; tangent-plane placement using `sequence/coverage.py` helpers, bounded size), one route in `app.py` (owner), the Gallery preview beside the picker, `test_w4_mosaic_preview.py` (corner pixels of a 2x2) | L | place by panel order instead of WCS: the corner test fails |
| 4.4 | #1126 C-20 | #601 #602 #893 #657 #192 | `server/astrodeck/sequence/roof.py` (envelope owner; D-B: `close_observatory` never closes without a confirmed park, reopens a dropped link once to read it, else refuses and pages), `devices/base.py` (DomePolicy callers), `sequence/resume_arm.py` (open in recovery), the sim backend's cover and roof, `engine.py` (shared: open at run start, bind after connect), tests with and without actuators | L | skip the open: the sim-with-cover night solves through the cover |

Release checkpoint: 0.3.48 after wave 4.

## Wave 5 (engine owner C-09)

| WP | Issue | Covers | Owned files | Size | Mutant |
|---|---|---|---|---|---|
| 5.1a | #1115 C-09 (primitive) | #183 | `server/astrodeck/sequence/{group_rules,models,policy}.py` (ordered phases on TargetGroup; POOL as a one-phase group with a frame quota and a selection rule), `engine.py` (owner: phase advance), `test_w5_group_phases.py` | L | merge phases: cycle 2 starts before cycle 1 completes |
| 5.1b | #1115 C-09 (compiler) | #1074 #774 | `server/astrodeck/flows/to_plan.py` (chained cycles to phases; POOL to a group; nothing dropped silently), `flows/doctor.py` (dropped structure is an error with the node id), `flows/nodes.py` (POOL engine-capable), the two examples run on the sim | M | flatten chained cycles: the filter-order test fails |
| 5.2 | #184 | on_mosaic_complete | `flows/to_plan.py` (the trigger node), `engine.py` (shared: fire once when the last panel completes), test | M | fire per panel: the count test fails |
| 5.3 | #185 | autofocus inside a mosaic | `engine.py` (shared: a sweep runs on the panel with the most stars in the last solve, once per mosaic visit cycle, never on a star-poor panel), `flows/nodes.py` policy field, test | M | sweep on the star-poor panel: the visit is lost |
| 5.4 | #186 | per-panel exposure overrides | `flows/models.py`, `to_plan.py`, `engine.py` (shared: per-panel exposure), the panel editor UI, test | M | ignore the override: the edge panel shoots the core exposure |

Release checkpoint: 0.3.49 after wave 5.

## Wave 6 (mosaic finish and the deferred features)

| WP | Issue | Covers | Owned files | Size |
|---|---|---|---|---|
| 6.1 | #187 | measure-then-lay-out mode for a fixed camera (ruling 3 on #189 settles re-frame identity) | `catalog/framing.py`, `flows/continuation.py`, `engine.py` (owner: first centring solve sets the grid angle), UI | L |
| 6.2 | #180 Part B (D-E) | failed-panel rotation: skip to the next panel, revisit, never drop | `sequence/group_rules.py` (a failed panel goes to the back of the panels that still need frames and is retried every round), `flows/tonight.py` note (which panel was skipped and when it is next due), test whose mutant drops the panel after three failures | M |
| 6.3 | #766 (D-L) | NINA import design: spec + fixtures, no code | `docs/flows/nina-import.md`, `server/tests/fixtures/nina/` | M |
| 6.4 | #189 close | the tracker closes when #172 to #188 are closed or ruled | none | S |

## Rig-gated (code done or not needed; the next step is a measurement when the rig is on)

#16 (D-10 reconnect profile check), #90 (two device numbers), #145 (pier-east sign on a real night), #144 (trust-position and the 409 on the rig; closes with C-03), #308 (overcast frames through the audit tool, now with the class column), #496 (D-12 supervisor restart), #594 (hardware), #597 (close on 2026-10-28 if no drops since relay v10), #789 (one measured hop), #851 and #882 (east-pulse bench walk; the per-night refused-sync count), #879 (NINA frame bench check), #1071 (pier-east flip night with C-01 deployed), #1051, #1056, #1057, #1061 (the owner's device recording, T31), #1055 (S54 after T31), #125 (closes when C-22 is on the rig).

The coordinator runs read-only rig checks itself over ssh when the rig is on; anything that moves the mount or restarts a process is handed to the owner as a one-line `!` command (the classifier refuses those even with a go-ahead).

## Owner-gated (no code until the ruling; defaults above)

Still awaiting the owner's reply to the explanations on PR #1130: #718 (D-G), #638 (D-F), #826 (D-I, WP 3.9 builds the default if no reply by wave 3). Ruled 2026-10-22 and placed in the waves: #880 (D-A, WP 3.1), #893 (D-B, WP 4.4), #1094 (D-D, WP 3.10), #180 Part B (D-E, WP 6.2), #748 and #1067 (D-H, WP 2.9), #766 (D-L, WP 6.3). Closed on the rulings: #526 (D-C), #605 (D-J). #823 (D-K): the owner cancels the job; WP 1.10 adds the guard.

## New issues the coordinator will file as this runs

Every integration defect, every coder-reported neighbour, and: the durable single-owner library load (#875 follow-up) if seen; the `a.datas` WCSLIB filter result (on #1073); the weekly replay CI run's first red, if any (on #1120).

## What Sonnet must not do

- Do not widen a timing bound, skip a test or loosen a tolerance without the named mutant shown red first; C-13 and C-15 exist because that happened before.
- Do not touch `engine.py`, `app.py` or `hub.py` outside the owner or shared functions named for the wave.
- Do not base anything on the `C:\Users\bear\astro` working tree or on `feat/*` branches.
- Do not run full suites per WP; once per wave on the integrated tree.
- Do not deploy to the rig during a night; self-update after a release in the daytime, or the staged ssh path, and watch the deploy log to `DEPLOY_<ver>_DONE`.
