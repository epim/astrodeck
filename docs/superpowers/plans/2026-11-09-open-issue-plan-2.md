# AstroDeck open-issue plan 2 (2026-11-09)

Written for a Sonnet 5.5 coordinator running Sonnet 5.5 coders and verifiers. Every step below is a command or a brief a Sonnet agent can execute without inventing architecture. Where a decision is the owner's, the plan names the default and goes on.

This plan follows `2026-10-22-open-issue-consolidation.md`, whose six waves are merged. Wave numbers continue from it (7 to 12) so the ledger, the usage reports and the branch names keep one sequence.

## Goal

Resolve 96 open issues: the 89 open on 2026-11-09, and the seven the testing review filed on 2026-11-10 (#1255 to #1261). Each of the 89 was read against main and classified (`C:/Users/bear/astro-wt/program/plan2/all.json`, not in the repo): what remains, which files and tests it touches, its size, and whether it changes a stored or wire representation.

| Class | Count | Meaning |
|---|---|---|
| code | 67 | work a coder can do now (36 S, 22 M, 9 L) |
| rig | 14 | code done or not needed; the next step is a measurement or a log read on the rig |
| owner | 10 | waits for an owner decision or an owner action |
| close | 5 | already fixed on main; close with evidence |

The plan has a second purpose. Until now every pull request and every merge ran every CI job and every test. Wave 7 changes that where the measurements say it pays, and the later waves run on that footing. The measurements and what they ruled out are in `2026-11-09-testing-methodology-review.md`, next to this file.

## State on 2026-11-09

- `origin/main` = dd96bfe4, the merge of release PR #1253 (waves 5 and 6), tagged `v0.3.49`. Every worktree is cut from it. The measurements below were taken at d96cfef0, the commit before the release; the release changes the version number and evidence files only.
- The working tree `C:\Users\bear\astro` is on `feat/photosphere-production`, far behind main, with a large untracked set. Never build on it. Every worktree is cut from `origin/main`.
- Worktrees live under `C:/Users/bear/astro-wt/`. Coordination files live in `C:/Users/bear/astro-wt/program/` (`ledger.md`, `COMMON.md`, briefs, `usage-wave*.md`). The session scratchpad is not durable: nothing that matters is kept there.
- Finished worktrees from waves 4 to 6 and the two release worktrees are still registered. Remove them before wave 7: delete the `ui/node_modules` junction first (`(Get-Item <path>).Delete()` in PowerShell), then `git worktree remove`. `git worktree remove --force` on a tree that still holds the junction empties the shared `node_modules`.
- The rig is behind main. Rig-gated items wait for the owner's deploy decision (D-9).
- GitHub writes are direct `gh` commands, one per call, never from a script loop.
- Measured test cost on main (run 42699820446): server job 19.6 min, ui job 7.1 min, 35.8 job-minutes in all. A pull request run costs about the same again (39.7 job-minutes on run 42695961395). With one pull request per work package, each package pays for two full runs, and for 47 of the last 77 runs on main the second run tested a tree byte-identical to the one its pull request had already passed (#1260).
- Measured server suite on a 24-CPU dev machine at d96cfef0, `-n 12`: 19629 tests in 1214 files, 907 s wall clock, 9724 CPU seconds. 135 tests take over 10 s each and hold 39 percent of the CPU time. 49 files hold half of it. 555 files finish in under 1 s each.
- One test fails locally at about half the hours of a non-UTC day and never on CI: #1254.

## The open issues

Code issues, grouped by area. R marks a change to a stored or wire representation (design-first, see the loop). The WP column is where the issue is scheduled.

| Area | Issue | Title (short) | Size | WP |
|---|---|---|---|---|
| test selection | #1152 | UI runner ignores file arguments; no `typecheck` script | S | 7.1 |
| test selection | #914 | appHeaderDom exited 1 while printing 2/2 passed | S | 7.1 |
| test selection | #1258 | UI tests cannot be scoped to a change | M | 7.1 |
| CI | #1259 | CI runs every job for every change | M | 7.2 |
| CI | #1260 | The run on main retests a tree its pull request already passed | M | 7.3 |
| tests | #1261 | Server suite time is held by a few dozen tests | M | 7.4 |
| tests | #1256 | Four PPEC tests wait out a 150 s budget in real time | S | 7.4 |
| tests | #1255 | At least 406 wheel-gated server tests run on no CI job | M | 7.7 |
| catalog | #1257 | GET /api/catalog/tonight takes about 90 s | M | 7.8 |
| test tooling | #802 | mutate_build_contexts rewrites a tracked evidence file | S | 7.5 |
| test tooling | #1122 | Test tooling hygiene (remaining members) | S | 7.5 |
| test tooling | #1157 | pip_audit_retry has no per-attempt timeout | S | 7.5 |
| tests | #1167 | Image-step tests run WSL's bash on a Windows dev machine | S | 7.6 |
| tests | #1150 | r7Parity import regex runs into the next import | S | 7.6 |
| tests | #1119 | Tests that fail only under parallel full-suite load | S | 7.6 |
| tests | #1149 | FlowRig._report polls for a fixed 2 s | S | 7.6 |
| tests | #1188 | test_h4_recoverable_says_why control fails its premise under load | S | 7.6 |
| tests | #1189 | test_old_nights_are_pruned pins July dates against the live clock | S | 7.6 |
| tests | #1254 | Arm-gate status test reads the wall clock | S | 7.6 |
| guide | #1222 | The RMS reject gate judges a frame on one guider sample | S | 8.1 |
| guide | #1144 | Report frames store an idle guider's RMS | S | 8.1 |
| guide | #1146 | Guide verdict follow-ups, parts 1 to 3 | L, R | 8.1 |
| engine | #1174 | Engine start writes the session file on the event loop | M | 8.2 |
| engine | #1176 | Two tests that pass without exercising their claim | S | 8.2 |
| asiair | #1193 | Preview frames left on the box's SD card | S | 8.3 |
| asiair | #1194 | Abort and halt queue behind a cancelled RPC | M | 8.3 |
| release | #1160 | 38 of 39 reviewed-input digests no longer match main | S | 8.4 |
| release | #1186 | Release evidence tooling: stale ledger base, two approval places | M, R | 8.4 |
| starless | #1145 | A partial obstruction skews whole-frame stats | M | 8.5 |
| hub | #1173 | An operator reconnect can lose a race with the status poll | S | 8.6 |
| flows | #829 | WP-117/118/126 loose ends | S | 8.7 |
| flows | #1124 | Flow editor input: remaining members | S | 8.7 |
| flows | #1151 | Comments name the wrong home of RUN_PHASE_BRIDGE_MS | S | 8.7 |
| docs | #1158 | build_ngc_extras docstring says to curl from master | S | 8.7 |
| focus | #1077 | A sweep on a lightless sky discarded a good slope | L, R | 9.1 |
| focus | #1181 | curve_verdict refuses a clean wide-stepped V | M | 9.2 |
| engine | #1239 | A run ended by a stop time says "stopped at dawn" | M | 9.3 |
| starless | #721 | The no-light hold treats a missing solver as "no light yet" | S | 9.4 |
| config | #1206 | solve_saved_lights follows the solver only at config load | S | 9.5 |
| mosaic | #1175 | Fixed-camera angle tolerance loose for a grid with skipped panels | M, R | 9.6 |
| engine | #1231 | Live stage status: unnamed lane nodes, a deferred stage stays "running" | L, R | 10.1 |
| engine | #1165 | A site_derived warning never pages | M | 10.2 |
| report | #1148 | attach_existing never sets `_pending` | S | 10.3 |
| guide | #1142 | Three TODO(remove in 0.3.36) blocks remain | S | 10.3 |
| starless | #1182 | Hold classification reads the wrong frame | S, R | 10.4 |
| sim | #1227 | The sim camera and the sim solver disagree | M | 10.5 |
| release | #1154 | server/pyproject.toml declares no licence | S | 10.6 |
| release | #1159 | WCSLIB removal left stale records | M | 10.6 |
| photosphere | #1051 | Lucas-Kanade reads each step 0.2 to 0.4 percent short | M | 11.1 |
| photosphere | #1056 | The latency estimator reads 118.8 ms against a true 50 ms | M | 11.2 |
| photosphere | #1058 | The tracer publishes false-open Measured bins | L | 11.3 |
| photosphere | #1117 | The scanner publishes only what it measured | L | 11.3 |
| test tooling | #1156 | photosphere_sim tests run in no CI workflow | M | 11.4 |
| ui | #1153 | Phone stage-editor sheet has no undo | M | 11.5 |
| ui | #770 | FlowHeader row estimated to overflow at 700 to 770 px | S | 11.6 |
| ui | #1205 | Two same-named mosaics' bundle rows read identically | S | 11.6 |
| photosphere | #1201 | The focal prior pulls f_lock | L | 12.1 |
| mosaic | #1228 | Assembled preview assumes standard solve parity | M | 12.2 |
| tonight | #1245 | No retry for a set-aside POOL candidate | L, R | 12.3 |
| tonight | #1196 | Three lines promise "the next night takes it up" with resume Off | S | 12.4 |
| starless | #1093 | A mosaic resumed behind a tree: every hold blamed the sky | S | 12.5 |
| starless | #1108 | One classifier for a starless frame (combined issue) | L | closes with #1077, #721, #1093 and the #308 measurement |
| docker | #1123 | The container image ships the native engine | S | gated on D-1 |
| docker | #1155 | Release image gate assumptions unproven | S | gated on D-1 |
| docker | #800 | Docker docs say the image installs no native engine | S | gated on D-1 |
| guide | #16 | A camera stuck in video mode reports connected:true | M | gated on D-6 |
| photosphere | #1057 | The gyro axis mapping never confirms | L | gated on the owner's phone recording |

Rig, owner and close issues are listed in their own sections below.

## How a wave runs (the coordinator's loop)

1. `git -C C:/Users/bear/astro fetch origin` and record BASE = `origin/main`. Read the owner's comments on every issue in the wave (`gh issue view <n> --comments`): answers arrive there.
2. For each WP: `git -C C:/Users/bear/astro worktree add -b wave<N>/wp-<N>-<k> C:/Users/bear/astro-wt/w<N>-<k> origin/main` (absolute path). UI WPs get `ui/node_modules` as a junction to an installed copy (`cmd //c mklink /J <wt>/ui/node_modules <installed>/ui/node_modules`).
3. Write one brief per WP in `program/` from the wave table row: the issue numbers, the owned files, the named mutant, the test commands, the report path. Every brief points at `program/COMMON.md` for the standing rules. Dispatch one Sonnet coder per WP (`model: sonnet`, set explicitly), at most four at once.
4. A WP marked R, or any WP sized L, is design-first: the coder's first hand-back is `design.md` plus a reader inventory (every reader and writer of the changed representation, found by grep, with file and line) and it stops there. The coordinator checks the inventory against the tree, rules on open points, then sends the coder on to code. Wave 6 lost nothing this way; wave 5 lost 16 percent of its tokens to rework without it.
5. Coordinator review of each hand-back: reread the issue; `git status` shows only owned files; rerun the WP's tests; rerun the named mutant from a byte backup and check the restore with sha256; privacy scan (`grep -l -F -f C:/Users/bear/.astrodeck/privacy-needles.txt <files>`, file names only), BOM scan, emoji scan. Fix rounds go back to the same coder.
6. One pull request per WP. Commit with explicit pathspecs (`git commit -F <msg> -- <paths>`), push the branch, `gh pr create`. Each issue the PR finishes gets its own "Closes #n." sentence.
7. Merge order follows the table. Wait for the PR's checks on the current head, then `gh pr merge <n> --merge --match-head-commit <full sha>`. A fresh push reads CLEAN with an empty check list for a few seconds: never merge on that reading.
8. After a merge that touched `engine.py`, `app.py` or `hub.py`, the next PR that touches the same file is rebased on the new main and its tests rerun before it merges (the relaxation rule below).
9. Close each issue with the commit hash and the proving tests. Leave rig-gated and owner-gated remainders open with a comment saying what remains.
10. File every new defect found, at the time it is found (check `gh issue list --search` first). Then write `program/usage-wave<N>.md` with per-WP tokens and wall-clock span (`diag/usage_report.py`), update `program/ledger.md`, and stop. The next wave starts on the owner's go-ahead, not before.

Relaxation rule for `engine.py`, `app.py` and `hub.py`: one WP per wave owns each file. A second WP may edit the same file only if the functions or routes it names are disjoint from the owner's, and the two merge serially with the second rebased and retested between. The wave tables mark the owner with (owner) and the sharers with (shared: <functions>).

Rules every WP follows (in full in `program/COMMON.md`): tests first; a named mutant shown red; owned files only; no commits, pushes or GitHub writes by coders; a made-up site in tests; UTF-8 without a BOM; no emojis; mutation runs from a byte backup inside the worktree; no silent change of what a stored value means (a flip needs a migration).

Model rule: coders and verifiers are Sonnet 5.5; Haiku may run scans and close-out commands. Never Opus.

Test rule for coders, every wave: a coder runs the tests it added or changed, the existing test files of the modules it edited (found by grep), and the cross-cutting pins `COMMON.md` rule 12 names for the files it touched. Nobody runs a full suite per WP; CI runs it on the PR. Once WP 7.1 has merged, a UI coder may run `npm test -- --changed origin/main` in place of the grep. There is no selector command for server tests and none is planned: three measured ways of choosing server tests by dependency each chose 75 to 100 percent of the suite's time for a typical change (see the review).

What CI runs once wave 7 has merged:
- A pull request runs only the jobs whose area the change touches (WP 7.2). A job that runs, runs in full, except the ui job, which runs the test files the import graph connects to the change (WP 7.1).
- A push to main skips every job that already passed on the same tree in the pull request's run (WP 7.3). The ui job always runs in full on main, because the pull request ran only part of it.
- The full suites also run weekly under the date replay and on every release.

## Close-outs before wave 7 (coordinator alone, no code)

Run the evidence command, read the issue once, then close with the line.

- #1147: the test's own watchdog constants (`SPIN_LIMIT_S`, `BOUND_S + 1.0`) are gone; `git grep -n "SPIN_LIMIT_S" origin/main -- server/tests/test_s7_sim_rotating_2x2.py` prints nothing. Commit 28ea2e63.
- #1136: fixed by PR #1215 (08cb941a); the test awaits the write instead of reading the counter.
- #1099: the five tests were fixed in 79526279, 74e409f2 and 28ea2e63. What is left of the class is carried by #1119 (WP 7.6).
- #1116: members #787, #828, #788, #790 and #806 are closed (d4355947 in PR #1169, and 3ceda342). The leftover is #1175 (WP 9.6).
- #1112: all nine members are closed (c5f9662c in PR #1172, 2f616ceb in PR #1197). The spillover is #1196 (WP 12.4).

## Owner decisions

Each is written to be answered without reading the issue. The recommended answer is in bold; the plan proceeds on it unless the owner says otherwise. Answer on the plan's pull request or on the issue.

- D-1 (#1073): since v0.3.28 the release has shipped a signed source tarball and no frozen Windows executable, because the executable's licence gate does not pass. Is the frozen executable still something we deliver? **No: stay source-first. Split the container image job from the frozen legs so the image can ship, add a RELEASE.md line that nobody hand-attaches an asset whose gate did not pass, and correct the v0.3.41 release text that promises binaries.** A yes means a separate licence programme before any code. The docker trio (#1123, #1155, #800) waits on this.
- D-2 (#1220): dusk flats at a rotated angle need to know where the rotator's zero sits on the sky, and a freshly started server does not know that until its first plate solve after dark. **Shoot the unrotated flat set at dusk; shoot each rotated set right after the night's first plate solve.** The alternative is to save the rotator anchor to disk, which broke 27 tests the last time a learned hardware fact was stored that way.
- D-3 (#1143): the guider's mass gate accepted two wrong stars in the bench test. Should it be tightened until it rejects them? **No: it stays a saturation and cloud guard. Telling one star from another is the job of the identity check added in #1083. Write that intent into `engine.rs` and close.**
- D-4 (#754, #442): two one-off test stalls from September that have not been seen since. **Close both as not reproduced; reopen on the next sighting with the log.**
- D-5 (#1192): an Android build that runs AstroDeck on an old phone as a smart-scope controller. **Park it: label it `later`, leave it open, schedule nothing.**
- D-6 (#16): a camera stuck in video mode reports itself connected. The fix reconnects the camera when a capture fails that way. Do you still want it? **Yes, as one medium WP after wave 9.** It has not been seen since July, so no is also reasonable.
- D-7 (#1157): when the Python package index is down, the dependency audit warns and passes instead of failing the build. **Keep warn-and-pass; add a per-attempt timeout (WP 7.5).**
- D-8 (test policy): you asked that a change run only its related tests. Measured, that works for UI tests and for whole CI jobs, and does not work for server tests: 5306 server tests start a whole hub, they hold 87 percent of the suite's time, and nearly every server change reaches them, so choosing by dependency still runs 75 to 100 percent of the suite. The larger waste is repetition: one fifth of all CI job-minutes went to re-testing, on main, a tree the pull request had already passed. **Recommended: (1) a pull request runs only the jobs whose area changed; (2) the ui job runs only the test files the import graph connects to the change; (3) the server job, when it runs, runs in full; (4) the run on main skips what already passed on the same tree; (5) the slowest server tests are made cheap (#1261, #1256, #1257). A pull request run costs 39.7 job-minutes today, of which the server job is 23.3 and the ui job 9.0.** The alternative that takes "only related tests" literally for the server: a pull request runs only the test files it changed plus the test files of the modules it edited, and the full server suite runs on main after the merge. That cuts the wait on a pull request from about 23 minutes to a few, at the price that a regression outside those files is found after the merge, and main can then no longer skip its run. It is not recommended: its miss rate is unmeasured beyond one replay (129 of 134 change-dependent failures were in the pull request's own test files, a sample biased toward tests written for that change).
- D-9 (rig): rig-gated items need the rig on the current release. **The coordinator self-updates the rig to 0.3.49 in daytime under the standing deploy approval, once the owner says the rig is on.**

Carried over, still owed: the #766 spec section 12 answer; the D-03 cap of six held passes and what a rejecting panel costs; the #594 and #308 rulings, which follow their measurements.

Owner actions (things only the owner can do):

1. #178: download one real Telescopius mosaic CSV, and import one AstroDeck export (2x2 at a non-zero angle) into Telescopius or ASIAIR. About 10 minutes.
2. #1061, #1055, #1057, #1056: one 360-degree phone pan with scanner recording on (`#/next` with `?scanner=pano`), then hand over the JSONL. About 5 minutes. One recording serves all four.
3. #90: one real phone scan; paste the alignment report's `preview` and `readback` p50, p95 and max. About 5 minutes.
4. #125: the dead-man check URL.

## Wave 7 (CI and test cost; app.py owner 7.8, `catalog_tonight` only; no engine or hub edits)

Why this is first: every later WP pays for its tests twice on CI and once on the coder's machine. The measurements behind the design are in `program/testing-review/` and summarised in `2026-11-09-testing-methodology-review.md`.

| WP | Issue | Covers | Owned files | Size | Mutant |
|---|---|---|---|---|---|
| 7.1 | #1152, #914, #1258 | UI runner takes file arguments and a `--changed <base>` mode driven by the import graph; `npm run typecheck`; #914's missing export | `ui/run-tests.mjs`, `ui/package.json`, `ui/tools/selectTests.mjs` (new), `ui/src/__tests__/wp15uRunnerReport.test.ts`, `ui/src/__tests__/selectTests.test.ts` (new), `ui/src/__tests__/appHeaderDom.test.tsx`, `.github/workflows/ci.yml` (shared: the `ui` job's test step only, after 7.3 merges) | M | the runner ignores its arguments again: a one-file run executes every file. Second mutant: a filter that matches no file exits 0. Third: the fail-open list is emptied, and a change to `ui/package.json` selects nothing |
| 7.2 | #1259 | CI runs a job only when its area changed: `changes` job, job-level `if:`, fail-open, one required `ci-ok` job | `tools/ci/changed_areas.py` (new), `tools/ci/test_changed_areas.py` (new), `tools/ci/areas.json` (new), `.github/workflows/ci.yml` (owner) | M | an unknown path maps to no area instead of every area: the fail-open test goes red |
| 7.3 | #1260 | A push to main skips each job that already passed on the same tree in the pull request's run | `tools/ci/already_green.py` (new), `tools/ci/test_already_green.py` (new), `.github/workflows/ci.yml` (shared: the `changes` job and the job-level `if:` lines, after 7.2 merges) | M | the tree comparison is dropped: a merge whose tree differs from its pull request head is skipped, and the tree-differs case goes red. Second: a job the pull request run skipped counts as passed |
| 7.4 | #1261, #1256 | Slow-test cuts, each keeping its red mutant; a ceiling on any one test's CI time; the whole duration list kept as an artifact on main | `server/tests/test_catalog.py`, `server/astrodeck/catalog/objects.py` (`search_catalog` only), `server/tests/test_s7_ledger_cost.py`, `server/tests/test_mosaic_spec_claims.py`, `server/tests/test_flows_compile_never_raises.py`, `server/tests/test_ppec_persist_needs_measured_points.py`, `server/tests/test_ppec_file_horizon.py`, `server/tests/conftest.py` (the ceiling hook), `server/tests/test_slow_test_ceiling.py` (new), `.github/workflows/ci.yml` (shared: the `server` job's pytest flags, after 7.1 merges) | M | one per file: the mutant each test was written against still turns it red after the cut. Ceiling: a test that sleeps past the ceiling and is not on the allowlist fails the run |
| 7.5 | #802, #1122, #1157 | Mutation tools stop rewriting tracked files; pip-audit retry gets a per-attempt timeout and a recorded fixture | `tools/licence/mutate_release_workflow.py`, `deploy/reverse-proxy/mutate_build_contexts.py`, `tools/pip_audit_retry.py`, `server/tests/test_w1_tooling_hygiene.py`, `server/tests/test_1106_pip_audit_retry.py` | S | a mutation run leaves `git status` dirty: the hygiene test goes red |
| 7.6 | #1167, #1150, #1119, #1149, #1188, #1189, #1254 | Test-only fixes, one commit each | `server/tests/_bash.py` (new), `server/tests/test_w16_image_native.py`, `server/tests/test_wp18_release_image_steps.py`, `ui/src/next/__tests__/r7Parity.test.ts`, `server/tests/test_local_auth.py`, `server/tests/_flow_night.py`, `server/tests/test_h4_recoverable_says_why.py`, `server/tests/test_ux_review_s4.py`, `server/tests/test_w3_7_arm_gate.py` | M (seven S) | one per issue, named in the brief; #1254: `poll_status` drops `arm_refusal`, and the test is green at each of the 24 hours of a day |
| 7.7 | #1255 | The wheel-gated server tests run on CI: one `@pytest.mark.native` marker in place of the eight gating idioms, the `native` job runs `pytest -m native` over all of `server/tests`, and a wheel skip is an error where the wheel is installed | `server/tests/conftest.py` (the marker and the `ASTRODECK_REQUIRE_NATIVE=1` hook; after 7.4 merges), `server/pyproject.toml` (the marker's registration), the 41 test files #1255 lists plus the ten in the `native` job's list (gating lines only), `server/tests/test_native_gate_ratchet.py` (new), `.github/workflows/ci.yml` (shared: the `native` job's test steps, after 7.4 merges) | M | a test file gates itself with a bare `skipif` on the wheel again: the ratchet names it. Second, on the branch: break one line of the probation path in `server/astrodeck/guide/native.py` and show the `native` job red |
| 7.8 | #1257 | The tonight route computes the night for the whole catalogue in one vectorised pass and caches it per night, site and altitude limit, in process memory | `server/astrodeck/catalog/visibility.py` (a new whole-catalogue function; `compute_night` stays), `server/astrodeck/api/app.py` (owner: `catalog_tonight` only), `server/tests/test_tonight_route.py`, `server/tests/test_the_catalogue_without_a_site.py`, `server/tests/test_tonight_vectorised_matches.py` (new) | M, design-first | the per-row loop restored: the route's time bound goes red. Second: the cache ignores a site change, and the site-change case returns the old site's list |

Merge order: 7.6 and 7.5 first (7.6 removes flakes that would otherwise cost reruns on every later pull request), then 7.2, 7.3 (rebased on 7.2's `ci.yml`), 7.1 (rebased on 7.3's), 7.8, 7.4 (after 7.8, so the two tonight tests are under the ceiling when it lands), then 7.7 (after 7.4, so the four 150 s waits are gone before those files reach CI). Five WPs edit `ci.yml`; they merge one at a time in that order and each rebases on the one before.

WP 7.2 in detail:
- `areas.json` maps each CI job to the paths that make it run. `server`: `server/**`, `native/**`, `tools/**`, `packaging/**`. `ui`: `ui/**`. `native`: `native/**`. `relay`: `relay/**`. `orangepi-security`: `orangepi5/**`, `security_acceptance/**`. `container-security`: `Dockerfile`, `docker-compose*.yml`, `deploy/**`, `security_acceptance/**`, `server/**`, `ui/**`, `native/**` (it costs 2.9 job-minutes, so it stays broad). `windows-private-state`: `server/**`, `security_acceptance/**`. `windows-com` already runs on main and tags only and is left alone. Top-level directories the map does not name (`supervisor/`, `site/`, `scripts/` and the loose scripts at the root) are unknown paths until a WP maps them.
- `changed_areas.py --base <sha> --head <sha>` prints the areas as GitHub outputs. A path that matches no glob selects every area. A change under `.github/`, to `areas.json` or to the tool itself selects every area. A tag and a manual dispatch select every area.
- A job that is skipped must not block the merge: the required check is one final `ci-ok` job that needs every other job and fails only when one of them failed.
- Cross-area readers: a regex estimate found 68 server test files that mention `ui/`, and 130 UI test files read files from disk. So the server job also runs, in full, when `ui/` changed. The WP's first step is the run-time read map (prototype: `program/testing-review/readmap_plugin.py` and `readmap_report.py`, one full local run of about 15 minutes): which server tests read `ui/`, `.github/`, `docs/` or a Markdown file. Its result decides two things: whether a `ui/` change can run a named subset of server tests in place of the whole job, and whether today's `paths-ignore` hides a file some test reads. If it does, that is a defect to file.
- Measured value, replaying the last 50 merges: 303 of 1985 job-minutes saved (15 percent), nearly all of it the ui job not running on the 18 merges that changed no `ui/` file. 49 of the 50 touch something the server job owns.

WP 7.3 in detail:
- A first job on a push to main decides, per job, `already_green`: the head commit is a merge with two parents, `git rev-parse HEAD^{tree}` equals `git rev-parse HEAD^2^{tree}`, and the GitHub API reports a completed `ci.yml` pull request run with `head_sha` equal to `HEAD^2` in which that job ran and concluded `success`. A job the pull request run skipped is not green.
- Why the tree and not the commit: a pull request run tests `refs/pull/N/merge`, the branch merged into the base at run time. When the two trees are equal the branch already contained main, so the tree that run tested is the tree now on main.
- When `already_green` is true the job is skipped with a logged line naming the pull request run. `windows-com` and any other job that runs only on main still run. The `ui` job is never skipped on main once WP 7.1 has merged, because the pull request ran a selection of it.
- When the tree differs, the push is not a merge, or the API call fails or finds nothing, every job runs (fail open).
- `already_green.py` is a pure decision function over recorded inputs plus a thin wrapper that calls `git` and `gh api`. The unit test covers: identical tree with a green run; identical tree with a failed run; identical tree with only a cancelled run; tree differs; squash or direct push; API error; a job skipped by the area filter on the pull request.
- Measured value: 47 of the last 77 runs on main had an identical tree and spent 1735 job-minutes on jobs the pull request had already run, 20.8 percent of all CI job-minutes in the window.
- The weekly `date-replay.yml` run stays as the full run that sees drift in the runners when no merge has a differing tree.

WP 7.1 in detail (the `--changed` mode):
- Build the import graph of `ui/src` from relative imports (the only style the tree uses). Select every test file whose import closure contains a changed file, plus every changed test file.
- Run everything when any of these changed: `ui/package.json`, `ui/package-lock.json`, any `ui/tsconfig*.json`, `ui/run-tests.mjs`, the graph tool, any file under `ui/` outside `ui/src`, or a file an import did not resolve to (37 imports do not resolve today).
- The 130 test files that read other files from disk always run, until each declares what it reads. A ratchet test fails when a new test file reads from disk without a declaration.
- Pull requests run `--changed` against the merge base. Main runs the full UI suite.
- Acceptance is a replay: over the last 50 merged pull requests that changed `ui/src`, mutate one changed line of each and show a selected test goes red. Any merge where the full run fails a test the selector left out is reported, not explained away.
- Measured value: a changed source file reaches a median of 28 of 774 test files (p90 201, max 574 for `ui/src/types.ts`); `npm test` is 389 s of the ui job's 432 s.

WP 7.4 in detail, in this order, each measured before and after with the same junit comparison (`program/testing-review/junit_cost.py`):
1. #1256 first: the four PPEC tests wait 150 s each in real time because the fixture's clock virtualises `time()` and leaves `monotonic` real. Set the probation budget to zero for these tests, which do not test the probation, and make `_WallClock` refuse `monotonic` unless a test opts in. 750 s of summed local time.
2. `test_catalog.py::test_every_catalog_id_finds_itself_first_however_it_is_spelled`: 26,740 linear scans of 13,370 rows, 504 s across its 20 slowest parameters on CI. Give `search_catalog` an exact-id index (which also speeds the product) or assert over a table of normalised keys built once. The #1210 mutant stays red.
3. `test_s7_ledger_cost.py::test_a_2000_frame_mosaic_night_keeps_the_ledger_budget`: 163 s on CI. Its recorded mutants miss the budget by a factor of 20 to 90; find the shortest night that still separates them and use it.
4. `test_mosaic_spec_claims.py`, the five `test_revision_N_lists_every_section_*` tests for revisions 7 to 11: 261 s on CI, while revisions 4 to 6 take under 4 s. Profile first (`python -m cProfile -o out.prof -m pytest <node id> -n0`), then fix what the profile shows.
5. `test_flows_compile_never_raises.py::TestPastCompilePlan::test_only_graph_not_runnable_leaves_to_sequence_plan`: 56 s on CI. Profile first.
6. The ceiling: a conftest hook fails the run when one test's call exceeds 60 s on CI (`CI=true`), unless the test is on an allowlist in `test_slow_test_ceiling.py` with a reason. The allowlist starts with whatever is still over 60 s after steps 1 to 5 and WP 7.8.
7. The `server` job writes `--durations=0` output to an artifact on main, so the whole distribution on CI is known and not only the top 30.

A cut that cannot keep its mutant red is not made. Pull requests do not skip tests marked `slow`: `server/pyproject.toml` already says every gate runs the full suite, and that stays.

WP 7.8 in detail: one `GET /api/catalog/tonight` takes 86.9 s on the dev machine and 106 to 109 s on CI, because the route calls `compute_night` once per catalogue row (13,370 calls) and recomputes the time-only quantities for each. Design first: the coder's hand-back names every caller of `compute_night` and of the route (`ui/src/components/atlas/TonightPicker.tsx`, `ui/src/next/hubs/sky/`), the array shape of the vectorised pass, the cache key and what invalidates it. The cached value is a function of the site: it stays in process memory and behind `view.site_derived`, never on disk. Tests: the vectorised form equals `compute_night` for a sample of rows on a made-up site; the route answers under 5 s on CI; a site change returns the new site's list. The same shape in `post_order` is checked and filed if present, not fixed here.

WP 7.7 in detail: #1255's table is the work list. The count to reproduce is two `--collect-only` runs, one with the wheel hidden (`program/testing-review/wheelgate_report.py`): 491 tests skip only because the wheel is absent, 85 of them are in the `native` job's list, 406 run on no job. After the WP the `native` job's `-rs` output names no wheel skip, and its collected count is at least 491. Expected added cost on the `native` job once #1256 is fixed: about 390 s of summed test time.

Wave close, coordinator: #1119 closes only after ten consecutive full `-n auto` server runs under 40 CPU burners with no failure. One run takes about 15 minutes unloaded and longer under the burners; it costs machine time and no tokens, so run it overnight.

## Wave 8 (engine.py owner 8.1; hub.py owner 8.6)

| WP | Issue | Covers | Owned files | Size | Mutant |
|---|---|---|---|---|---|
| 8.1 | #1222, #1144, #1146 parts 1 to 3 | The reject gate needs a minimum sample count; the report record reads RMS only from a guiding guider; the verdict window, the silent-guider case and `require_guiding` | `server/astrodeck/sequence/engine.py` (owner: `_check_quality`, `_reporter_record`, `_guide_start_bound`, `_guide_rms`, `_guide_rms_judged`, `_guide_verdict`, `_guide_over_ceiling_now`, `_guide_reject_armed`, `_guide_gate_refuses`, `_guide_retry_wait`, the `require_guiding` sites), `server/astrodeck/sequence/report.py` (record field only), `server/astrodeck/guide/native.py` (verdict inputs only), `native/crates/astro-guide/src/engine.rs` (`RECENT_CAP`), `server/astrodeck/guide/phd2.py` | L, R, design-first | `_check_quality` judges on one sample: the single-spike frame is rejected. Second: `_reporter_record` reads an idle guider's RMS |
| 8.2 | #1174, #1176 | The session file write leaves the event loop; the two tests exercise their claim | `server/astrodeck/sequence/engine.py` (shared: `SequenceEngine.start` and the run task's first awaited step), `server/astrodeck/sequence/session.py`, `server/tests/test_878_night_log_off_the_loop.py`, `server/tests/test_1075_remember_the_park.py` | M | the write goes back on the loop: the loop-hold test reads over its bound |
| 8.3 | #1193, #1194 | The preview frame is deleted on every exit path; abort does not wait behind a cancelled RPC | `server/astrodeck/devices/backends/asiair_backend.py`, `server/tests/test_asiair_backend.py`, `server/tests/test_w3_8_abort_during_download.py` | M | the delete moves back inside the success path: the cancelled-download case leaves the file |
| 8.4 | #1160, #1186 | One ledger of reviewed inputs with one place to approve a file; digests regenerated by a command | `tools/licence/audit_release.py`, `tools/licence/release-reviewed-inputs.json`, `tools/licence/version_bump_evidence.py`, `tools/licence/release_evidence.py` (new), `tools/licence/README.md`, `tools/licence/test_release_gate.py` | M, R, design-first | a stale digest is accepted: the gate test goes red |
| 8.5 | #1145 | Star detection, cloud score and the no-light check measure per region, so a half-blocked frame is not averaged into a clear one | `server/astrodeck/imaging/stars.py`, `server/astrodeck/imaging/clouds.py`, `server/astrodeck/solve/light.py`, `server/astrodeck/imaging/frame_class.py` | M | whole-frame statistics again: the half-obstructed fixture frame classifies as clear |
| 8.6 | #1173 | The status poll does not run between a reconnect's disconnect and connect | `server/astrodeck/hub.py` (owner: `reconnect_role`, the status poll's entry), `server/astrodeck/devices/alpaca.py`, `server/tests/test_967_reconnect_role_profile_alpaca.py` | S | drop the guard: the interleaved poll makes the reconnect fail |
| 8.7 | #829, #1124, #1151, #1158 | Leftovers: the position-unknown note's level and wording, stale comments, one docstring | `server/astrodeck/flows/to_plan.py` (note level and docstrings only), `ui/src/lib/slewController.ts`, `ui/src/views/MountView.tsx`, `ui/src/next/hubs/rig/sheets/mount.tsx`, `ui/src/components/flows/flowsSlice.ts`, `ui/src/components/flows/flowsTypes.ts`, `ui/src/next/hubs/session/flows/canvas/FlowCanvasToolbar.tsx`, `server/astrodeck/catalog/build_ngc_extras.py` | S | the note text drifts between the two UIs: the shared-constant test goes red |

Merge order: 8.7, 8.3, 8.4, 8.5, 8.6, 8.2, then 8.1 (rebased on 8.2). Part 4 of #1146 (a guide-optics field on the profile) is a new issue, filed when 8.1's design is accepted.

## Wave 9 (engine.py owner 9.1; hub.py and app.py owner 9.4)

| WP | Issue | Covers | Owned files | Size | Mutant |
|---|---|---|---|---|---|
| 9.1 | #1077 | A sweep that saw no stars keeps the stored slope and says why; the default span is retried once from the last good position | `server/astrodeck/focus/native.py` (all but `bracketed_minimum`, which 9.2 owns), `server/astrodeck/focus/autofocus.py` (all but `curve_verdict`, which 9.2 owns), `server/astrodeck/sequence/engine.py` (owner: `_post_flip_focus_is_owed`, `_autofocus_for_hop`, `_autofocus`), `server/astrodeck/solve/light.py` | L, R, design-first; code starts after 9.2 and 8.5 have merged | a starless sweep overwrites the slope: the stored calibration changes |
| 9.2 | #1181 | The roughness measure is scaled by the step, so a clean wide-stepped V passes | `server/astrodeck/focus/autofocus.py` (`curve_verdict` only), `server/astrodeck/focus/native.py` (`bracketed_minimum` only), `server/tests/test_focus_curve_acceptance.py` | M | roughness unscaled again: the wide-stepped fixture curve is refused |
| 9.3 | #1239 | A run ended by a stop time or a run-length cap says so; only a dawn ending says dawn | `server/astrodeck/sequence/engine.py` (shared: the end-of-windows block that sets `detail` and `end_reason`, and the `_dawn_cutoff` setters), the five tests that pin "stopped at dawn" | M | every window ending reads "stopped at dawn" again |
| 9.4 | #721 | A missing plate solver is its own hold reason, not "no light yet" | `server/astrodeck/hub.py` (owner: `arm_refusal`), `server/astrodeck/api/app.py` (owner), `server/astrodeck/sequence/resume_arm.py`, `server/tests/test_w2_holds_classified.py` | S | the missing-solver case falls through to no-light |
| 9.5 | #1206 | The saved-lights solve follows the solver that is installed now | `server/astrodeck/config.py`, `server/astrodeck/hub.py` and `server/astrodeck/api/app.py` (shared: the three read sites the issue lists), `server/tests/test_w3_9_solve_saved_lights.py` | S | read once at load again: installing the solver mid-run changes nothing |
| 9.6 | #1175 | The angle tolerance uses the panels that will be shot, not the full grid | `server/astrodeck/flows/to_plan.py` (compile geometry only), `server/astrodeck/sequence/angle_check.py` (`_convergence_term_deg`), the plan-hash goldens | M, R, design-first | the full grid again: the skipped-panel fixture gets the looser tolerance |

Merge order: 9.2, 9.6, 9.4, 9.5 (rebased on 9.4), 9.3, then 9.1 (rebased on 9.3).

Release checkpoint after wave 9 (owner's call): 0.3.50.

## Wave 10 (engine.py owner 10.1; hub.py owner 10.4)

| WP | Issue | Covers | Owned files | Size | Mutant |
|---|---|---|---|---|---|
| 10.1 | #1231 | Every lane node carries its stage name in live status; a deferred stage reads deferred | `server/astrodeck/sequence/engine.py` (owner: the stage-status emit sites), `server/astrodeck/flows/to_plan.py`, `server/astrodeck/sequence/models.py`, `ui/src/store.ts`, `ui/src/components/flows/flowRunState.ts`, `server/tests/fixtures/stage_status_stream.json` | L, R, design-first | a deferred stage keeps "running": the stream fixture test goes red |
| 10.2 | #1165 | A warning flagged site_derived pages with its site-derived text withheld, instead of not paging | `server/astrodeck/sequence/engine.py` (shared: the flip-point cloud-hold log call), `server/astrodeck/polar/native.py`, `server/tests/test_w2_alerting_withholds_site_derived.py`, `server/tests/test_w2_ratchets.py` | M | the flag suppresses the page again |
| 10.3 | #1148, #1142 | `attach_existing` sets `_pending`; the pre-#204 lock rule and its three TODO blocks are deleted | `server/astrodeck/sequence/report.py` (`attach_existing`), `server/astrodeck/guide/native.py` (`_lock_xy_brightest` and the three blocks) | S | drop the `_pending` assignment: the first record on a re-attached report raises |
| 10.4 | #1182 | The hold classifier is given the frame the failed solve ran on | `server/astrodeck/hub.py` (owner), `server/astrodeck/sequence/engine.py` (shared: `_classify_failed_solve`) | S, R | read `hub.last_frame` again: a newer preview frame changes the verdict |
| 10.5 | #1227 | The sim camera and the sim solver agree on orientation, scale and binning; the strict xfail is removed | `server/astrodeck/devices/sim.py`, `server/astrodeck/solve/simsolver.py` | M; full server suite once on the branch before the PR | the half-turn returns: the round-trip test goes red |
| 10.6 | #1154, #1159 | The server package declares its licence; the WCSLIB records, the frozen baseline note and the dated evidence match main | `server/pyproject.toml`, `tools/licence/audit_artifact.py`, `tools/licence/artifact-registry.json`, `packaging/RELEASE.md`, `tools/licence/bundled-components.json`, the mutation-evidence files the issue lists | M; after 8.4 | the licence field removed: the artifact audit reports the gap |

Merge order: 10.3, 10.6, 10.5, 10.2, 10.4, then 10.1 (rebased on 10.4).

## Wave 11 (photosphere and UI; no engine, app or hub edits)

| WP | Issue | Covers | Owned files | Size | Mutant |
|---|---|---|---|---|---|
| 11.1 | #1051 | The Lucas-Kanade step bias is corrected or bounded with a measured figure | `ui/src/next/hubs/sky/sheets/pano/align.ts`, `phaseCorrelate.ts` in the same folder | M | the correction removed: the step-scale case reads short again |
| 11.2 | #1056 | The latency estimate on a reverse pan | `poseTrack.ts`, `tracker.ts` in the pano folder | M | sign of the pan ignored: the reverse-pan case reads over 100 ms |
| 11.3 | #1058, #1117 | A bin is published as Measured only where a step was observed | `ui/src/next/hubs/sky/sheets/pano/horizon/horizonTrace.ts`, `tools/photosphere_sim/cases/treeline-pan1-41.json`, `tools/photosphere_sim/CONTRACT.md` | L | publish every bin: the chart-yard case shows false-open bins |
| 11.4 | #1156 | The photosphere_sim tests run in CI when their paths change; fixtures are regenerated and checked | `.github/workflows/photosphere-sim.yml` (new, with `paths:`), `tools/photosphere_sim/tests/**` | M | a stale fixture is accepted |
| 11.5 | #1153 | The phone stage-editor sheet has undo and redo, checked in a real browser | `ui/src/next/hubs/session/flows/inspector/FlowNodeEditor.tsx`, `ui/src/next/hubs/session/flows/canvas/FlowStagesPhoneSheet.tsx`, `ui/src/next/hubs/session/flows/canvas/canvas.css` | M | the sheet's edits skip the history: undo does nothing |
| 11.6 | #770, #1205 | Measure the header at 700, 768 and 820 px and fix only if it overflows; a bundle row shows what tells two same-named mosaics apart | `ui/src/components/flows/FlowHeader.tsx`, `ui/src/next/hubs/session/report/reportModel.ts`, `ui/src/views/ReportView.tsx`, `ui/src/next/hubs/session/report/BundlePanel.tsx` | S | the distinguishing field dropped: two rows read the same |

Cases regenerate with `python -m sim make-case <id>` under the system Python with Playwright, about 50 s each. 11.2 precedes 12.1: they share `tracker.ts` and `poseTrack.ts`.

## Wave 12 (engine.py owner 12.4; app.py owner 12.2)

| WP | Issue | Covers | Owned files | Size | Mutant |
|---|---|---|---|---|---|
| 12.1 | #1201 | The focal prior no longer pulls `f_lock`; the yaw chain's scale error is measured before and after | `focal.ts`, `tracker.ts`, `poseTrack.ts` in the pano folder, `tools/photosphere_sim/sim/score.py` | L | the prior weight restored: the closure error returns |
| 12.2 | #1228 | The assembled preview reads the solve's parity instead of assuming it | `server/astrodeck/imaging/mosaic_preview.py`, `server/astrodeck/sequence/coverage.py`, `server/astrodeck/api/app.py` (owner), `server/tests/test_w4_mosaic_preview.py` | M; after 10.5 | parity assumed: the mirrored fixture draws each panel flipped |
| 12.3 | #1245 | The Tonight card offers retry for a set-aside POOL candidate | `server/astrodeck/flows/progress.py`, `ui/src/lib/flowsApi.ts`, `ui/src/components/flows/framing/sections/PanelsSection.tsx`, `ui/src/components/flows/framing/TargetFramingSheet.tsx` | L, R, design-first | the candidate's retry flag dropped from the payload |
| 12.4 | #1196 | The three skip lines promise the next night only when Automatic resume is on | `server/astrodeck/sequence/engine.py` (owner: the two skip lines), `server/astrodeck/flows/tonight.py`, the `tonight_mosaic_2x3.json` golden, `server/tests/test_set_aside_promises.py` | S | the promise printed with resume Off |
| 12.5 | #1093 | A hold behind an obstruction is named as one | `server/astrodeck/sequence/engine.py` (shared: the hold-reason call site), `server/astrodeck/sequence/group_rules.py` (`close_pass`, `_held_pass_reason_code`) | S | every centring hold blames the sky again |

Release checkpoint after wave 12 (owner's call): 0.3.51. #1108 closes here if #1077, #721 and #1093 are closed and #308 has its measurement.

## Rig-gated (code done or not needed; the next step is on the rig)

Log reads, which the coordinator does itself over ssh when the rig is on (read-only; the durable log is `captures/logs/<night>.jsonl`):

- #597: count `relay connection lost` per night since 2026-10-09 with the minute of the hour. The nights of 10/11 to 10/16 are excluded: the scope was stowed.
- #882: re-count SyncRefused and SyncUnverified per night. Three healthy nights at zero close it.
- #789: after the first corrected high-declination mosaic night, read the rotator moves per hop.
- #125, #1128: on the current release, read the `rig_precheck --report` line, the boot cause and the beacon.

Steps that restart a process or move the mount, handed to the owner as one-line `!` commands (the classifier refuses them from the coordinator even with a go-ahead):

- #496: end the server child and watch the supervisor relaunch it.
- #144, #1109: in daylight after a reset: `position_known` false, a nudge answers 409, the pad is locked, hold works, a solve sync or TRUST POSITION clears it. Never advise a slew while the position is unknown.
- #145: TEST ROTATOR after a flip; compare `sky_sign` per pier side.
- #594: `scripts/rig_rotator_follow.py` at dusk, then the ruling.
- #851, #1071: `POST /api/mount/pulse-probe` for 241 s on both pier sides, then 20 minutes of post-flip drift with the flight recorder on. Intermittent: one clean run does not close it.

Other:

- #308: moonless overcast frames through `tools/no_light_audit.py --moonless <night>`. Weather-gated.
- #879: the NINA and ASCOM-simulator topocentric test. This one runs on the dev machine, no rig needed; it is scheduled as a coordinator task at the close of wave 8.

## Owner-gated (no code until the answer; defaults above)

#1073 (D-1) and with it #1123, #1155, #800. #1220 (D-2). #1143 (D-3). #754 and #442 (D-4). #1192 (D-5). #16 (D-6). #178 (owner action 1). #1061, #1055 and #1057 (owner action 2). #90 (owner action 3).

## New issues the coordinator will file as this runs

- Filed already by the testing review: #1255 to #1261 (wave 7).
- #1146 part 4: a guide-optics field on the profile.
- From WP 7.2's read map: any file under `paths-ignore` that a test reads.
- From WP 7.8: the per-row `compute_night` fan-out in `post_order`, if it has the same cost.
- Every integration defect and every neighbour a coder reports.

## What Sonnet must not do

- Do not widen a timing bound, skip a test or loosen a tolerance without the named mutant shown red first.
- Do not touch `engine.py`, `app.py` or `hub.py` outside the owner or shared functions named for the wave.
- Do not base anything on the `C:\Users\bear\astro` working tree or on `feat/*` branches.
- Do not run a full suite per WP. Run the tests named by the test rule and let CI run the rest.
- Do not build a per-test selector for the server suite. It was measured three ways and selects 75 to 100 percent of the suite's time; the review has the numbers.
- Do not treat a UI selector miss as a flake. A UI test that fails on main after its pull request ran green is an issue against the selector, filed with the test, the changed file, and why the import graph did not connect them.
- Do not make a job skippable on main unless the pull request run executed that same job in full and it passed.
- Do not have pull requests skip tests marked `slow`, and do not add a test to the 60 s allowlist without a reason on the same line.
- Do not start a wave without the owner's go-ahead, and do not start the code stage of a design-first WP before the coordinator accepts the design.
- Do not write the site's latitude, longitude or label, nor any mount alt/az, into anything.
- Do not deploy to the rig during a night.
