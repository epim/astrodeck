# AstroDeck Loop 2 field review — Codex

Reviewed the real app at `http://127.0.0.1:8802` on 2026-07-16/17 using the simulator rig. This is a review-only pass. Evidence is in `review-evidence-3-codex/`.

## Executive summary

The fix wave materially improved first-light and night-operation trust: simulator connection is coherent, paused-sequence camera ownership is explicit, invalid exposures are blocked with actionable text, autofocus survives in-app navigation and its plotted minimum agrees with the reported position, Atlas has useful zero states, site verbs are much clearer, export feedback is visible, night mode remains legible at 100% brightness, and weather chart labels now explain their threshold.

One new Major truth defect remains: a finished session can simultaneously show **COMPLETE**, **10/10**, all target rows done, and **CAPTURE STALLED?** for minutes. At 2 a.m. that makes the operator distrust the entire recovery model.

The requested disposable-account role pass could not be completed on this deployment. Local accounts could be enabled and disposable users created, but loopback clients remained trusted local admin and the server was not reachable on its LAN address, so there was no clean way to authenticate the operator in a separate client. A direct `/login` route also fell into a disconnected viewer shell rather than a usable sign-in flow. The app was returned to its original open-access mode; the disposable operator was disabled. Because the ChatGPT browser host crashed before those screenshots were flushed, I do not treat the role observations as evidence-backed UI findings below.

## Fixed-or-not matrix

| Round-2 ID | Verdict | Fresh evidence / note |
|---|---|---|
| R2-EQ-01 simulator connection desync | **FIXED** | `R3-EQ-sim-connected.png` |
| R2-EQ-02 no next step after connect | **NOT FIXED** | Same screenshot: connection is coherent but no prominent path to first image |
| R2-CAP-01 manual capture during paused sequence | **FIXED** | `R3-CAP-sequence-lockout.png` |
| R2-CAP-02 save-to-library switch naming | **FIXED** | `R3-CAP-sequence-lockout.png` |
| R2-CAP-03 capture destination expectation | **NOT FIXED** | Same screenshot: FITS switch remains the only destination cue |
| R2-FOC-01 autofocus result lost on navigation | **FIXED** | `R3-FOC-result.png`, `R3-FOC-persisted-after-navigation.png` |
| R2-FOC-02 curve point detail / focus history | **NOT FIXED** | `R3-FOC-result.png` |
| R2-PLN-01 paused/global status mismatch | **FIXED** | `R3-CAP-sequence-lockout.png` |
| R2-PLN-02 stalled recovery cause | **REGRESSED** | `R3-MON-complete-still-stalled.png`: complete and stalled at once |
| R2-PLN-03 export feedback | **FIXED** | `R3-PLAN-export-toast.png` |
| R2-PLN-04 current plan / sessions / library boundaries | **NOT FIXED** | `R3-PLAN-complete-structure.png` |
| R2-ATL-01 unsupported planet zero state | **FIXED** | `R3-ATL-jupiter-zero-state.png` |
| R2-ATL-02 framing-value ownership | **NOT FIXED** | `R3-ATL-framing-ownership-remedy.png` |
| R2-ATL-03 missing survey remediation | **FIXED** | `R3-ATL-framing-ownership-remedy.png` |
| R2-SIT-01 ambiguous site verbs | **FIXED** | `R3-SET-site-verbs-active.png` |
| R2-SIT-02 active site / preset provenance | **FIXED** | `R3-SET-site-verbs-active.png` |
| R2-WEA-01 chart labels / threshold | **FIXED** | `R3-MON-weather-fixed-1280.png` |
| R2-WEA-02 night-mode weather legibility | **FIXED** | `R3-MON-night-100.png` |
| R2-WEA-03 narrow header | **PARTIALLY FIXED** | Observed clean at ~900px, but the screenshot was lost in the browser-host crash |
| R2-WEA-04 radar tile health | **PARTIALLY FIXED** | `R3-MON-weather-fixed-1280.png` shows `loading…`; final loaded/failed state could not be preserved |
| R2-WEA-05 visible radar zoom | **FIXED** | `R3-MON-weather-fixed-1280.png` |
| R2-WEA-06 session weather override visibility | **FIXED** | `R3-MON-weather-fixed-1280.png` |
| R2-LOG-01 timestamps / severity | **PARTIALLY FIXED** | Observed timestamps and `[Info · focus]` labels, but screenshot was lost in crash |
| R2-AUT-01 role preview | **NOT REFILED** | Explicit non-goal |

## Equipment / connect

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R3-EQ-01 | Minor | As First-light Fran, I tried to connect the simulator and understand whether it really worked. | The rig connected coherently: 8/8 roles were assigned and up, with the guider explicitly marked degraded rather than silently disagreeing with the toast. | `R3-EQ-sim-connected.png` | Keep the role-level status and honest degraded state. |
| R3-EQ-02 | Opportunity | As First-light Fran, I tried to continue directly to my first image. | Connection succeeded, but the screen did not promote the next task. The first-image path still depends on already knowing that Capture is next. | `R3-EQ-sim-connected.png` | After successful connection, show one primary action: **Take a first image**, with a secondary **Run autofocus**. |

## Capture

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R3-CAP-01 | Major | As 2 a.m. Oliver, I tried to take a manual frame while the sequence was paused. | Single and Loop were disabled and the UI said **Sequence paused — camera reserved**. The ownership model is now predictable. | `R3-CAP-sequence-lockout.png` | Keep this inline explanation visible for every camera-owning sequence state. |
| R3-CAP-02 | Minor | As First-light Fran, I entered an impossible exposure. | `-1` produced **Exposure must be greater than 0s** and capture remained unavailable; the message is specific and adjacent. | `R3-CAP-exposure-validation.png` | Also constrain pasted scientific notation and very large values with explicit supported bounds. |
| R3-CAP-03 | Opportunity | As Returning Riley, I tried to tell where the next FITS file would go. | The **Save FITS to library** switch says whether the frame is retained, but no persistent path/library destination or last-saved filename is shown. | `R3-CAP-sequence-lockout.png` | Show `Saving to <library/path>` and the most recent saved filename near the switch. |

### First image — intuitive leaps

- **Expected here but could not:** jump from a successful equipment connection into a safe first exposure, and see exactly where it would be saved.
- **NINA / ASIAIR expectation:** a guided first-capture action, camera readiness, exposure settings, and storage destination in one flow.
- **Would save a click, squint, or doubt:** a post-connect **Take first image** action plus a persistent destination line.

## Focus

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R3-FOC-01 | Blocker | As 2 a.m. Oliver, I tried to trust the autofocus answer. | The curve, best marker, reported best position, and current focuser position agreed at 19,972. Axes include units; the result reported HFR 1.38 px / 2.02 arcsec and R² 0.828. | `R3-FOC-result.png` | Keep the explicit current-position vs best-position agreement; warn when they differ beyond backlash tolerance. |
| R3-FOC-02 | Major | As Returning Riley, I navigated away and back to Focus. | The entire curve and result persisted during in-app navigation. | `R3-FOC-persisted-after-navigation.png` | Keep this lifecycle; page reload persistence remains an explicit non-goal. |
| R3-FOC-03 | Opportunity | As First-light Fran, I tried to inspect a suspicious sample and compare it with earlier runs. | Chart points are not selectable and no run history is available from this screen. | `R3-FOC-result.png` | Make points keyboard/click inspectable with position, HFR, star count, exposure, and rejection reason; retain a compact recent-run list. |

### Achieve focus — intuitive leaps

- **Expected here but could not:** click or focus a chart point to understand why it influenced the fit, and compare this run with the previous one.
- **NINA / ASIAIR expectation:** sample tooltips, rejected-point explanations, fit quality, backlash direction, and recent autofocus history.
- **Would save a click, squint, or doubt:** a one-line truth check: **Best 19,972 · focuser now 19,972 · fit R² 0.828**.

## Plan / sessions

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R3-PLAN-01 | Minor | As Returning Riley, I exported a saved plan. | A persistent, dismissible toast named the exported `.astroplan.json` file. | `R3-PLAN-export-toast.png` | Keep the filename; add **Show in downloads** only where browser APIs permit. |
| R3-PLAN-02 | Major | As Returning Riley, I tried to distinguish tonight's editable draft from completed sessions and reusable plans. | Current plan, Sessions, and Plan library still read as one long surface. The completed session and draft controls compete visually. | `R3-PLAN-complete-structure.png` | Use three clearly bounded regions/tabs: **Tonight's plan**, **Session history**, **Saved plan library**; show explicit draft/saved state. |
| R3-PLAN-03 | Opportunity | As Returning Riley, I tried to name the plan at the moment of saving and know whether later edits were saved. | The surface lacks a strong `Saved / Unsaved changes` ownership cue next to the plan name. | `R3-PLAN-complete-structure.png` | Put name, storage state, and Save/Save as actions in one sticky plan header. |

### Plan tonight — intuitive leaps

- **Expected here but could not:** see which object is the live draft, name it as I save, and know whether edits diverge from the library copy.
- **NINA / ASIAIR expectation:** sequence tabs/templates, duration and completion totals that reconcile with rows, and clear save-as semantics.
- **Would save a click, squint, or doubt:** `Weather Review Session · Saved · edited 2 min ago` in a sticky header.

## Monitor / recovery / weather

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R3-MON-01 | Major | As 2 a.m. Oliver, I tried to decide whether a completed session needed recovery. | The same screen said **COMPLETE**, **10/10**, and showed the target complete, yet also displayed **CAPTURE STALLED? last frame … ago** for minutes. The warning becomes false after completion. | `R3-MON-complete-still-stalled.png` | Gate stall detection to states that can still produce frames. Clear the timer and stale warning immediately on COMPLETE/ABORTED/ERROR. Add a regression test for terminal-state transitions. |
| R3-MON-02 | Minor | As First-light Fran, I tried to understand cloud categories and the hold rule. | Labels now consolidate cloud layers sensibly and state **hold ≥50% for 30m**, with source and update age. | `R3-MON-weather-fixed-1280.png` | Keep the threshold in the chart itself; expose the configured value in a tooltip/details view. |
| R3-MON-03 | Major | As Remote Rae, I tried to distinguish a blank radar map from clear sky. | The map now exposes `loading…`, but this run did not preserve evidence of a final **loaded** or **tiles unavailable** state. The screenshot therefore proves progress, not the full truth contract. | `R3-MON-weather-fixed-1280.png` | Persist a positive **updated N min ago** state after successful paint and an explicit unavailable state on total failure; never let the badge simply disappear. |
| R3-MON-04 | Minor | As 2 a.m. Oliver, I used the weather panel in night mode at 100% screen brightness. | Labels, chart hierarchy, status chips, and controls remained readable without relying on hue alone. | `R3-MON-night-100.png` | Preserve text/icon state in night mode and keep the brightness floor documented at 50%. |
| R3-MON-05 | Opportunity | As 2 a.m. Oliver, I tried to recover after interruption without second-guessing state. | Recovery is conceptually split between Monitor, Plan, and Sessions, and the false stall warning makes that split harder to trust. | `R3-MON-complete-still-stalled.png` | Give terminal runs a plain summary: **Complete — no recovery needed**; interrupted runs should show one reason and one primary resume action. |

### Recover — intuitive leaps

- **Expected here but could not:** see one authoritative explanation of what stopped, what survived, and whether recovery is needed.
- **NINA / ASIAIR expectation:** terminal state, last successful frame, reason for stop, resume position, and one safe recovery action in the same place.
- **Would save a click, squint, or doubt:** suppressing all stall language after completion and showing **No recovery needed**.

## Atlas

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R3-ATL-01 | Minor | As First-light Fran, I searched for Jupiter. | The zero state explicitly said planets are not supported and offered manual coordinates/free roam rather than looking broken. | `R3-ATL-jupiter-zero-state.png` | Keep this honest zero state; planet ephemerides remain an explicit non-goal. |
| R3-ATL-02 | Minor | As First-light Fran, I opened M31 without survey imagery. | The screen now explains how to obtain an offline sky pack or enable online fetch. | `R3-ATL-framing-ownership-remedy.png` | Provide a direct deep link to the relevant settings subsection when permissions allow. |
| R3-ATL-03 | Opportunity | As Returning Riley, I tried to use the framing values that belong to my rig. | Focal length and sensor/pixel fields remain editable in Atlas, while some zero values are sourced from the connected camera. Ownership is ambiguous and duplicates rig-scoped optics. | `R3-ATL-framing-ownership-remedy.png` | Make Atlas a read-only consumer of the active rig/profile by default; offer an explicit temporary framing override with a reset-to-rig action. |

### Find something to shoot — intuitive leaps

- **Expected here but could not:** search a planet as naturally as a deep-sky object and trust the FOV to come from the active rig without re-entering optics.
- **Stellarium / SkySafari expectation:** mixed object search, current ephemerides, visibility, FOV overlays, and equipment profiles.
- **Would save a click, squint, or doubt:** `Framing from profile: <rig name>` beside locked optical values.

## Settings / site

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R3-SITE-01 | Minor | As First-light Fran, I tried to understand whether I was applying coordinates or storing a reusable location. | **Set site**, **Load selected preset**, and **Save as location preset…** now describe different verbs. The active site is named separately from the saved preset selector. | `R3-SET-site-verbs-active.png` | Keep the separation and the inline Save/Cancel naming field. |
| R3-SITE-02 | Opportunity | As Returning Riley, I tried to understand the two-step preset flow before clicking. | The UI is accurate, but **Load selected preset** followed by **Set site** is still easy to miss when cold or on a phone. | `R3-SET-site-verbs-active.png` | After loading, show a prominent `Loaded into form — not active yet` state with **Make active site** as the next action. |

### Set where I am — intuitive leaps

- **Expected here but could not:** save and activate a named site in one deliberate action, or get an unmistakable next step after loading.
- **NINA / ASIAIR expectation:** named observing locations with coordinates, elevation, horizon, and active-state marker.
- **Would save a click, squint, or doubt:** `Preset loaded; active site unchanged` plus **Make active**.

## Access roles — requested pass

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R3-ROLE-01 | Major | As Remote Rae, I tried to follow the disposable-role procedure and prove every enabled operator control avoided 403. | **BLOCKED / not graded.** Loopback clients were still treated as trusted local admin, the server was not reachable at its LAN address, and the direct `/login` route did not provide a usable authenticated operator client. The browser host crashed before screenshots flushed. No claim about operator gating is made from this run. | Evidence unavailable after browser-host crash | Provide a documented test mode that disables loopback trust for one browser session, or bind the review build to a reachable private interface. Add automated capability/UI contract tests so every enabled action is guaranteed accepted by its route. |

## Documentation re-review

The rewritten guide is substantially better at task orientation, terminology, privacy, persistence, weather provenance, and recovery. `monitor.md` is a valuable addition. The major remaining problem is that four role-related guides still document controls that are intentionally visible/clickable but server-forbidden; that is exactly the UI contract the fix wave says was re-gated.

| Document | Verdict | Finding | Recommendation |
|---|---|---|---|
| `docs/guide/README.md` | Good | Clear task index and persona-friendly entry points. | Keep. |
| `getting-started.md` | Good | Simulator and first-image path are understandable. | Add the post-connect **Take first image** cue when UI supports it. |
| `equipment-and-profiles.md` | **Major contradiction** | Lines 5–6 say operator or admin can connect using `config.backend`, but the role matrix says operator does not hold `config.backend`; line 153 also says connect/profile writes require it. | Say **admin** consistently, or change the capability grant and all related UI/routes. |
| `capture.md` | Good | Matches paused camera reservation and validation behavior. | Add storage destination semantics when UI gains them. |
| `focus.md` | Good | V-curve vocabulary and result interpretation match observed UI. | Add point inspection/history when implemented. |
| `sky-atlas.md` | Good | Offline/survey remediation and planet limitation are honest. | Clarify rig-profile optics vs temporary Atlas override. |
| `plan-and-sequences.md` | **Major stale role guidance** | Lines 11–15 still say run control needs admin and point to a UI/server gating caveat. | Rewrite after role fix: state exactly which role can see and successfully use each control; remove “button shown but 403” language. |
| `sessions-multi-night.md` | **Major stale role guidance** | Lines 80–84 say viewer/operator can tap regrade and receive a permission error. | Document the new disabled/hidden behavior and the reason inline; no guide should normalize an enabled control that is guaranteed to fail. |
| `monitor.md` | **Major stale role guidance** | Lines 19–34 say operator controls appear but route enforcement returns permission errors. This contradicts the intended re-gating and undermines the new guide. | Rewrite the control section from the tested capability contract; remove the 403 caveat. |
| `weather.md` | **Minor contradiction** | Lines 11–15 say weather is admin-only, while lines 88–93 say changing **ignore weather tonight** needs operator or admin. On an admin-only panel, the operator wording is confusing. | State whether an operator can reach the override elsewhere; otherwise say admin-only. |
| `site-and-locations.md` | **Major contradiction** | Lines 8–10 say operator or admin can edit site with `config.site_optics`, while the role matrix says operators do not hold it; lines 106–112 also say operator sees hidden coordinates. | Say admin-only consistently and explain that lower roles can see only the default/horizon hints. |
| `remote-access-and-roles.md` | **Major stale source of truth** | Lines 117–144 explicitly describe clickable operator run controls and viewer/operator regrade controls that 403. Lines 146–160 give a useful disposable procedure, but it assumes a client can escape loopback trust. | Update the matrix/caveat to the new re-gated UI. Add a loopback-trust prerequisite and a reachable-LAN verification step to the disposable procedure. |
| `safety-and-automation.md` | Good | Clear separation between advisory weather and hard safety guards. | Keep. |
| `troubleshooting.md` | Good with one gap | Useful stall/recovery checklist, but terminal-state false positives are not a user-fixable condition. | Add: if COMPLETE still shows stalled, treat it as a UI defect and do not resume. |

## Top 10 ranked by user pain

1. **Major — COMPLETE and CAPTURE STALLED appear together**, making session truth and recovery untrustworthy.
2. **Major — operator/viewer role pass could not be executed on this loopback-only trusted-admin deployment**, leaving the critical no-403 contract unverified.
3. **Major — four guides still normalize enabled controls that return 403**, contradicting the intended re-gating.
4. **Major — Plan, Sessions, and Plan library remain weakly separated**, increasing the chance of editing or resuming the wrong object.
5. **Major — radar tile health is not yet fully proven after loading**, so a blank/failed map may still be misread as clear sky.
6. **Opportunity — no post-connect first-image action**, leaving Fran to infer the next screen.
7. **Opportunity — Atlas optics still have ambiguous ownership**, risking an incorrect FOV despite a correctly configured rig.
8. **Opportunity — autofocus points cannot be inspected and runs cannot be compared**, preserving doubt around an otherwise truthful curve.
9. **Opportunity — capture does not persistently show save destination / last filename**, making storage less trustworthy at 2 a.m.
10. **Opportunity — loaded site presets still require a second, easy-to-miss activation step**, despite much better verbs.
