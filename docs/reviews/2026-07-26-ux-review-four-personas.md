<!-- Four independent persona-driven UX reviews of AstroDeck, run 2026-07-26 against
commit 1317670 on four ISOLATED server instances (one config dir each, so the novice
got a genuine cold start and no reviewer could contaminate another).

Each reviewer drove the live UI with Playwright, wrote screenshots, and READ them - the
findings come from looking at rendered pixels, not from reading source. Alongside that,
an automated harness measured WCAG contrast (alpha-composited against ancestor
backgrounds), overlapping text rects, sub-44px touch targets, sub-12px text, truncation,
unnamed controls and horizontal clipping, across phone/tablet/desktop and both themes.

130 screenshots reviewed, 100 raw findings, deduped and triaged here into
MEASURED DEFECT / BLOCKED TASK / TASTE. Personas: novice OSC first night; OSC user
switching to mono + filter wheel (the only one who did NOT reach their goal);
professional; professional designer. -->

# AstroDeck — Consolidated UX Review

Four personas (novice / mono / pro / designer), 130 screenshots reviewed, three viewports, both themes, five sequence runs. All four independently reported **zero console errors, zero page errors, zero failed requests**. That is the headline context for everything below: almost nothing here is a crash or a broken API. Nearly every defect is *presentation and plumbing* — the engine is repeatedly more correct than the screen that reports on it.

Convergence is high. Three of four personas independently measured the *same* Mount clipping bug to the same pixel (779 vs 732, GOTO at x=787→850). Two independently found the same filter-identity corruption from opposite ends (plan editor vs FITS header). Two found the preflight modal unusable on tablet with different symptoms and different root causes. Where four people driving four different journeys land on the same rect, that is not opinion.

---

## Part 1 — The five systemic problems

### S1. Overlays do not respect the viewport, and there is no recovery gesture
*Underneath findings #3, #4, #22, #29, #40, and the event-log and MORE-sheet craft items. Hit by all four personas.*

Every overlay surface in the app is broken in the same family of ways, on different screens, for at least two distinct root causes:

- **Wrong containing block.** Mono measured `.dim-content` computing `filter: brightness(1)` — an always-on filter makes that element the containing block for any descendant `position: fixed`, so the preflight's `fixed inset-0` overlay resolved to y=-1018, height=1902 against a 900px viewport. Novice measured the setup wizard computing `position: relative` despite carrying `fixed bottom-0 inset-x-0`, i.e. an unlayered rule beating a Tailwind utility. Different causes, identical symptom: the dialog lands wherever the document happens to be, not where the eye is.
- **No viewport clamp.** Pro measured the same preflight at x=-10 (and a child at x=-80) on tablet — the entire READY / NOT-NEEDED status column off the left edge, labels reading "mera", "ount", "iding", "rizon", "cuser", "sk".
- **No escape hatch.** `document.body` computes `overflow: hidden` while `documentElement.scrollHeight` (1541) exceeds `innerHeight` (1180). So when an overlay lands off-screen there is literally no gesture that reveals it. Mono had to fire the click from the browser console. Novice pressed "OPEN THE SETUP GUIDE" and nothing visibly happened.
- **No opaque surface, no scrim.** Designer found the phone MORE sheet and the event-log drawer stacking with log text legible straight through menu rows; mono found the frame-review drawer with AUTOMATION toggles reading through frame metadata; designer found the filter modal leaving the bright red STOP and cyan LIGHT chips as the loudest things on screen while the dialog held focus.

**Why this is the top pattern:** the two most consequential dialogs in the product — the one that teaches a first-time user what to do, and the one that gates a six-hour unattended run — are both members of this broken class, on the stated primary device. Fixing the overlay primitive once (portal outside the filtered subtree, clamp to viewport, opaque surface, sticky footer inside `max-height: 100dvh`) closes five separate findings.

### S2. Tablet portrait is a second-class layout, and it is the primary field device
*Underneath findings #3, #6, #7, #31, and several minors. Hit by all four.*

The pattern is mechanical: `main` is `overflow-x: hidden`, several view grids carry min-widths that exceed the 820px-minus-rail content box, and the document itself cannot scroll horizontally (`document.scrollWidth == clientWidth`). Content past the right edge is therefore *permanently unreachable by any finger gesture*. Mono proved the content exists by forcing `scrollLeft = 190` from JS.

- Mount: 47px of overflow — the entire GOTO column and the CENTER AFTER SLEW toggle (three personas, identical measurements)
- Plan with a target loaded: 184px of overflow — dither, refocus cadence, meridian flip, cool-to-temp, count=accepted, SAVE AS…, Import
- Settings: the tab strip clips AUTH with no fade, chevron or scrollbar (two personas)
- Preflight modal: off the left edge

The same views are fine at 1440. This is not a design failure, it is missing `min-width: 0` and missing narrow-width column collapses. The rule to adopt: **if `main` is `overflow-x: hidden`, then nothing may ever be wider than `main`** — that combination is a promise that everything fits, and it is currently a false promise on four views.

### S3. Night mode recoloured the pixels but not the information encoding
*Underneath findings #14, #15, and several minors. Hit by novice, mono, pro, designer — all four.*

The team clearly understands the technique. Designer found the proof: `.led-on` is an 11×11 circle, `.led-off` an 11×2 dash, `.led-bad` a **square** — shape-encoded status that survives a monochrome palette intact. Tonight's difficulty badges do the same (filled vs half-filled). Padlock glyphs on disabled controls. `PAUSED` / `ABORTED` / `stale` as words, not colours.

And then every place where meaning is carried by hue alone, the red collapse destroys it:

| Distinction | Day | Night | Ratio |
|---|---|---|---|
| STOP vs LOOP (Capture) | `rgb(255,84,112)` vs `rgb(232,236,247)` | `rgb(255,143,143)` vs `rgb(255,122,122)` | **1.15:1** |
| RA vs DEC trace (Guide) | `rgb(0,210,255)` vs `rgb(255,180,84)` | `rgb(255,58,58)` vs `rgb(255,102,51)` | **1.22:1** |
| Selected vs unselected filter chip | purple border + cyan text | selected 5.5:1, unselected 8.2:1 | **inverted** |

Two of those are safety-critical and one is diagnostic-critical. Worse, designer found the token itself inverted: `--bad` in night resolves to `rgb(255,143,143)`, which is *paler* than body text and far paler than `--accent` — **danger is currently the least alarming red in the palette.** Novice independently observed the flip side on Mount: the full-width STOP block is the highest-luminance object on the entire dark-adapted screen.

Both chart traces measure `stroke-width: 1.5px` with `stroke-dasharray: none`. There is no second channel anywhere.

**Rule to adopt:** any distinction that must survive night mode needs a non-hue channel — dash pattern for traces, fill/weight/glyph for danger, fill for selection. And cap the luminance of large danger surfaces rather than filling them.

### S4. The system knows the truth and shows something else
*Underneath findings #1, #2, #8, #11, #12, #13, #21, #28, and more. Hit by mono and pro hardest.*

This is the pattern that costs the most trust, because in every case the correct answer already exists server-side:

| The system knows | The screen says |
|---|---|
| Wheel is physically at L; FITS writes `FILTER = L` | Report: `filter: null`. UI: `—`. Bundle folder: `M27/NoFilter/`. CSV: empty |
| `/api/safety/state` = unsafe, rain detected | Preflight: green **READY**, no Safety row at all |
| Engine's `_flip_armed` latch: no flip owed (acquired west) | Monitor: permanent red **⚠ FLIP DUE**, every run, never resolves |
| `GuideStats.is_arcsec` exists; `InstructionsPanel` already respects it | Plan gate hardcoded "max guide RMS (arcsec)", engine compares pixels, log prints `″` regardless |
| `/api/site/sky` returns `place_hint: "N hemisphere · E longitude · ~Asia"` | Nothing renders it. A US longitude saved as +110.3 EAST, propagated into every `SITELONG`/`AIRMASS` |
| Engine aborted, parked the mount, closed the roof | Report `safety_events: [{action: "pause"}]`, no roof event |
| Plan editor computes `Ha 1h 40m / OIII 1h 40m / SII 1h 40m` | Session card: `15/18`. Per-band owed: unavailable |
| `/api/catalog/tonight` returns transit_unix, best_window, moon_sep_deg | Tonight renders none of them |
| `/api/reports`, `/bundle.zip`, `/frames.csv` all routed and populated | No nav entry on tablet or desktop |
| Weather monitoring is off, nothing is watching the sky | "✓ Night looks OK" |

This is not a features problem. It is missing wires between a correct backend and the display — and it produces a product that is *more trustworthy than it appears* in some places and *less trustworthy than it appears* in the dangerous ones.

### S5. The product is configured for someone who already knows what to change
*Underneath findings #4, #5, #18, #20, #24, #34, #35, #42, and the whole novice journey.*

Two halves of the same thing.

**Defaults assume expertise.** Save-FITS-to-library: OFF. `count_mode`: `attempts`, so rejected frames consume your requested count. Every quality gate (min stars, max guide RMS, max eccentricity, HFR spike flag): 0/OFF. Alert sinks: `[]`. Dead-man URL: `""`. Auto-resume-at-dusk: off. Filter on a new Light step: "no filter" — on a rig with a wheel connected. Flat step: 120s, no filter, TARGET ADU 0. Weather monitoring: off. Each is individually defensible; together they mean **a first unattended night silently delivers less than the user asked for, with no notification when it breaks and nothing on disk from the Capture page.**

**Vocabulary assumes expertise, and the screens built to fix that are the broken ones.** Cold start is twelve "— unassigned —" slots plus `autofocus unavailable: native engine present but no camera + focuser connected` and `no ASTAP — simulator solver`. The setup wizard that would fix this renders off-screen. The only forward CTA from choosing a target lands on a 20-row automation wall (dither / meridian flip / max eccentricity 0–1). Disabled controls carry genuinely excellent reasons — delivered exclusively through the HTML `title` attribute, which never fires on the touch device the product is designed around. The Guide view tells you to set focal length "in Optics"; there is no Optics panel anywhere in the app. The page is called Rig in the copy, Equipment in the nav, and "GO TO RIG" on the button.

The frustrating part is that this product *can* write beautifully for beginners — Tonight, FOCUS MY SCOPE, the RBAC role descriptions, "a wet scope beats a crushed one", the WCS explanation in Settings. None of it is on the path.

---

## Part 2 — Ranked findings

Ordered by expected cost to a real user. Data loss and lost nights first; tablet weighted above desktop. Tags: **[DEFECT]** measured/reproducible · **[BLOCKED]** intended task could not be completed · **[TASTE]** defensible judgement.

### Tier 0 — loses a night or corrupts data

**1. Filter identity disagrees three ways; "no filter" is the default on a rig with a wheel** — [DEFECT] [CONFIRMED] — hit by **mono + pro independently**
Mono: selected SII on Capture, ran a plan whose Light step read "no filter". Session recorded `filter: null`, preflight said "Filters — NOT NEEDED", files landed as `Light_M13_SII_..._0001.fits`. Pro, from the other end: FITS `FILTER = L`, report `filter: null`, report UI `—`, bundle folder `M27/NoFilter/4s_g100_bin1`, `frames.csv` filter column empty. Every `+ STEP` starts at "no filter".
*Cost:* the stacking bundle — the feature that is supposed to save an hour — hands your stacker the wrong flats, silently, and the gradients land in a finished image. Two personas hit this without coordinating. **Fix:** resolve "no filter" to the wheel's actual position at capture time, key `by_filter`/bundle grouping off that (same source as the FITS keyword), and make preflight *fail* — not "NOT NEEDED" — on a Light step with no filter while a wheel is connected.

**2. Preflight declares READY in green while the safety monitor reads UNSAFE** — [DEFECT] [CONFIRMED] — pro
Sim safety set unsafe (`is_safe: false`, "rain detected"), verified. Preflight rows are Camera / Mount / Guiding / Cooling / Horizon / Focuser / Filters / Calibration / Exposure / Disk. **There is no Safety row.** Header chip: green READY. Starting the run produced 2 frames before the 3-poll debounce paused it.
*Cost:* an unparked mount and an open roof under rain for the duration of the debounce plus one exposure, on a rig the operator cannot reach. A go/no-go screen that omits the go/no-go input is worse than no screen.

**3. The preflight dialog is unreachable or unreadable on tablet** — [BLOCKED] [CONFIRMED] — **mono + pro, different symptoms**
Mono (820×1180, after the normal scroll to reach RUN SEQUENCE): CANCEL/RUN SEQUENCE at y=-90, bottom=-42, `inView: false`, page scroll-locked, checklist half-cut at the right edge. Reproduced on desktop 1440×900 too (buttons at y=922, vh=900). Root cause measured: `.dim-content` `filter: brightness(1)` capturing the `fixed inset-0` overlay. Pro (clean load ×2): panel at x=-10, largest child at x=-80/right=620 — the whole status column off the left edge.
*Cost:* mono could only start a three-filter narrowband night by firing the click from the console. Pro would have pressed RUN blind or gone indoors. This is the single action that starts a six-hour run.

**4. The first-run setup wizard renders off the bottom of every viewport and cannot be scrolled to** — [BLOCKED] [CONFIRMED] — novice
`.panel.sheet-enter`, computed `position: relative` despite `fixed bottom-0 inset-x-0`. Rect {x:-16, y:1164, w:380, h:397} at vh 1180 (tablet); {x:0, y:844} at vh 844 (phone); {x:-16, y:884} at vh 900 (desktop). `body { overflow: hidden }` while `scrollHeight` 1541 > 1180 — no gesture reveals it. Pressing "OPEN THE SETUP GUIDE" from the Capture empty state visibly does nothing (screenshot 81).
*Cost:* the novice's stated give-up point is ninety seconds into the first run. The 5-step checklist with per-step copy and CTAs exists and is invisible on first load on all three viewports, and stays invisible when the user explicitly asks for it. Root-cause hypothesis (`.panel { position: relative }` beating the utility) is **NEEDS-REPRO in source** — the measurement is confirmed, the culprit rule is inferred.

**5. Frames are not saved by default and nothing warns you** — [DEFECT] [CONFIRMED switch state / NEEDS-REPRO consequence] — novice
`<button role="switch" aria-checked="false" aria-label="Save FITS to library">` on a fresh install, with no help text, no (i), no mention anywhere in the capture flow or the setup checklist.
*Cost:* a beginner presses FIRST LIGHT then LOOP, watches pictures appear all night, and finds out the next morning. Note: pro's *sequence* runs wrote frames to disk correctly, so this is specific to the Capture page path — the novice did not verify the disk, so "my first picture was never written anywhere" is inferred from the switch state, not observed. The default is still wrong regardless.

**6. Mount TARGET CATALOG is clipped off the tablet's right edge with no way to reach it** — [BLOCKED] [CONFIRMED ×3] — **novice + pro + designer, identical measurements**
Viewport 820; `main` scrollWidth 779 vs clientWidth 732, `overflow-x: hidden`; grid 763px inside a 700px container; every GOTO rect x=787→right=850, leaving a 33px sliver of a 63px button reading "GO…". CENTER AFTER SLEW sliced in half — you cannot read its state, let alone set it. Document cannot scroll horizontally either.
*Cost:* the button that points the telescope is amputated on the primary field device, and the toggle deciding whether the mount plate-solves and re-centres after a slew is hidden. Pro only got it pressed because Playwright forced a scroll a finger cannot.

**7. With a target loaded, Plan's entire right column is off-screen on tablet portrait** — [BLOCKED] [CONFIRMED] — mono
`main.scrollWidth` 916 vs clientWidth 732. "meridian flip (German mount)" at x=957→993 in an 820px window; "dither every N frames" at x=929→993. Horizontal wheel leaves `scrollLeft` at 0; forcing `scrollLeft=190` reveals the content. With an empty plan the same view is fine — the target card's width is the trigger.
*Cost:* dithering, refocus cadence, meridian flip, cool-to-temp and count=accepted are unsettable on the tablet — precisely the settings a multi-night narrowband project depends on. Mono had to go indoors to a desktop.

**8. The session report records the safety action as "pause" when the run aborted, parked and closed the roof** — [DEFECT] [CONFIRMED] — pro
Observed: state → aborted, "cloud sensor: overcast — closing roof", dome shutter open → closed. Stored report: `end_reason: "unsafe"`, `safety_events: [{reason: "cloud sensor: overcast", action: "pause"}]`, no roof event. Cause located: `engine.py:1626` calls `record_safety(reason, act)` with `act = cfg.safety.on_unsafe` *before* the dome-close branch escalates. The report UI renders the reason with no action at all.
*Cost:* the morning-after artefact actively misinforms. It says we paused for cloud; the night ended and the roof shut. For a hosted remote rig that is a billing and a facility-safety question.

**9. The event log is a 200-entry in-memory ring buffer with no persistence and no export** — [DEFECT] [CONFIRMED] — pro
`GET /api/logs?limit=5000` → exactly 200 rows, oldest 53 minutes back; the "rig connected" entry from 29 minutes earlier had already fallen off. `events.py:27`: `deque(maxlen=history)`, nothing written to disk. The log sheet has two controls: close × and "Troubleshooting guide →".
*Cost:* on a ten-hour run the log emits one line per frame — 200 entries is roughly the last forty minutes. The 01:15 cloud pause, the 02:40 re-calibration and the 03:10 reconnect are gone by 07:00, and a restart or auto-update wipes what's left. The persisted per-frame report covers *outcomes* but not the narrative, so you can see that frames stopped and not why. **This is the product's own stated value proposition failing.**

**10. On night 2 the prominent action is RE-RUN PLAN; RESUME is buried three screens down** — [DEFECT/hierarchy] [CONFIRMED] — mono
Banner: "SEQUENCE · NGC7000 SHO — ABORTED · 15/18 frames" offering RE-RUN PLAN / EDIT PLAN / VIEW LOG, no resume link. The working RESUME is in a SESSIONS panel at y=1413 of a 2027px page in a 900px viewport — and on tablet portrait that is the column clipped by #7.
*Cost:* on 300s narrowband, re-shooting 15 subs is 75 minutes of clear sky burned before you notice. The resume machinery itself is excellent (see Part 4) — it is just hidden behind the wrong default action.

**11. The frame-reject gate labelled "arcsec" compares against a value in pixels** — [DEFECT] [CONFIRMED BY CODE / behaviour NEEDS-REPRO] — pro
Label hardcoded "max guide RMS (arcsec)" (`SequenceView.tsx:1051`). Guide view on the same tablet: "RMS is in guide-camera pixels. Set the guide scope's focal length in Optics to report arcsec." Default `optics.guide_focal_length_mm = null`. `engine.py:2426` returns `guider.stats().rms_total` with no unit check; `engine.py:2473` logs with a literal `″` either way. `GuideStats.is_arcsec` exists and `InstructionsPanel.tsx:584` already respects it — the engine gate ignores it.
*Cost:* type 1.5 meaning arcsec, get ~3.2 arcsec of real error on a typical 240mm/3.76µm guide scope — a gate twice as loose as asked for, and a rejection log that reports the threshold in the wrong unit so you cannot tell which way you were wrong. Pro did not observe a wrong rejection in the field; the code path is unambiguous.

**12. Observing site: buried, unverified, and it fabricates a confident night plan from 0°,0°** — [DEFECT + BLOCKED] — **novice + pro**
Novice: OBSERVING SITE at scroll offset 1485 of a 3168px Settings page, inside a tab called CONNECT, below BACKEND DRIVERS / NINA / Alpaca / PHD2. Before setting it, Tonight ranks 20 targets with "↑84°" figures and Atlas draws "ASTRO-DARK 12:25–21:50" and "TRANSIT 15:35" — a plan for a point in the ocean, warned about only by a small orange line. The blocking dialog that finally stopped a slew pointed at Settings but did not navigate there, and its glowing default button was **SLEW ANYWAY**.
Pro: hemisphere is a chevron-less `<select>` in a 40×40 box defaulting to N/E. A US longitude entered as 110.3 saved as +110.3 EAST with a "Site saved" toast. `GET /api/site/sky` returns `place_hint: "N hemisphere · E longitude · ~Asia"` — declared in `types.ts:1104`, rendered by nothing. The wrong sign then propagated into `SITELONG`, `OBJCTALT` and `AIRMASS` in every delivered FITS.
*Cost:* silently poisons twilight times, meridian timing, horizon gating, visibility ranking, and delivered file headers. The one string that would catch it in half a second is already computed.

**13. Session reports and exports have no entry point on tablet or desktop** — [BLOCKED] [CONFIRMED, mono's stronger claim corrected] — mono + pro
Mono swept all 12 views, the event log and the review drawer for report/bundle/csv/zip and found zero anchors, concluding the reports were unreachable. Pro found the truth: `NavMoreSheet.tsx:53` defines `{id: "report", label: "Reports"}`, and enumeration gives **desktop 1440 → Reports visible: false; tablet 820 → false; phone 390 → inside the MORE sheet.** So the feature is reachable — from a phone overflow menu only, and pro only found it by reading the source.
*Cost:* the whole PixInsight hand-off (bundle.zip, frames.csv, per-filter hours, rejected-frame list) is behind a phone-only door, for a task performed at a desk on a big screen every morning of the year. Mono's "there is no way" is **not literally true** and should be recorded as "no way on the devices where you'd do it".

### Tier 1 — blocks a task or breaks trust

**14. Night mode destroys the danger encoding on every abort control** — [DEFECT] — **novice + pro + designer**
See S3 for the numbers. Capture's STOP is 1.15:1 from LOOP, same 90×56 size, same fill, borders 0.05 alpha apart; PAUSE and ABORT on the phone Monitor are two matching outlined boxes. Novice adds the inverse failure on Mount: the pale-pink full-width STOP is the *brightest object on a dark-adapted screen*. Hold-to-abort is the saving grace and all three said so.

**15. Guide chart RA and DEC traces are encoded by colour alone; night collapses them to 1.22:1** — [DEFECT] — designer
Legend RA = `var(--accent)`, DEC = `var(--warn)`. Both paths `stroke-width: 1.5`, `stroke-dasharray: none`.
*Cost:* telling RA drift (periodic error, wind) from DEC excursion (backlash, polar misalignment) is the only reason the chart exists, and in the mode you actually use at 2am it cannot answer that.

**16. Device assignments are lost on reload before CONNECT RIG is pressed** — [DEFECT] [CONFIRMED] — mono
11 rows ASSIGNED, page reload, all back to "— unassigned —", button reading "CONNECT RIG (0)" `disabled: true`, opacity 0.35. Nothing warns that selections are volatile until you have lost them.

**17. DISCONNECT fires with no confirmation, then the UI reports every device still CONNECTED** — [DEFECT] [CONFIRMED] — novice
Immediately after DISCONNECT: RIG ACTIONS correctly reverts to "No equipment yet…", while LINK STATUS shows all ten devices green CONNECTED and the header still reads "SIM · STATUS TRACKING · 05h 35m 24.0s". Only a page reload corrects it. Button is `btn btn-danger`, `disabled: false`, no dialog. **Two subsystems reading different state from the same event.**

**18. A target chosen in Tonight cannot be slewed to** — [BLOCKED] [CONFIRMED for Atlas] — novice
Enumerating every button in `main` on the Atlas target page returns: CALIBRATE FROM LAST SOLVE, −, +, FIT OBJECT, −, +, −5°, +5°, −, +, −, +, −, +, **ADD TARGET TO PLAN**. Nothing else. Mount's TARGET CATALOG is a separate magnitude-sorted list that did not contain M11 in visible rows and showed 11 of the first 15 entries below the horizon with ⚠.
*Cost:* the app's own instruction is "Tap one to frame it", and framing is a dead end whose only exit is the automation wall. **Important caveat:** the capability exists — novice recovered by typing "NGC 7000" into the Mount search, and pro used the Plan typeahead (which even shows max altitude inline). This is a broken *handoff*, not a missing feature, which makes it cheap to fix: a "GO TO THIS TARGET" primary on the Atlas target page, and carry the selection into Mount pre-filtered.

**19. `text-faint` measures 3.39–3.52:1 and is used for instructional copy** — [DEFECT] — **novice + designer, independently measured**
`rgb(90,102,132)` on `rgb(11,13,20)` = 3.39:1; on `rgb(6,7,11)` = 3.52:1. Against a 4.5:1 requirement, at 11–14px. Applied to: "Not started — press Start Alignment to measure." (14px, the only instruction on the Align screen), "Run autofocus to measure focus quality.", "Refocus: manual only", "Planning works with the rig switched off.", "awaiting first frame", and eleven instances of "UNASSIGNED". Some of it is also placed over the photographic wallpaper, where novice measured it as effectively invisible. `text-muted` (`9AA6C2`) passes and is right there.

**20. No per-filter calibration workflow, and the master-matching keys are never stated** — [BLOCKED] — mono
Settings → CALIBRATION is a heading, a paragraph and a REBUILD LIBRARY button; `GET /api/calibration/masters` returns `[]`. The target-level "Cal" toggle flips cleanly and adds no steps, no counts, no integration change. Building flats by hand is one step per filter, each defaulting to "no filter" and 120s (a fully saturated flat). Nothing states whether masters match lights by filter, exposure, gain, binning or temperature.
*Cost:* flats matching filters is the entire reason to own a wheel. Mono would not trust this to calibrate a narrowband project.

**21. "MERIDIAN FLIP ⚠ FLIP DUE" is a permanent red alarm for something the sequencer has correctly decided to ignore** — [DEFECT] — **pro + designer**
Present in every Monitor capture across three runs, never resolving. `meridian = {status: "due", hours_to_flip: -0.6455, flip_enabled: true, pier_side: "west"}`; no flip appeared in ~90 frames of log. The engine is right (`engine.py:1155` arms `_flip_armed` only when the target is acquired east); the widget reports raw hour angle. Designer separately found the *same* warning rendered twice on one screen in two wordings and two banner geometries (a 450px floating amber banner unaligned to anything, plus a COUNTDOWNS card 850px below).
*Cost:* alarm fatigue on the exact indicator that must never cry wolf — and it teaches you to distrust every other red thing on the screen.

**22. Reopening the dashboard mid-run shows "NO FRAME YET" with dozens of frames on disk** — [DEFECT] [CONFIRMED] — pro
Fresh tablet load at 29/40 with 29 frames written: LAST FRAME renders an empty black box captioned NO FRAME YET. Only populates when the next frame arrives over the socket.
*Cost:* the 3am-from-bed case. First read is "the camera has died", and you wait a full exposure to learn otherwise.

**23. Abort messaging invents faults that did not happen** — [DEFECT/copy] — **mono + pro**
Mono held ABORT deliberately and got "Something interrupted the sequence and it couldn't continue. Open the event log for the exact message" followed by an unrelated guider-calibration advisory — a diagnostic wild-goose chase at 3am for a fault that never existed. Pro found the same card quoting four log lines, the first two of which belonged to a *different run ten minutes earlier*, inventing a second weather event.
**Fix:** distinguish user-abort from fault-abort in copy ("You aborted the run at 15/18 frames"), and filter the inline excerpt to `ts >= run.started_at`.

**24. Disabled-control reasons are delivered only through the `title` attribute** — [DEFECT] — designer
"Unavailable — The magnifier needs linear data — this frame came from NINA", "Clip mask needs linear data + known full well" — genuinely excellent copy, on a mechanism that never fires without hover, on the primary touch device. And inconsistently applied: five Capture buttons explain themselves; WARM on the cooler panel, Focus's GO and Guide's STOP are disabled with `title=''`.

**25. The (i) affordances are 9–14px wide with no accessible name** — [DEFECT/touch] — designer
Measured Capture rects: 14×14, 14×14, 14×14, 10×14, 9×14, 13×14 — all `aria-label=''`, `title=''`. Four different widths for one component. These sit next to GAIN (E-/ADU), READ NOISE (E-) and BIAS (ADU) — exactly the fields a user will not understand, where the (i) is the only explanation.

**26. Twelve unnamed interactive controls on Plan, eight on Capture, three on Settings** — [DEFECT/a11y] — **mono + pro + designer**
Plan step rows: TYPE, FILTER, EXP S, GAIN, BIN, COUNT all return `aria-label ''`, with the column headings rendered as a single caption row *underneath the last step*. Sighted users lose the association too on narrow widths, where the caption scrolls out of the clipped region — mono had three unlabelled number boxes and counted columns to find COUNT. The rest of the app is well-labelled (every device select, every switch), which makes Plan the outlier rather than the norm.

### Tier 2 — friction and gaps

| # | Finding | Bucket | Who |
|---|---|---|---|
| 27 | Roof/dome state appears nowhere on Monitor; the only roof indicator in the app is a badge in Settings → Safety. `/api/status` has no dome block. The roof closed and the dashboard said nothing. | DEFECT | P |
| 28 | "✓ Night looks OK" is asserted with `weather.enabled: false`. `lib/health.ts` only raises a weather issue when `weather?.alert` exists, so with monitoring off no cloud issue can ever fire and the empty-issues branch prints the calm line unconditionally — on the same phone screen as a red FLIP DUE. | DEFECT/copy | P |
| 29 | `+ STEP` never inherits: after Ha/300/×20, the new step is `['Light','','120','100','1','10']`. Three filters ≈ 15 interactions; seven filters ≈ 21 field edits typed on a tablet in the dark. No duplicate-row control, no LRGB/SHO starter. | Friction | M |
| 30 | `count_mode: "attempts"` by default, with every quality gate at 0/OFF. You asked for 20×300s; you get 20 *attempts*. Nothing in the UI reports the shrinking real total. | DEFECT/default | M |
| 31 | Nothing ever asks an unattended rig to configure an alert channel, and preflight doesn't check for one. `alerts: []`, `deadman_url: ""` by default. The rain abort produced a beautiful red banner on a browser tab nobody was watching. | Gap | P |
| 32 | Capture root is an env var only — no UI setting, no absolute path in the naming preview, and the report store follows it. Preflight says "88 GB free" without naming the volume. | Gap | P |
| 33 | Safety trips emit two identical toasts sometimes and zero other times (DOM query for "UNSAFE:" returned `[]` at +20s and +45s on a trip whose banner and pause fired correctly). | DEFECT/intermittent | P |
| 34 | The Guide caption points at an "Optics" panel that exists nowhere by that name (Settings tabs are Connect/Profiles/Calibration/Safety/Alerts/Updates/Account/Users/Auth; the control lives in `AtlasView.tsx:740` behind framing). Consequence of not finding it is #11. | DEFECT/copy | P |
| 35 | Session cards carry no dates — five entries all named "Tonight". `created_ts`/`updated_ts` exist in `/api/sessions`; neither is shown. Pro clicked RESUME on the wrong one. | DEFECT | P |
| 36 | Per-filter focus offsets — the single most important mono setting, with an excellent learn-offsets routine — are behind a 24px slider glyph labelled "Edit filter slot names". Mono spent the evening believing the feature didn't exist. | Discoverability | M |
| 37 | Frame review cannot filter or group by filter band; chips are target / night / verdict only, though each card shows the band. A three-night SHO project is 180 thumbnails scrolled by eye. | Gap | M |
| 38 | The review drawer is semi-transparent — AUTOMATION labels and toggles read through frame metadata — and its right column of cards is clipped mid-line ("HFR 2.51 · ★20 ·"). | DEFECT | M |
| 39 | Calibration frames count toward INTEGRATION: one Flat 120s×10 moved the header from "60 · 5h 0m" to "70 · 5h 20m" and added an unlabelled "— 0h 20m" to the per-filter line. | DEFECT | M |
| 40 | Flat steps default to no filter / 120s (saturated against a panel) with TARGET ADU 0, no unit, no placeholder, no indication whether 0 means auto or off. | DEFECT/default | M |
| 41 | Every Equipment row renders a green ✓ LED beside a select reading "— unassigned —" and a faint "UNASSIGNED" label. GUIDING breaks the row rhythm with a second line indented 31px off the column. | DEFECT/copy | D |
| 42 | Settings tab strip clips AUTH at the right edge with no fade, chevron or scrollbar. Driver rows print each name twice in two typefaces ("Simulator  Simulator", "ASTAP  ASTAP"). | DEFECT | N + D |
| 43 | Focus V-curve: the "HFR" axis title overstrikes the topmost tick ("HFR2.8"; 40px² overlap, both 11px), and a dotted gridline runs through the "RUN AUTOFOCUS" empty-state text. X ticks are placeholder 0 and 1. | DEFECT/overlap | N + D |
| 44 | Phone: opening MORE with the event-log drawer open stacks two translucent overlays — "08:23:14 [Info · hub] simulator rig connected" reads straight through the "Monitor" row. | DEFECT | D |
| 45 | Phone header drops the mount state and coordinates entirely rather than condensing them. On phone Capture there is no pointing information anywhere on screen. You can run a capture with no indication the mount stopped tracking. | DEFECT | D |
| 46 | No `env(safe-area-inset-bottom)` anywhere in `ui/src/` (grep empty). Bottom nav sits flush at 787→844 in an 844px viewport; connected-LEDs measure 1px past the edge. | DEFECT in source / NEEDS-REPRO on device | D |
| 47 | Header icon buttons: brightness 44×44 radius 0, night 36×44 radius 10, log 36×44 radius 10 — an obvious set that isn't one, two below the 44px touch minimum. | DEFECT/touch | D |
| 48 | Naming tokens omit exposure, gain, binning and sensor temperature, so a dark library is undistinguishable by filename (300s g100 −10°C and 600s g0 −20°C both land as `Dark_..._0001.fits`). | Gap | M + P |
| 49 | `frames.csv` drops gain, offset, binning, eccentricity and altitude that the JSON already carries, and emits raw epoch floats (`1785084747.5023835`). | Gap | P |
| 50 | Filename timestamps are local, `DATE-OBS` is UTC, 7h apart, with nothing indicating the difference. `$$NIGHT$$` may already solve the rollover — its semantics are simply not visible. | Gap/docs | P |
| 51 | The event log button is a ⚠ caution triangle in the status bar that opens a log of Info lines. And the same page is "Rig" in the copy, "GO TO RIG" on the button, "EQUIPMENT" in the nav. | Copy | N |
| 52 | An unexplained orange "GUIDING · DEGRADED" appears on a freshly connected simulator rig with no cause and no link to a fix. | Copy | N |
| 53 | Nav rail labels render at 9px (Chakra Petch, ls 1.62px) — a legitimate axis-tick size, on primary navigation, read at arm's length in the dark. Hit area is a fine 70×62. | DEFECT/legibility | D |
| 54 | Align's TOTAL ERROR renders a grey skeleton-shimmer bar in a *not-started* state, then says "Not started — press Start Alignment". A non-loading state drawn as a permanent loading state. | DEFECT | D |
| 55 | Atlas's control header (title, focal length, pixel size, sensor W/H, guide scope FL, CALIBRATE FROM LAST SOLVE) sits directly on the photographic wallpaper with no `.panel` behind it — dim grey text over a bright nebula. | DEFECT/legibility | N |

### Tier 3 — craft [TASTE]

These are defensible judgements, several of them measured. They are not defects and should not be triaged as such — but the designer's measurement pass makes them unusually actionable.

- **Four visual treatments for "stop the thing"** (Mount solid fill 256×56 / Capture ghost 90×56 / Focus red-outline 108×52 / Guide plain neutral), only one of which survives night mode. You should be able to find abort by silhouette without reading it. Mount's solid fill is the right primitive.
- **Three visual languages for "selected"** (teal fill + cyan text + purple border / solid cyan fill + near-black text / solid violet + cyan). Treatment (b) is the loudest element on any screen it appears on — on Atlas the BEGINNER filter chip outshines the primary CTA next to it. Hierarchy inverted by a filter control.
- **Wallpaper budget.** Measured mean luminance of the empty lower band: 28.9 day (p95 78.8, 9% of pixels above L=60) vs 2.2 night. Roughly 55–75% of Plan / Tonight / Power / Atlas at tablet size is a full-brightness colour photograph, showing through `rgba(12,14,22,0.85)` panels — so guide traces and scatter plots are drawn over nebula filaments with non-uniform contrast. Night mode correctly kills it, which shows the team already knows. Related: Tonight's target list is capped at `max-h-72` (scrollHeight 899 in a 286px box) while ~700px below it is wallpaper — six of twenty suggestions visible, in a nested scroll region, with gloves on.
- **Monitor's grid doesn't resolve.** 12px stack gap where Align/Power/Capture/Equipment/Settings all measure 16px; 344px-wide cards interleaved with 700px cards leaving hanging gutters; desktop row splits at x=1355/1382 then x=1043/1068. Misaligned vertical gutters between adjacent rows is the strongest "unfinished" signal a layout can send, on the densest screen in the app.
- **Empty states at three finish levels on one screen** — "NO RUN ACTIVE" (icon + headline + copy + CTA) beside "NO FRAME YET" (centred text in a black rectangle) beside "NOT GUIDING" (centred text). Same concept, two treatments, two views: Capture's "NO CAPTURE YET" does get an icon. Guide's chart and scatter have no empty state at all.
- **Type system:** five sizes (commendably tight) across three families and **nine** letter-spacing values; 11px alone appears in four configurations, so panel titles and field labels differ only by family and tracking. Explanatory prose runs at 11px with 15.125px line-height.
- **Unstyled browser range inputs** — ~1120px of near-white 4px track on Power's dew heaters, the brightest sustained element on the page, in an otherwise fully custom dark UI. The unfilled remainder is brighter than the cyan filled portion, so "more" looks like "less".
- **Smaller craft items,** all measured: SAVE/SAVE AS…/Import in three casings across 450px; three ways to say "off" in one AUTOMATION panel; twelve (i) icons at twelve different x positions because they're inline after labels of different lengths; "BAHTINOV FOCUS" three times in 200px, one in a typeface used nowhere else; plan search placeholder truncated mid-word ("search cata") at *desktop* width with 1400px free beside it, while the empty state above points at it; Capture toolbar rows starting at x=121/121/136/121 with 6px and 47px gaps in one row; filter modal's SAVE (110px) narrower than CANCEL (138px); event drawer taking 62% of the tablet to show three lines; guide chart's zero line at y=380 in a plot centred at 421 with no −2″ label and no time axis; "[Info" violet / "·" grey / "hub]" cyan splitting a bracket pair across three colours; MORE-sheet rows sentence-case where every other nav surface is uppercase-tracked, with device LEDs on Help and Settings.
- **PLAN → AUTOMATION as a wall.** Twelve ungrouped rows of "dither every N frames / refocus on temp Δ°C (0=off) / max eccentricity (0–1)" with no beginner/advanced split — reached as the *only* forward CTA from picking a first target — next to ~660×1650px of empty wallpaper in the left column.
- **Capture preview top-alignment** leaves ~230px of black inside the bordered box under a 4:3 frame. It is a beginner's first-ever photograph and it reads as half-loaded.

---

## Part 3 — Discarded

| Claim | Why |
|---|---|
| "There is no way to see or export a report — zero links in the UI" (mono) | Refuted by pro: `NavMoreSheet.tsx:53` defines it and it renders in the phone MORE sheet. The real, narrower finding is #13. Kept, corrected. |
| "The FIRST LIGHT preset immediately produced two orange ⚠ icons next to HFR" (novice, journey) | Observed, never diagnosed, no finding filed and no reading given. **NEEDS-REPRO** before it means anything. |
| "Two instances on one machine cross-contaminate their report lists" (pro) | Real mechanism (report store follows `CAPTURE_DIR`), but observed on a shared test server four reviewers were driving simultaneously. Confounded. Retained only as motivation for #32. |
| "On any iPhone the entire 57px bar sits under the home indicator" (designer) | The grep is confirmed and the fix is right; the device consequence is inferred from a 1px headless clip. Filed as #46 with the inference flagged. |
| "Filter names degrade into a ragged 1-per-row stack with five right edges" (designer) | Designer's own measurement shows `mainScrollW == mainClientW` after saving four vendor-length names — it reflows correctly, which is the *good* outcome. Aesthetic only, and thin. |
| Raw audit counts quoted without judgement (`unnamed: 12`, `truncated: 1`, `textOverlap: 1`) | Retained only where a persona identified *which* controls and why it matters (#26, #42, #43). Standalone counts are not findings. |
| "The 'Cal' toggle adds no steps" framed as broken | It flips state cleanly and persists; what's missing is a spec editor. Correctly a gap (#20), not a broken control. |
| Novice's "the app called the same thing three names" and pro's "SEQ 29/40 IDLE" | Both real, both minor; merged into #51 and the copy list rather than filed separately. |

---

## Part 4 — What genuinely works

Worth stating plainly, because the defect list above is long and the failure-handling engineering underneath it is better than the reviewers expected.

**Failure behaviour is the strongest thing in the product** (pro, who has run NINA/SGP/ACP for years, called it "ACP-grade and I did not expect it"). Rain trip → 3-poll debounce → "paused (unsafe): rain sensor wet" with the literal sensor reason, ETA replaced by "NO ETA WHILE PAUSED", last frame stamped STALE, alert badge on the bell. Clearing → "conditions safe again — resuming" and "re-acquiring M27 after pause (tracking on, re-center, restart guiding)" — and it genuinely does re-centre and restart guiding. Roof-close-on-unsafe **parks the mount first and refuses to close if park can't be confirmed**, with copy that explains why ("a wet scope beats a crushed one"). Below-horizon GOTO refused with the actual number: "M42 is at -33° — below the horizon, so it isn't visible now".

**Multi-night sessions are the real thing** (mono and pro both). DORMANT / ACTIVE / COMPLETE, a night counter that incremented 1→2, one-tap RESUME that picked up at exactly 15/18 with no re-shooting, update-from-plan, abandon, auto-resume-at-dusk, resume from a phone, and a persistent "SEQUENCE RUNNING · 83% · OPEN LIVE" bar that follows you across views. NINA has no native equivalent. It genuinely knows what it owes — findings #10 and #35 are about *finding* it, not about whether it works.

**Data integrity at the file layer is excellent.** FITS headers complete and correct: EXPTIME, GAIN, OFFSET, XBINNING, IMAGETYP, DATE-OBS, CCD-TEMP, OBJECT, FILTER, RA/DEC, OBJCTRA/DEC, INSTRUME, FOCALLEN, XPIXSZ, SITELAT/LONG/ELEV, OBJCTALT, AIRMASS, FOCPOS, FOCTEMP, ROTATANG, EGAIN, EQUINOX, RADESYS, SWCREATE. Frame numbering per-target and monotonic across runs (a second run started at `_0015`); nothing silently overwritten across five sequences. The per-frame report is richer than NINA's, and the **stacking bundle** — one zip pre-sorted into PixInsight/Siril/APP folders with matching masters and a per-frame quality score — is better than anything NINA ships. (Which is exactly why #1 hurts: the bundle's grouping key is the one field that's wrong.)

**Real mono support, once found.** Filter slot names *and* per-filter focuser offsets in one dialog, with a working "learn offsets automatically" routine, a reference-filter picker, and honest copy ("Point at a star field first. Takes a few minutes."). `apply filter focus offsets` is ON by default. The default naming template already puts the filter in the filename. The plan editor gives a per-filter integration breakdown. Filter chips measure 56×44 — meeting the touch minimum several other controls miss.

**The preflight checklist's *content* is right** (both mono and pro said so independently): Camera / Mount / Guiding "will start after slew" / Cooling / Focuser / Filters READY / Calibration / Exposure / Disk 89GB free. It is exactly the reassurance a six-hour unattended run needs. Findings #2 and #3 are one missing row and one broken container around a good idea.

**Monitor during a run** is genuinely good for the long-sub shooter: current filter in the header, "NGC 7000: OIII 5s [2/6]", 7/18 38.9%, "last frame 5s ago", meridian countdown, last sub tagged "HFR 2.55 · 14★ · 5s·g100·1×". Hold-to-abort is the correct interaction in a dark field, and RUN SEQUENCE requiring a second confirm through a checklist is the right friction on the two irreversible actions.

**Onboarding copy is excellent where it exists.** RIG ACTIONS ("No equipment yet — Detect hardware rig to auto-assign your connected gear, or start the Simulator rig to explore") — and the simulator connected 11 devices in ~3 seconds, which three personas independently called the fastest rig connect they'd used. Tonight ("Point-and-shoot targets that are up right now, ranked by how high they climb tonight") with a BEGINNER/ALL toggle and plain-English (i) tooltips that work on tap. "FOCUS MY SCOPE" with a collapsed ▸ADVANCED, resolving to "Focused. Stars look good — you're ready to shoot." The FIRST LIGHT capture preset. The disconnected-Capture empty state. The RBAC role descriptions and the dead-man's-switch framing ("Its ABSENCE — not a local error — is what pages you if the whole box goes dark"). Signed updates that refuse to install while the rig is imaging and roll back on failure.

**And the design system exists**, which is why its lapses are legible: 7 text colours with clear roles applied consistently; a 16px gap / 16px padding / 16px radius card primitive; shape-encoded LEDs; padlock glyphs; genuinely-written disabled reasons; Monitor's and Atlas's empty states as the standard.

---

## Part 5 — Fairness notes

Three complaints are preferences imported from other software rather than defects here, and should be priced accordingly:

- **File-naming tokens** (`$$EXPOSURE$$`, `$$GAIN$$`, `$$BINNING$$`, `$$SENSORTEMP$$`) — pro cites NINA parity explicitly. The dark-library argument behind it is legitimate and cheap to satisfy, but this is a feature request, not a break.
- **`$$DATEMINUS12$$`** — a NINA idiom. `$$NIGHT$$` may already provide noon-to-noon semantics; pro's actual complaint is that those semantics aren't visible in the preview. That's a documentation fix, not a missing token.
- **Time-based reject limits** (mono) — "20 consecutive rejects" isn't wrong at 600s, it's just un-annotated. Showing the wall-clock cost beside the field satisfies the real concern without a new mechanism.

And two places where personas disagreed for good reasons:

- **Tonight.** Novice called it "the single best thing in the app". Pro called it under-informative and wants transit time, best-window span and moon separation surfaced. Both are right — the BEGINNER/ALL toggle is already the correct seam, it just doesn't change what the rows show. Make ALL a denser view and remember the choice; don't compromise the beginner default.
- **The preflight.** Mono and pro both praised its content in `worked_well` and both filed a blocker against its geometry. Read those two together before touching the checklist itself.

---

## Suggested order of work

1. **One overlay primitive** — portal outside `.dim-content`, clamp to viewport, opaque surface, sticky footer in a `max-height: 100dvh` scroll region. Closes #3, #4, #22(drawer), #29, #38, #44 and the wizard.
2. **Resolve "no filter" to the wheel's actual position at capture time**, key `by_filter` and bundle grouping off it, make preflight fail on an unfiltered Light step with a wheel present. Closes #1 — the only data-corruption item.
3. **Add a Safety row to preflight** fed by `/api/safety/state`, forcing NOT READY. Closes #2. Add an Alerting row while you're in there (#31).
4. **`min-width: 0` and narrow-width column collapses** on Mount, Plan and the Settings tab strip. Closes #6, #7, #42 — three personas' blockers, all on the primary device.
5. **Non-hue channels for danger, traces and selection**, plus fix the inverted `--bad` token. Closes #14, #15 and the mono filter-chip finding.
6. **Persist the event log to a rolling per-night file** with level filtering and a download button, and put Reports in the primary rail. Closes #9 and #13 — together these are the "trust what I find in the morning" promise.
7. **Render what the backend already knows:** `place_hint`, the flip-arming latch, `GuideStats.is_arcsec`, per-filter session progress, roof state on Monitor, the actual escalated safety action in the report. That's #8, #11, #12, #21, #27 and half of #6's trust cost — and every one of them is a wire, not a feature.
8. **Flip the defaults that quietly cost a night:** save-FITS on for Light frames, `count_mode` accepted when any gate is armed, and a nag when no alert sink exists.