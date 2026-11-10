# Testing methodology review (2026-11-09)

The question: are we running every test for every code change, and can a change run only the tests related to it?

## The answer

1. Yes, today every change that is not docs-only runs every CI job and every test, and it does so twice: once on the pull request and once more on main after the merge.
2. "Only related tests" works at two levels and is planned at both: whole CI jobs (a server-only change stops running the UI job) and UI test files (a changed source file reaches a median of 28 of 774 test files).
3. It does not work for server tests. Three ways of choosing server tests by dependency were measured, and each chose 75 to 100 percent of the suite's time for a typical change. One of them ran slower than the whole suite.
4. The largest waste is not unrelated tests. It is the same tree tested twice: 20.8 percent of all CI job-minutes went to re-running, on main, a tree its pull request had already passed.
5. The server job is 59.2 percent of all CI job-minutes, and a few dozen tests hold most of its time. Making those cheap is worth more than any way of running fewer of them.

The work is scheduled as wave 7 of `2026-11-09-open-issue-plan-2.md`. Seven issues were filed: #1255 to #1261. The owner decision is D-8 in the plan.

## What runs today

- `ci.yml` runs on every pull request and every push to main. Its only path rule is workflow-wide: a change touching only Markdown, `docs/**`, `.remember/**` or `tools/ux/**` runs nothing. Any other change runs every job.
- No job has a path filter. A change to `relay/` runs the 23-minute server suite and the 9-minute UI suite.
- The server job runs `pytest -n auto` over all of `server/tests`. The UI job runs `npm test`, and `ui/run-tests.mjs` walks every test file and ignores its arguments (#1152), so a UI coder cannot run one file through the runner either.
- A pull request run costs 39.7 job-minutes (run 42695961395: server 23.3, ui 9.0, container-security 2.9, native 2.5, windows-private-state 1.3, relay 0.4, orangepi-security 0.3). The run on main after the merge costs about the same again.
- Coders do not run full suites. The coordinator's `program/COMMON.md` (outside the repository), rule 3, already scopes a coder to the tests it changed, the test files of the modules it edited, and the cross-cutting pins. The cost in question is CI.

The repository is public, so Actions minutes are not billed. The cost is waiting: a pull request waits for the server job (median 26.8 minutes when green, p90 29.4, n 132), and a flake costs a rerun of the same length.

## Where the CI minutes go

The last 200 `ci.yml` runs, 2026-10-10 to 2026-11-10, every attempt counted: 8330 job-minutes.

| Runs | Job-minutes | Share |
|---|---|---|
| pull request, succeeded | 3201 | 38.4 percent |
| push to main, succeeded | 1437 | 17.3 percent |
| pull request, failed | 1059 | 12.7 percent |
| push to main, cancelled by the next merge | 885 | 10.6 percent |
| pull request, earlier attempts of a rerun | 827 | 9.9 percent |
| pull request, cancelled | 460 | 5.5 percent |
| push to main, failed | 391 | 4.7 percent |
| manual dispatch | 69 | 0.8 percent |

| Job | Job-minutes | Share |
|---|---|---|
| server | 4933 | 59.2 percent |
| ui | 1534 | 18.4 percent |
| native | 643 | 7.7 percent |
| container-security | 614 | 7.4 percent |
| all others | 606 | 7.3 percent |

Two readings of the first table:

- Runs on main hold 2713 job-minutes (32.6 percent).
- Failed pull request runs plus the superseded attempts of reruns hold 1886 (22.6 percent). Not every failed pull request run is a flake, so this is a ceiling on what flakes cost, not a measurement of it.

## The levers, ranked by measured size

| Rank | Lever | Measured size | Issue | WP |
|---|---|---|---|---|
| 1 | Main skips a job that already passed on the same tree | 1735 job-minutes, 20.8 percent of all (1415, 17.0 percent, once the UI job must run in full on main) | #1260 | 7.3 |
| 2 | Remove the open flakes | part of the 22.6 percent above; 8 of 36 completed runs on an already-green tree failed | #1119, #1188, #1254 | 7.6 |
| 3 | Make the slowest server tests cheap | ten tests and one parametrised test hold 1222 s of summed time on CI; the server job is 59.2 percent of all minutes | #1261, #1256, #1257 | 7.4, 7.8 |
| 4 | A pull request runs only the jobs whose area changed | 303 of 1985 job-minutes over the last 50 merges, 15 percent | #1259 | 7.2 |
| 5 | The UI job runs only the test files the change reaches | median 28 of 774 test files per changed source; `npm test` is 389 s of the job's 432 s | #1258 | 7.1 |

### 1. The same tree tested twice (#1260)

A pull request run tests `refs/pull/N/merge`: the branch merged into main at run time. When the branch already contains main, that merge has the same tree as the merge commit that lands on main. The run on main then repeats a finished run.

- Of the last 77 runs on main, 47 had a tree byte-identical to the pull request head (`git rev-parse <merge>^{tree}` equal to `git rev-parse <merge>^2^{tree}`). 30 differed, because main had moved under the branch.
- Those 47 runs spent 1735 job-minutes on jobs the pull request had already run: server 1031, ui 320, native 151, container-security 129, windows-private-state 64, relay 23, orangepi-security 17.
- The same check on git alone: 50 of the last 80 first-parent merges have identical trees.

The fix skips, per job, a job that ran and passed on the same tree in the pull request's run. It fails open: a differing tree, a squash, a direct push or an API error runs everything. Once the UI job runs a selection on pull requests it can no longer be skipped on main, which leaves 1415 job-minutes (17.0 percent).

These 47 runs are also the only flake measurement here that needs no judgement. A failure on a tree that was green on its pull request is a flake by construction. 28 succeeded, 11 were cancelled, 8 failed: 8 of the 36 that completed, 22 percent. All eight had a cause already filed (#1210, #1184, #664, #1136, #1139, #1106), and each of those has since been fixed, so 22 percent describes the window and overstates today.

### 2. Flakes

- 18 rerun attempts on 14 runs; 11 of the 14 ended green after the rerun.
- Still to fix: #1119 (the class of test that fails only under full-suite load), #1188, #1254 (a test that fails at about half the hours of a non-UTC day and never on CI).
- WP 7.6 merges first in wave 7 for this reason: every later pull request pays for a flake with a 25-minute rerun.

### 3. A few dozen tests hold the server suite's time (#1261)

Local full run at d96cfef0, `-n 12`: 19629 tests, 907 s wall clock, 9724 s of summed test time.

| Tests slower than | Count | Share of tests | Share of suite time |
|---|---|---|---|
| 30 s | 28 | 0.1 percent | 21.7 percent |
| 10 s | 135 | 0.7 percent | 39.0 percent |
| 5 s | 364 | 1.9 percent | 55.0 percent |
| 1 s | 1549 | 7.9 percent | 81.3 percent |

On CI (main run 42764830405, pytest step 1359 s), the 30 slowest calls sum to 1222 s and belong to ten tests and one parametrised test:

| Test | Seconds on CI | Cause |
|---|---|---|
| `test_catalog.py`, one parametrised test, its 20 slowest parameters | 504 | 26,740 linear scans of the 13,370-row catalogue |
| `test_mosaic_spec_claims.py`, five revision tests | 261 | not profiled yet |
| two tests of `GET /api/catalog/tonight` | 216 | the route itself takes that long (#1257) |
| `test_s7_ledger_cost.py`, one test | 163 | a 2000-frame night, sized far past what its mutants need |
| `test_flows_compile_never_raises.py`, one test | 56 | not profiled yet |
| `test_cloud_hold_follows_the_mount.py`, one test | 22 | not profiled yet |

How much wall clock the cuts return depends on the runner's worker count, which the log does not print. Assuming the 4 cores GitHub gives a standard runner on a public repository, 1222 s of summed time is at most about 5 minutes of the 22.6-minute pytest step.

Two of these are product findings, not test findings:

- #1257: one request to `GET /api/catalog/tonight` takes 86.9 s on the dev machine and 106 to 109 s on CI. The route runs `compute_night` once per catalogue row and recomputes the time-only astronomy for each. The same wait reaches whoever opens the tonight list in the UI.
- `search_catalog` scans all 13,370 rows per call (9.2 ms). An exact-id index speeds the product and the test together.

### 4. Whole jobs by area (#1259)

Replay of the last 50 merges with each changed path mapped to the jobs that own it:

- 18 of 50 changed no file under `ui/`, and ran the UI job anyway.
- 49 of 50 touched something the server job owns, so the server job is rarely skipped.
- 6 of 50 touched a path no area owns (`.github/`, `.gitattributes`, `scripts/`); those run everything.
- Saving: 303 of 1985 job-minutes (15 percent). Median per merge 39.7 to 36.5.

The saving is modest because work packages here usually touch server code. A skipped job must not block a merge, so the required check becomes one final `ci-ok` job.

### 5. UI test files by import graph (#1258)

- 774 test files, 912 source files. The tree uses relative imports only, so the graph is cheap to build.
- Test files reached per changed source file: median 28, p75 56, p90 201, p99 467, max 574 (`ui/src/types.ts`).
- 130 test files read other files from disk, which the import graph cannot see. They always run until each declares what it reads. That sets a floor of 130 of 774 files (17 percent) on any UI change.
- 37 relative imports do not resolve; a change to a file such an import points at runs everything.
- Main runs the full UI suite, so a miss by the selector is caught there and filed against the selector.

Not measured: the share of UI test time a typical multi-file change selects. WP 7.1's acceptance replay measures it.

## Why server tests cannot be selected by dependency

Three methods, one result.

| Method | What it selects for a typical change | Verdict |
|---|---|---|
| Static import graph | every test file reaches 231 of 257 source files; over the last 50 merges the median selection is 97 to 100 percent of suite time | selects everything |
| Executed-file map (which source files each test ran) | over the 37 of the last 50 merges that change server source: median 87.2 percent of suite time, p25 82.5, p75 87.7; over 80 percent for 30 of 37; under 20 percent for 6 of 37 | selects nearly everything |
| pytest-testmon (executed code blocks, the finest grain available) | PR #1252: 33 percent of tests holding 88 percent of the time. PR #1242, an 11-line change to `hub.py`: 15 percent of tests holding 75 percent of the time | selects most, and costs more than it saves |

The cause is one fact about the suite: 5306 of 19434 tests (27 percent) execute `astrodeck/hub.py`, and those tests hold 87.2 percent of the suite's time. The expensive tests are integration tests that start a whole hub, and nearly every server change is reachable from a hub.

testmon in detail, because it is the tool usually proposed for this:

- Building its database took 2697.5 s where the plain run takes 907 s, 3.0 times as long, because every test runs under coverage tracing.
- 92 tests fail under that tracing and pass without it.
- With nothing changed it selected only those 92, in 3.3 s. The mechanism works.
- For PR #1252 (8 files, 497 insertions) it selected 6431 of 19446 tests. Running the selection took 771.2 s. Running the whole suite without testmon took 700.5 s. Selection was slower than no selection.
- It was safe on that replay: the full run failed 134 tests because of the change, and all 134 were in the selection.
- The selected run ended with an internal error in the plugin under xdist.

PR #1242's full-run comparison and a third replay were still running when this was written; their numbers will be added below.

What would change this verdict: hub-booting tests becoming cheap (a shared, session-scoped hub fixture, or tests that exercise a module without a hub). That is a redesign of the test suite, not a selection tool, and it is not in this plan.

## What was ruled out

- A per-test selector for the server suite (`testmon`, an import graph, a file map). Measured above.
- Pull requests skipping tests marked `slow`. `server/pyproject.toml` says of that marker that CI and every gate keep running the full suite. A test left out of the pull request run fails on main after the merge, which is the expensive place to find it.
- Requiring branches to be up to date before merging, so that the pull request run alone suffices. It removes the 30 tree-differs runs too, but every merge then invalidates every other open pull request's run. With work packages merging in sequence that costs more than it saves.
- Server pull requests running only the test files they changed plus those of the modules they edited, with the full suite on main. This is the literal reading of "only related tests" for the server. On the one replay available, 129 of the 134 change-dependent failures were in test files the pull request itself changed, which is a sample biased toward tests written for that change. Its miss rate on older tests is unmeasured, and a miss is found after the merge. It is the alternative offered in D-8, not the recommendation.

## Found along the way

| Issue | Finding |
|---|---|
| #1255 | At least 406 server tests in 41 files are gated on the native wheel and run on no CI job. The `native` job runs a hand-written list of ten files; every wheel-gated test added since was silently left out. Eight different gating idioms hide them. |
| #1256 | Four PPEC tests wait 150 s each in real time. Their fixture's clock virtualises `time()` and leaves `monotonic` real, so a 150 s probation budget is waited out. 750 s of summed local time, invisible on CI only because of #1255. |
| #1257 | The tonight route takes about 90 s per request (above). |
| #1254 | A test that pins one of two clocks fails at about half the hours of a non-UTC day. |

#1256 is fixed before #1255, so the four waits are gone before those files reach CI.

## What a pull request waits for, before and after

- Today: the server job, median 26.8 minutes, whatever the change touches.
- After wave 7, a change with no server-owned path: the server job does not run. This was 1 of the last 50 merges, so it is rare here.
- After wave 7, any other change: the server job still runs in full, shorter by whatever the slow-test cuts return (at most about 5 minutes under the 4-worker assumption). The UI job is skipped or runs a selection.
- After the merge: main re-runs only what the pull request did not already pass on that tree.

The honest summary: wave 7 removes about a fifth of CI minutes by not repeating work, removes the UI job from server-only changes, and shortens the server job. It does not make a server pull request fast. Only cheaper hub tests would.

## Not measured

- Which server tests read files outside `server/` at run time. A regex estimate found 69 server test files that mention `ui/` and 21 that walk the whole tree. The run-time read map (`readmap_plugin.py`) is written and smoke-tested but was not run over the full suite, because the testmon experiment held the machine. It is the first step of WP 7.2, and it also answers whether today's `paths-ignore` hides a file some test reads.
- The CI runner's xdist worker count, and therefore the exact wall-clock return of the slow-test cuts.
- The tonight route's time on the rig PC and on the Orange Pi, both slower than the machines measured.
- The full distribution of test durations on CI. `--durations=30` shows 30 tests; WP 7.4 keeps the whole list as an artifact.
- The flake rate today. The 22 percent is over a month in which most of the causes were fixed.

## Reproduce

Scripts and data are in the coordinator's `program/testing-review/` directory on the dev machine, outside the repository. Each script prints its usage line.

| Number | Script | Input |
|---|---|---|
| CI ledger by event, outcome and job | `ci_ledger.py` | `ci-runs.tsv`, `ci-jobs.tsv` |
| Identical-tree runs on main | `main_dup.py`, `main_dup_byjob.py` | the same two files and a checkout |
| Area filter replay | `area_replay.py` | a checkout |
| Slow-tier shares | `junit_cost.py` | `server-junit.xml` |
| Static import selection | `import_select.py` | a checkout |
| Executed-file map and its replay | `tm_filemap.py`, `tm_share.py` | `tm/base.testmondata` |
| testmon replays | `tm_experiment.py` | writes `tm/experiment.log`, `tm/result.json` |
| UI import graph | `ui_graph.py` | writes `ui-graph.json` |
| Wheel-gated tests on no job | `wheelgate_report.py` | two `--collect-only` runs |
| Tonight route profile | `tonight.prof` | cProfile of one request |
