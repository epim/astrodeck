# AstroDeck review round 2 — Codex

Reviewed the live factory instance at `http://127.0.0.1:8802` on 2026-07-16. I used the simulator, saved a Denver-area site, enabled weather, took a first frame, ran native autofocus, built/saved/exported a short M31 plan, interrupted and paused it, searched Atlas for M31 and Jupiter, reused a saved location, and exercised the weather chart/radar at 900, 1280, 1680, and 2200 CSS-pixel widths. I did not touch `:8800`, code, docs, or `ui/dist`.

Evidence is in `review-evidence-2-codex/`.

## Round-one delta

Reconfirmed as unresolved: the simulator connection-state contradiction, absent planets and silent Atlas empty state, unreadably dim night mode, ambiguous active-site/preset verbs, narrow-header telemetry loss, and unreliable pause/progress semantics.

New in this round: weather chart labels overlap exactly; its threshold is unlabeled; IR can look empty without a loading/error/clear state; the last autofocus result disappears after leaving Focus; manual Capture controls remain enabled during a paused sequence but fail only after click; and a paused run can present simultaneous PAUSED/RUNNING/stalled truths.

Verified improvements/truths worth preserving:

- Native autofocus again reported best position **19,972**, drove the focuser to **19,972**, and the drawn minimum agreed with that result. The fit itself passed the truth sweep.
- The weather radar accepted drag-pan and wheel-zoom, recentered correctly, changed layers, reported mount Az/Alt, and kept the scope pierce-point overlay spatially coherent.
- The weather override uses explicit text (`weather override active — resume will ignore clouds tonight`), so its meaning is not carried by hue alone.
- Weather settings reject a cloud threshold of 101 with `Cloud threshold must be 0-100 %`.
- `save current` made the plan findable by name with correct `1t · 10f · 1m` totals. Export initiated, but—as in round one—the automation surface cannot complete the native file-picker import round trip; this limitation is not counted as an app defect.
- Saving a reusable location now prompts for a name and pre-fills the active site name.

## Live UI/UX findings

### Equipment

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R2-EQ-01 | Major | As First-light Fran, connect the simulator and know that the rig is ready | The success toast and simulated device names say connected, but all roles remain `UNASSIGNED`, their dropdowns remain `— unassigned —`, `Connect Rig (0)` is disabled, and Link Status says `No rig connected yet`. Three connection truths still conflict. | `review-evidence-2-codex/UI-equipment-sim-desync.png` | Make one canonical rig state. Either populate simulator assignments or explicitly say `Simulator connected — assignments bypassed` and suppress the false unassigned/no-rig states. |
| R2-EQ-02 | Opportunity | As First-light Fran, move from connection to first light without knowing the information architecture | Successful simulator connection leaves me on an advanced assignment/provider/rotator surface with no next action. | `review-evidence-2-codex/UI-equipment-sim-desync.png` | Add `Rig ready → Take a test exposure` and a compact readiness checklist. NINA/ASIAIR train users to expect connection to culminate in a single, trustworthy ready summary. |

### Capture

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R2-CAP-01 | Major | As 2 a.m. Oliver, take a manual diagnostic frame while a sequence was paused | `Single` and `Loop` remained enabled. Only after clicking did a vague toast say `a sequence is running`; the page neither explains that PAUSED still owns the camera nor takes me back to the sequence. | `review-evidence-2-codex/UI-capture-manual-enabled-during-sequence.png` | Disable the controls with `Sequence paused — camera reserved`, or offer an explicit, safe `Take diagnostic frame` path that explains the consequences. |
| R2-CAP-02 | Minor | As Remote Rae using assistive semantics, identify the save-to-library toggle | The switch immediately before `save FITS to library` has no accessible name, unlike the other switches on the page. | `review-evidence-2-codex/DOC-capture-first-frame.png` | Associate the visible label with the switch and expose the same accessible name. |
| R2-CAP-03 | Opportunity | As 2 a.m. Oliver, understand where the first saved image went | The first image succeeds, but the exposure panel still gives no persistent destination, filename preview, remaining storage, or active project/session context. | `review-evidence-2-codex/DOC-capture-first-frame.png` | Show `Will save as …` and the active library/session path beside the save toggle. |

### Focus

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R2-FOC-01 | Major | As Returning Riley, revisit the autofocus result after planning | Immediately after the run, Result showed HFR 1.38 px / 2.02 arcsec, R² 0.828, hyperbolic fit, and best 19,972. After navigating away and back, the chart and Result were empty and said `Run autofocus to measure focus quality`; only the focuser position 19,972 survived. | `review-evidence-2-codex/DOC-focus-autofocus-result.png`, `review-evidence-2-codex/UI-focus-result-lost-after-navigation.png` | Persist at least the latest run until a new run starts, including samples, fit, result, provider, time, filter, and final-frame preview. |
| R2-FOC-02 | Opportunity | As an operator trained by NINA/ASIAIR, inspect why the fit was accepted | The fit minimum is truthful, but samples are not discoverably tappable and there is no run history. The only evidence disappears on navigation. | `review-evidence-2-codex/DOC-focus-autofocus-result.png` | Make points tappable for position/HFR/frame/error and retain a short autofocus history. A one-line `best 19,972 · final 19,972 · R² .828` would remove a 2 a.m. doubt. |

### Plan, library, and Sessions

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R2-PLN-01 | Major | As 2 a.m. Oliver, pause a ten-frame run and trust its state | The Plan card said `PAUSED` at 6/10 while the global strip simultaneously said `SEQUENCE RUNNING`; the stage still read `dithering`. There is no `Pause requested — finishing current operation` intermediate state. | `review-evidence-2-codex/UI-plan-paused-weather-session.png` | Model `PAUSE REQUESTED` separately, then declare PAUSED only when quiescent. Make the global strip, header counter, session ledger, and stage use the same state snapshot. |
| R2-PLN-02 | Major | As Returning Riley, understand why the interrupted run was not progressing | Monitor eventually showed PAUSED at 7/10 plus `CAPTURE STALLED? last frame 4m 44s ago`, a stale last frame, and `FLIP DUE`, but offered only Resume and Abort. It did not identify the cause, last completed operation, or what Resume would retry. | `review-evidence-2-codex/UI-recovery-stalled-paused.png` | Add a recovery summary: cause/time, last accepted frame, current equipment state, interrupted operation, and exact next step on Resume. Link directly to relevant log entries. |
| R2-PLN-03 | Minor | As Returning Riley, export a saved plan and know the file operation succeeded | `export` initiated without any toast, filename, destination, or downloadable-file confirmation. | `review-evidence-2-codex/DOC-plan-library-saved.png` | Confirm `Exported Weather Review Session.astroplan.json` and offer `Copy`/`Share` where supported. |
| R2-PLN-04 | Opportunity | As Returning Riley, distinguish draft, saved plan, running snapshot, and session ledger | A live sequence, editable Plan fields, a saved-plan row, and a session card coexist. Their relationship remains inferential, especially after interruption. | `review-evidence-2-codex/UI-session-review-after-interruption.png` | Use explicit object labels and flow: `Draft → Saved plan → Running snapshot → Session ledger`; show which one Resume uses. |

### Sky Atlas

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R2-ATL-01 | Blocker | As First-light Fran, find Jupiter | `Jupiter` produced no result, no `no matches` message, no solar-system explanation, and no alternative path. M31 appeared immediately in the same field, proving the search was active. | `review-evidence-2-codex/UI-atlas-jupiter-empty.png` | Add Sun/Moon/planet ephemerides to the common catalog. Until then, render `Planets are not supported yet` plus a manual-coordinate/free-roam alternative. |
| R2-ATL-02 | Major | As Returning Riley, trust the optical train used for framing | Focal length, pixel size, and sensor dimensions are editable directly in Atlas, even though they are rig/camera properties that drive FOV, pixel scale, and mosaic truth. | `review-evidence-2-codex/DOC-atlas-m31-no-survey.png` | Read these from the equipment/profile, show them read-only, and make temporary overrides explicit and non-persistent. |
| R2-ATL-03 | Opportunity | As an operator trained by Stellarium/SkySafari, recover from missing survey imagery | The canvas correctly says no survey source and points to Settings, but there is no one-click `Use schematic`/`Open Sky Atlas settings`; the target and FOV controls remain separated from the remedy. | `review-evidence-2-codex/DOC-atlas-m31-no-survey.png` | Add an inline recovery action and a solar-system-aware common search. |

### Settings · Observing Site and Weather

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R2-SIT-01 | Major | As First-light Fran, understand `Save site`, `Save current…`, and `Apply` | `Save site` changes the active site; `Save current…` creates a named preset; selecting a preset immediately repopulates the form; `Apply` then gives no visible confirmation. The verbs still hide two scopes and the selection appears to do the action before the button does. | `review-evidence-2-codex/UI-site-saved-location-verbs.png` | Rename to `Apply active site`, `Save as location preset…`, and `Load selected preset`; do not mutate the form merely by highlighting a preset, or remove the redundant Apply button. |
| R2-SIT-02 | Opportunity | As Returning Riley, see what survived and where coordinates came from | The form contains the correct saved coordinates, but there is no persistent `Active site`, source, last-updated time, or weather-fetch location summary. | `review-evidence-2-codex/UI-settings-weather-enabled-1280.png` | Show `Active: Denver Review Site · manual · saved` and the coordinate provenance used by Atlas/weather. |

### Monitor · Sky Conditions and Radar

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R2-WEA-01 | Major | As 2 a.m. Oliver, distinguish total/low/mid/high cloud and the configured threshold | At the right edge, `total` and `mid` occupy the exact same y-coordinate; `low` and `high` do the same. The dashed 50% threshold is visible but unlabeled. When lines coincide at zero, the chart does not identify which layers are coincident. | `review-evidence-2-codex/UI-monitor-ignore-weather.png` | Use a fixed legend plus patterned strokes, label the threshold (`Hold above 50% for 30 min`), and consolidate coincident end labels (`total + mid 0%`). |
| R2-WEA-02 | Major | As 2 a.m. Oliver, read weather at the displayed 100% night brightness | Night mode at 100% is almost black. Chart labels, radar controls, scope overlay, attribution, override status, and thermal numbers require dark-room image enhancement to read. | `review-evidence-2-codex/UI-weather-night-mode.png` | Raise the maximum-night luminance/contrast floor, test text and outlines against WCAG-style contrast even in red, and preserve distinct line styles. |
| R2-WEA-03 | Major | As Remote Rae, check the rig at 900 px | Essential header telemetry disappears or clips: RA/Dec and sequence progress are gone while a stray cyan fragment remains near the temperature; brightness controls keep their full footprint. | `review-evidence-2-codex/UI-weather-radar-900.png` | Prioritize sequence state, mount state, and a compact coordinate summary; move brightness/log controls into a mobile overflow sheet. |
| R2-WEA-04 | Major | As 2 a.m. Oliver, switch to IR satellite and trust the layer | The IR view can render only the star background and scope overlay, with no tile imagery and no `loading`, `clear`, `stale`, or `failed` state. A blank layer is operationally ambiguous. | `review-evidence-2-codex/UI-radar-ir-satellite.png` | Add per-layer loading/error/stale badges and a tile timestamp; never let missing imagery look like meteorological truth. |
| R2-WEA-05 | Opportunity | As Remote Rae on a phone, zoom the radar without a mouse wheel | The accessible description mentions `+/-`, but there are no visible zoom buttons or zoom level. Narrow layout requires scrolling before Radar and offers no touch-specific instruction. | `review-evidence-2-codex/UI-weather-radar-900.png` | Add visible 44 px `−/+` controls, a zoom indicator, and pinch support/instruction. |
| R2-WEA-06 | Opportunity | As Returning Riley, connect the override to the session it affects | The override is clear on Sky Conditions, but the active session card contains only `review`; there is no session-level weather-policy summary until the narrower dormant/high-cloud condition occurs. | `review-evidence-2-codex/UI-monitor-ignore-weather.png`, `review-evidence-2-codex/UI-session-review-after-interruption.png` | Mirror `Weather: override active until dawn` on the affected session card and link back to Sky Conditions. |

### Settings · Auth and Users

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R2-AUT-01 | Opportunity | As Remote Rae, verify a read-only experience without changing security state | The fresh instance is explicitly open-admin and has no users. Testing viewer/operator behavior requires enabling auth and creating accounts, with no safe `Preview as viewer` path. | `review-evidence-2-codex/DOC-auth-open-server.png`, `review-evidence-2-codex/DOC-users-empty.png` | Add a capability matrix and a non-authorizing `Preview UI as viewer/operator` mode. Preserve the strong open-server warning. |

### Event Log

| ID | Severity | As persona, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| R2-LOG-01 | Major | As 2 a.m. Oliver, learn why capture stalled | The log lists sources/messages newest-first but has no timestamps, severity words, pause/resume transitions, correlation/session IDs, or direct match for the stall. It confirms frames were saved but not why the engine stopped. | `review-evidence-2-codex/DOC-troubleshooting-event-log.png` | Add local/UTC timestamps, severity text/icons, session/target filters, and linked state transitions; surface the matching event from the stalled banner. |

## Intuitive-leap answers

### Get the rig connected and take a first image

- **Expected here but could not:** one authoritative `ready` state and a direct next step to Capture.
- **Audience expectation:** ASIAIR/NINA summarize connected devices and make camera readiness unambiguous.
- **2 a.m. saver:** `Simulator ready · Camera/Mount/Focuser/Guider connected → Take test exposure`.

### Achieve focus

- **Expected here but could not:** return to the last fit, tap a point for its frame, and compare recent runs.
- **Audience expectation:** NINA/ASIAIR keep the most recent curve, effective filter/camera settings, fit quality, and final position.
- **2 a.m. saver:** persistent `best 19,972 = final 19,972` with timestamp and a final-frame link.

### Plan tonight

- **Expected here but could not:** know exactly when Pause became quiescent and whether the running object was the draft, saved plan, snapshot, or session.
- **Audience expectation:** explicit pause-requested/paused states, immutable running snapshots, checkpoint/resume descriptions, and acknowledged export/import operations.
- **2 a.m. saver:** a state timeline plus `Resume will retry M31 frame 8/10`.

### Find something in Atlas

- **Expected here but could not:** find Jupiter, receive an explicit no-result reason, or switch directly to a usable fallback map.
- **Audience expectation:** Stellarium/SkySafari provide solar-system ephemerides and time-aware altitude from the same search as deep-sky targets.
- **2 a.m. saver:** `Jupiter · Alt … · rises …` or an honest unsupported message with a manual-coordinate path.

### Set where I am

- **Expected here but could not:** predict whether selection, Apply, Save site, or Save current changed the active site versus the preset library.
- **Audience expectation:** named location profiles with unambiguous Load/Save as/Delete and a visible active location.
- **2 a.m. saver:** `Active site: Denver Review Site` plus object-specific verbs.

### Recover

- **Expected here but could not:** see why the run stopped, what hardware state survived, and the exact first action Resume would take.
- **Audience expectation:** NINA-style sequence checkpoints, interruption cause/time, last accepted frame, retry/skip/abort controls, and one consistent state.
- **2 a.m. saver:** `Paused after frame 7/10 · last save 08:04:37 · Resume starts frame 8`.

### Monitor weather

- **Expected here but could not:** tell whether blank IR meant clear sky or failed tiles, read coincident layers, and see the threshold policy on the chart.
- **Audience expectation:** weather tools show layer timestamps/health, labeled thresholds, fixed legends, touch zoom, and warnings tied to the affected plan/session.
- **2 a.m. saver:** `IR failed/stale`, `threshold 50% / 30 min`, and readable red-mode labels.

## UI Top 10 ranked by user pain

1. **R2-ATL-01 — Planets remain impossible and fail silently.**
2. **R2-PLN-01/02 — Pause, running, progress, and stalled states cannot be trusted as one coherent truth.**
3. **R2-EQ-01 — The simulator is simultaneously connected, unassigned, and “not connected.”**
4. **R2-WEA-02 — Night mode makes operational weather nearly unreadable even at 100%.**
5. **R2-WEA-01 — Cloud-series labels overlap and the hold threshold is unlabeled.**
6. **R2-WEA-03 — Narrow width drops essential rig and sequence telemetry.**
7. **R2-WEA-04 — A visually blank IR layer has no health/failure state.**
8. **R2-FOC-01 — A truthful autofocus result disappears as soon as the user navigates away.**
9. **R2-SIT-01 — Site/preset verbs and selection behavior still conceal ownership.**
10. **R2-CAP-01 — Manual capture appears available during a paused sequence, then fails only after click.**

# Documentation field review

## `docs/guide/capture.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-CAP-01 | Gap | 2 a.m. Oliver | “You can't capture while polar alignment is running” (`capture.md:30`). | A paused sequence also owns the camera, but Single/Loop stay enabled and fail only after click with `a sequence is running`. The guide never explains this state. | `review-evidence-2-codex/UI-capture-manual-enabled-during-sequence.png` | Document all camera-ownership conflicts and what PAUSED permits; align the UI with the rule. |

## `docs/guide/equipment-and-profiles.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-EQP-01 | Major | First-light Fran | “The Link Status panel … shows the per-role connected/error state at a glance” (`equipment-and-profiles.md:61-62`). | After `▶ Simulator rig`, Link Status says only `No rig connected yet`, while each role remains `UNASSIGNED` despite simulated device names. | `review-evidence-2-codex/UI-equipment-sim-desync.png` | Correct the app state or document the simulator-assignment bypass explicitly. |
| DOC-EQP-02 | Minor | First-light Fran | “Go to Settings → Backend Drivers” (`equipment-and-profiles.md:17`). | Settings opens a section selector; Backend Drivers is a heading inside **Connect**, not a Settings destination. | `review-evidence-2-codex/UI-settings-weather-enabled-1280.png` | Use the literal path `Settings → Connect → Backend Drivers`. |

## `docs/guide/focus.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-FOC-01 | Major | Returning Riley | “Autofocus result (the Result panel): after a sweep it shows … fit R² … and the best position” (`focus.md:73-76`). | True immediately after the sweep, but navigating away/back erases Result and the entire curve without warning. | `review-evidence-2-codex/DOC-focus-autofocus-result.png`, `review-evidence-2-codex/UI-focus-result-lost-after-navigation.png` | State the lifetime only if intentional; preferably persist and document the last-run/history behavior. |

## `docs/guide/getting-started.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-GET-01 | Major | First-light Fran | After `▶ Simulator rig`, “the Devices panel + Link Status show the camera, mount, focuser, filter wheel and the rest coming up” (`getting-started.md:67-70`). | The toast appears, but Devices stays `UNASSIGNED` and Link Status says no rig connected. Literal walkthrough produces contradiction at the first hardware-success checkpoint. | `review-evidence-2-codex/UI-equipment-sim-desync.png` | Do not tell a novice to use those panels as confirmation until they tell the truth; specify the authoritative indicator. |
| DOC-GET-02 | Minor | First-light Fran | “Equipment tab / left-rail ‘Rig’” (`getting-started.md:67`). | The left rail is labeled **Equipment**, not Rig. | `review-evidence-2-codex/DOC-getting-started-equipment-before.png` | Use the exact current label only. |

## `docs/guide/plan-and-sequences.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-PLAN-01 | Major | 2 a.m. Oliver | “Pause, resume, and abort are safe at frame boundaries, and progress is persisted for resume” (`plan-and-sequences.md:59-60`). | PAUSED appeared while the global strip still said running and the stage said dithering; later Monitor flagged the paused run as capture-stalled. The doc gives no intermediate-state semantics. | `review-evidence-2-codex/UI-plan-paused-weather-session.png`, `review-evidence-2-codex/UI-recovery-stalled-paused.png` | Define pause-requested, frame-boundary behavior, timeout/stall behavior, and exactly which counter is persisted. |
| DOC-PLAN-02 | Minor | Returning Riley | “Live progress, ETA, and telemetry are on the Monitor view” links to `README.md` (`plan-and-sequences.md:111`). | The link lands on the guide index because there is no Monitor guide; it does not explain Monitor state or recovery. | `review-evidence-2-codex/UI-recovery-stalled-paused.png` | Add a Monitor/operations guide and link directly to its recovery/status section. |

## `docs/guide/README.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-IDX-01 | Gap | 2 a.m. Oliver | “Find it by task” lists six questions (`docs/guide/README.md:47-58`). | It omits `Why is capture stalled?`, `What does RMS/HFR mean?`, `Why is radar blank?`, `How do I recover this run?`, and `Why can't I find a planet?`—the questions the live walkthrough raised. | `review-evidence-2-codex/UI-recovery-stalled-paused.png`, `review-evidence-2-codex/UI-atlas-jupiter-empty.png` | Add an operator-oriented task index and a dedicated Monitor/Guide glossary path. |

## `docs/guide/remote-access-and-roles.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-ROLE-01 | Gap | Remote Rae | “Viewers get a View-only badge … and controls are hidden for their role” (`remote-access-and-roles.md:46-48`). | The fresh instance is open-admin with no users. Verifying the claim requires enabling auth and creating accounts; neither the guide nor app offers a safe preview/test procedure. | `review-evidence-2-codex/DOC-auth-open-server.png`, `review-evidence-2-codex/DOC-users-empty.png` | Add `Preview as role` or a documented disposable verification flow and screenshots of each role's visible navigation. |
| DOC-ROLE-02 | Minor | Remote Rae | The capability table says operator cannot use mount, power, config, media, or precise site (`remote-access-and-roles.md:57-61`). | This may be true server-side, but the guide does not map capabilities to concrete hidden/disabled screens and controls, so Rae cannot predict the narrow UI. | `review-evidence-2-codex/DOC-auth-open-server.png` | Add a screen-by-screen role matrix with visible/hidden/read-only behavior. |

## `docs/guide/safety-and-automation.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-SAFE-01 | Minor | First-light Fran | “Today the Settings → Safety UI panel exposes only sun avoidance” (`safety-and-automation.md:65-68`), but later “the UI sets 10° when you enable the floor” (`:80-82`). | The live Safety panel exposes only sun avoidance and a 30° angle; there is no floor UI to enable. The document contradicts itself. | `review-evidence-2-codex/DOC-safety-sun-avoidance.png` | Remove the UI claim at line 82 or identify the exact existing surface. Keep REST/config-only features in a clearly separated admin section. |

## `docs/guide/sessions-multi-night.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-SES-01 | Gap | Returning Riley | A dormant card shows `resume`, `auto-resume at dusk`, weather warning, and sometimes `ignore weather tonight` (`sessions-multi-night.md:90-118`). | A browser interruption preserved the run as active/paused with only `review`; there is no guidance for distinguishing frontend disconnect, PAUSED, STALLED, ACTIVE, and DORMANT, or for knowing which recovery controls should appear. | `review-evidence-2-codex/UI-session-review-after-interruption.png`, `review-evidence-2-codex/UI-recovery-stalled-paused.png` | Add a state/trigger/control table and a `What should I see after browser loss vs server reboot?` procedure. |
| DOC-SES-02 | Minor | Returning Riley | “At boot, any session still marked active … is swept to dormant” (`sessions-multi-night.md:124-131`). | This high-value claim could not be safely live-verified without restarting Claude's designated review service. The guide offers no non-destructive simulator/test command or UI diagnostic. | `review-evidence-2-codex/UI-session-review-after-interruption.png` | Add a simulator-only recovery test procedure and observable expected events. |

## `docs/guide/site-and-locations.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-SITE-01 | Major | First-light Fran | “Pick one and press Apply to load it into the form (then Save site to make it active)” (`site-and-locations.md:63-65`). | Merely selecting Denver immediately restored all form values; Apply then produced no visible confirmation. The literal action model and the screen disagree. | `review-evidence-2-codex/UI-site-saved-location-verbs.png` | Document the actual two-stage semantics after fixing them, and use `Load preset`/`Apply active site` terminology. |

## `docs/guide/sky-atlas.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-ATL-01 | Major | First-light Fran | “The catalog is a curated list of Messier … NGC/IC … Sh2 objects—not an exhaustive survey catalog” (`sky-atlas.md:18-20`). | This is technically honest but never says planets are excluded or how to locate one. Searching Jupiter gives a silent empty state. | `review-evidence-2-codex/UI-atlas-jupiter-empty.png` | State the solar-system limitation prominently and give a workaround; preferably document planet search after implementing it. |
| DOC-ATL-02 | Minor | First-light Fran | Search “shows the top matches” (`sky-atlas.md:12-16`). | The guide omits the zero-result state entirely, and the UI renders no message. | `review-evidence-2-codex/UI-atlas-jupiter-empty.png` | Document loading, no-results, offline, and catalog-scope states with recovery actions. |

## `docs/guide/troubleshooting.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-TRB-01 | Major | 2 a.m. Oliver | Event Log is “the first place to look for why something refused or failed” (`troubleshooting.md:83-93`). | The log has no timestamps or pause/resume/stall transition and did not explain the live `CAPTURE STALLED?` warning. It confirms saved frames but not cause. | `review-evidence-2-codex/DOC-troubleshooting-event-log.png`, `review-evidence-2-codex/UI-recovery-stalled-paused.png` | Document what the log does *not* contain, add a stall-diagnosis checklist, and link Monitor warnings to filtered events. |

## `docs/guide/weather.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-WEA-01 | Major | 2 a.m. Oliver | Four lines are “distinguished by line style and an inline label … readable in night mode,” and a dashed rule marks the threshold (`weather.md:47-50`). | Inline labels overlap in pairs; the rule has no label; at 100% night mode the panel is nearly unreadable. | `review-evidence-2-codex/UI-monitor-ignore-weather.png`, `review-evidence-2-codex/UI-weather-night-mode.png` | Correct the chart, then document a fixed legend, labeled policy threshold, and coincident-series behavior. |
| DOC-WEA-02 | Major | 2 a.m. Oliver | The Radar panel has selectable Radar/IR layers and dimmed night imagery (`weather.md:108-115`). | IR can show no imagery at all and no loading/error/stale/clear state. The guide gives no way to distinguish failure from meteorological truth. | `review-evidence-2-codex/UI-radar-ir-satellite.png` | Document layer health/timestamps and an empty/error recovery path; add these states to the UI. |
| DOC-WEA-03 | Gap | Remote Rae | Weather and radar are admin-only because they require precise-site access (`weather.md:11-15`). | The claim could not be verified without enabling auth. The guide gives no viewer screenshot or safe verification method. | `review-evidence-2-codex/DOC-auth-open-server.png` | Cross-link a concrete role-preview procedure and show what replaces the absent panels for viewer/operator roles. |

## Root `README.md`

| ID | Severity | Persona | Doc says (quoted line + file:line) | App shows / gap | Evidence (screenshot) | Recommendation |
|---|---|---|---|---|---|---|
| DOC-ROOT-01 | Major | First-light Fran | “The Settings view is a placeholder (site/optics/safety/alerts are edited through the API…)” (`README.md:123-126`). | Settings now contains working Connect, site, saved locations, Weather, Sky Atlas, Safety, Accounts, Users, and Auth surfaces. | `review-evidence-2-codex/UI-settings-weather-enabled-1280.png`, `review-evidence-2-codex/DOC-auth-open-server.png` | Replace the stale rough-edge note with the current UI/API split. |
| DOC-ROOT-02 | Minor | First-light Fran | “hit Connect Simulator Rig” (`README.md:146`). | The live control is `▶ Simulator rig`. | `review-evidence-2-codex/UI-equipment-sim-desync.png` | Use the exact button label and link to Getting started. |
| DOC-ROOT-03 | Major | First-light Fran | Native focus is described as “parabola fit” (`README.md:68-70`). | The native run reported a hyperbolic fit with R² and trendlines. | `review-evidence-2-codex/DOC-focus-autofocus-result.png` | Match the guide: native/NINA can use a real hyperbola; avoid promising one model for every provider. |
| DOC-ROOT-04 | Gap | 2 a.m. Oliver | Monitor is listed as ETA/progress/mount/cooler/guiding/flip/HFR/thumbnail (`README.md:99-100`). | The shipped Weather/Sky Conditions/Radar surface is absent from the feature list. | `review-evidence-2-codex/UI-monitor-sky-radar-1280.png` | Add Weather to Features and link directly to `docs/guide/weather.md`. |

## Findability sweep from `docs/guide/README.md`

Hops count link clicks from the guide index; `fail` means I could not reach a sufficient answer within 60 seconds.

| Persona | Five task-shaped questions | Result |
|---|---|---|
| First-light Fran | Connect simulator; take first image; what is HFR; save/reuse a site; find Jupiter | 1 hop; 1 hop; 2 hops; 1 hop; **fail** (Atlas explains catalog scope but not planets/workaround) |
| 2 a.m. Oliver | Resume at dusk; why did capture stall; what does the cloud warning mean; what is RMS; how do I abort safely | 1; 1 but insufficient; 1; **fail** (no Guide/glossary guide); 2 |
| Remote Rae | What can viewer do; why are coordinates/weather missing; configure relay; what is visible on mobile; safely test a viewer | 1; 1; 1; **fail**; **fail** |
| Returning Riley | What survives reboot; resume a dormant session; load/export a saved plan; reuse a location; understand draft vs session snapshot | 1; 1; 1; 1; **fail** |

## Documentation Top 10 ranked by user pain

1. **DOC-GET-01 / DOC-EQP-01 — The literal simulator success check contradicts the live screen.**
2. **DOC-WEA-01 — The guide promises a night-readable, non-color chart that is neither.**
3. **DOC-PLAN-01 — “Safe at frame boundaries” omits the contradictory pause-requested/stalled states users actually see.**
4. **DOC-SES-01 — Recovery controls are documented without a state/trigger model that lets Riley know why they are absent.**
5. **DOC-ROOT-01 — README still calls the now-substantial Settings UI a placeholder.**
6. **DOC-ATL-01 — Catalog scope is technically disclosed but planet failure and workaround are not.**
7. **DOC-FOC-01 — The guide implies a useful Result panel but omits that its data disappears on navigation.**
8. **DOC-TRB-01 — Event Log is presented as causal diagnosis, but cannot explain the observed stall.**
9. **DOC-ROLE-01/02 — Strong role claims lack a safe verification flow and screen-level matrix.**
10. **DOC-SITE-01 — Apply/load/save semantics in the guide do not match the live interaction.**

## The three doc changes that would most help each persona

For **First-light Fran**, make Getting started use one authoritative simulator-ready indicator, replace every path/button with the literal current label, and state Atlas's planet limitation plus workaround. For **2 a.m. Oliver**, add a Monitor/recovery guide with a state table and stall checklist, a compact HFR/RMS/weather glossary, and screenshots of readable night-mode charts with threshold/layer-health semantics. For **Remote Rae**, add a screen-by-screen viewer/operator/admin matrix, a safe `Preview as role` verification procedure, and narrow/mobile navigation screenshots including what is deliberately absent. For **Returning Riley**, diagram Draft → Saved plan → Running snapshot → Session ledger, document exactly what survives browser loss versus server reboot, and use unambiguous Load/Apply/Save-as language for plans and locations.
