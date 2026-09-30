# AstroDeck open-issue backlog plan (2026-09-30)

## Goal

Work the 168 open issues on epim/astrodeck in this order: unattended nights made safe and private first, then the things the next rig nights (mosaics) depend on, then correctness, then copy, then test hygiene and docs. Sonnet agents write all the code. The orchestrator verifies, commits, deploys, and files new issues.

State when this was written: branch feat/photosphere-production at 5c3a6373. The rig runs 0.3.37 (S7 plus two hotfixes). H4 hardening (7be51e5a) and the hotfix port (7fb416f5) are committed but not deployed. The Fly relay runs v9 (584c11e0). The rotator coupling is physically loose and the owner will fix it later.

Where the 168 issues go:

| Home | Issues |
|---|---|
| Work packages (73 WPs in 13 waves; one of them, WP-65, carries no issue of its own) | 133 |
| Close without code | 6 |
| Verify first | 1 |
| Rig-gated (waiting on rig or device work) | 7 |
| Owner-decision-only (no code) | 4 |
| Backlog features | 17 |

Each open issue number appears exactly once in this document, written as `#N`, at its home. Everywhere else the plan points at WP ids (WP-nn), decision ids (D-nn) and rig ids (R-n), and within a WP it labels issues by letter (a, b, c). A WP split in two keeps its number with a suffix (WP-05a, WP-05b). An issue moved out of a WP gets a new id from WP-60 up, so existing references stay stable.

The critical path is `server/astrodeck/sequence/engine.py`. Thirteen WPs must edit it (WP-01, WP-11, WP-21, WP-31, WP-37, WP-44, WP-49, WP-53, WP-55, WP-56, WP-57, WP-58, WP-59), and only one WP may own it per wave, so the plan runs to 13 waves. `server/astrodeck/hub.py` has an owner in every wave from 1 to 9 (WP-10, WP-11, WP-22, WP-32a, WP-32b, WP-45, WP-51, WP-53, WP-66) and `server/astrodeck/api/app.py` in every wave from 1 to 9 as well, which is why two small late items (WP-66, WP-67) sit in wave 9. After wave 8 the waves are thin. Fill their free slots from the backlog, in the order the backlog lists, as long as file ownership allows.

## Engine lane relaxation (orchestrator's option)

Two WPs that both edit engine.py may share a wave when the engine functions each one names are disjoint. Each runs in its own worktree. The orchestrator merges them one after the other and reruns the full server suite between the two merges. The orchestrator may use this to compress waves 9-13, where their Depends on rows allow it. The waves below are laid out without it.

## How each wave runs

- Coders and verifiers are Sonnet agents (`model: sonnet`).
- Each wave is one workflow. Its pipeline is build, then an independent verify, for every WP in the wave. Each WP runs in its own git worktree cut from the wave's base commit. The verifier is a different agent from the coder: it rereads the issue, reruns the named mutant, reruns the owned-area tests, and checks that nothing outside the owned files changed.
- When the wave's WPs are verified, the orchestrator merges them onto one tree and runs the full server suite (`-n auto`), the UI suite (vitest, typecheck, build) and the relay suite on the merged tree.
- It runs the needles scan (file names only), a BOM scan and an emoji scan over every changed file.
- It commits each WP separately with explicit pathspecs (`git commit -F msg -- <paths>`), closes that WP's issues with the commit hash and the tests that prove each fix, and files every new defect a coder or verifier reports, after checking `gh issue list`.
- It deploys and pushes only with the owner's word.

## Rules for every work package

1. One WP is one Sonnet coding task of about 2 to 6 hours. Effort key: S is 1-2 h, M is 3-4 h, L is 5-6 h (up to about 7 h), and L+ is about 8 h. The orchestrator may run an L+ WP as two sequential dispatches in the same worktree.
2. Tests come first. Every behaviour change is shown red under a named mutant (the one-line source change that brings the defect back), and the failing assertion is quoted in the coder's return. A test-only WP names the mutant its new case catches and shows it red.
3. A coder edits only the files its WP owns. If it needs any other file, it stops and reports without editing that file. New test files carry a wave prefix (`test_w1_...`, `test_w2_...`) so parallel WPs cannot collide.
4. Coders never commit, never push, never write to GitHub (reading with `gh issue view` is fine), never deploy, never ssh, and never make a network call to astrotown.
5. Coders never write the site's latitude, longitude or label, or any mount alt/az number, into code, tests, comments or their return. Tests use a made-up site. WPs marked Privacy get the needles scan from the orchestrator before commit (`grep -l -F -f C:/Users/bear/.astrodeck/privacy-needles.txt <changed files>`), which reports file names only.
6. Files are UTF-8 without a BOM. No emojis. Control characters are written as escapes.
7. Isolation (recommended in D-01): each WP runs in its own git worktree cut from the wave's base commit. Mutation runs happen only inside that worktree, from a byte backup, with a grep afterwards to confirm the mutant is gone. Dependencies are linked read-only. Before removing a worktree, remove any junction inside it first (the scratch-junction trap).
8. Done means: tests for the owned area pass, the full server suite (`-n auto`) passes in the worktree, and UI WPs also pass vitest for the touched area, typecheck and build. The return lists changed files, new tests, the mutant evidence, suite results, and any new defect found. The orchestrator checks `gh issue list` and files each new defect.
9. The orchestrator commits each WP separately with explicit pathspecs (`git commit -F msg -- <paths>`), and only after verifying it.
10. Shared harnesses (`server/tests/_group_harness.py`, `server/tests/_flow_night.py`), `server/tests/conftest.py` and `server/astrodeck/devices/sim.py` may be edited only by the WP whose owned-files list names them in that wave.

## Owner decisions (ask all at once, now)

**APPROVED 2026-09-30 14:07: the owner approved every recommended answer below ("Approve all recommended").** Each row is now a ruling. Cite them as "backlog ruling D-nn (owner-approved 2026-09-30)".

Each row gives a recommended answer so it can be approved in one line. A gated WP waits for its decision. If its wave arrives with no ruling, the WP ships the parts the decision does not cover, or moves to the next wave where its files are free.

| D | Needed by | Question | Recommended answer | Blocks |
|---|---|---|---|---|
| D-01 | before W1 | Should tooling force every mutation and full-suite run into an isolated worktree or byte copy under its own scratch directory, since parallel agents have collided in the shared tree three times? Issue: #254 | Yes. Adopt rule 7 for this plan starting in W1. The issue stays open until three things hold: the workflow scripts enforce the isolation, the discipline text says "in a copy of the tree", and each gate run records that the tree was quiet (the same `git status --porcelain` output before and after the run). The orchestrator then closes it with those three as the evidence. | process |
| D-02 | before W1 | Should `server_ctl.py start --fresh` require the probe marker everywhere, or accept unmarked directories under the default `.probe/` path? Should `seed_session.py` add a live port-8800 check? | `--fresh` requires the marker everywhere, with a one-time exception: an unmarked directory under `<repo>/.probe/` is accepted once and marked. `seed_session.py` keeps its static marker guard, refuses any marker that names port 8800, and adds no network check, since its pinned test is meant to keep it offline. | WP-12 |
| D-03 | W3 | How should a mosaic's centring holds escalate: the all-fail hold, the all-transient passes, and the last live panel? | Keep one counter per group of consecutive held passes, with all-fail and all-transient passes both counting. Alert the operator at 3 held passes (about 30 min). Set the group aside for the night at 6, or immediately when every failure in two passes in a row carries the same rig-side reason code. Drop the "at least two attempted" condition, so a pass where every live panel was tried and missed is a hold. The escalation caps what a truly bad last panel can cost. | WP-21, WP-33 |
| D-04 | W4 | Starting a run disarms every other armed session without saying so. Keep the singleton and make it visible, or support a queued "next" armed session? | Keep the singleton. WP-31 adds a warning line naming each disarmed session and a `disarmed` list in the start response, and WP-65 (wave 5) shows it in the UI. File the queued next session as a new feature issue. | WP-31, WP-65 |
| D-05 | W5 | Now that the coupling will be re-fixed, should rotation require a self-test, and should the follow-check refuse rather than warn? | Yes to both. If the camera turns less than 90% of the commanded move, the rotation is untrusted: the panel is deferred and not shot, and the night log says why. A +/-20 degree self-test with two solves runs once per night before the first rotating mosaic. If it fails, rotation is off for the night and panels are shot at a fixed angle. The backlash constant is decided separately (D-18). WP-32a does not wait for this ruling. | WP-32b |
| D-06 | W5 | When a plan sets max_guide_rms, is a frame shot while the guider is not guiding rejected? Is a mosaic pass where every panel failed to start guiding a guider fault for guiding_action (ruling 5's tentative answer)? | Yes to both. A frame with no RMS because guiding had stopped is rejected when a ceiling is set. The all-panels-failed pass goes to guiding_action. | WP-37 |
| D-07 | W7 | Should PANELS tell apart a set-aside that expires tonight from one that lasts all night? And do items 3 (learned horizon mask) and 4 (retry set-aside action) get their own issues? | Yes. Publish `kind` and a `for_now` flag on each set-aside record and word them "set aside for now" and "set aside tonight". File items 3 and 4 as new issues. Build this as its own WP, including regenerating the recorded fixture. | WP-49 |
| D-08 | W9 | When no site is saved, should a DUSK/DAWN-bounded run refuse to start, run and stop at the first daylight frame, or be blocked at compile? | Refuse to start (fail closed), with a sentence naming the missing site setting. Compile shows the same sentence as a warning. | WP-55 |
| D-09 | W11 | After two failed sparse-field autofocus runs, should the run continue at the last good sweep position or where the sweeps started? | Move to the last good sweep position from this run if there is one. If there is none (the initial autofocus), stay where the sweeps started and say so in the night log and the session report. Change ruling 4's wording to match. | WP-57 |
| D-10 | W6 | Should escalation.reconnect_resume default to True now that camera-silence detection exists? | Build a measured `connected` for the camera backends (WP-47). Turn reconnect_resume on in the astrotown profile only, after a daytime rig check. Leave the global default False until a night has shown it works. | WP-47 |
| D-11 | W7 | What should the meridian block report while the mount is not tracking? Should the simulator's RA drift while tracking is off? | No countdown, with the reason "not tracking". Yes, make the simulator's RA drift with the sidereal clock, as a real GEM's does. | WP-51 |
| D-12 | W8 | For the Windows proactor fault (KeyError, then WinError 6 out of run_forever): switch to the selector loop, or catch it and let the supervisor restart the server? | Keep the proactor loop. On Windows the selector loop cannot run asyncio subprocesses, so it is not an option if the server uses any (check at dispatch). Install a loop exception handler that logs this fault once with its traceback and exits non-zero, then confirm in daylight that the rig's supervisor restarts the server. | WP-54 |
| D-13 | W7 | What should the resumable-run cards be titled for each end_reason? | "RESUME STOPPED RUN" after an operator STOP. "RESUME INTERRUPTED RUN" after a restart, crash or safety stop. One shared helper for all three cards, which depends on WP-44 separating the two end reasons. | WP-52 |
| D-14 | W6 | A never-run flow card says so twice. Which line keeps it? | The status word keeps it. The meta line drops its last-run slot when the status reads NEVER RUN. | WP-48b |
| D-15 | W6 | In the classic mosaic footer's extreme case, drop the grid before cutting the name to an ellipsis (as built), or apply ruling 9 literally? | Keep what is built and amend ruling 9's text. | WP-48b |
| D-16 | W7 | What should the end-of-night dome/roof close default be, and should ResumeArm open the roof and dust cover before its first slew? | WP-50 fixes the copy now so it claims only what the code does, and sets close_dome_when_done to default True when a dome is connected. Opening the roof and cover before the first slew (at run start as well as in ResumeArm) is filed as a new issue with the other two unbuilt items of WP-50 (c)'s issue, and is built only for a rig that has an actuated cover (astrotown's saved profiles have no cover calibrator). WP-50 (c)'s issue stays open until they land. | WP-50 (the default; the copy half proceeds without a ruling) |
| D-17 | any | Should #/next GENERATE FLOW image a kept framing's dragged centre, or keep using the catalogue position, leaving SEND TO FLOW WIZARD as the only path that carries the centre? Issue: #459 | Keep what is built. The orchestrator closes the issue with this ruling only after the owner approves this row. | none (owner-decision-only) |
| D-18 | after rig fix | Once the coupling is fixed, what should ROTATOR_BACKLASH_DEG be, given true reversal backlash was measured at 0.1-0.2 degrees? | Re-measure after the fix, then use about 0.5 degree of overshoot, or none. Its home is R-6. WP-53's manual-move path uses whatever value is current when it runs. | WP-53 (soft) |
| D-19 | now | Dead-man switch and remote power: configure a dead-man URL, and buy a network-switched outlet? Issue: #125 | Owner actions only. Configure a dead-man URL (for example healthchecks.io) in Settings > Alerts, and decide on the outlet. The issue closes when rig_precheck's "watched:" line shows the URL, and only after its two remaining gaps are filed as their own issues: remote power (a network-switched outlet) and off-box evidence (a record of an outage that survives the rig PC going dark). | none (owner-decision-only) |
| D-20 | before S8 | Which open confirmations must land before S8 and the supervised S7 night: the ruling-5 guide-start confirmations, the dome default, the README amendment signature? Issue: #189 | Settle D-06 and D-16, and sign the README amendment. The orchestrator rewrites the tracking issue's body as a status table after each release and keeps it open until S8 ships. | S7 night, S8 |

## Where the priorities land

- P0 safety and privacy:
  - Wave 1: WP-01 to WP-09 (WP-05 as WP-05a and WP-05b), and WP-12, gated on D-02.
  - Wave 2: WP-11. Wave 3: WP-21 (D-03). Wave 4: WP-31 (D-04).
  - Wave 5: WP-32b (D-05; rig validation only after the coupling fix) and WP-65 (the UI half of D-04).
  - One rig-gated item (R-5).
  - WP-10 is P1. It sits in wave 1 because 0.3.38 is a daytime deploy.
- The mosaic path for the next rig nights:
  - Set-aside and hold rules: WP-07, WP-21, WP-33, WP-49.
  - Solve: WP-30a, WP-45.
  - Rotator software: WP-32a, WP-32b, WP-53. Rotator hardware: R-4, R-6.
  - Relay reliability: WP-13, WP-28.
  - Flow compile and run: WP-09, WP-19, WP-34.
- Correctness, copy and test hygiene: waves 3-13.

## Wave 1 (P0 first; one owner gate, D-02, asked before the wave)

Base commit is HEAD. Harness owners: `_group_harness.py` goes to WP-07 and `_flow_night.py` to WP-09. No WP owns sim.py or conftest.py in this wave. If D-02 has no ruling when the wave starts, WP-12 moves to wave 2, where no WP touches its files.

| WP | Title | Issues | Sev | Owned files | Effort | Depends on |
|---|---|---|---|---|---|---|
| WP-01 | Engine: unattended hazards | #199 (a), #577 (b), #134 (c) | P0 | server/astrodeck/sequence/engine.py; server/tests/test_idle_park_hold.py | L+ | none |
| WP-02 | Dead mount link | #133 (a), #138 (b) | P0 | server/astrodeck/devices/serial_link.py; server/astrodeck/devices/backends/zwo_am5.py; server/astrodeck/dawn_park.py | L | none |
| WP-03 | Sun watch escalates an unreadable mount | #137 | P0 | server/astrodeck/sun_watch.py | S | none |
| WP-04 | Alert delivery: priority, per-sink senders, dead-man off the frame path | #549 (a), #542 (b) | P0 | server/astrodeck/alerting.py | L | none |
| WP-05a | Rig server access log is path-only (Privacy) | #550 | P0 | server/astrodeck/__main__.py; new server/astrodeck/logfmt.py | S | none |
| WP-05b | rig_precheck prints no mount alt/az (Privacy) | #140 | P0 | scripts/rig_precheck.py; server/tests/test_rig_precheck_site_is_redacted.py | M | none |
| WP-06 | Viewer report and bundle redaction (Privacy) | #567 | P0 | server/astrodeck/api/app.py; server/astrodeck/api/redact.py; server/astrodeck/auth/capabilities.py | M | none |
| WP-07 | Group rules: rise-branch privacy, pass copy, reset test (Privacy) | #564 (a), #575 (b), #580 (c) | P0 | server/astrodeck/sequence/group_rules.py; server/tests/_group_harness.py; server/tests/test_h4_group_rules_centring.py; server/tests/test_group_floor_stop_counted.py; server/tests/test_group_rotation.py | M | none |
| WP-08 | #/next acts only on the flow it opened | #553 (a), #555 (b), #499 (c) | P0 | ui/src/next/hubs/session/flows/canvas/FlowStagesPhoneSheet.tsx; ui/src/next/hubs/session/flows/FlowsCanvasHost.tsx; ui/src/next/hubs/session/flows/create/wizard.tsx; ui/src/next/hubs/session/flows/tonight/TonightSheet.tsx; ui/src/next/hubs/session/flows/openFlow.ts; ui/src/components/flows/flowsSlice.ts; ui/src/next/hubs/session/now/__tests__/nowRunOpensThePressedFlow.test.tsx | L | none |
| WP-09 | DUSK window reaches the schedule | #191 (a), #547 (b), #540 (c) | P0 | server/astrodeck/flows/compile.py; server/astrodeck/flows/nodes.py; server/astrodeck/sequence/schedule.py; server/astrodeck/flows/tonight.py; ui/src/components/flows/nodeDefs.ts; server/tests/_flow_night.py | L | none |
| WP-10 | Cooling restore tells the truth and leaves daylight alone | #557 (a), #143 (b) | P1 | server/astrodeck/hub.py | L | none |
| WP-12 | Probe tools refuse real directories | #541 (a), #539 (b) | P0 | tools/ui_probe/server_ctl.py; tools/ui_probe/seed_session.py; tools/ui_probe/test_probe_s7.py; server/tests/test_h4_seed_session_allow_list.py | M | D-02 (needed before W1) |

Fix shapes:

- WP-01
  - (a) The calibration frame loop runs the same idle, floor and flip checks as the light loop (at least `_idle_hold_tick`), so a mount still tracking the last light target is covered for the whole block.
  - (b) The focuser read that only fills in a log number catches its own timeout, uses a short query budget instead of the 180 s move budget, and never lets SafetyAbort escape.
  - (c) The recovery bound resets only on a banked frame whose guiding was confirmed. A trailed, unguided frame does not reset it.
  - The three items stay in one WP because engine.py has one owner per wave. It is labelled L+, and the orchestrator may run it as two sequential dispatches in the same worktree: (a) first, then (b) and (c).
- WP-02
  - (a) serial_link turns OSError and SerialException on write or read into LinkError and abandons the handle, so `is_open` becomes False and the AM5's `connected` follows. After N consecutive park failures dawn_park reopens the link, instead of only when `connected` is False.
  - (b) dawn_park's next line reports the failed park-state read instead of stating "the mount is unparked".
  - Rig check after deploy: in daylight with the mount parked, unplug and replug the mount's USB.
- WP-03: An unreadable-mount hold repeats at warning and then error, with a repetition count, like `dawn_park._fail`.
- WP-04
  - (a) The outbox is ordered by severity, each sink sends from its own task, and evictions are counted and reported.
  - (b) `deadman_ping` and `emit_heartbeat` enqueue and return at once. engine.py is not edited. The test drives `_frame_alerts_tick` against a sink that never answers and requires it to return within 100 ms.
- WP-05a: The rig server passes uvicorn a path-only log_config, using formatters in the new logfmt.py that mirror the relay's. Nothing is imported from relay/.
- WP-05b: The guard test (test_rig_precheck_site_is_redacted.py) runs rig_precheck's mount block against a fake mount parked at its home position and asserts no alt/az text appears. A static scan checks that nothing under scripts/ or tools/ prints mount altitude or azimuth fields.
- WP-06
  - For a principal without view.site_derived, `_redact_report_for` and frames.csv drop per-frame altitude_deg.
  - For the same principal, `exposed_at` is nulled on every sky_angles row (or sky_angles is dropped). Mosaic meridian-wait hop rows are timed at a crossing too, not only flip re-slews, so the rule covers every row.
  - The same redaction covers the bundle routes report_bundle, report_bundle_zip and report_bundle_materialize, including their weight_altitude option, which folds altitude into the sub weights. For such a principal the routes either refuse weight_altitude or strip what it produces; the return says which.
  - A named mutant skips the strip.
- WP-07
  - (a) The rise branch of set_aside_expiry gets a time floor that every latitude can reach, so when it fires reveals nothing about latitude. The branch may be dropped instead. Compute the floor from the geometry, not by guessing.
  - (b) The guide_start pass reason mentions any centring misses counted or set aside in the same pass. test_group_floor_stop_counted.py and test_group_rotation.py record that text and follow it.
  - (c) A test reads reject_visits after expiry, which turns mutant G2 red.
- WP-08
  - (a) All four surfaces act only once the open has landed on the requested id (the openFlowById pattern) and show a waiting card until then.
  - (b) flowsOpen clears libraryError when an open starts, or flowOpenFailure compares a per-open token instead of error text.
  - (c) Add the plain-404 case.
- WP-09
  - (a) Compile reads DUSK start (astro, nautical, civil, clock) and stop (dawn, clock, none) into start_mode and stop_mode, with clock-time fields. A stop of "none" gets a compile warning. Tonight shows the compiled window.
  - (b) `_num` reads "10.0", "1e1" and "-30.0" as numbers.
  - (c) gating_status and constraint_gate go through site_gate.site_lat_lon and report "no site" instead of computing against 0,0.
- WP-10
  - (a) restore_cooling only runs in darkness or when a run window is armed or upcoming, using the same daylight judgement as the dawn-warm path. In daylight it logs that it left the cooler off.
  - (b) It reads back cooler state and setpoint before it logs "restored".
  - The teardown epoch fence that used to be item (c) is WP-66 in wave 9, the first wave after this one where hub.py has no owner.
  - This WP is in wave 1 because 0.3.38 is a daytime deploy. Rig check: after the daytime start, the camera stays warm.
- WP-12: Build what D-02 rules. If the ruling keeps the static guard, (b) closes with that explanation and needs no code. New cases go in the two listed test files or a new `test_w1_` file. The M label assumes the recommended ruling; if D-02 asks for a live port check in `seed_session.py`, treat the WP as L.

## Wave 2

Harness owners: `_group_harness.py` goes to WP-11 and `_flow_night.py` to WP-19.

| WP | Title | Issues | Sev | Owned files | Effort | Depends on |
|---|---|---|---|---|---|---|
| WP-11 | Site timing out of viewer state and logs (Privacy) | #166 (a), #302 (b) | P0 | server/astrodeck/sequence/engine.py; server/astrodeck/hub.py; server/astrodeck/api/redact.py; server/astrodeck/alerting.py; server/tests/test_s7_sim_meridian_straddle.py; server/tests/_group_harness.py | L | WP-01, WP-04, WP-06, WP-10. Blocker for (a): the one-owner engine.py lane (WP-01 holds engine.py in wave 1) |
| WP-13 | Relay reliability and deploy identity | #597 (a), #556 (b), #486 (c) | P2 (mosaic-night priority) | relay/requirements.txt; relay/Dockerfile; relay/relay/server.py; relay/tests/test_h4_access_log_path_only.py; .github/workflows/deploy-relay.yml; relay/README.md; docs/relay-deploy.md | L | orchestrator pre-step |
| WP-14 | ResumeArm: messages, hold, rotation, dead branch | #139 (a), #261 (b), #295 (c), #543 (d) | P2 | server/astrodeck/sequence/resume_arm.py | L | none |
| WP-15 | Guider reuse path proves a mount command before claiming | #135 | P1 | server/astrodeck/guide/native.py | M | none |
| WP-16 | Flow run controls and editor exits | #162 (a), #500 (b) | P1 | ui/src/components/flows/flowsSlice.ts; ui/src/components/flows/flowRunControls.tsx; ui/src/next/hubs/sky/sheets/flow.tsx | L | WP-08 |
| WP-17 | Monitor, banners and incident cards | #259 (a), #258 (b), #260 (c) | P1 | ui/src/views/MonitorView.tsx; ui/src/App.tsx; ui/src/next/shell/Banners.tsx; ui/src/next/hubs/monitor/live/RecoveryCards.tsx; ui/src/next/hubs/session/now/NowEmpty.tsx; ui/src/next/hubs/session/now/incidentActions.ts; ui/src/next/lib/incidents.ts | L | none |
| WP-18 | AM5 park cannot lose its command or interleave with a pulse | #342 | P1 | server/astrodeck/devices/backends/zwo_am5.py | M | WP-02 |
| WP-19 | Flow compile: lanes by wires, one quota definition, classic count mode | #151 (a), #155 (b), #141 (c) | P1 | server/astrodeck/flows/compile.py; server/astrodeck/flows/to_plan.py; server/astrodeck/flows/tonight.py; server/astrodeck/plans.py; server/astrodeck/sequence/models.py; server/tests/_flow_night.py | L | WP-09 |
| WP-20 | Slew pad can recover a reset mount | #144 | P1 | ui/src/lib/slewController.ts; server/astrodeck/api/app.py; server/astrodeck/mount_offset.py | L | WP-06 |
| WP-60 | Cross-hub banner dismissal is keyed on the incident, not its kind | #268 | P2 | ui/src/next/hubs/session/crossHub.ts | S | none |

Fix shapes:

- WP-11
  - (a) For a principal without view.site_derived, `_redact_sequence_for` removes live.meridian_eta_s and the meridian timing in detail and schedule. The three flip log lines carry site_derived, and alerting holds site_derived lines back from sinks. test_s7_sim_meridian_straddle compares `live` again.
  - (b) While its group waits on the meridian rule, a follower's hop line and its state publishes are flagged site_derived. The after_group reason tells "set aside tonight" apart from "out of window tonight".
- WP-13
  - Orchestrator pre-step (read-only, through flyctl): record the package versions running in the v9 machine and, if they can be recovered, in the v8 image.
  - (a) Pin every relay requirement to an exact version, going back to the v8 versions of uvicorn, websockets and starlette where those differ. A new `test_w2_` test fails on any unpinned line. Set the websocket ping keywords (ws_ping_interval, ws_ping_timeout) explicitly in `uvicorn_options` in relay/relay/server.py. The Dockerfile's CMD is `python -m relay`, so they cannot go on a command line. test_h4_access_log_path_only.py runs the server through `uvicorn_options` and pins them.
  - (b) The fixture restores the relay logger.
  - (c) The CI workflow passes the same identity build args as scripts/deploy_relay.ps1 and checks /healthz identity after it deploys; the alternative is to disable it with a note. Both READMEs point at deploy_relay.ps1.
  - Warning: the deploy-relay workflow is active and runs on any push to relay/** on main. Land (c) in the same commit as any relay/** change, and push only with the owner's say-so.
- WP-14
  - (a) The not-armed message names the session by id and origin as well as by name.
  - (b) Starting a ladder clears the previous hold.
  - (c) commanded_rotation checks the lock against a connected rotator, as the engine does.
  - (d) Remove the unreachable except branch and its docstring line, or re-pin a test that reaches it. The return says which.
- WP-15: Before announcing "calibrated and guiding" and persisting, the reuse path must see one mount read and one pulse succeed. If either fails, it falls back to a fresh calibration and logs a warning.
- WP-16
  - (a) The classic STOP and the Sky flow card compare run identity, the pattern of the Send-to-Wizard fix, so STOP never posts an abort to a run this flow did not start.
  - (b) flowsCloseEditor keeps the graph when its save fails. flowsOpen waits for an in-flight save that carries the on-screen graph and keeps the edit if that save fails, along with its error text.
- WP-17
  - (a) runActive includes holding.
  - (b) hold.site_detail is shown to entitled principals on the classic header, Monitor, the #/next banner, the recovery cards and Now.
  - (c) The ignore-weather action says it lifts only the forecast veto, and it is not the primary action on a cloud-hold card.
- WP-18: Send `:Td#` every time before `:hP#`, and make park and pulse share one lock. Rig check after deploy: park in daylight while a guide pulse is in flight.
- WP-19
  - (a) Extend the wire-scoped owner_of/panel_lane rule from mosaics to ordinary multi-target flows.
  - (b) The POOL quota either drives the engine's stop, or Tonight's done verdict reads the same step counts, so there is one definition.
  - (c) Classic Plan-tab plans default to count_mode "accepted".
- WP-20: When the mount's position is unknown or it has just been reset, nudge refuses or offers a rate move instead of a goto. SLEW_RATES gains a rung at the driver's max_rate_deg_s.
- WP-60: Banner dismissal is keyed on the incident's identity, not its kind alone, so a fresh cloud hold after midnight, or a deferred hold, banners again after an earlier one was dismissed.

## Wave 3

Harness owner: `_group_harness.py` goes to WP-21.

WP-21 and WP-22 are paired. WP-22 (b) splits the `solve_transient` key in hub.py that WP-21 reads. WP-21 reads the new centring key with a fallback to the old one, so it builds and passes on its own, but the orchestrator merges WP-22 first and WP-21 after it, with a full server suite run between them.

| WP | Title | Issues | Sev | Owned files | Effort | Depends on |
|---|---|---|---|---|---|---|
| WP-21 | Mosaic hold escalation | #563 (a), #576 (b) | P0 | server/astrodeck/sequence/engine.py; server/astrodeck/sequence/group_rules.py; server/tests/_group_harness.py | L | WP-04, WP-07, WP-11, D-03. Blockers: the one-owner engine.py lane (WP-11 holds engine.py in wave 2) and D-03 |
| WP-22 | Pre-flight reads the obstruction horizon; split solve-transient keys | #132 (a) | P1 | server/astrodeck/api/app.py; server/astrodeck/hub.py | L | WP-20, WP-11 |
| WP-23 | UI types mirror what the engine publishes | #578 (a), #552 (b) | P3 | ui/src/types.ts; server/tests/test_types_mirror_groups.py | M | none |
| WP-24a | Sky hub: FRAME refusal and ranked-list window | #568 (a), #551 (b) | P2 | ui/src/next/hubs/sky/SkyHub.tsx; ui/src/next/hubs/sky/finder/model.ts; tools/ui_probe/routes_s5_s6.json | L | none |
| WP-24b | Atlas canvas: zoom limit and overlay boxes | #182 (a), #491 (b) | P2 | ui/src/components/atlas/SkyCanvas.tsx | L | none |
| WP-25 | #/next copy that states what is true | #279 (a), #290 (b), #430 (c) | P2 | ui/src/next/hubs/session/gallery/cardActions.ts; ui/src/next/hubs/session/gallery/__tests__/galleryUnreadableDom.test.tsx; ui/src/lib/standards.ts; server/tests/test_set_aside_promises.py; ui/src/next/hubs/session/now/useCampaign.ts; ui/src/next/hubs/session/now/sessionData.ts | M | none |
| WP-26 | Wizard number parsing | #501 (a), #546 (b) | P2 | server/astrodeck/flows/wizard.py | M | none |
| WP-27 | Test isolation of module singletons | #443 (a), #522 (b), #587 (c) | P3 | server/tests/conftest.py; server/tests/test_wcs_stamp.py; server/tests/test_h4_quick_unguided_line.py; server/tests/test_h4_dusk_offset_whole_minutes.py; server/tests/test_h4_no_test_probes_the_real_network.py | L | none |
| WP-28 | Report persistence and relay drop count | #579 (a), #521 (b) | P2 | server/astrodeck/sequence/report.py; server/astrodeck/persist.py; server/astrodeck/remote/relay_client.py; server/tests/test_s7_sim_continue_second_night.py | L | none |
| WP-29 | One field of view for both UIs and the server; framing doc | #168 (a), #161 (b) | P2 | ui/src/lib/framing.ts; ui/src/next/lib/fov.ts; server/astrodeck/config.py; server/astrodeck/catalog/framing.py; docs/superpowers/specs/2026-06-15-ux-sky-atlas-framing-design.md; ui/src/views/AtlasView.tsx | L | none |
| WP-30a | Solve path: newest angle wins, polar frame | #292 (a), #532 (b) | P2 | server/astrodeck/sky_angle.py; server/astrodeck/polar/native.py | L | none |
| WP-30b | No-light verdict with several calibration masters (tests only) | #274 | P3 | server/tests/test_failed_solve_says_no_light.py | M | none |
| WP-61 | Sky flow card loading copy | #592 | P3 | ui/src/next/hubs/sky/sheets/flow.tsx | S | WP-16 |

Fix shapes:

- WP-21: Build what D-03 rules for (a) and (b). The counter covers both hold kinds. The alert goes through the WP-04 dispatcher. `_group_hop_checks` reads `centring_solve_transient` from goto_and_center's result, falling back to `solve_transient` when the new key is absent (WP-22 (b) adds it).
- WP-22
  - (a) hub._check_horizon reads cfg.safety.horizon, and `_preflight_alt` and `_horizon_block` go through it. `_start_preflight` gives single targets the same verdict as the engine's `_mount_floor_verdict`. The UI's Horizon light agrees with it.
  - (b) For WP-21: goto_and_center's single `solve_transient` key is split into `rotation_solve_transient` and `centring_solve_transient`. `solve_transient` stays, as the union of the two, for compatibility. A test sets each key by its own failure and checks the union.
  - Out of scope: the issue also asks whether a below-horizon destination met mid-run should be StopTarget rather than SafetyAbort. That is an engine.py change, and WP-21 owns engine.py in this wave, so it is filed as a new issue (see the list at the end) and not built here.
- WP-23
  - (a) SetAsideRecord declares kind, ts and expired, and the mirror test builds records through the engine's own path.
  - (b) start_ts and stop_ts are typed `number | null`.
- WP-24a
  - (a) FRAME's no-site refusal takes its sentence from finder/model.ts, and the probe walks get past that step.
  - (b) The ranked-list window is gated on dawnKnown.
- WP-24b
  - (a) ZOOM_MAX is fitted to the grid's extent, with a notice when the survey is too coarse at that scale.
  - (b) The reserved overlay boxes use the toggle's measured footprint and include the legend.
- WP-25
  - (a) The delete confirm and toast read backup_kept and say what is kept.
  - (b) The hint says the step is "set aside for tonight, retried next night", and standards.ts is added to the promises scan.
  - (c) With no list row, show no night number rather than the run count, and fix the sessionData.ts comment.
- WP-26: Both readers catch OverflowError and oversized digit runs and raise the house-style ValueError naming the row. The route is unchanged.
- WP-27
  - (a) An autouse active-provider reset.
  - (b) An autouse unshadow of session_store's instance dict.
  - (c) The three fixtures use isolated_config.
  - Each guard gets a named mutant that removes it, plus a seeded order-dependent failure. The fourth isolation item that used to sit here is WP-62 in wave 4.
- WP-28
  - (a) A failed non-final snapshot write schedules a bounded retry. The failure is intermittent, so the proof is a forced write failure, not a rerun. test_s7_sim_continue_second_night.py, the test it was seen in, is owned so its expectations can follow.
  - (b) The session report counts relay tunnel drops per hour from the link-check events relay_client logs.
- WP-29
  - (a) One FOV function serves both UIs and follows config.py's documented reducer semantics. The server's `fov_deg` (catalog/framing.py, reading config.py) applies the reducer the same way. A test pins both UIs and the server to the same FOV for one config. Feeding in the solve-measured scale is filed as a new feature issue.
  - (b) Correct the spec line and the AtlasView comment: a centred rectangle turned 180 degrees covers the same footprint.
- WP-30a
  - (a) note_solved_rotation keeps the record with the newest exposed_at.
  - (b) The polar solve writes a unique frame through `_write_solve_frame` and `_retire_solve_frame`, with the same retry.
- WP-30b: Add cases with several bias and dark masters at different temperatures and a widened tolerance, so three named mutants on solve/light.py go red. light.py itself is not edited.
- WP-61: The loading copy either says something the empty card does not already show, or there is no copy.

## Wave 4

Harness owners: `_group_harness.py` goes to WP-33, `_flow_night.py` to WP-34, and `conftest.py` to WP-62.

| WP | Title | Issues | Sev | Owned files | Effort | Depends on |
|---|---|---|---|---|---|---|
| WP-31 | Run start: visible disarms, setup waits for the target | #595 (a), #596 (b) | P0 | server/astrodeck/sequence/engine.py; server/astrodeck/api/app.py; server/astrodeck/flows/tonight.py | L | WP-21, WP-22, D-04 (for a). Blockers for (a): the one-owner engine.py lane (WP-21 holds engine.py in wave 3) and D-04 |
| WP-32a | Rotator approach: motion epoch per leg; tie cases; the learned sky/mechanical sign (R-4) | #574 (a), #584 (b), R-4 (c) | P1 | server/astrodeck/hub.py; server/astrodeck/rotation.py; server/tests/test_h4_rotator_backlash.py; new server/tests/test_w4_rotator_sign.py | L | WP-22 |
| WP-33 | Last live panel is held, not charged | #591 | P1 | server/astrodeck/sequence/group_rules.py; server/tests/_group_harness.py | L | WP-21, D-03 |
| WP-34 | DUSK single night; one report per mosaic | #195 (a), #184 (b) | P1 | server/astrodeck/flows/compile.py; server/astrodeck/flows/nodes.py; server/astrodeck/flows/to_plan.py; server/astrodeck/sequence/models.py; server/astrodeck/flows/doctor.py; server/astrodeck/sequence/resume_arm.py; ui/src/components/flows/nodeDefs.ts; server/tests/_flow_night.py | L | WP-09, WP-14, WP-19 |
| WP-35 | Cancel-safe task awaits, part A (non-hot files) | #252 | P2 | server/astrodeck/aio.py; server/astrodeck/focus/autofocus.py; server/astrodeck/remote/relay_client.py; server/astrodeck/alerting.py; server/astrodeck/dawn_park.py; server/astrodeck/dew.py; server/astrodeck/dusk_arm.py; server/astrodeck/gallery.py; server/astrodeck/guide/phd2.py; server/astrodeck/polar/session.py; server/astrodeck/sun_watch.py; server/astrodeck/sync/runner.py; server/astrodeck/cloudmap/service.py; server/astrodeck/catalog/ephemeris/elements.py; server/tests/test_no_task_await_eats_its_callers_cancel.py | L | WP-02, WP-03, WP-04, WP-28 |
| WP-36 | Tap wiring gets the drag rules; stale comments | #197 (a), #397 (b), #593 (c) | P2 | ui/src/components/flows/flowsSlice.ts; ui/src/components/flows/FlowCanvas.tsx; ui/src/components/flows/flowLoop.ts; ui/src/components/flows/FlowPhoneGraph.tsx; ui/src/next/hubs/session/flows/canvas/FlowStagesPhoneSheet.tsx; ui/src/next/hubs/session/flows/canvas/FlowCanvasSurface.tsx; ui/src/store.ts; docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-design.md | L | WP-08, WP-16 |
| WP-62 | Pier-side test reaches the config singleton everywhere | #497 | P3 | server/tests/conftest.py; server/tests/test_pier_side_is_published.py | M | WP-27 |

Fix shapes:

- WP-31
  - (a) Build what D-04 rules on the server: the warning line and the `disarmed` list in the start response. The UI half is WP-65 in wave 5.
  - (b) A single target's one-time setup waits for that target's own window, taken from the Tonight route's per-target window (flows/tonight.py). Before autofocus and guider calibration it confirms there is light (a star count or a solve), holding the way the group centring hold does, so setup never runs on a dark or obstructed field. Once the hold clears, it re-runs whatever setup step failed (autofocus, guider calibration) before the first light frame.
  - The teardown 409 wording that used to be item (c) is WP-67 in wave 9, after WP-39 makes the UIs show those details.
- WP-32a
  - (a) `_approach_rotator` checks the motion epoch before each leg.
  - (b) Add zero-travel and exact-180-degree tie cases.
  - (c) R-4's learned sign. The rotator calibration learns and stores the sign of d(sky PA)/d(mechanical) from two solves around a small known move. Measured on the rig: -1 on pier west. map_sky_target and the rotate loop apply it, instead of assuming `sky = mech + offset`. Until the sign has been learned, a rotation is refused with a line saying why; guessing the sign could turn the camera the wrong way. Simulator tests cover both signs. The named mutant is "sign forced to +1".
  - Needs no ruling and no rig. The D-05 behaviour is WP-32b in wave 5.
- WP-33: Build what D-03 rules.
- WP-34
  - (a) "Single night" compiles to a session that auto-resume does not arm across nights, which takes a model field, a ResumeArm check and help text on the node field.
  - (b) REPORT "target done" on a rotating mosaic fires once per mosaic, through a mosaic-complete trigger or a gate that waits for the group. Doctor M14 covers a REPORT placed in another lane.
- WP-35: An aio helper cancels and awaits a task without swallowing the caller's cancel. Convert every site outside engine.py, app.py and resume_arm.py. The guard test learns both the `suppress(BaseException)` spelling and the try/except CancelledError spelling. The hot-file sites that remain are allowlisted by name until WP-59 converts them.
- WP-36
  - (a) flowsConnect runs the lane and self-wire checks for both drag and tap, and the self-wire toast exists.
  - (b) The comment matches the compile's consumed-wire behaviour.
  - (c) Correct the three "flow.node tick" comments and add the Revision 11 row.
- WP-62: A sys.modules sweep in conftest.py reaches every module that bound config_store, and test_pier_side_is_published's `_am5` helper patches through it instead of a single-module attribute. A named mutant removes the sweep, and a seeded order-dependent failure turns red without it. The failure was intermittent (one parallel run), so the proof is the seeded order, not a rerun. config.py is not edited.

## Wave 5

Harness owner: `_group_harness.py` goes to WP-40 (it is one of the `_Clocked` files).

WP-32b needs D-05. If D-05 has no ruling when the wave starts, WP-32b waits for the first later wave where hub.py and rotation.py are both free (wave 10 at the earliest), and WP-45 and WP-53 go ahead without it.

| WP | Title | Issues | Sev | Owned files | Effort | Depends on |
|---|---|---|---|---|---|---|
| WP-32b | Rotator software trust: refusing follow-check and nightly self-test | #594 | P0 | server/astrodeck/hub.py; server/astrodeck/rotation.py | L | WP-32a, D-05. Blockers: the hub.py chain (WP-10, WP-11, WP-22, WP-32a hold hub.py in waves 1-4), D-05, and the coupling fix, which rig validation waits for |
| WP-37 | Frame gate: cloud source, unguided frames, flip-flat order | #193 (a), #142 (b), #194 (c) | P1 | server/astrodeck/sequence/engine.py; server/astrodeck/config.py; server/astrodeck/weather.py; new server/tests/test_w5_flat_panel_cover_order.py | L | WP-31, D-06 (for b) |
| WP-38 | Session store: locked edits, honest migrated frames | #167 (a), #265 (b) | P1 | server/astrodeck/api/app.py; server/astrodeck/sequence/session.py; server/astrodeck/sequence/resume_arm.py | L | WP-31, WP-34 |
| WP-39 | Connect and profile UIs show the server's reason | #256 | P2 | ui/src/next/hubs/rig/devices/rigConnect.ts; ui/src/views/EquipmentView.tsx; ui/src/components/settings/ProfileList.tsx; ui/src/next/hubs/rig/profiles/profilesModel.ts | M | none |
| WP-40 | Intermittent tests: clocks and diagnostics | #271 (a), #299 (b) | P3 | server/tests/test_native_guider_idle_preview.py; server/tests/test_engine_logs_carry_no_site_numbers.py; server/tests/_group_harness.py; the 13 `_Clocked` test files without a monotonic patch (test_waits_that_watch_the_mount, test_h4_pause_closes_the_idle_latch, test_s7_waits_read_the_weather, test_h4_monitor_to_read_needs_safety_check, test_safe_again_after_cooler_gate, test_refused_close_keeps_idle_stop, test_reopen_close_guider_bound, test_run_end_completes_the_idle_stop, test_cooling_wait_watches_the_mount, test_idle_park_hold, test_cloud_hold_watch, test_one_setup_per_acquisition, test_idle_stop_retry_clock) | L | WP-01 |
| WP-41 | Guider lock published by the Rust engine | #204 | P1 | native/crates/astro-guide/src/engine.rs; native/crates/astrodeck-native/src/lib.rs; server/astrodeck/guide/native.py | L | WP-15 |
| WP-42 | Naming preview has every server token | #278 | P2 | ui/src/lib/naming.ts; ui/src/components/settings/NamingPanel.tsx; ui/src/next/hubs/settings/tuning/files/NamingEditor.tsx | M | none |
| WP-43 | UI tests that can fail; polar NaN guard | #255 (a), #198 (b), #269 (c) | P3 | ui/src/views/__tests__/equipmentConnectClaim.test.tsx; ui/src/next/__tests__/r7Parity.test.ts; ui/src/components/polar.tsx | M | none |
| WP-63 | Intermittent altitude-floor test explains itself | #442 | P3 | server/tests/test_altitude_floor.py | M | none |
| WP-64 | Focus: remove the unreachable before-the-probe arm | #583 | P3 | server/astrodeck/focus/native.py | S | none |
| WP-65 | UI names the sessions a run start disarmed | UI half of WP-31 (a) | P0 | ui/src/next/hubs/session/now/NowEmpty.tsx; ui/src/components/flows/flowRunControls.tsx | M | WP-31, D-04 |

Fix shapes:

- WP-32b: The follow-check refuses and the self-test runs, as D-05 rules. It is gated on D-05 and validated on the rig only after the coupling fix. Until then its tests run against the simulator and the release notes say rotation trust is unvalidated.
- WP-37
  - (a) The frame-verdict cloud fallback engages when the assigned monitor cannot report clouds, not only when no monitor is assigned. The config comment is corrected to match.
  - (b) Build what D-06 rules.
  - (c) On a combined cover and calibrator, close the cover before the panel is switched on.
  - New tests go in new `test_w5_` files, because the clocked files belong to WP-40.
- WP-38
  - (a) SessionStore gets a locked read-modify-write, and both PATCH and ResumeArm's start go through it.
  - (b) Migrated frames are marked synthesized and ADOPT skips the ephemeris test for them, or the legacy file's times are carried over instead.
- WP-39: Show the coded 409 detail. The force-activate confirm says that forcing disarms the recovering session's auto-resume.
- WP-40
  - (a) Pin the clock in the first half of the test.
  - (b) Fake `_Clock.monotonic` in the remaining files, or once in the harness. test_engine_logs_carry_no_site_numbers prints `_where(found, runs)` when it fails (the issue's point 1).
- WP-41: GuideStatsSnapshot publishes the engine's lock position through the astrodeck-native binding, and the different-star guard reads it. The deploy needs the native wheel rebuilt. Rig check on a bright field.
- WP-42: Add GAIN, EXPOSURE, BINNING and SENSORTEMP, and a test that compares the preview's token set with the server's KNOWN_TOKENS.
- WP-43
  - (a) Count every Link Status row for a role, whatever word it prints.
  - (b) The scanner matches `export ... from` and `export *`.
  - (c) Guard against from <= 0 and non-finite results.
- WP-63: The precondition assertion prints end_reason and the last night-log lines, so the next failure explains itself. If that evidence, or a seeded order, shows a cause inside test_altitude_floor.py, fix it under a named mutant. Otherwise the return says only the diagnostic shipped, and the issue stays open and is marked intermittent. A fix that needs another file is reported, not made.
- WP-64: Remove the unreachable n0 == -1 arm in focus.native's `_failed` and its docstring line. The surviving mutant is equivalent, so the evidence is the deleted arm and a green focus suite; the return names the equivalent mutant.
- WP-65: Show the start response's `disarmed` list (from WP-31 (a)) as a warning line naming each disarmed session, on the #/next Now empty state and on the classic flow run controls. If D-04 rules for a queued next session instead, show whatever that ruling puts in the response.

## Wave 6

No harness owner in this wave.

| WP | Title | Issues | Sev | Owned files | Effort | Depends on |
|---|---|---|---|---|---|---|
| WP-44 | Report and log say what happened | #565 (a), #524 (b), #200 (c) | P2 | server/astrodeck/sequence/engine.py; server/astrodeck/api/app.py; server/astrodeck/sequence/report.py; server/astrodeck/alerting.py; server/tests/test_recovery_centring_is_measured.py | L | WP-37, WP-38, WP-28 |
| WP-45 | Solve bookkeeping in the hub | #324 (a), #264 (b) | P2 | server/astrodeck/catalog/coords.py; server/astrodeck/hub.py; server/astrodeck/catalog/region.py; server/astrodeck/catalog/solar_system.py; server/tests/test_failed_solve_says_no_light.py | L | WP-32b, WP-30a, WP-30b |
| WP-46a | Engine mutant gaps (tests only) | #301 (a), #572 (b), #585 (c) | P3 | server/tests/test_group_meridian.py; server/tests/test_group_followers.py; server/tests/test_group_member_after_group.py; server/tests/test_h4_complete_targets_dropped.py | L | none |
| WP-46b | Relay-link and gate-scan mutant gaps (tests only) | #581 (a), #586 (b) | P3 | server/tests/test_h4_relay_link_check.py; server/tests/test_h4_gate_scan_partial_step.py | M | none |
| WP-47 | Camera `connected` is measured | #16 | P1 | server/astrodeck/config.py; server/astrodeck/devices/backends/zwo_asi.py; server/astrodeck/devices/backends/player_one.py; server/astrodeck/devices/backends/ascom_local.py; server/astrodeck/devices/cameras/engine.py; server/astrodeck/devices/alpaca.py | L | WP-37, D-10 |
| WP-48a | #/next card and classic quick-flow copy | #554 (a), #588 (b) | P2 | ui/src/next/hubs/session/flows/canvas/FlowNode.tsx; ui/src/components/flows/QuickFlow.tsx; ui/src/next/hubs/session/flows/create/quick.tsx | L | none |
| WP-48b | Classic flow card footer and never-run copy | #357 (a), #232 (b) | P3 | ui/src/components/flows/targetSummary.ts; ui/src/components/flows/__tests__/cardFooterDom.test.tsx; ui/src/components/flows/FlowLibraryCard.tsx; ui/src/components/flows/__tests__/unreadableFlowCard.test.tsx | M | D-14, D-15 |

Fix shapes:

- WP-44
  - (a) A polite server shutdown finalizes with its own end reason, distinct from an operator STOP, and `_why_dormant` uses it. `_FLOW_RESULT` gets the new word. Every reader of "aborted" is audited: the alerting terminal alert (alerting.py reads end_reason, owned here), the report, and the flow cards. A UI reader that needs a change is listed in the return and filed, not edited; WP-52 in wave 7 already consumes the split for the resumable-run cards.
  - (b) mark_skipped takes a reason and records it, and every call site passes one.
  - (c) The recovery line says "re-centred" only when goto_and_center converged. test_recovery_centring_is_measured.py pins it.
- WP-45
  - (a) angular_sep_deg refuses non-finite input and every caller handles that. The mount-moved staleness check treats non-finite as moved.
  - (b) measure_guide_offset sends a failed solve through light.failed_solve_error, and it comes off NEVER_RAISES.
- WP-46a
  - (a) A case in test_group_meridian.py or test_group_followers.py that turns MERIDIAN_WAKE_PAD_S red.
  - (b, c) Tests that build state directly to reach the two branches no production path reaches. Alternatively, record them as equivalent mutants and propose deleting the branch in a later engine WP; the orchestrator rules which.
  - Mutation runs on engine.py happen only in this WP's worktree.
- WP-46b
  - (a) The gateway-probe composition and the non-Windows ping verdict.
  - (b) A horizon at or before now.
- WP-47: Camera backends derive `connected` from a live query the camera has to answer, so the reconnect gate sees a stranded camera. That includes the remembered flag cameras/engine.py sets and the Alpaca camera client in devices/alpaca.py. The stale "Alpaca-only" comment is corrected. The astrotown profile change is made by the orchestrator at deploy, per D-10. Rig check in daytime.
- WP-48a
  - (a) The three loop-run card states fit CARD_OVERHANG_PX.
  - (b) The classic quick flow shows the unguided sub-limit note, sharing its constant with #/next.
- WP-48b
  - (a) Build what D-15 rules.
  - (b) Build what D-14 rules.

## Wave 7

Harness owners: `_group_harness.py` goes to WP-49 and sim.py to WP-51.

| WP | Title | Issues | Sev | Owned files | Effort | Depends on |
|---|---|---|---|---|---|---|
| WP-49 | Set-aside "for now" marker end to end | #573 (a), #534 (b) | P2 | server/astrodeck/sequence/engine.py; ui/src/types.ts; ui/src/components/flows/flowRunState.ts; ui/src/components/flows/framing/sections/PanelsSection.tsx; server/tests/fixtures/sequence_state_mosaic.json; server/tests/_group_harness.py; ui/src/components/flows/framing/__tests__/framingSections.test.tsx; ui/src/components/flows/framing/__tests__/runMode.test.tsx; ui/src/components/flows/framing/__tests__/panelRowsSetAside.test.ts; ui/src/components/flows/__tests__/flowRunState.test.ts | L | WP-44, WP-23, D-07 |
| WP-50 | Tonight: per-block budget, dashes, honest dome and auto-resume claims; dome close default | #562 (a), #561 (b), #192 (c) | P1 | server/astrodeck/flows/tonight.py; server/astrodeck/api/app.py; server/astrodeck/config.py; ui/src/next/hubs/session/flows/tonight/TonightStoryList.tsx; ui/src/next/hubs/session/flows/tonight/__tests__/tonightDom.test.tsx; ui/src/components/flows/nodeDefs.ts | L | WP-44, WP-34, D-16 (for the default only) |
| WP-51 | Meridian countdown while not tracking | #519 | P2 | server/astrodeck/hub.py; server/astrodeck/devices/sim.py; server/tests/test_s7_sim_meridian_straddle.py | M | WP-45, D-11 |
| WP-52 | Resumable-run card titles | #487 | P2 | ui/src/next/hubs/session/now/Interrupted.tsx; ui/src/next/hubs/session/plan/PlanResume.tsx; ui/src/next/hubs/monitor/live/RecoveryCards.tsx | S | WP-44, WP-17, D-13 |

Fix shapes:

- WP-49: `_group_state` publishes kind and for_now, and so do the types, flowRunState and PanelsSection. Regenerate the recorded S5 fixture and re-pin the four listed UI tests.
- WP-50
  - (a) BUDGET is computed per block from a per-target bank mapping.
  - (b) Story rows pass through plainDashes, the server stops writing em-dashes, and the test uses server-shaped rows that contain an em-dash.
  - (c) Built only for its copy item (item 4): the tooltip and the Tonight lines claim only what the code does. The issue stays open after this WP. D-16's default is built here as well: close_dome_when_done defaults to True when a dome is connected (config.py). The issue's items 1 to 3 are filed as new issues (see the list at the end): opening the roof and cover before the first slew, the dome-to-mount binding (item 2's default half lands here), and DUSK FLATS wiring.
  - Effort: (c) counts as copy plus one default, not as the whole issue, which is why the WP is L.
- WP-51: Build what D-11 rules. While tracking is off, the hub publishes `hours_to_flip` as None and a status of "not tracking", so `_live_block` in engine.py reads it unchanged and engine.py needs no edit. The simulator's RA drifts with the sidereal clock while tracking is off. test_s7_sim_meridian_straddle.py, where the issue was seen, is re-recorded here. Any other sim test that needs new recorded values is reported in the return, not edited.
- WP-52: Build what D-13 rules.

## Wave 8

Harness owner: `_group_harness.py` goes to WP-53.

| WP | Title | Issues | Sev | Owned files | Effort | Depends on |
|---|---|---|---|---|---|---|
| WP-53 | Rotator: non-centred slews say they skipped rotation; manual moves use the one-sided approach | #160 (a), #589 (b) | P1 | server/astrodeck/sequence/engine.py; server/astrodeck/hub.py; server/astrodeck/api/app.py; ui/src/components/equipment/RotatorCard.tsx; ui/src/next/hubs/rig/rotator/RotatorPanel.tsx; server/tests/_group_harness.py | L | WP-32a, WP-32b, WP-49, WP-50, WP-51, D-18 (soft) |
| WP-54 | Event-loop fault is logged and restarts cleanly | #496 | P1 | server/astrodeck/__main__.py | M | WP-05a, D-12 |

Fix shapes:

- WP-53
  - (a) A non-centred slew that was asked to rotate sets the rotation-skipped flag, so the group angle check and PanelDeferred run.
  - (b) Manual Go and the nudges go through `_approach_rotator`.
- WP-54: Build what D-12 rules. Rig check in daylight that the supervisor restarts the server.

## Waves 9 to 13 (the engine lane)

| Wave | WP | Title | Issues | Sev | Owned files | Effort | Depends on |
|---|---|---|---|---|---|---|---|
| 9 | WP-55 | Site-less runs; per-target time budget | #559 (a), #582 (b), #164 (c) | P2 | server/astrodeck/sequence/engine.py; server/astrodeck/sequence/schedule.py; server/tests/test_h4_dusk_unresolved_is_said.py | L+ | WP-53, D-08 |
| 9 | WP-66 | Teardown epoch fence for camera ramps | #281 | P2 | server/astrodeck/hub.py | M | WP-10 |
| 9 | WP-67 | Teardown 409 details point at the Monitor | #272 | P3 | server/astrodeck/api/app.py; docs/superpowers/specs/2026-09-23-flows-mosaic-target-block-design.md; server/tests/test_teardown_routes_stop_the_ladder.py | S | WP-39 |
| 10 | WP-56 | Overhead EMA and flip retry point | #297 (a), #566 (b) | P2 | server/astrodeck/sequence/engine.py; server/astrodeck/devices/base.py; server/astrodeck/devices/nina.py | L | WP-55 |
| 11 | WP-57 | Focus carry-on | #590 (a), #507 (b) | P2 | server/astrodeck/sequence/engine.py; server/astrodeck/focus/native.py; server/astrodeck/sequence/report.py | L | WP-56, D-09 |
| 12 | WP-58 | A zero dither skips its settle; guider settle fields persist | #560 | P2 | server/astrodeck/sequence/engine.py; server/astrodeck/config.py; ui/src/next/hubs/rig/sheets/guider.tsx; server/astrodeck/guide/native.py; server/astrodeck/guide/phd2.py | M | WP-57, WP-47, WP-41, WP-35 |
| 13 | WP-59 | Cancel-safe task awaits, part B (the remainder of WP-35's issue) | continues WP-35 | P2 | server/astrodeck/sequence/engine.py; server/astrodeck/api/app.py; server/astrodeck/sequence/resume_arm.py; server/tests/test_no_task_await_eats_its_callers_cancel.py | M | WP-58, WP-53, WP-38 |

Fix shapes:

- WP-55
  - (a) Build what D-08 rules.
  - (b) Tests with only one sun mode set each, so both clauses are graded.
  - (c) max_run_min is measured from the target's own start, not the run's.
  - Labelled L+. The orchestrator may run it as two sequential dispatches: (a) and (b), then (c).
- WP-66: A teardown epoch fence. warm_camera and cool_camera check the epoch again after their awaited reads, including the wait on `_warm_lock`, and do not start a ramp on a camera that has been disconnected. It sits in wave 9 because hub.py has an owner in every wave from 1 to 8.
- WP-67: The teardown-path 409 details point at the Monitor, matching `_RESUME_RECOVERING`, now that WP-39 makes both UIs show the coded detail. test_teardown_routes_stop_the_ladder.py records the quoted texts again. The spec line is corrected to match.
- WP-56
  - (a) The first frame after setup is left out of the overhead EMA.
  - (b) Add a tracking-limit setting per mount or profile. With no limit known and zero lead, aim flip attempts before the meridian instead of after it.
- WP-57
  - (a) Build what D-09 rules.
  - (b) The carry-on is recorded in the session report as well as the night log. The other residuals of this issue are covered by WP-01 (b), WP-64 and R-7.
- WP-58: A dither distance of 0 skips both the dither and its settle. The settle fields persist in GuideConfig and reach both guider backends.
- WP-59: Convert the cancel sites in engine.py (including engine.abort and the shielded flip-wait finally), app.py and resume_arm.py, and empty the guard's allowlist.

## Close without code (orchestrator verifies, then closes with the evidence)

| Issue | Evidence to cite |
|---|---|
| #159 | resume_arm.py recentre_candidates filters and orders the targets and tries each reachable one; the fix is complete. |
| #169 | All three stale claims are fixed on HEAD's ancestry (doctor.py header, IA map line, contract amendment). |
| #276 | skyCards.test.ts asserts OVERLAP === DEFAULT_OVERLAP from lib/framing.ts; closed by the S56 re-pin. |
| #389 | All three S4-COMPILE re-verify findings are fixed and committed at HEAD. |
| #418 | deploy_0337.ps1 routes every native call through Invoke-DeployNative, and test_deploy_common_ps1.py covers the case. |
| #490 | engine.py takes up an owed-but-complete target before it asks its gating; test_s7_consumed_jump_last_frame.py pins it. The narrower gap is tracked in a closed-scope issue. |

Three other homes close still-valid issues without a WP, each on a stated condition: D-01 (the isolation is enforced and recorded), D-17 (the owner approves the ruling) and D-19 (the URL is configured and the two remaining gaps are filed).

## Verify first

No issue was classified unclear. This one is listed because its named failure is fixed and all that remains is a residual the owner deliberately parked.

| Issue | Check | Then |
|---|---|---|
| #289 | Run test_session_thumbs.py under -n 8 ten times. | If it is green, close it, and file the reap-by-poll wind-down latency (up to 0.25 s) as its own P3 issue if it is not already filed. |

## Rig-gated (the next step is rig or device work, not code)

| R | Issue | What must happen on the rig or device | Then |
|---|---|---|---|
| R-1 | #14 | On a guided night, check the durable night log for any Dec demand over 1000 ms that is not a dither. | If there are any, open a WP in zwo_am5.py (after WP-18) that splits the correction into consecutive pulses. If there are none, close it. |
| R-2 | #22 | Set the fan power, then read the Poseidon status block's fan_power back on the rig. | Close if they match. |
| R-3 | #90 | Run one photosphere scan on the target phone and paste the preview and readback numbers. This needs the phone, not the rig. | Close with the numbers. |
| R-4 | #145 | The sign is already measured: on 2026-09-29, 15 small steps gave solved PA = -1 x mechanical on pier west (numbers on the issue). Still owed: one pier-east pair after the coupling fix, to confirm that a flip does not change the sign. | Build the learned-sign fix now, as part (c) of WP-32a in wave 4. WP-32a owns hub.py and rotation.py and needs no ruling: learn the sign from two solves around a small known move, store it with the rotator calibration, and apply it in map_sky_target and the rotate loop. sky_angle.py changes, if any, go to the first free wave after WP-30a. The pier-east check closes it. This gates S8 and the backlog items for per-panel convergence, angle-keyed flats and CSV exchange. |
| R-5 | #308 | Record moonless-overcast frames on the rig with a dark master, and add them as a fixture. | A WP that validates the no-light verdict backed by a dark master. This is the one P0 still waiting on the rig. |
| R-6 | #526 | Re-measure reversal backlash after the coupling fix, then take D-18. | Set ROTATOR_BACKLASH_DEG. WP-53 picks up the value. |
| R-7 | #558 | On one clear night, log light-frame star counts beside the focus-probe counts. | Derive the re-sweep threshold scale, then open a small engine WP after WP-57. |

Rig validation is also owed after the deploys that carry WP-02 (USB replug), WP-10 (camera stays warm on a daytime start), WP-18 (park during a pulse), WP-32b (after the coupling fix), WP-41 (bright field), WP-47 (stranded-camera reconnect) and WP-54 (supervisor restart). The orchestrator or the owner does these, never a coder.

## Owner-decision-only

These four issues get no code. Their homes are the owner-decisions table: D-01 (process), D-17, D-19 and D-20.

## Backlog features (ordered; they fill free slots in waves 8-13)

| Order | Issue | Why it is here and what gates it |
|---|---|---|
| 1 | #196 | Send to Flow Wizard needs its stop and auto-resume steps. Starts after WP-34 lands the single-night field. |
| 2 | #180 | Policy for a panel set aside night after night. Builds on the D-03 escalation once WP-21 and WP-33 exist. |
| 3 | #183 | POOL "best of several" on the engine. The Best-of-four and Campaign example flows cannot run until this exists. |
| 4 | #464 | The engine should publish the in-flight stage so the card LEDs and wires show a run. Deferred by S7 ruling 10. Needs engine lane capacity. |
| 5 | #185 | Autofocus policy for mosaics (when, and on which panel). Needs a design. Promote it if a sweep on a star-poor panel costs a visit on a rig night. |
| 6 | #179 | Reopen a complete session when its flow now owes more (I-30). |
| 7 | #280 | List, restore and clean up kept session .bak files through the API. Promote it the first time a recovery needs a shell on the rig. |
| 8 | #187 | "Measure, then lay out" mosaic angle from the night's first solve. |
| 9 | #188 | Mosaic grouping in Gallery, Session Review and stacking bundles. |
| 10 | #181 | Catalogue major and minor axes and position angle for object ellipses. |
| 11 | #172 | A live stack per panel that survives panel revisits. |
| 12 | #177 | Check a saved light's WCS against its panel footprint. |
| 13 | #186 | Per-panel exposure overrides. |
| 14 | #175 | Per-panel angle correction for north convergence. Blocked on R-4. |
| 15 | #176 | Flats keyed by rotator angle. Only matters after item 14. |
| 16 | #178 | Telescopius CSV import and export. Blocked on R-4 for the angle convention. |
| 17 | #516 | Memoize accepted-count ledger walks. A cost item; the owner deferred it on purpose, and the issue gives the shape and the stale-count caveat. |

## Release checkpoints

| Release | Contents | Notes |
|---|---|---|
| 0.3.38 | H4 + the hotfix port (already committed) + wave 1 | Daytime deploy with a per-version script on deploy_common.ps1. Restart the supervisor, not just the child, because the supervisor reads `current` once at startup. Rebuild the web UI. Why ship after wave 1 alone: H4 has waited since the first rig mosaic, and wave 1 closes most of the P0 hazards. Rig checks: WP-10, WP-02, and a needles scan of the server's stdout log (file names only) for WP-05a. The probe-tool fixes (WP-12) are not deployed to the rig. |
| Relay v10 | H4-RELAY (tunnel-end line, relay logger fix) + WP-13 | Deploy through scripts/deploy_relay.ps1 after wave 2, independent of the rig version. For 24 h, watch the tunnel-end lines to learn which side closes the hourly drop and whether the pins cure it. Never let the CI workflow do a bare deploy (WP-13 (c)). |
| 0.3.39 | Waves 2 and 3 | Site timing redaction, ResumeArm fixes, guider reuse, flow run and Monitor UI, AM5 park lock, compile lanes, slew pad, mosaic hold escalation, pre-flight horizon and the split solve-transient keys, solve path, report retry and drop count, one FOV for both UIs and the server. This is the build for the supervised S7 mosaic night, at a fixed angle if the rotator is still loose. |
| 0.3.40 | Waves 4 and 5 | Run-start visibility (server in wave 4, UI in wave 5) and setup gating, rotator approach fixes (WP-32a), rotator trust (WP-32b; its validation waits for the coupling), last-panel hold, DUSK single night, cancel-safe part A, frame gate, session lock, Rust guider lock. The deploy must rebuild the native wheel for WP-41. |
| 0.3.41 | Waves 6 to 8 | Report and log truth, solve bookkeeping, camera liveness (astrotown profile change per D-10), set-aside for-now marker, Tonight fixes and the dome close default (D-16), meridian display, rotator manual moves, event-loop fault handling. |
| 0.3.42 | Waves 9 to 13 | The rest of the engine lane, the teardown epoch fence (WP-66) and the teardown 409 wording (WP-67). |

Every release: the orchestrator updates the mosaic tracking issue's status, and pushing upstream needs the owner's explicit OK.

## New issues the orchestrator should file as this plan runs

- The measured-plate-scale feed into mosaic tiling (split from WP-29).
- A queued "next" armed session (D-04).
- A learned horizon mask, and a run-mode "retry set-aside panels" action (D-07).
- The three unbuilt items of WP-50 (c)'s issue, which stays open until they land:
  - Open the roof and dust cover before the first slew, at run start as well as in ResumeArm (D-16; build only for a rig with an actuated cover).
  - Bind the dome to the mount through `DomePolicy.apply_binding`. The close_dome_when_done default from the same item ships in WP-50.
  - Wire the DUSK FLATS stage into the engine.
- From WP-22's issue: should a below-horizon destination met mid-run end the target (StopTarget) rather than abort the run (SafetyAbort)? This is an engine.py change, out of scope for WP-22.
- From D-19's issue, before it closes: remote power for the rig PC (a network-switched outlet), and off-box evidence of an outage (a record that survives the rig PC going dark).
- The reap-by-poll wind-down latency (from the verify-first item, if it is not already filed).
- Whatever defects coders and verifiers report in their returns.
