# AstroDeck next front-end - deviations from the design

This is the reviewer-facing record of every place the shipped code (branch
`feat/ui-next`, `fec53970`..`e1740db7`, 124 commits ahead of `main`) departs
from `ASTRODECK-UPDATE/design_handoff_astrodeck_mobile/README.md` and
`GAP-ANALYSIS.md`. Every row was checked against the working tree, not just
against a plan document - a deviation a later commit already closed is not
listed here (see the report that accompanies this document for the ones that
were dropped for exactly that reason). Sources: `ARCHITECTURE.md`, each hub's
own plan (`<scratch>/plan/hub-*.md`, section H or E or F "Deviations and
resolved questions"), `<scratch>/plan/INTEGRATION-BACKLOG.md`, and
`<scratch>/plan/REVIEW-FINDINGS.md`. On 2026-09-10 the owner reviewed every
row; the Decisions section below records what was kept and what was
commissioned as wave 2 work.

Status is binary: **deliberate** (the design asked for something the engine
cannot do, or asked for it dishonestly, and this is the permanent shape) or
**follow-up** (known, scoped, not done - tracked in the Follow-ups list, not
re-litigated here).

## Decisions (2026-09-10 review)

The owner went row by row through every table below. Some rows are confirmed
as the permanent, correct shape; the rest are commissioned as wave 2 work,
tracked here by decision id so later tasks can cite them.

### Kept as shipped

- The Sky lens now holds all 7 kinds (D-SKY-1 shipped); see the Sky table for
  the one part of that decision still open (reticle markers for satellites).
- The advisory quick-session subs math stays advisory, on the condition that
  it must stay correct and beautiful for OSC cameras, where the per-filter
  arithmetic collapses to one channel - verified in wave 2: `resolveColour`
  reads `camera.is_color`/`bayer_pattern` off the status bus first and the
  last frame second, so an OSC camera with no filter wheel gets `MONO - NO
  WHEEL` or `ONE-SHOT COLOUR` correctly rather than a guess (`04cab434`).
- The campaign `night n of ~total` projection stays a projection - the tilde
  stays.
- Monitor's weather block stays tablet/desktop only.
- Night mode keeps the `:root.night` token swap - no blend layer.
- Sticky fatal/UNSAFE toasts stay sticky; everything else keeps the 2.8 s
  toast.
- The local-storage split lands as proposed. Keep local (per-device): sky
  lens/layers/mode/floor/frame-mode/survey-bright (finder view state),
  stretch, mon-level, mon-night, monitor-thumb-brightness, wx-sub (nav
  memory), dl (downloads started from this phone), dlpref (FITS vs JPEG on
  this device), throughput (per-connection EMA), conn-pref (which origin
  this browser prefers), autolock, touch-size, night, coach-seen.

### Commissioned as wave 2, now shipped

Every item below shipped in wave 2 (U7b). Status columns in the tables further
down read **shipped** for these, not commissioned; a row marked **partly
shipped** still carries a live deviation alongside the closed one - read its
Why column, not just this list.

- **D-SKY-1** - Server-side ephemerides for satellites (ISS via Celestrak,
  Tiangong/CSS, and other bright visible satellites such as HST) and comets
  (MPC elements), cached server-side, so the lens dial's Satellites and
  Comets kinds can go live. Closes the Sky table's "the lens dial holds 7
  target kinds" row - PARTLY: the two kinds are real (search, list, passes)
  but `finder/targets.ts SATELLITE_MARKERS` stays `false` (see the Sky
  table), because the two-minute ephemeris cadence is right for a comet and
  wrong for a body crossing four degrees of sky a second. Shipped:
  `16095c08`, `335b6b62`, `a0cd19f8`.
- **D-SKY-2** - Mount the skydome card on the Sky finder itself, in addition
  to the one on the Weather hub. Closes the Sky table's "a full 3D skydome
  card sits on the Sky finder" row. Shipped: `0042e98a`.
- **D-SKY-4** - Fix the em-dash end to end: the server's beginner-example
  match string, `CatalogSearch`'s placeholder, and the tests that pin it.
  Closes the Sky table's "Hyphens, never em-dashes" row. Shipped: `2ac7c9e6`,
  `5901fc59`.
- **D-SES-1** - A real per-channel preview: `?channel=` on `GET
  /api/sequence/stack/preview.jpg`, per-filter accumulators in the stacker,
  and the UI swapping the actual image instead of a CSS tint. Closes the
  Session table's "channel strip lets you view that channel alone" row.
  Shipped: `b1de3afc`, `056722a5`.
- **D-SES-3** - Rebuild the Flows canvas fully in the design's vocabulary on
  this branch, folding `FlowHeader`'s actions into the shell chrome. Closes
  the Session table's "the Flows canvas becomes a full surface" row.
  Shipped: `248efea6`, `badd9ac3`.
- **D-SES-4** - A promote route: the server buffers the last raw frame per
  camera and `POST /api/capture/last/save` writes it with normal naming, so
  the button becomes SAVE TO GALLERY. Closes the Session table's "RE-SHOOT
  AND SAVE" row - the button survives beside it as a secondary action for
  the case the rig is not holding a frame. Shipped: `8d5bea9f`, `b76abfcf`.
- **D-RIG-1** - SER video capture: `POST /api/capture/video {roi, fps,
  exposure_ms, gain, duration_s, format}`, an SER writer, a `video` busy
  lane, and `.ser` file serving, so `VIDEO · PLANETS` mode's readouts,
  RECORD, QUICK STACK, and DOWNLOAD SER go live. Closes the Rig table's
  `VIDEO · PLANETS` capture row, and the Sky table's lock-card RECORD clause
  that used to say the control was honest-locked. Shipped: `cdf2e792`,
  `38465750`, `d4b8756f`.
- **D-RIG-2** - Focuser temperature compensation: a steps-per-C coefficient
  in the focus config and a loop that nudges the focuser between frames,
  plus the sheet's toggle. Closes the Rig table's "Focuser TEMPERATURE
  COMPENSATION toggle" row; the design's dial shipped as a signed
  STEPS PER DEGREE number field (-500..500) instead, because a dial's fixed
  stops cannot express an arbitrary negative coefficient. Shipped:
  `f7f17903`, `20d0d628`.
- **D-RIG-3** - A real dew loop: the controller scales heater power (sensor
  window plus Power dew ports) from the S2 dew margin, with per-port enable
  and manual override. Closes the Rig table's "sensor window heater and
  Power dew heaters follow the dew margin" row. Shipped: `57aafb9f`,
  `f91c58cf`.
- **D-RIG-4** - A relative-offset nudge verb (RA/Dec, 1 arcmin to 10 degrees
  per tap) and a SLEW RATE tile up to the mount's real maximum, raising the
  0.6 deg/s touch ceiling by the user's own safety call for the AM5. Closes
  the Rig table's "SLEW RATE 0.5x...800x tile" row and its "RA STEP / DEC
  STEP" row. The tile and `SlewPad`'s centre cell are one lifted rate state,
  never two. Shipped: `cf839885`, `94e2affb`. The four dials this decision
  put on the mount sheet (SLEW RATE, RA STEP, DEC STEP, TRACKING) shipped
  collapsed to a 2px border in the sheet's flex-column body until the
  whole-branch review's `4be00832` gave `.nx-dial` (and the same-shape
  `.nx-seg`/`.nx-bar`) `flex-shrink: 0`.
- **D-RIG-5** - A `protect_during_run` flag on the switch port config,
  editable per port, with the existing name heuristic surviving as the
  fallback for an engine older than S7h/S7L, which sends no annotation at
  all. Closes the Rig table's "mount/camera/USB locked while a session
  runs" row. Shipped: `d87ad0a4`, `30120e68`.
- **D-WX-1** - An overlay slot and a yaw/geometry callback on
  `SkyDomePanel`/`SkyDome`, so the dome can draw the target path to dawn,
  horizon fill, and +30 min ghost tiles; serves both the Weather dome and
  the new Sky dome from D-SKY-2. Closes the Weather table's skydome-draws
  row and the Follow-ups "SessionStack overlay slot / dome overlay yaw
  hook" bullet. Shipped: `1474867c`.
- **D-SET-1** - New server fields `aperture_mm` and `reducer` on the optics
  config, persisted with the profile with f-ratio derived server-side, plus
  a full audit of every phone-local value in `ui/src/next` to move anything
  that is rig data onto the server. Closes the Settings table's Optics
  APERTURE/REDUCER row. Shipped: `55e5ec80`, `cb9b36cf`.
- **D-SET-2** - A hand-written QR encoder (byte mode, ECC M, tested against
  a known vector) alongside the existing link and copy button. Closes the
  Settings table's "PAIR ANOTHER RIG · QR" row. Shipped: `28c4b376`.
- **D-X-3** - Restyle every legacy panel mounted as-is, in the design's own
  vocabulary: the plan editor, the RotatorCard sheet, and every tuning
  editor (guide algorithms, quotas, safety limits, calibration
  library/tolerances, standards, naming, WCS stamp, sync, restricted
  assets, update, credits), alongside the Flows canvas work tracked under
  D-SES-3. Closes the Rig table's RotatorCard row and the Cross-hub table's
  "rebuilt in the new language" row. Shipped: `ab2769b5` (plan editor),
  `0abca01c` (rotator, as `RotatorPanel`), `7ae8f4ad`, `5836bf52`,
  `ac0ff000`, `c54a1e4b`, `5b8cf692`, `f8b27af6`, `a62d55d2`, `ba29ed23`,
  `0b5ac0b4` (the tuning editors and the remaining device sheets).
- **D-FU-1** - Execute the local-storage audit's proposed split: move
  `astrodeck-next-optics-aux`, `astrodeck-next-sky-quick`,
  `astrodeck-next-sky-site`, and `astrodeck-next-sky-pool` to the server,
  leaving every per-device key local. Closes the local-storage split named
  under Kept as shipped above - all four keys done. Shipped: `854c0ebb`
  (quick, pool, site), `cb9b36cf` (optics-aux, the last of the four).
- **D-FU-2** - Split the bundle: `React.lazy` per hub and per sheet through
  a new registry (`hubs/sheets.ts`'s `SheetEntry {id, load}`, composed into
  `hubs/index.ts`'s `SHEET_REGISTRIES`), with `HubBoundary` as the suspense
  fallback, then re-run the probe. Closes the Follow-ups "No code-splitting"
  bullet. Shipped: `a5bc0230`, `badd9ac3` (the Flows area).
- **D-FU-3** - Give Session > Now an empty state that lists saved plans with
  RUN and each plan's TONIGHT verdict. Closes the Follow-ups "phone path to
  start a plan run" bullet. Shipped: `c634ac12`.
- **D-FU-4** - Four small items on this branch: a gallery chip count, the
  two latent capability under-declarations (ignore-tonight needs
  `view.weather`; horizon SAVE needs `config.site_optics`), a tier-2 read
  in `health.ts` for viewers, and the `ui/package.json` version bump to the
  engine's version. Closes the Follow-ups "Gallery chip carries no count",
  "Latent, non-exploitable capability under-declarations", "`health.ts` has
  no tier-2 read", and "UI version unbumped" bullets. Shipped: `a5bc0230`
  (gallery chip), `f0dbe058` (both latent caps), `90806c3f` (health.ts
  tier-2, wave u7a), and the version bump now on `ui/package.json` (`0.3.28`
  at last count - re-verify against the engine's own version before citing
  the number).

## Sky

| Design says | What shipped | Why | Status |
|---|---|---|---|
| README 1 (line 67): the lens dial holds 7 target kinds | All 7 kinds are real (galaxies, nebulae, clusters, planets, moon, satellites, comets - `finder/targets.ts SKY_KINDS`). Satellites and comets are searchable, listed, and satellites carry a passes card - but neither kind may be drawn on the reticle: `finder/targets.ts SATELLITE_MARKERS` stays `false` | S7L's server-side ephemerides (Celestrak/SGP4 for satellites, MPC elements for comets, cached under `CONFIG_DIR/ephemeris`) closed the missing-data-source half of this row. The reticle-marker half stays open on purpose: a satellite's RA/Dec is geocentric and needs a topocentric alt/az recomputed every second to plot honestly, but the finder's ephemeris poll is `SOLAR_TTL_MS` (120 s) - right for a comet, wrong for a body crossing about four degrees of sky a second; a comet the server could not place topocentrically has no horizon point to draw either. `finder/targets.ts:29-72` | partly shipped (D-SKY-1) |
| README 1 (line 71): "a MOSAIC stage enters the flow" | DONE on a multi-panel framing writes N plan targets sharing a `mosaic_group` via `panelsToTargets`/`addTargetsToPlan`; the flow card shows a synthetic MOSAIC row with the footnote naming the shape. The quick-session route (`GENERATE FLOW`) itself still takes one target | `nodeDefs` has 21 node types and none is `mosaic` - the engine's mosaic mechanism IS N targets sharing a group, not a flow stage (`hub-sky.md` H.6). `SkyHub.tsx` (comment at the `panelsToTargets` call site) and `hubs/sky/sheets/quick.tsx:322-350` | deliberate (previously broken - review #3 found `panelsToTargets` called by one test and nothing else; fixed in `b83751f8`, see Legacy defects below) |
| README 3 (line 83-84): quick-session HOW LONG is a duration dial, subs computed from it | `subs` is PER FILTER and shared across every checked filter (one sub per filter per pass); the phone's duration arithmetic is advisory display math over the engine's real allocation | `FlowQuickBody.subs` is one count, not per-filter (`app.py:1382`, `wizard.py:575`); `cycle.params.plan`/`cycles` cannot express a distinct count per filter at all. `hubs/sky/sheets/quick.tsx:204` (`passesFor`), `hub-sky.md` H.7 | deliberate - confirmed 2026-09-10 |
| README screenshot 09/design: manual VIDEO capture for a locked planet/moon target | The lock card's `RECORD <NAME> · VIDEO` CTA still routes to Rig - Capture in video mode (matches `proto/logic.js:360`) rather than recording inline from the lock card | `hub-sky.md` H.2; `hubs/rig/capture/CaptureScreen.tsx:10-13`. VIDEO mode itself is live now (D-RIG-1, see the Rig table) - the "where the control is honest-locked" clause this row used to carry is no longer true; the deviation left standing is the ROUTE to another screen, not a lock | deliberate - confirmed 2026-09-10 |
| README 1: a full 3D skydome card sits on the Sky finder (prototype only) | A real `DomeCard` sits on the Sky finder, mounting the SAME renderer the Weather hub's dome uses (`SkyDomePanel`) rather than a second implementation; the `SKYDOME ›` pill in the status row now scrolls to it instead of leaving the hub | Two hemisphere renderers would have been two truths about the sky - reusing the one component, with the D-WX-1 overlay slot serving both mount points, avoids that. `hubs/sky/cards/DomeCard.tsx`, `hubs/sky/cards/StatusRow.tsx:10-14,80` | shipped (D-SKY-2) |
| README 1 (line 71): "camera-rotation dial 0-165 deg in 15 deg steps" | Kept as specified (0-165, 15 deg steps) on the Sky hub's FRAME dial; the Atlas's own manual stepper (0-360, step 5, tablet/desktop) is untouched | A sensor at 180 deg frames identically to 0, so 12 stops cover every distinct framing; the two dials serve different surfaces and both write the same `framing.rotation_deg`. `hub-sky.md` H.4 | deliberate |
| README 1: empty-lock card titled "NOTHING IN THE RETICLE" | Card reads "NO CATALOGUE TARGET HERE" (the prototype's later wording), with `IMAGE THIS PATCH` | The two titles contradict each other on one card - a button that images what is in the reticle cannot sit under a title saying there is nothing there. Copy rule "every string must carry information the pixels do not" agrees. `hub-sky.md` H.5 | deliberate |
| README ground rule (line 8): "Hyphens, never em-dashes, in UI copy" | `components/atlas/CatalogSearch`'s placeholder now reads `"Search catalog - e.g. M 31"`, hyphen intact, and a copy-rules test keeps it that way | The reason originally recorded here was wrong, per the 2026-09-10 review: no server string ever matched the placeholder character-for-character. `squash_designation` (`server/astrodeck/catalog/objects.py:187-198`) strips `[^a-z0-9]+` before comparing, so it is punctuation-blind - an em-dash, a hyphen, or a stray NBSP in the placeholder all resolve to the same match. The fix was purely cosmetic and cost nothing the widget had. `hubs/sky/sheets/targets.tsx:31-36` | shipped (D-SKY-4) |
| README "Formulas to lift": wind direction as a bearing | `weather.now.wind_dir_deg` (Open-Meteo `winddirection_10m`, the direction wind comes FROM) is converted `+180 % 360` before it reaches `advect()`, which wants a TOWARD bearing | Getting this backwards points every drift arrow the wrong way while still looking plausible; `hub-sky.md` H.8 pins a seeded DOM test (225 deg -> NE arrow) against it | deliberate |
| GAP-ANALYSIS 6: "Either the engine gains ephemerides... or those kinds are hidden until it does" | Resolved as above (7 kinds, D-SKY-1); additionally the useSolarSystem hook itself had a real bug, now fixed | `hubs/sky/finder/model.ts:298-306` - reading `rows` instead of `results` off `GET /api/catalog?q=` meant no planet or the Moon ever reached the finder; `sheets/targetsModel.ts` already read `results` correctly. Fixed - see Legacy defects below | deliberate (post-fix) |
| N/A (wave-2 wire fact) | `GET /api/satellites/passes` 409s on a rig with no site set, and the body is a bare string (`SITE_UNSET_NOTE`) rather than a coded refusal - so `sky-passes-retry` offers a retry for a condition retrying cannot fix. The card should read the note and point at Settings, not repeat the request | `server/astrodeck/catalog/ephemeris/satellites.py:87,390`. `hubs/sky/cards/PassesCard.tsx` | follow-up |

## Session

| Design says | What shipped | Why | Status |
|---|---|---|---|
| README 4 (line 92): six phase-pill words (SLEWING/FOCUSING/GUIDING/CAPTURING/PAUSED/DONE) plus the incident states | Eleven pills: the six, plus SOLVING, COOLING, WAITING, STOPPING, ERROR, NINA IS DRIVING | The engine publishes those states (`engine.py:2432,4525,1810,971,1457`; `SequenceState.state` includes `aborting`, `nina_native`) - a pill reading CAPTURING through a cooling gate is the UI lying. `hub-session-capture.md` H, D1 | deliberate |
| README 4 (line 91): campaign `night n of ~total` | `total` carries a tilde and is a projection (3.5 clear-hours/night from the prototype), not a server value | `server-routes.md` 4.13: "there is no persisted goal-per-filter object returned by the server." D2 | deliberate - confirmed 2026-09-10 |
| README 4: night-strip clouded nights marked amber | Marked with the word "held", not only a color/glyph | Night mode collapses warn/bad toward the same red; status must never be hue- or glyph-only. D3 | deliberate |
| README 4 (line 95): channel strip lets you "view that channel alone" | A tap on a chip re-fetches the image with `?channel=<key>`, answered from the stacker's own per-filter accumulator (`imaging/sessionstack.py:858-935 channel_preview`) - the picture really is that channel alone, not the composite with a CSS tint over it. The chip set is `status.channels[].channel` (the stacker's own keys: R/G/B/L/Ha/Oiii/Sii), never the wheel's filter name, because an unmapped wheel name folds onto L and a chip built from it would silently show the wrong picture under the right label | `api/sessionStack.ts sessionStackImageUrl`; `hubs/session/now/ChannelStrip.tsx`. D4 | shipped (D-SES-1) |
| N/A (implementation note, not a design gap) | The server answers a channel request with `X-Stack-Channel` (the resolved key) among other response headers - the UI reads none of them. An `<img>` element cannot see response headers, so the resolved channel and its frame count are read from `status.channels[].frames` (the cheap status poll) instead, never assumed from the image response | `X-Stack-Channel`/`X-Stack-Frames` are real and correctly set server-side; recorded here so nobody "fixes" the UI to read a header an `<img>` cannot reach. `api/sessionStack.ts:116-120` | deliberate - not a defect |
| README 4 (line 95): SOFT/AUTO/HARD stretch segmented control | Writes `localStorage["astrodeck-next-stretch"]`, not `store.stretch` | `store.stretch` is the live-preview B/M/W transfer function (`types.ts:656-664 StretchParams`); hijacking it would change the Inspect histogram too. D5 | deliberate |
| README 5 (line 105): "DOWNLOAD N SUBS · size with Wi-Fi vs relay ETA" | A `~38 MB/s` fixture is not shipped; a measured EMA throughput (`filesData.ts` `noteThroughput`, `EMA_ALPHA`, keyed by `via`) drives the ETA, falling back to bytes-only until a transfer has been measured on this connection | A plain `<a download>` exposes no live progress to simulate against; the fixture number would be a lie the first time it ran on a real link. `hubs/session/sheets/files.tsx:332-365`, `filesData.ts:460-538`. D6, D9 | deliberate |
| ARCHITECTURE 8 / `GAP-ANALYSIS` 1: FITS gated on admin | Files sheet locks FITS behind "FITS originals need syncer or admin access" | `accessPhrase("view.media")` (`ui/src/lib/caps.ts:79-119`) - a syncer genuinely holds `view.media`; hand-written role copy is the exact defect class `accessPhrase` exists to prevent. `hubs/session/sheets/files.tsx:354`, pinned by `__tests__/filesDom.test.tsx:15-18`. **Contract amendment**: `ARCHITECTURE.md` section 8 said "admin access" and has been corrected in place. D7 | deliberate |
| README 5: download the selected filters as a zip | A partial filter selection falls back to a `picked` selection policed by `downloadPlan`, with an "ALL N INSTEAD" escape when the pick is too large | iOS serves one file at a time in the foreground, so N separate zips is not an option; `PICKED_URL_BUDGET = 6000` refuses rather than silently truncating. D8 | deliberate |
| README 6 (line 109): Gallery card footer, legacy wording included "you clear them from the tablet" | That sentence is dropped | The phone now has its own DELETE on the card, so the sentence would be false. D10 | deliberate |
| README (platform, line 25): "the Flows canvas becomes a full surface" at tablet/desktop, in the new language | The cutover replaced `FlowsView` (whole) with a rebuilt canvas (`FlowsCanvasHost.tsx`): the canvas opens on a FLOW, never a library - the hub's `FlowsScreen` is the one library at every breakpoint - and `FlowHeader`'s duplicated chrome (wordmark, provider badge, library count) is gone; the rebuilt toolbar carries only what the shell does not already render. The cutover shipped with no save path at all - `FlowHeader`'s own door (save, clear, reload the library) had no equivalent, so MY FLOWS, the FLOWS chip, the browser Back button and the phone stage sheet all discarded an edited draft silently; the whole-branch review's `ab0ff78a` routes every exit through one save door with an in-flight guard, an explicit SAVE and a SAVED/UNSAVED EDITS/READ ONLY pill, and locks RUN while the draft is dirty (the engine compiles the stored record, not the canvas) | `hubs/session/flows/FlowsCanvasHost.tsx:1-19` (the cutover's own account of what it replaced and why); `canvas/FlowCanvasToolbar.tsx`, `FlowStagesPhoneSheet.tsx` (the save door). D11 | shipped (D-SES-3) |
| README 4: "Suggest settings" action on Capture | Stays on the Rig - Capture screen; its copy names the Camera sheet as the destination for the photometry inputs it used to hold inline | Sub-length is a capture-time decision; the underlying photometry inputs moved to hub 4's Camera sheet, so the string has to say where they went rather than pretend they are still here. D13 | deliberate |
| README 9 (line 131): result card offers "SAVE TO GALLERY" for a captured-not-saved frame | SAVE TO GALLERY is real now: the engine holds the last unsaved frame per camera, `GET /api/capture/last` tells the card whether one is buffered, and `POST /api/capture/last/save` writes those exact pixels - a `frame_id` interlock refuses if a later exposure has replaced it, rather than writing a frame the operator never saw. RE-SHOOT AND SAVE stays beside it as the fallback once the buffer is empty or on an older engine | `hubs/rig/capture/ResultCard.tsx:1-24`, `api/capture.ts`, `hub.py:2942-2949` (the promote route). D14 | shipped (D-SES-4) |
| README: the `view.media` note belongs to the preview toolbar area | The note sits **under** `PreviewToolbar`, not inside it | The toolbar takes no capability prop; forking it to add one would fork the download-gating logic that is otherwise kept in lockstep with the server's retention constants. D15 | deliberate |
| README: Inspect mode shows histogram/stars/stats for any frame | Inspect on `src=stack` (the live composite) shows neither - the composite JPEG carries no `PreviewInfo` | Fabricating stats for a JPEG that never had per-pixel data would be the "constant-where-it-should-vary" defect this repo already has a name for. D16 | deliberate |

## Rig

| Design says | What shipped | Why | Status |
|---|---|---|---|
| README 8 / GAP-3: Camera BINNING `1x1 / 2x2 / 3x3` | Powers of two, `1x1 / 2x2 / 4x4` | `status.camera.max_bin` defaults to 4 (`types.ts:185`); GAP-ANALYSIS 3 itself: "4x4 exists in AstroDeck - design offers 1/2/3; use 1/2/4". E1 | deliberate |
| README 8: Camera `USB` readout | Fourth tile is `E-GAIN` (`status.camera.egain` / `egain_learned`) | No `usb_limit`/`usb_bandwidth`/`BandWidth` field anywhere under `server/astrodeck`; E-GAIN is real and gives the e-/ADU learn loop a home. `hubs/rig/sheets/camera.tsx:22-23,357-360`. E2 | deliberate |
| README 8: Focuser `TEMPERATURE COMPENSATION` toggle, "-14 steps per C" | A `TEMPERATURE COMPENSATION` card: a switch, a signed `STEPS PER DEGREE` number field (-500..500 - not a dial, because a dial's fixed stops cannot express an arbitrary negative coefficient), the sign and effect stated in words, REFERENCE/NOW/NEXT MOVE readouts straight off the wire, an ANCHOR action, and an ADVANCED disclosure for max-step and deadband. The `REFOCUS AFTER n C` trigger stays beside it unchanged, with the precedence between the two stated verbatim from the server's own string | `focus/tempcomp.py` (config + rule table); `hub.py:6758-6771` (status_node). `hubs/rig/sheets/focuser.tsx:1360-1507`. E3 | shipped (D-RIG-2) |
| README 8: `AUTOFOCUS RUNS WHEN` chips (filter change / every 60 min / HFR +15%) | Filter change -> `SHIFT BY FILTER OFFSET ON A CHANGE` switch (the engine shifts by the stored offset instead of refocusing). Every-N-min -> read-only `REFOCUS EVERY n FRAMES`, sourced from the plan. HFR+15% -> read-only `HFR GATE xn -> <action>`, tapping to Safety | `standards.apply_filter_offsets` (`config.py:902-904`); `SequencePlan.autofocus_every` counts FRAMES per-plan (`sequence/models.py:282`); `escalation.hfr_reject_factor`/`hfr_reject_action` is a frame-quality gate, not a refocus trigger. E4-E6 | deliberate |
| README 8: Mount `TRACKING: sidereal / lunar / solar / king / off` | Four stops - `king` dropped | `TRACKING_RATES` is `("sidereal", "lunar", "solar")` with the comment `ASCOM's "King" rate is out of scope (YAGNI)` (`devices/base.py:236-239`). E7 | deliberate |
| README 8: Mount `SLEW RATE 0.5x...800x` tile | A `SLEW RATE` dial, one stop per named rate up to the driver's own ceiling (GUIDE / 8x SID / 0.5 deg/s / the ceiling itself, labelled by its real number - `1.44 deg/s` on the AM5), lifted into one state that `SlewPad`'s centre cell shares rather than editing a second copy | `/api/mount/move` now reads the ceiling off the connected driver (`getattr(tel, "max_rate_deg_s", None)`, `app.py:6026-6039`; the AM5N reports 1.44 deg/s, `devices/sim.py:916`) instead of the fixed `TOUCH_MAX_RATE_DEG_S = 0.6` touch cap. 800x (about 3.34 deg/s) is still not offered - it exceeds the driver's own maximum, not merely the old touch cap. `hubs/rig/sheets/mount.tsx:683-698`, `lib/slewController.ts`. E8 | shipped (D-RIG-4) |
| README 8: Mount `RA STEP / DEC STEP 1'...10 deg` per pad tap | Two `RA STEP` / `DEC STEP` dials (1 arcmin .. 10 deg) drive a real relative-offset nudge verb; the pad's existing tap model (fixed pulse or brief rate move, including the NINA relative-goto branch) is unchanged for what the new verb does not cover | `POST /api/mount/move` gained the relative-offset path; `hubs/rig/sheets/mount.tsx:700-719`, `api/mount.ts nudgeMount`. E9 | shipped (D-RIG-4) |
| README 8: Mount `offset from target - RA 0 - Dec 0` | Shows `mount.pointing.error_arcmin` ("measured at the last solve") or "offset unknown - run SOLVE + SYNC" | The engine publishes no cumulative pad-offset state; the prototype's field is fixture-only. E10 | deliberate |
| README 8 (line 126): five safety bars (rain/wind/cloud/power/humidity) with red limit ticks, always drawn | One bar per key actually present in `reading.detail`; ticks only where config supplies a limit; a single status row when `detail` is absent | `SafetyReading` is `{is_safe, reason, source, detail?, stale, ts}` with no fixed key set, and `SafetyConfig` carries no wind/cloud/humidity thresholds - the monitor device owns them. `hubs/rig/sheets/safety.tsx:9-12`. E11 | deliberate |
| README 8: chain `STOP CAPTURE -> PARK -> CLOSE -> WARM COOLER -> NOTIFY`, always drawn in full | Nodes built from config (`on_unsafe`, `close_dome_on_unsafe`, `cooling.warm_ramp`, whether a sink exists); unlit nodes dim with their own reason | The chain is config-conditional, not a fixed pipeline. E12 | deliberate |
| README 8: Power `mount/camera/USB locked while a session runs` | The lock is the engine's own answer now: `power_guard.py` holds the name pattern as the DEFAULT rather than the rule, and every port row carries `protected_now` plus a tri-state `protect_during_run` (null = follow the port's name and keep following it through a rename; true/false = an operator's explicit override, which survives a rename). The name-heuristic (`/mount\|camera\|usb/i`) survives as exactly one thing: the fallback for an engine older than S7h/S7L that sends no annotation at all - that row keeps the shipped sentence, not the new one ("clear the protection... in Power settings" - there is nothing to clear on that engine) | `power_guard.py:16-27,67,73-75`; `hubs/rig/lib/portSettings.ts:1-45`; `hubs/rig/sheets/power.tsx:1-45`. E13 | shipped (D-RIG-5) |
| README 8: Guider `DITHER BETWEEN SUBS` on/off, "every N subs" | A `DITHER n PX` stepper (0 = off) plus a read-only "every n subs - set per night in the plan" line and a `DITHER NOW` action | Cadence is per-plan (`SequencePlan.dither_every`); distance is rig-level (`config.guide.dither_pixels`, `config.py:486`) - two different scopes the design drew as one toggle. E14 | deliberate |
| README 8: Guider `AGGRESSION` / `MIN MOVE` as single dials | The dial writes BOTH axes through `validateGuideSettings`/`toSnake`, with the note "Sets both axes. Per-axis values are in the tuning editor" | They are per-axis params (`ra_params`/`dec_params`) under the hood. E15 | deliberate |
| README 8: Guider STAR tile `SNR 48 - mag 8.1 - 3.1 px` | Renders `snr` only; magnitude and size are dropped | Only `snr` is on the stats bus - the rest would be invented. E16 | deliberate |
| README 8: `CameraDial` fan-out over the polar reticle | `PolarQuickBar` pickers carry the same `solve`-scope writes | There is no preview stage in the Polar sheet to hang a `CameraDial` over. E17 | deliberate |
| README 8: polar summary "checkmark aligned to X'"; rotator "warning PA n deg is outside..." | Glyphs dropped, text unchanged | House rule: no glyphs, plain text only (global no-emoji rule extends to status glyphs). E18 | deliberate |
| README 8: filter wheel drawn with exactly 7 slots | Radius `clamp(floor(74*sin(PI/n)) - 3, 12, 22)`; a list fallback above 12 slots | Wheels report `names.length`, which is not always 7. E19 | deliberate |
| README 8: "Offsets are steps relative to L" | "relative to `<reference slot name>`" | The reference slot is `ref_slot`, a configurable field, not always L. E20 | deliberate |
| GAP-3: filter wheel row needs a `type` column, including "blackout" | Derived: `opaque -> blackout`, `narrowband -> narrowband`, else `broadband` | No type enum exists on the wire (`server-routes.md` 4.2); this also lands GAP-3's missing blackout type. E21 | deliberate |
| README 8: "ADD A DEVICE scans for INDI - Alpaca - ASCOM" | Copy reads "scan this computer for USB, Alpaca and ASCOM drivers" | There is no INDI backend anywhere in the server. E22 | deliberate |
| README 8/GAP-9: sensor window heater and Power dew heaters "follow the dew margin from Weather" | Both halves are real now. A FOLLOW DEW switch on the camera sheet arms the sensor-window heater against the S2 dew margin, with a DEW RAMP disclosure (full power at / back to min at / min power / max power / hand-set override minutes) and a live loop line; the hand stepper stays live as an override that expires after the configured minutes rather than racing the loop. Any writable switch port with its own `follow_dew` flag is driven the same way, scaled into that port's own range, never a shared percentage. The whole-branch review added a RESUME FOLLOWING button beside the hand stepper (`camera.tsx:798-802`), calling `POST /api/dew/resume` (`app.py:6881`, `control.power`) to clear a per-surface manual override early instead of waiting it out; an older engine's 404 retires the button and names the hand-set way out instead | `hubs/rig/sheets/camera.tsx:670-829` (E23, sensor window); `hubs/rig/sheets/power.tsx` port rows (E24, switch ports); server `dew.py` loop, `config.py DewConfig`. E23-E24 | shipped (D-RIG-3) |
| README 8: camera cooling curve as a smooth exponential | Plots the sensor temperatures this client has actually received; fewer than 3 points shows the setpoint line plus "building the curve" | The smooth curve is a prototype fabrication. E25 | deliberate |
| README 14 (fixture): camera spec line naming a ZWO ASI2600MM | Built from the driver's own `describe()` fields; any field the driver did not report is dropped rather than guessed | The design's line is a fixture, not read from a real driver. E26 | deliberate |
| README 8: polar home/park row "set - counterweights down" | `mount.parked` + `can_find_home`, or "no home sensor" | The design's line is a fixture with no engine backing. E27 | deliberate |
| GAP-2: "Missing - rotator" sheet | `RotatorPanel` (`hubs/rig/rotator/`), rebuilt in the design's own vocabulary; `RotatorCard`'s presentation logic was re-implemented rather than forked blind, so there is still one copy of the behaviour | `hubs/rig/rotator/RotatorPanel.tsx`, mounted from `hubs/rig/sheets/rotator.tsx:45,117`. E28 | shipped (D-X-3) |
| README 8/11: Profiles as a popover holding rename/update-from-rig/import/export | Popover = quick switch + save + delete; a `profiles` sheet mounts the existing `ProfileList` for the rest | Those actions do not fit a popover's interaction shape. E29 | deliberate |
| Legacy `NotConnectedInterstitial` per view | Folded into the FIRST NIGHT card (four rows) on Rig - Devices; other hubs get the design's dashed browse banner instead | The new IA has one Rig screen where the legacy UI had several equipment views. E30 | deliberate |
| Legacy `BackendLinkGrid` table | Its per-role connection reason is preserved as the device row's third line | `BackendLinkGrid` is a Settings/Connection-shaped surface, not part of the new Rig - Devices IA. E31 | deliberate |
| N/A (cross-cutting) | `lib/humanize.ts`'s busy-lane sentences still name "the Mount page" / "the Focus page" / "the Rig page" at two call sites | The busy-lane and diagnostic sentences have been repointed to action/hub-neutral language; "the Rig page" is kept on purpose - the new IA has a Rig hub, and the comment at `ui/src/lib/humanize.ts:132` says so. E32 | resolved |
| README 9/20 (line 131): `VIDEO · PLANETS` capture - readouts, RECORD, QUICK STACK, DOWNLOAD SER | `VIDEO · PLANETS` is live: a ROI picker (whole sensor or a centred 1024/640/320 window, aligned to `camera.roi_align`), FPS/exposure/gain/duration controls with a byte estimate and a clamp note, RECORD with a live progress bar, QUICK STACK (a lucky-imaging pass over the written file), and a video library sheet with DOWNLOAD SER per recording | `POST /api/capture/video {roi, fps, exposure_ms, gain, duration_s, format}` (`video_routes.py:96`), a `video` busy lane plus a separate `video_stack` lane for the stack pass, `.ser` file serving. `hubs/rig/capture/video/*.tsx`, `api/video.ts`. D12 | shipped (D-RIG-1) |
| N/A (wave-2 wire fact) | `store.ts`'s `video` slice stays `VideoEvent \| null` - a STRICT SUBSET of `GET /api/capture/video`'s `VideoState` with no `roi`, `actual_fps`, `clamp_reason` or `camera` - rather than widened to the full shape; `VideoMode.tsx` widens it itself, merging the narrow bus tick over its own cold GET (`videoModel.ts mergeVideoState`) | Writing nulls into the store for fields the bus event does not carry would be the "publish 0/null as a fallback" mistake the camera status node's own comment already warns against; every OTHER consumer of `store.video` would then have to know those fields are meaningless there. Accepted: one screen does the merge, not the store | `store.ts:592-604`, `hubs/rig/capture/video/videoModel.ts:275-286`. | deliberate |
| N/A (wave-2 wire fact) | `POST /api/capture/video` takes no `target` field, so a recording's `.ser` carries no OBJECT header card at all - unlike a still capture, which names the locked target | The route was designed for planetary/lunar work at the bench, where "what is this of" is a filename decision the operator makes on download, not a header the rig can know without a target lock the route does not require. `api/video.ts VideoStartBody` | follow-up |
| N/A (wave-2 wire fact) | The `video_stack` busy lane is a boolean edge on `status.busy_lanes` (present/absent), not a numeric progress - QUICK STACK shows a busy state, never a percentage or an ETA for the stack pass itself | `hub.py:2101` publishes `busy_lanes` unreduced. `hubs/rig/capture/video/videoModel.ts:406-412 laneBusy` | deliberate |
| N/A (wave-2 wire fact) | The focuser's `tempcomp.py` `status_node` carries no `clamped` / `delta_steps` fields, so a move the server capped at `max_step_per_move` reads on the NEXT MOVE tile exactly like an unclamped one - only `last_reason` hints at it, and only after the move has already happened | `focus/tempcomp.py` status_node; `hubs/rig/sheets/focuser.tsx:1414-1419 tempcomp-next` | follow-up |
| N/A (wave-2 wire fact) | The last-frame promote buffer (D-SES-4) is not on the status bus: a capture from another client (a second tab, the classic UI) leaves SAVE TO GALLERY's offer stale on this screen until the Rig - Capture sheet remounts and re-fetches `GET /api/capture/last`. An EMPTY buffer answers `nothing_to_promote` (or `already_saved` when the id matches what was last saved) rather than the more specific `frame_id_mismatch`, which only fires when a DIFFERENT frame is still live in the buffer - so a client cannot tell "nothing was ever here" from "what was here got saved and cleared" without the `already_saved` case landing right. `promotable_summary` (`GET /api/capture/last`) never carries `saved_path` either way, by design - it describes the unsaved buffer, not history | `hub.py:2942-2960,2988-3003`, `hubs/rig/capture/lastFrame.ts` | follow-up |
| N/A (wave-2 wire fact) | The dew loop's `interval_s` (how often it re-evaluates the margin and adjusts power) has no control anywhere in the UI - the loop runs on the server's own cadence and nothing on the camera or power sheet lets an operator change it | `config.py DewConfig.interval_s`; `hubs/rig/sheets/camera.tsx` DEW RAMP disclosure (no interval field) | follow-up |

## Weather

| Design says | What shipped | Why | Status |
|---|---|---|---|
| Legacy: `"Weather is off — enable it in Settings → Connect."` | `"Weather is off." "Turn it on in Weather settings."` with a CTA to the Weather settings sheet | The old copy names a route ("Settings -> Connect") that no longer exists in the new IA. `hubs/weather/conditions/verdict.ts:123-124`, used by both `ConditionsScreen.tsx:210` and `RadarScreen.tsx:52`. `hub-weather-monitor-settings.md` F.4 | deliberate |
| README 7 (line 113): the skydome draws "target path to dawn... horizon fill... +30 min ghost tiles" when dragged | `domeOverlay.tsx` now draws all of it: the target's path to dawn, horizon fill, and +30 min ghost tiles, plus the wind compass rose and legend it always had | `SkyDomePanel` gained an `overlay` slot and forwards the canvas's own paint geometry (`DomeOverlayArgs`: `{cssW, cssH, cx, cy, r, tiltDeg, yawDeg}` - the numbers the panel's own paint used, including the yaw it privately owns via `onYaw`), so the overlay draws at the SAME rotation the dome is showing rather than a fixed yaw of 0. One overlay, handed to both the Weather dome and the Sky finder's `DomeCard` (D-SKY-2). `components/cloudmap/SkyDomePanel.tsx:59-77,245-253`, `hubs/weather/dome/domeOverlay.tsx`. `hub-weather-monitor-settings.md` F.6 | shipped (D-WX-1) |

## Monitor

| Design says | What shipped | Why | Status |
|---|---|---|---|
| README 10: Monitor's Live screen carries a weather block unconditionally | Monitor's weather panels (Sky Conditions + Radar) render only at tablet/desktop | On a phone the WEATHER hub is one tab away and the design gives Monitor no weather block there; the `view.weather` caller-gate is unchanged, the breakpoint gate is additive. `hubs/monitor/live/LiveScreen.tsx:505` (`bp !== "phone" && canSeeWeather`). `hub-weather-monitor-settings.md` F.10 | deliberate - confirmed 2026-09-10 |
| Legacy: `LogDrawer` owns `openLog()`/`closeLog()` as a mount/unmount side effect of the drawer opening | The Log screen (`#/monitor/log`) itself calls `openLog()` on mount and `closeLog()` on unmount | `LogDrawer` is not mounted under `NextApp`; `logOpen` is otherwise only an `unseenError`-reset flag, which this preserves without a drawer. `hub-weather-monitor-settings.md` F.11 | deliberate |
| README 10: the radar/weather panel is always live when mounted | `LiveScreen` mounts `RadarMap` only when `weather.enabled` (`radarOff = !weather \|\| !weather.enabled`), matching the gate `RadarScreen.tsx` already used | Mounting the radar behind a dead weather feed fired a grid of `/api/weather/tile/...` requests every one of which 404s, on a timer, for as long as the screen stayed mounted. Fixed in `3f10681c` - see Legacy defects below | deliberate (post-fix) |

## Settings

| Design says | What shipped | Why | Status |
|---|---|---|---|
| README 11: Optics sheet's APERTURE and REDUCER dials persist with the rig's optics config | Both dials are part of the same `Optics` draft the focal length lives in, saved by the same `PUT /api/optics` press; the live f-ratio reads the server's `optics_computed.f_ratio` once saved. A phone that had `localStorage["astrodeck-next-optics-aux"]` migrates it once through `migrateKey`: PUT when the rig has no aperture yet, discarded - never merged - when it does, with one info toast on the conflict | `ui/src/types.ts:1102-1125` (`Optics.aperture_mm`, `.reducer`); `hubs/settings/sheets/OpticsSheet.tsx`, `opticsModel.ts`; `next/lib/storageMigration.ts`. `USE THE REDUCED FOCAL LENGTH` is still the only action that changes what the rig frames - `reducer` is recorded, never multiplied in. `hub-weather-monitor-settings.md` F.1 | shipped (D-SET-1) |
| README 11 (line 139): connection radio `DIRECT · RIG WI-FI / RELAY · ANYWHERE / HOME LAN` | Two cards (DIRECT, RELAY), not three, and not radio buttons - they are links/status cards, not a selectable control | "The one honesty fix on this screen": a radio implies the app applies the setting; it cannot - DIRECT vs RELAY is which network the browser itself is already on. `HOME LAN . ETHERNET` is dropped as a third option because nothing distinguishes it from DIRECT (same origin, different cable) and "a radio that cannot be wrong is not a control". `hubs/settings/sheets/ConnectionSheet.tsx:3-13`. F.2 | deliberate |
| Legacy: `astrodeck-autolock` (`setTouch({autoLockMs})`) is read by `TouchGuard.tsx` but no screen anywhere writes it | Settings > PHONE > AUTO-LOCK gives it a UI for the first time | A persisted setting the engine honors with no screen to change it is exactly the broken-promise shape this codebase has a taxonomy for. `hubs/settings/general/PhoneGroup.tsx:249-257`. F.3 | deliberate (new capability, not a loss) |
| Legacy: `StandardsPanel`'s footer link calls `useStore().setView("sequence")` | Still calls the same store action; `legacyBridge.ts` maps `store.view` changes to routes, landing it on the Session hub's `planEditor` sheet | The store action is ungated and still works, but the legacy root it used to navigate is not mounted - `legacyBridge.ts:54` (`sequence: "/session/flows/planEditor"`) is the bridge that makes it land correctly instead of silently doing nothing. F.5 | deliberate |
| README 11 (line 139): "PAIR ANOTHER RIG · QR" | A QR code renders beside the existing link and copy button: a hand-written encoder (byte mode, ECC level M), tested against a known reference vector so a wrong-bytes pairing code never ships silently | `hubs/settings/sheets/QrCode.tsx`. F.7 | shipped (D-SET-2) |
| README 11: `AUTO-SWITCH` toggle between DIRECT and RELAY | A 30 s down-link banner with a switch-CTA; the app does not silently move itself between origins | The prototype has no switching algorithm, and the client cannot silently move the browser between origins without risking in-flight work or the session itself. F.9 | deliberate |

## Cross-hub / shell

| Design says | What shipped | Why | Status |
|---|---|---|---|
| README (design tokens, line 192): toasts are 2.8 s, uniformly | Sticky (`ttl 0`) toasts - specifically the sequence-fatal error - stay until dismissed; success/info keep 2.8 s | An UNSAFE/fatal toast must not vanish on its own; `shell/Toasts.tsx:7-8` states this as a deliberate deviation. ARCHITECTURE section 5 already names it | deliberate - confirmed 2026-09-10 |
| README (design tokens, line 193): night mode is a `mix-blend-mode: color` red layer over the whole screen | The app's existing `:root.night` token swap (`index.css`) is reused; every primitive reads correctly under it, with shape/text carrying state (never hue alone) | The house mechanism already exists and a second night-mode implementation would be two sources of truth for the same toggle. ARCHITECTURE section 6 | deliberate - confirmed 2026-09-10 |
| README (platform, line 25/27): "rebuilt in the new language" at tablet/desktop for the flows canvas, plate-solve view and limit editing | The Flows canvas, the plan editor, and every tuning editor (guide algorithms, quotas, safety limits, calibration library/tolerances, standards, naming, WCS stamp, sync, restricted assets, update, credits) are now rebuilt in the design's own vocabulary, not mounted as legacy panels inside new chrome | Wave 2's largest single item. The whole-branch review found this row itself stale (ARCHITECTURE section 11 still narrated a hand-written list of ~35 things "mounted as-is", and this row claimed that list was "already shortened" when it had not been touched) - both are fixed now: section 11 points at `ui/src/next/__tests__/r7Parity.test.ts`'s `KEEP_AS_IS`/`HELPER_ONLY` tables as the enforced answer instead of narrating a list by hand. See "Whole-branch review (2026-09-10)" below | shipped (D-X-3) |

## Server

| Design says | What shipped | Why | Status |
|---|---|---|---|
| `ARCHITECTURE.md` section 12 (original): S5's frame row carries `path` | The frame row omits `path` entirely (`{id, ts, bytes, accepted, override, hfr, stars, guide_rms, thumb}`) | `SessionFrame.path` is redacted below `config.backend` (admin-only) - shipping it would render blank for the Files sheet's real audience (operator/admin), and nothing needs it as a join key. `server/astrodeck/sequence/session_files.py:146-158`. **Contract amendment**: section 12 has been corrected in place; REVIEW-FINDINGS #67 says explicitly not to "fix" this back in | deliberate |
| Original interim state (H.9 in `hub-sky.md`): a single global `config.safety.horizon` polyline stands in for per-site horizon until Wave S lands | S1 landed; the Sites and Horizon sheets read/write real per-site `horizon_points` via `PUT /api/locations/{id}` and `POST /api/locations/{id}/apply` | `hubs/sky/sheets/sites.tsx:126-369`, `horizon.tsx:76-121` | resolved (not a current deviation - noted here only because the plan called it out as temporary) |

## Follow-ups

Closed since the first draft: the two horizontal-overflow bugs (fixed in `3f10681c`, `3fb692d8`, `3b248396`), REVIEW-FINDINGS #72-74 (`archive.tsx` now uses `useCanControlCapture` for REBUILD PREVIEWS; `files.tsx` gates the JPEG viewer at `view.preview`), and the page-naming copy sweep (`troubleshoot.ts`, `FocusView`, `ConnectionBanner`, `GuideView`, `PowerView` reworded; `humanize.ts` keeps "the Rig page" deliberately because the new IA has a Rig hub - see the comment at `humanize.ts:132`).

Also closed, in wave 2: the UI version bump (`ui/package.json` now `0.3.28`, D-FU-4), code-splitting (`React.lazy` per hub and per sheet through `SHEET_REGISTRIES`, D-FU-2), the gallery chip count (`hubs/index.ts` `galleryCount`, D-FU-4), the RotatorCard restyle (now `RotatorPanel`, D-X-3), the dome overlay's yaw hook and overlay slot (D-WX-1), the phone path to start a saved plan (Session > Now's empty state, D-FU-3), `health.ts`'s tier-2 read for a viewer (`90806c3f`, wave u7a), and both latent capability under-declarations (`incidentActions.ts` `cap2: "view.weather"`; `sky/sheets/horizon.tsx`'s SAVE now locks on `config.site_optics` too, `f0dbe058`). See the Decisions section above for the commit shas.

Known and not done on this branch, in no particular priority order:

- **Inline styles not yet moved into `next.css`.** Several hub tasks reported inline-styled surfaces (capture, inspect, files/gallery) that `next.css` does not yet have `nx-*` classes for; `INTEGRATION-BACKLOG.md`'s "Must do in the integration wave" names the specific files.
- **Satellite reticle markers need a per-second position source.** `finder/targets.ts SATELLITE_MARKERS` stays `false` (see the Sky table's 7-kinds row); every branch behind a `true` value is written, but the constant is where that work starts, not where it lands. A `SkyHub.tsx` block that would apply it was drafted during T-U7b-1 and deliberately NOT committed - it is unreachable with markers off, and there is no anchor for it in the tree today. Next wave: either a server `/api/satellites/position` per-second route, or client-side SGP4 over the cached elements.
- **`targetsModel.ts` duplicates `useSkyModel`'s ranking, and the two have already diverged.** The finder's hook ranks comets; the Suggested-targets sheet's own module does not (`sky/sheets/targetsModel.ts:1-20` states why it cannot reuse the hook directly - mounting it twice would open the AR camera and the cloud-dome poll a second time). One owner should merge the ranking logic behind two thin views next wave.
- **`next/lib/planning.ts` imports the legacy readers from `hubs/sky/finder/prefs.ts`** (`readLegacyPool`/`readLegacyQuick`) - a `lib` -> `hubs` import, backwards from ARCHITECTURE section 1's directory-ownership rule. Deliberate for this wave (the migration needs to read the old keys before deleting them) and scheduled for deletion together with `prefs.ts`'s legacy readers next wave, per T-U7b-11's own header.
- **`PlanningRead.error` is rendered nowhere, by design.** `usePlanning()` returns an `error` field set on a failed write, but no consumer (`quick.tsx`, `coords.tsx`, `QuickDefaultsSheet.tsx`) reads it - a failed write falls back silently to the pre-write state rather than surfacing a banner. Left as a deliberate gap for this wave; worth a toast next wave if silent write failures turn out to be common.
- **The simulator rig still publishes `camera.video_path: "none"`.** `devices/sim.py` carries `burst_supported`/`max_fps` as camera attributes, not through the capability negotiation the real adapters use, so VIDEO mode never goes live against the sim rig - `test_status_bus_s7` pins the current (honest) sim answer. A sim video path is a later wave's work, not this one's.
- **`catalog/ephemeris/elements.py` binds `CONFIG_DIR` at import time** (`ELEMENTS_DIR = CONFIG_DIR / "ephemeris"`, `elements.py:51,58`), so a config-dir override after import (the isolated probe server, a test fixture) does not move it; tests repoint it by hand today. The root fix is resolving `CONFIG_DIR` at call time instead of at import - a server-side task, not owned by this document's tables.

## Legacy defects found and fixed on this branch

Pre-existing defects the branch's review found and this branch's own commits
fixed (not design deviations - genuine bugs, several predating this branch):

- **Guide drawer whole-block PUT.** The classic `GuideView.tsx` drawer (and the
  guide-assistant's Apply) sent six fields to `PUT /api/guide/settings`, which
  the server persists wholesale - resetting `dither_pixels`,
  `recover_guiding`, exposure/gain/binning/offset and
  `recalibrate_after_pier_change` to defaults on every save. Both now read the
  live block and spread the edit over it. Fixed in `2beb42f9` (17 tests,
  including a 385-line save-path test).
- **Quick flows commanding a fixture position angle.** `wizard.quick` left the
  node vocabulary's shipped `rotation: 23.4` in place on the TARGET node and
  only replaced name/ra/dec; `compile.py` read that 23.4 as a real position
  angle, so every quick-session flow quietly commanded a connected rotator to
  PA 23.4 regardless of the framing the user actually chose. Fixed in
  `b83751f8` (also the commit that wired the mosaic panels into the plan; `-1`
  is `to_plan`'s own "no angle constraint" sentinel, used when there is no
  real framing).
- **Forecast-hold false claim.** Multiple surfaces (`App.tsx`, `NextApp.tsx`,
  `SkyConditionsPanel.tsx`, `SessionsPanel.tsx`) said "Auto-resume will hold
  unless ignore weather tonight is set." The engine's auto-resume veto is
  RAIN-only and fail-open on a stale forecast (`server/astrodeck/weather.py`
  `veto_reason`); cloud holds are a separate, in-run mechanism. All surfaces
  now carry the rain-only, fail-open wording. Fixed across `50a2c4a4` (the
  last two surfaces + page-naming reasons) and earlier work on the same
  string.
- **Permissions-Policy blocking the app's own origin.** The server sent
  `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(),
  usb=()` - an empty allowlist that blocked the API for every origin,
  including same-origin, so the Sky finder's AR camera mode, the photosphere
  and "fill from phone" geolocation could never work in any browser regardless
  of device permissions. Now scoped to allow this origin. Fixed in `3f10681c`.
- **Monitor snapshot leaking the site to viewers.** The monitor snapshot (and
  the REST/WS status payloads) carried the meridian-flip block unredacted to
  every role. Fixed alongside the longitude leak below, in `a34338cf`.
- **`meridian.hours_to_flip` leaking the rig's longitude to a viewer.**
  `_compute_meridian` derives the flip countdown from the site's longitude
  (`lst_hours(site["longitude"])`); `meridian` sits at the top level as a
  sibling of `mount`, not inside it, so the key-name filter that correctly
  stripped `mount.alt`/`az` never reached it - a viewer's `GET /api/status` or
  the `/ws` `status` event recovered the rig's longitude to within ~120 m from
  one authorized request, and the flip status leaked latitude coarsely too
  (`flip_unnecessary_over_pole`). `server/astrodeck/api/redact.py` now strips
  `hours_to_flip` to null and collapses site-derived statuses to "unknown" for
  principals without `view.site_derived`, on REST, WS, and the monitor
  snapshot alike. Fixed in `a34338cf` (40 new tests).
- **Relay fence for locations.** Location mutations (apply, and the
  write-through into `config.safety.horizon`) reached the site config over the
  relay tunnel, past the LAN-only fence every other site-mutating route
  already enforced. Locations now join that fence. Fixed in `a34338cf`.

Commit hashes are from `git log --oneline main..HEAD` on `feat/ui-next` as of
this document.

## Whole-branch review (2026-09-10)

Nine independent reviewers ran across the whole branch after wave 2 shipped
(the scratchpad's `REVIEW-FINDINGS-W2.md`, routed to fixers by file); this
section is the fix wave's own record.

### P0s found and closed

- **Dew margin leak via `camera.dew_heater` and the `follow_dew` port
  value.** A principal without `view.weather` could read the dew loop's own
  duty cycle under two other names - the camera node's `dew_heater` register
  and the `value` of any `follow_dew` switch port - and invert the S2 ramp to
  recover the dew margin to about 0.2 C, the exact quantity `view.weather`
  exists to withhold. Both are now stripped while the loop is following, on
  REST, WS and `GET /api/switch/ports` alike, through one predicate
  (`server/astrodeck/api/redact.py:167` `dew_is_following`, `:228`
  `_strip_camera_dew`, `:328` `_redact_switch_ports_for`). Fixed in
  `e1740db7`.
- **Temperature compensation undoing its own filter offsets.**
  `_apply_temp_comp` computed an absolute focuser target from a reference no
  filter-offset move ever updated, so every filter change was undone at the
  next frame boundary - a frame shot at the previous filter's focus while the
  log said both moves had happened. The engine now shifts the reference by
  the offset it just applied (`server/astrodeck/sequence/engine.py:5447`
  `_shift_temp_comp_reference`, called from `_apply_filter` at
  `engine.py:4793`). Fixed in `e1740db7`.
- **`MoveAxisBody.rate_deg_s` accepted NaN and infinities**, and the min/max
  clamp turned a NaN into the mount's full driver ceiling with the deadman
  armed. `rate_deg_s` now carries `Field(..., allow_inf_nan=False)`
  (`server/astrodeck/api/app.py:1057`) and the 422 handler survives a
  non-finite input. Fixed in `e1740db7`.
- **Flows had no save path.** The rebuilt canvas cutover (D-SES-3) replaced
  `FlowHeader`'s save-then-reload door with nothing: MY FLOWS, the FLOWS
  chip, the browser Back button and the phone stage sheet all discarded an
  edited draft silently. Every exit now goes through one save door with an
  in-flight guard, an explicit SAVE, and a SAVED/UNSAVED EDITS/READ ONLY
  pill; RUN is honest-locked while the draft is dirty because the engine
  compiles the stored record, not the canvas. Fixed in `ab0ff78a`.
- **Flows RUN on a list row ran the previously loaded flow.** A press
  awaited `flowsOpen(id)` and then ran whatever was already loaded when that
  open failed silently, so pressing RUN on flow B could `POST
  /api/flows/A/run`. RUN now re-reads which flow actually landed after the
  open and posts nothing, with a toast, on a mismatch. Fixed in `ab0ff78a`.

### P1/P2 closed per area

Counts below are `REVIEW-FINDINGS-W2.md`'s own per-reviewer headers (nine
reviewers total: P0 x5, P1 x22, P2 x~46 - some defects were flagged
independently by more than one reviewer, e.g. R4 and R9 both caught
`next/lib/planning.ts`'s missing write-sequence guard, so the per-area counts
below do not sum cleanly to that total). Everything listed is closed:

- R1 server ephemeris/config/planning: 3 P1 + 6 P2, closed in `83370181` and
  `e1740db7` (the `config.f_ratio` dead-code P2, and half of the
  `active_location_id` P2 - `update_location`'s write-through - landed with
  the three server P0s rather than with the rest of R1).
- R2 server imaging/loops/devices (excluding its 2 P0s, counted above): 3 P1
  + 4 P2, closed in `e1740db7`; the UI-side halves of two of them (the
  nudge-clamp toast, the dew RESUME FOLLOWING button) shipped in `c028f075`.
- R3 server app.py/hub.py spine (excluding its 1 P0, counted above): 2 P1 +
  6 P2, closed in `e1740db7`.
- R4 sky + weather: 5 P1 + 8 P2, closed in `65c36a2a`.
- R5 session flows (excluding its 2 P0s, counted above): 4 P1 + 6 P2, closed
  in `ab0ff78a`.
- R6 session now/plan/report/gallery: 5 P1 + 8 P2, closed in `e8a954e4`.
- R7 rig: 1 P1 + 4 P2 (+ 3 counted, non-severity items - two undocumented
  debounce timers and an uncleared safety receipt), closed in `c028f075`.
- R8 settings + monitor (including the cross-cutting relay-fence P1 that
  FIX-U-gate closed first): 3 P1 + 6 P2, closed in `4ac9c0d8` (the gate
  itself) and `6c17482b` (settings/monitor's own fenced writes).
- R9 next lib/ui/shell + legacy additive: 1 P1 + 5 P2 (including the
  hubMeta sheet-id parity test and the docs-pass item), closed in `4ac9c0d8`,
  `65c36a2a`, and this update (the docs pass - section 11 above and the
  Cross-hub row's correction).

### Leftovers recorded as deliberate or next-wave

- `components/flows/flowsSlice.ts:402` sets `flows.run.phase = "running"`
  and is still the only writer app-wide; nothing resets it on its own.
  Session > Now now clears it before a start
  (`ui/src/next/hubs/session/now/NowEmpty.tsx:357-369`, reading
  `isRunPhaseLive`) rather than trusting it to expire. The slice's own reset
  (on the sequence idle edge) is next-wave, additive work on a legacy file.
- Satellite reticle markers still need a per-second position source.
  `finder/targets.ts SATELLITE_MARKERS` stays `false`; the passes CTA case
  now exists and is locked (`ui/src/next/hubs/sky/SkyHub.tsx:801` `case
  "passes"`, `:881` `satLock`), so the marker flag is the only switch left
  once a position source (a server `/api/satellites/position` route, or
  client-side SGP4 over the cached elements) lands.
- 442 em-dash hits remain in legacy modules the new UI imports. Out of scope
  for this additive-only wave, and the modules are shared with `#/classic`;
  recorded as the honest next sweep rather than swept silently into this
  wave's count.
- `PlanningRead.error` is still rendered nowhere. `usePlanning()` sets it on
  a failed write; none of `quick.tsx`, `coords.tsx` or
  `QuickDefaultsSheet.tsx` reads it, so a failed write still falls back
  silently to the pre-write state. Deliberate for this wave; worth a toast
  next wave if silent write failures turn out to be common.
- The simulator rig still publishes `camera.video_path: "none"`.
  `server/astrodeck/devices/sim.py:462-463` carries `burst_supported`/
  `max_fps` as plain attributes, not through the capability negotiation the
  real adapters use, so VIDEO mode never goes live against the sim rig. A sim
  video path is later-wave work.
- `server/astrodeck/catalog/ephemeris/elements.py:63` still binds
  `ELEMENTS_DIR = CONFIG_DIR / "ephemeris"` at import time, so a config-dir
  override after import (the isolated probe server, a test fixture) does not
  move it. The root fix - resolving `CONFIG_DIR` at call time instead - is
  server-side work, not owned by this document's tables.
- The passes route's semaphore refuses, rather than queues, a second
  concurrent satellite/comet search: `server/astrodeck/catalog/ephemeris/routes.py:55`
  `_PASSES_GATE = asyncio.Semaphore(1)` answers a second caller 409
  `passes_busy` (`:122`) instead of making it wait. Accepted as the honest
  shape for now (`sky-passes-retry` reads the code and offers a retry, never
  a spinner with no ETA to show) rather than building a request queue for a
  search two operators rarely run at once.
