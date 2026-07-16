# AstroDeck live UI/UX field review

Reviewed the running app at `http://localhost:8802` on 2026-07-15 with the simulator rig. I took a first image, ran autofocus, built and saved a two-step M42 plan, exported it, found and framed M31, saved and reused a site, resumed an interrupted M31 session, paused it, and exercised a second browser tab.

Viewport sweeps covered 900, 1280, 1680, and 2200 CSS pixels. The in-app browser rasterized 2200 px screenshots at roughly 1664 px, so 2200 px conclusions use DOM geometry; screenshots of wide-layout findings use 1680 px.

Verified truths worth preserving:

- Autofocus reported best position **19,972**; the fitted curve's drawn minimum, the best marker, and the focuser's final position agree horizontally.
- Plan totals are correct: `10 × 120 s + 5 × 300 s = 45 min`, and the UI shows 15 frames / 0 h 45 m. The export JSON matches.
- Site validation rejects latitude 91 with the actionable message “Latitude must be between 0 and 90°.”
- Resume did pick up the existing session at 1/130 rather than restarting it.

One test limitation is not counted as an app finding: export produced a valid `.astroplan.json`, but the in-app automation surface could not drive the native file picker, so the import round-trip could not be completed.

## Equipment

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| EQ-01 | Major | As First-light Fran, connect the simulator and confirm the rig is ready | The simulator toast and green device dots say connected, while every driver still says `unassigned` / `UNASSIGNED`, `Connect Rig (0)` is disabled, and Link Status says “No rig connected yet.” The app presents three mutually incompatible connection truths. | [EQ-01-simulator-status-conflict-top.png](C:/Users/bear/astro/review-evidence/EQ-01-simulator-status-conflict-top.png) | Make simulator connection populate assignments and one authoritative rig status. If simulator bypasses assignments, say “Simulator connected (assignments bypassed)” and remove the false unassigned/no-rig states. |
| EQ-02 | Minor | As Remote Rae, use Equipment in a 900 px window | The right status column disappears but the remaining column stays narrow, leaving a large unused strip and making the dense setup text smaller than necessary. | [EQ-900-unassigned.png](C:/Users/bear/astro/review-evidence/EQ-900-unassigned.png) | Let the primary column consume the released width or stack Link Status below it. |
| EQ-03 | Opportunity | As First-light Fran, know the next step after installation | The first view exposes eight driver assignments, provider routing, rotator controls, profiles, and three rig-action buttons at once. The one-click simulator is present but not framed as a first-light path. | [EQ-1280-unassigned.png](C:/Users/bear/astro/review-evidence/EQ-1280-unassigned.png) | Add a short first-light checklist: choose/connect rig → verify camera/mount → take test exposure, with advanced routing collapsed. |

## Capture

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| CAP-01 | Blocker | As 2 a.m. Oliver, enter an exposure and trust the resulting metadata | `-5` seconds is accepted as valid, a frame is generated, and the live metadata reports `-5s`. No minimum, inline error, or blocking validation exists. | [CAP-03-invalid-exposure-no-feedback.png](C:/Users/bear/astro/review-evidence/CAP-03-invalid-exposure-no-feedback.png) | Enforce a positive camera-supported range before capture, keep the previous valid value, and state the allowed range beside the field. Never generate or label a negative exposure. |
| CAP-02 | Major | As 2 a.m. Oliver, use night mode at a readable brightness | Night mode becomes almost unreadable even at the displayed maximum; the mode also produced a 45% header brightness state while a transient badge showed 100%. | [CAP-02-night-mode-first-image.png](C:/Users/bear/astro/review-evidence/CAP-02-night-mode-first-image.png) | Raise minimum text/outline contrast, keep the brightness value consistent across controls/toasts, and test against a night-mode contrast target rather than hue alone. |
| CAP-03 | Minor | As First-light Fran, predict what `Center` does | `Center` appears selected while the fitted image is visibly anchored left with a large black gutter on the right. It is unclear whether the verb means center the image, show a center mark, or center the mount. | [CAP-01-first-image.png](C:/Users/bear/astro/review-evidence/CAP-01-first-image.png) | Rename it to the exact object/action (`Center image`, `Center reticle`, or `Center mount`) and make the selected state match the visible result. |
| CAP-04 | Minor | As Remote Rae, read the global rig telemetry at 900 px | RA/Dec collapse into fragments such as `0`, `--`, and wrapped `ALT`, while less critical brightness controls retain full width. | [CAP-900-empty.png](C:/Users/bear/astro/review-evidence/CAP-900-empty.png) | Prioritize coordinates/state, abbreviate deliberately, or move secondary controls into an overflow menu at the narrow breakpoint. |
| CAP-05 | Opportunity | As Returning Riley, decide where a first-light image will be kept | `save FITS to library` is only a switch; there is no visible project/session name, destination, filename pattern, or storage estimate where the exposure is configured. | [CAP-01-first-image.png](C:/Users/bear/astro/review-evidence/CAP-01-first-image.png) | Show the active capture project/path and naming rule, with a quick “save this frame as…” action after the first image. |

## Focus

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| FOC-01 | Major | As 2 a.m. Oliver, reconcile the autofocus result with the image beside it | The result says `FOCUS — GOOD`, HFR 1.38 px, best 19,972, while Live Preview remains the stale bad frame saying “Few stars” with unrelated statistics. | [FOCUS-03-result-900.png](C:/Users/bear/astro/review-evidence/FOCUS-03-result-900.png) | Update Live Preview with the final autofocus frame or label it unmistakably as “Pre-autofocus frame.” Keep result and preview timestamps together. |
| FOC-02 | Major | As First-light Fran, understand the V-curve | The chart draws a solid hyperbolic curve plus a second dashed V/polyline with no legend, while `R² 0.828` and `hyperbolic` receive no context. The best marker itself aligns correctly with 19,972. | [FOCUS-02-vcurve-truth.png](C:/Users/bear/astro/review-evidence/FOCUS-02-vcurve-truth.png) | Label measured samples, sample-connecting line, fitted model, uncertainty bars, and best position. Explain R² quality and the acceptance threshold inline. |
| FOC-03 | Minor | As Remote Rae, read chart axes without squinting | The y-axis label `HFR` overlaps the top `2.8` tick and renders like `HFR.8`; Position has only two endpoints and no unit/context. | [FOCUS-02-vcurve-truth.png](C:/Users/bear/astro/review-evidence/FOCUS-02-vcurve-truth.png) | Reserve axis-title space, use `HFR (px)` and `Focuser position (steps)`, and add at least one interior x tick. |
| FOC-04 | Opportunity | As an operator trained by NINA/ASIAIR, inspect or control an autofocus sample | Chart points are not discoverably interactive, and autofocus exposes only exposure and step size—not filter, gain, binning, or a per-run reason/history. | [FOCUS-03-result-900.png](C:/Users/bear/astro/review-evidence/FOCUS-03-result-900.png) | Make points tappable for position/HFR/error/frame preview and expose the effective camera/filter settings with an “advanced” disclosure. |

## Plan editor, library, and sessions

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| PLN-01 | Major | As Returning Riley, find a long-named saved plan at 900 px | The library truncates the name to `UX Revie…`, stacks `1t / 15f / 45m` cryptically, makes `load` only 37 px wide, and clips `import` at the edge. Similar plans would be indistinguishable. | [PLAN-04-saved-library.png](C:/Users/bear/astro/review-evidence/PLAN-04-saved-library.png) | Give the library the full narrow layout width, wrap to two lines, add a full-name tooltip/details row, spell out targets/frames/time, and keep actions at least 44×44 px. |
| PLN-02 | Major | As Returning Riley, edit the same plan from a second tab | Tab 2 silently changed the draft name to `Concurrent Tab Rename` while Tab 1 kept the original. Neither tab showed a stale/conflict indicator or ownership/version warning. | [PLAN-05-conflict-tab2.png](C:/Users/bear/astro/review-evidence/PLAN-05-conflict-tab2.png), [PLAN-06-conflict-tab1.png](C:/Users/bear/astro/review-evidence/PLAN-06-conflict-tab1.png) | Version drafts, broadcast changes, or detect stale saves and offer compare/reload instead of last-write-wins. |
| PLN-03 | Opportunity | As an operator trained by NINA, set every exposure parameter in a sequence step | Steps expose filter, exposure, gain, binning, and count, but not offset—even though the exported steps silently contain offset 30. | [PLAN-03-long-name-900.png](C:/Users/bear/astro/review-evidence/PLAN-03-long-name-900.png) | Show inherited offset in each step and allow override, or clearly state “Offset 30 (from camera/capture profile).” |
| PLN-04 | Opportunity | As First-light Fran, know what is being saved or updated | `save current`, `load`, `update from plan`, a live draft, a saved-plan row, and a resumable session coexist without naming their object boundaries. | [PLAN-04-saved-library.png](C:/Users/bear/astro/review-evidence/PLAN-04-saved-library.png) | Use object-specific verbs: `Save draft to library`, `Load into editor`, `Replace session plan`, `Export file`; show Draft → Saved plan → Session as distinct entities. |
| PLN-05 | Minor | As Remote Rae, operate plan automation with gloves at night | Critical guide/flip/safety/recovery switches are about 36×20 px and densely packed; their ON/OFF words are absent in the Plan view. | [PLAN-900-empty.png](C:/Users/bear/astro/review-evidence/PLAN-900-empty.png) | Make the entire label row tappable, show explicit ON/OFF text, and increase target size and vertical rhythm. |

## Sky Atlas

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| ATL-01 | Blocker | As First-light Fran, find a planet to shoot | `Jupiter` and `Mars` return nothing in Atlas, with no “no results” state. The alternate Mount catalog explicitly says `no matches` for Jupiter. | [ATLAS-01-jupiter-no-result.png](C:/Users/bear/astro/review-evidence/ATLAS-01-jupiter-no-result.png), [MOUNT-01-jupiter-no-matches.png](C:/Users/bear/astro/review-evidence/MOUNT-01-jupiter-no-matches.png) | Add Sun/Moon/planet ephemerides to the common target provider and distinguish “not in catalog” from “not observable tonight.” |
| ATL-02 | Major | As First-light Fran, search for M31 | Typing appears inert; a result appears only after pressing Enter. There is no Search button, Enter hint, loading state, or no-results message. | [ATLAS-01-jupiter-no-result.png](C:/Users/bear/astro/review-evidence/ATLAS-01-jupiter-no-result.png) | Search as the user types, or add a visible Search button and `Press Enter` hint; always render an explicit empty/error state. |
| ATL-03 | Major | As Returning Riley, trust the saved rig geometry | Focal length, pixel size, and sensor dimensions are editable directly in Atlas. These are rig/camera properties and can diverge from Equipment while silently changing FOV, pixel scale, and mosaics. | [ATLAS-02-m31-framing-1280.png](C:/Users/bear/astro/review-evidence/ATLAS-02-m31-framing-1280.png) | Store optical train geometry on the equipment/profile; show it read-only here with `Edit equipment profile` and an explicit temporary-override action. |
| ATL-04 | Major | As 2 a.m. Oliver, trust tonight's visibility chart | The curve, hatched region, vertical boundaries, and 15° line have no legend or time ticks; the summary concatenates values (`21:4548°`) and truncates at narrow widths. | [ATLAS-03-tonight-visibility.png](C:/Users/bear/astro/review-evidence/ATLAS-03-tonight-visibility.png) | Label time on x, altitude on y, dark window, moon window, horizon, transit, and now; keep text summaries separated as `21:45 · 48°`. |
| ATL-05 | Opportunity | As an operator trained by Stellarium/SkySafari, turn “needs a mosaic” into a plan | Atlas says the object is 6.4× the frame, but Mosaic remains 1×1 and offers no suggested grid. | [ATLAS-03-tonight-visibility.png](C:/Users/bear/astro/review-evidence/ATLAS-03-tonight-visibility.png) | Offer `Fit object with suggested mosaic`, show coverage %, and preview the recommended rows/cols before adding to Plan. |
| ATL-06 | Minor | As Remote Rae, frame at 900 px | Camera-source badges crowd field labels, and the large canvas pushes all framing/mosaic/visibility controls below the fold. | [ATLAS-04-m31-900.png](C:/Users/bear/astro/review-evidence/ATLAS-04-m31-900.png) | Collapse equipment geometry into a summary row and provide a sticky compact framing toolbar at narrow widths. |

## Mount target catalog

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| MNT-01 | Major | As First-light Fran, use the alternate catalog path Atlas itself suggests | Jupiter produces `no matches`, confirming that there is no planet path through Mount either. | [MOUNT-01-jupiter-no-matches.png](C:/Users/bear/astro/review-evidence/MOUNT-01-jupiter-no-matches.png) | Share one catalog/ephemeris service between Mount, Atlas, and Plan so supported targets do not depend on screen. |

## Settings · Observing Site

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| SIT-01 | Major | As First-light Fran, save and reuse a location | `Save site` persists the active site but does not add it to Saved locations; `Save current…` opens a second naming/save flow; `Apply` then activates that preset. The three verbs do not reveal their different scopes. | [SET-01-site-save-verbs.png](C:/Users/bear/astro/review-evidence/SET-01-site-save-verbs.png) | Rename to `Apply active site`, `Save as location preset…`, and `Load selected preset`; add a sentence explaining active site vs preset library. |
| SIT-02 | Minor | As Returning Riley, see which saved location survived | The active site's values survive in a new tab, but Saved locations resets to `—`, so the UI no longer identifies the matching preset and disables Apply/Delete. | [SET-05-persisted-site-unselected.png](C:/Users/bear/astro/review-evidence/SET-05-persisted-site-unselected.png) | Keep the matching preset selected or show `Active: UX Review Backyard (matches saved preset)`. |
| SIT-03 | Minor | As Remote Rae, scan compact coordinate values | At 1680 px a latitude input grows to about 1,074 px for a nine-character number, increasing scan distance and wasting most of the panel. | [SET-06-site-1680.png](C:/Users/bear/astro/review-evidence/SET-06-site-1680.png) | Cap numeric field width, align values in a compact grid, and use the remainder for map/provenance/accuracy. |
| SIT-04 | Opportunity | As First-light Fran, understand where the current coordinates came from | Manual, browser-location, mount-GPS, and saved-preset sources can all populate the same fields, but the active site has no provenance or timestamp. | [SET-01-site-save-verbs.png](C:/Users/bear/astro/review-evidence/SET-01-site-save-verbs.png) | Show `Source: manual / mount GPS / browser · updated …` and the precision/accuracy used by Atlas and planning. |

## Settings · Auth and roles

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| AUT-01 | Opportunity | As Remote Rae, verify what a read-only viewer can see | The current configuration has no auth method and therefore grants every client admin. Viewer/operator/admin roles exist, but there is no safe `Preview as viewer` or capability matrix without changing live auth state and creating users. | [SET-04-auth-open-server.png](C:/Users/bear/astro/review-evidence/SET-04-auth-open-server.png) | Add a read-only role matrix and `Preview UI as viewer` mode; keep the existing explicit open-server warning. |

## Monitor

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| MON-01 | Major | As 2 a.m. Oliver, read guiding in night mode | The whole monitor becomes extremely low-contrast; RA and DEC are two solid colored lines that both collapse to similar red, so the chart cannot be decoded without color. | [MON-02-night-guiding.png](C:/Users/bear/astro/review-evidence/MON-02-night-guiding.png) | Use distinct solid/dashed line styles, direct labels, and a higher-contrast red palette with a minimum readable floor. |
| MON-02 | Major | As 2 a.m. Oliver, trust Last frame metrics | Monitor shows `HFR 2.10` without px/arcsec, while Capture distinguishes HFR px and HFR″. The same number changes meaning across screens. | [MON-1680-paused.png](C:/Users/bear/astro/review-evidence/MON-1680-paused.png) | Use one canonical label such as `HFR 2.10 px (3.07″)` everywhere and keep capture settings fully qualified. |
| MON-03 | Minor | As Remote Rae, use a wide monitor | At 1680 px the paused header and progress bar span almost the whole window with large empty regions, while countdowns are pushed into a separate far-right card. | [MON-1680-paused.png](C:/Users/bear/astro/review-evidence/MON-1680-paused.png) | Cap the monitoring column width or form a stable telemetry grid so gaze travel does not scale with screen width. |
| MON-04 | Minor | As First-light Fran, interpret `meridian flip 6:11` | The countdown has no unit/format (`6 h 11 m`, `06:11 local`, or clock time) and no tooltip visible at a glance. | [MON-1680-paused.png](C:/Users/bear/astro/review-evidence/MON-1680-paused.png) | Render `in 6 h 11 m` and add the predicted clock time. |

## Recovery · active sequence and session review

| ID | Severity | As <persona>, I tried to… | What happened | Evidence (screenshot file) | Recommendation |
|---|---|---|---|---|---|
| REC-01 | Blocker | As 2 a.m. Oliver, abort the resumed sequence | Abort ignored repeated clicks in both PAUSED and RUNNING states on Monitor and Plan. Pause worked; Abort never changed state and emitted no error. The review run had to be left paused. | [REC-05-abort-ignored.png](C:/Users/bear/astro/review-evidence/REC-05-abort-ignored.png) | Make Abort reliable, confirm once with the exact consequences, immediately acknowledge `Aborting…`, provide a timeout/fallback, and log an actionable failure. |
| REC-02 | Major | As 2 a.m. Oliver, trust PAUSED to mean quiescent | The UI declared PAUSED at 1/130, later showed 2/130 and then 3/130; the global strip simultaneously continued to say `SEQUENCE RUNNING`. An in-flight frame may finish, but the UI never says “pausing after current exposure.” | [REC-04-paused.png](C:/Users/bear/astro/review-evidence/REC-04-paused.png), [REC-05-abort-ignored.png](C:/Users/bear/astro/review-evidence/REC-05-abort-ignored.png) | Distinguish `Pause requested—finishing frame 2/10` from `Paused`; freeze progress only after the state is actually quiescent, and make the global strip match. |
| REC-03 | Major | As Returning Riley, understand what plan the resumed session is running | The active header says `Sequence · Tonight` and `slewing to M31`, while the same screen's Plan card and target editor show the unrelated long-named M42 draft. | [REC-02-resumed-run-mixed-plan.png](C:/Users/bear/astro/review-evidence/REC-02-resumed-run-mixed-plan.png) | When a session is active, show its immutable session snapshot in the main context; move the unrelated draft behind `Edit another plan` and label it clearly. |
| REV-01 | Major | As Returning Riley, review what happened last night | The frame card shows `HFR 2.23`, `RMS 0.59`, and `223727` without units, date, timezone, or readable timestamp. | [REC-01-session-review.png](C:/Users/bear/astro/review-evidence/REC-01-session-review.png) | Show `HFR 2.23 px`, `RMS 0.59″`, and `2026-07-15 22:37:27 PDT`; retain the compact form only as a secondary label. |
| REV-02 | Opportunity | As Returning Riley, learn why the run stopped and what Resume will do next | The recovery card says only “stopped at 1/130.” Session Review shows an accepted frame but no interruption reason, last completed action, next action, or recovery checklist. | [REC-01-session-review.png](C:/Users/bear/astro/review-evidence/REC-01-session-review.png) | Add a recovery summary: cause/time, last accepted frame, interrupted operation, equipment state changes, and exact next actions on Resume. |

## Intuitive-leap answers by task

### Get the rig connected and take a first image

- **What I expected right here but couldn't do:** see one authoritative connected state, follow a first-light checklist, name/locate the image library, and know exactly what `Center` centers.
- **What NINA / ASIAIR trained this audience to expect:** a connected-device summary, an obvious preview/capture action, camera limits applied to inputs, and a visible capture destination/session.
- **What saves a click, squint, or doubt at 2 a.m.:** take the user directly to Capture after a successful connection, show `Camera ready · Mount ready`, and clamp invalid exposure values before the shutter action.

### Achieve focus

- **What I expected right here but couldn't do:** tap a point for position/HFR/error/frame, understand both drawn lines, and set the effective autofocus filter/gain/binning.
- **What NINA / ASIAIR trained this audience to expect:** measured points plus a named fit, clear quality criteria, backlash/run details, and a final-frame preview that agrees with the result.
- **What saves a click, squint, or doubt at 2 a.m.:** label the final frame and time, add a fit legend, and put `best 19,972 · final 19,972` in one compact result line.

### Plan tonight

- **What I expected right here but couldn't do:** edit or at least verify offset per exposure step, distinguish draft/saved plan/session without inference, and safely resolve a second-tab edit.
- **What NINA / ASIAIR trained this audience to expect:** named sequence templates, per-step camera settings, duplication/reordering, explicit save-as/load, and an immutable running-sequence snapshot.
- **What saves a click, squint, or doubt at 2 a.m.:** a Draft → Saved plan → Session breadcrumb, full plan names at narrow widths, and object-specific verbs.

### Find something to shoot in the Atlas

- **What I expected right here but couldn't do:** find Jupiter or Mars, get a clear no-results explanation, and accept an automatic mosaic suggestion.
- **What Stellarium / SkySafari trained this audience to expect:** solar-system ephemerides, search-as-you-type, time-aware altitude, labeled sky overlays, and equipment profiles feeding FOV.
- **What saves a click, squint, or doubt at 2 a.m.:** one common target search, a labeled time/altitude chart, and `Fit object with 3×3 mosaic` when the object is 6.4× the frame.

### Set where I am

- **What I expected right here but couldn't do:** know whether `Save site` meant apply now or store a reusable preset, and see which preset is currently active after reopening.
- **What NINA / ASIAIR trained this audience to expect:** site profiles with unambiguous Save/Load/Delete, coordinate source, elevation, and the active site shown wherever visibility is computed.
- **What saves a click, squint, or doubt at 2 a.m.:** `Active site: UX Review Backyard · manual · saved`, plus `Save as preset…` and `Load preset` verbs.

### Recover

- **What I expected right here but couldn't do:** abort, know why the interruption happened, and see the exact next operation before resuming.
- **What NINA / ASIAIR trained this audience to expect:** explicit recovery/restart behavior, stage history, skip/retry/abort controls, and a single plan/session context.
- **What saves a click, squint, or doubt at 2 a.m.:** `Stopped during frame 2/10 because …; Resume will restart that frame`, a trustworthy `Pausing after current exposure` state, and an Abort control that acknowledges immediately.

## Top 10 ranked by user pain

1. **REC-01 — Abort does nothing:** the operator cannot reliably stop a resumed sequence.
2. **CAP-01 — Negative exposure accepted:** impossible metadata is generated and displayed as truth.
3. **ATL-01 / MNT-01 — Planets are absent:** the explicit Jupiter/Mars task is impossible across both catalogs.
4. **REC-03 — Active M31 session beside M42 draft:** the screen mixes two different plan contexts during a live run.
5. **EQ-01 — Connection truth conflict:** connected simulator, unassigned devices, and “No rig connected yet” coexist.
6. **REC-02 — PAUSED is not trustworthy:** progress changes and the global strip still says running.
7. **FOC-01 / FOC-02 — Focus truth is internally confusing:** stale bad preview and unexplained fits sit beside `FOCUS — GOOD`.
8. **MON-01 / CAP-02 — Night mode defeats glanceability:** contrast collapses and chart series become color-only red.
9. **PLN-01 — Saved plans are not findable at narrow width:** long names and actions are truncated or undersized.
10. **SIT-01 / PLN-04 — Scope and verbs are unclear:** Save/Save current/Apply and draft/plan/session actions require inference.
