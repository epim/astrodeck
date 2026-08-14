# Handoff: AstroDeck Flows - visual automation surface

## Ground rules for the implementing agent - READ FIRST
This is a **scoped implementation task, not a design task**. The design is finished and approved.

1. **Order of authority** when anything seems ambiguous: (1) this README → (2) the screenshots in `screenshots/` → (3) the prototype source (`AstroDeck Flows.dc.html`) → (4) the existing app's conventions (`ui/src/index.css`, `components/ui.tsx`) → (5) **stop and ask the user**. Never resolve ambiguity by inventing.
2. **Do not redesign.** No layout "improvements", no new colors/fonts/spacing, no component-library swaps, no extra features, no renamed concepts. If you believe something is a genuine defect, note it in your summary and implement as specified anyway unless the user says otherwise.
3. **Scope fence.** You may touch: the new Flows UI code, the new server modules/endpoints in "Backend work list", new device roles + their sim backends, tests for the above, and nav registration for the new surface. You may NOT refactor, reformat, or "clean up" unrelated files, existing views, the token file, or the engine - additive changes only, per this codebase's additive-enum/back-compat discipline.
4. **Definition of done** - all must hold:
   - Every screen matches its screenshot at the same breakpoint (side-by-side), tokens differing only where the README says token-instead-of-hex.
   - All ten doctor rules fire with the specified wording tone; the five example flows load, validate, and run end-to-end **on the simulator with no hardware**.
   - The M16 example reproduces the full cloud-dodge choreography from real engine events (hold → black slot → darks → bias skip → flats-if-panel → clean stop → restore filter → re-center → conditional refocus → resume).
   - Compile output validates against the existing pydantic models plus the additive fields defined here; `instructions` uses only the closed enum (plus the two additive cloud triggers).
   - Phone (390px), tablet (820px), desktop (1440px) tiers behave as specified; night mode works via the token ladder, not a filter.
   - Nothing in the "Do-not list" is violated; reduced-motion and 44px coarse-pointer rules hold.
   - **Visual verification protocol (mandatory - see §"Verify by looking")** completed and its artifacts committed.

### Verify by looking - not by DOM
DOM assertions pass while pixels are wrong (overlapping text, invisible SVG labels, wires sweeping off-canvas, z-index fights, unloaded fonts). Every UI milestone must therefore be verified from **rendered screenshots**, not selectors:

1. **Capture harness first.** Before building screens, add a script (Playwright headless or the repo's existing e2e tooling) that boots the dev server against the simulator and captures PNGs of named app states at exactly 1440×900, 820×1180, and 390×844, with fonts loaded (`document.fonts.ready`) and animations settled. Check it into the repo (`scripts/flows-visual-check.*`).
2. **Required capture set** (mirrors `screenshots/`): library; M16 editor fitted; calibration-queue inspector; Tonight ×3 tabs; mid-run cloud-hold (pause the sim or capture within the hold window); night mode; wizard; phone FLOW auto-graph; phone tap-to-wire armed; phone MONITOR. Save under `artifacts/flows-parity/`.
3. **Actually look at every capture.** Open each PNG and its reference from `screenshots/` side by side and compare as an image - layout, spacing, wire routing, label collisions, contrast, glow, dashed-vs-solid, LED silhouettes. If your tooling can read images, read both and describe the differences; if it cannot, stop and ask the user to eyeball the pair rather than declaring parity from DOM inspection.
4. **Hunt render-only bugs explicitly**: text overlap/truncation at each breakpoint, elements outside the viewport or clipped by `overflow`, white flashes on load, SVG text that measures fine but renders empty, wires drawn under nodes, safe-area collisions on phone, and the night-mode token swap leaving any hardcoded hex behind (grep is not enough - look at the night captures).
5. **Interactive states count as screens**: capture hover (desktop), armed tap-to-wire, an open edit sheet, an open palette sheet, a selected wire with its ✕ - a state that was never rendered was never verified.
6. **Report with evidence.** The final summary must reference the capture set (path per state) and call out any known visual deltas with justification. "Tests pass" without committed captures does not meet the definition of done.
5. **When blocked** (missing endpoint semantics, hardware you can't test, a conflict between this doc and the codebase): implement the sim path, leave a `TODO(flows-handoff): <question>` comment, and surface the question in your final summary. Never substitute a different behavior silently.

## Overview
**Flows** is a new AstroDeck surface: a node-graph ("Yahoo-Pipes-like") automation builder for astrophotography. Users wire **sources** (dusk window, targets, safety monitor, cloud watch), **equipment** (dome, flat panel), **rig ops** (slew+center, autofocus, guide, capture loop, dusk flats, calibration queue), **logic** (condition, target pool) and **actions/sinks** (notify, refocus, hold/resume, abort+park, session report) into a graph. A flow **compiles to what the engine already runs**: a `SequencePlan` (targets → steps) plus `Instruction` when/then rules. It must never invent a capability the server lacks.

Deployment targets, in priority order: web UI (primary, `ui/` - React 18 + TypeScript + Tailwind v4 + Zustand), tablet, phone, desktop shell. One responsive implementation, three layout tiers.

## About the Design Files
`AstroDeck Flows.dc.html` (+ `assets/bg_nebula.png`) is a **design reference prototype built in HTML** - it shows intended look and behavior; it is not production code to copy. Recreate it **inside the existing `ui/src` app** using its established patterns: the Zustand store with narrow selector hooks, the primitives in `src/components/ui.tsx` / `design-system.ts` (`Panel`, `Led`, `Field`, `Overlay`, `SegmentedControl`, `Icon`, …), Tailwind v4 utilities, and the token ladder in `src/index.css`. The prototype's logic (graph model, doctor, compile, simulation) is a faithful spec of behavior, not of architecture.

## Fidelity
**High-fidelity.** Recreate pixel-perfectly. One deliberate exception: the prototype hardcodes the **day palette as hex values** and fakes night mode with a CSS filter. Production must instead use the real CSS tokens (`--bg`, `--accent`, …) so the measured night/light ladders in `index.css` apply. The hex↔token mapping is in Design Tokens below. Second exception: ephemeris times in the Tonight panel are simulated placeholders - production computes them from `catalog/visibility.py` and `sequence/schedule.py`.

## Information Architecture
1. **Library** (`data-screen-label="Flow library"`) - grid of saved flows + "NEW FLOW" (opens the guided wizard).
2. **Editor** (`"Flow editor"`) - palette rail · canvas · inspector (desktop); canvas + floating ADD + bottom-sheet inspector (tablet); linearized rail (phone, `"Flow editor (phone)"`).
3. Overlays - Tonight panel, guided wizard, design notes, add-stage sheet, node-edit sheet. **All overlays must portal to the body-level overlay host** (use the existing `Overlay` primitive; see the containing-block war stories in `index.css`).

---

## Screens / Views

### 1) App header (all screens) - 54px
- Left: `‹ LIBRARY` button (editor only) · AstroDeck logo SVG at 26px (`src/components/Logo.tsx`, ink `--text-dim`) · two-line wordmark: `ASTRODECK FLOWS` (Chakra Petch 600, 11px, letter-spacing 0.22em; "FLOWS" in `--accent`) over the flow name (IBM Plex Mono 10px, `--text-faint`).
- Right cluster: provider badge `SIMULATOR` (pill, **dashed** border + **hollow** dot = simulator, per the `.prov-sim` shape rule - reuse `.prov` classes) · validation chip (mono 10px, bordered: `GRAPH VALID` in `--good` or `N OPEN CHECKS` in `--warn`; full issue list in tooltip) · ETA readout while running (mono, tabular-nums) · `◷ TONIGHT` button · `▶ RUN` / `■ STOP` (accent-fill / danger chrome) · night toggle `☾` · `i` design notes.
- Phone: hide provider + validation chips; `◷` icon-only.
- Header buttons: Chakra Petch 600 10.5–11px, letter-spacing 0.12em, radius 10, border `--line-bright`, bg `--bg-raise`; hover → accent border/ink + glow (match `.btn`). ≥44px targets on coarse pointers.

### 2) Library
- Background: `bg_nebula.png` under `linear-gradient(rgba(6,7,11,.82), rgba(6,7,11,.94))`.
- Max-width 1020, centered. H1 `FLOWS` (Chakra Petch 600 22px, ls 0.14em); sub (13px, `--text-dim`, max-width 560): "Visual automation for the rig. Wire targets, windows and sensors into capture stages and rules - a flow compiles to a sequence plan plus when/then instructions and runs on the engine, fail-closed."
- **Toolbar** (below the sub, wraps): search field (mono 12px, 40px tall, radius 10, magnifier icon inset-left, placeholder "Filter flows…", live substring match on name+tagline, case-insensitive) + folder chips (`All / My flows / Examples` - pills, active = accent border + `--accent-fill` bg). No-match state: quiet centered mono line `No flows match “…”` + hint, no animation.
- **Folders**: flows live in a directory structure. Each folder renders as a section - folder glyph + name (Chakra 600 10px ls 0.22em `--text-dim`) + count (mono, `--text-faint`) - with its own card grid. Seed folders: **MY FLOWS** (user-created; the dashed `+ NEW FLOW` card always leads this grid) and **EXAMPLES** (the five read-only seed flows). Wizard/blank creations are saved into My flows and re-saved on leaving the editor. Production: folders are user-creatable/renamable, flows draggable between them (`folder` field on the flow record); prototype ships the two fixed folders.
- Card grid per section: `repeat(auto-fill, minmax(232px,1fr))`, gap 14. Card = panel chrome (translucent `--bg-panel`, 1px `--line`, radius 16, backdrop-blur 14): name (Chakra 600 12.5px uppercase), tagline (12px `--text-dim`), meta line (mono 10px `--text-faint`: "14 stages · 14 wires · last run …"), status row (shape-coded LED + `COMPLETED CLEAN`/`NEVER RUN`). Hover: accent border + `0 0 14px rgba(0,210,255,.25)`.
- Last card: dashed-border `+ NEW FLOW` → wizard.
- Seed flows (fixtures): **Campaign - best of 4, month-scale**, **M31 - LRGB two-night**, **M16 - full-service night**, **M33 - LRGBSHO cycle ×45**, **Best-of-four pool night**, **NGC 7000 - Ha narrowband**, **EAA quick look** (graphs identical to the prototype's `PRESETS`).

### 3) Editor - canvas
- **Palette rail** (desktop ≥1080px): 192px, right border, groups `SOURCES / EQUIPMENT / RIG OPS / LOGIC / ACTIONS + SINKS` (Chakra 600 9.5px ls 0.2em `--text-faint`); items = category dot (7px, radius 2, glow) + mono 10.5px label; hover raises a bordered row. Click = drop node at canvas center.
- **Canvas**: nebula bg + 36px grid (two `repeating-linear-gradient`s at `rgba(120,140,200,0.06)`); pan = drag background (grab cursor), zoom = wheel (anchor under cursor) clamped **0.35–1.6**, plus `− / % / + / FIT` cluster bottom-left (FIT = bounding box + 30px margin, zoom ≤1.15).
- **Node card**: width **188px** (phone canvas: **150px compact** - summary footer hidden, same row heights); bg `rgba(12,14,22,0.92)`, 1px border `--line` (selected: `--accent` + `0 0 16px rgba(0,210,255,.35)`; busy: accent-tinted border + glow), radius 12, backdrop-blur 8.
  - Header 32px: category dot · label (Chakra 600 9.5px ls 0.14em, truncates) · status LED · **✎ edit** icon-button (20px, pencil; the ONLY thing that opens the edit sheet on tablet/phone). Header is the drag handle; dragging never opens the editor.
  - Port rows 20px each: inputs left-edge, outputs right-edge (row-reverse). Port dot 9px circle, 1.5px border; **flow ports cyan `--accent`, event ports amber `--warn`**; fill = port color when wired, else `--bg`. Hit target 20px, `data-port="nodeId|portId|in|out"`, cursor crosshair.
  - Footer: params summary, mono 9.5px `--text-faint`, truncates.
- **Status LED silhouettes** (shape, never color alone - night-safe): idle = 10×2px dash `--text-faint` · busy = 9px accent circle, sweep `clip-path` animation 1.4s · ok = 9px `--good` circle · warn = 9px `--warn` circle + 1.5px offset outline · bad = 9px `--bad` **square**, pulse. Reuse `.led-*` where possible.
- **Wires**: cubic bezier `M p1 C (p1.x+c) p1.y, (p2.x−c) p2.y, p2` with `c = max(46, |dx|·0.5)`; port anchor y = `node.y + 37 + rowIndex·20 + 10`, x = node edge. Flow wires solid `rgba(0,210,255,.45)` 1.8px; event wires **dashed 4 5** `rgba(255,180,84,.45)`. Active during a run (source node busy/ok): full-strength color, 2.5px, dash `7 6` animating (`stroke-dashoffset` −24, 0.6s linear infinite). Selected: `--text` ink + ✕ delete button at midpoint (22px round, danger chrome). Invisible 14px hit path per wire.
  - Drag from an **output** dot → pending dashed wire follows cursor; drop on an input (`elementFromPoint` → `[data-port]`, works for touch). Kind mismatch refuses with toast: "Flow output can't feed an event input". Rewiring an occupied input replaces the old wire.
- **Log strip** (bottom): 30px bar `LOG` + last line (mono 10.5px, tone-colored) + chevron; expands to 170px scrollback. Timestamps `HH:MM:SS` in `--text-faint`.
- **Inspector** (desktop right column, 284px): selected node → category dot + label + dashed cat chip; description (11.5px `--text-dim`); **LIBRARY HEALTH matrix when node = CALIBRATION QUEUE** (see §7); fields; `DELETE STAGE` (danger outline). Nothing selected → FLOW overview: name field, STAGES/WIRES stats, CHECKS list (the doctor, §8), port-grammar hint.
- Keyboard: Delete/Backspace removes selection (ignored while typing). Esc should close overlays (add in production).

### 4) Editor - tablet (700–1079px)
Canvas full-width; `+ ADD STAGE` floating button (44px, accent) bottom-right → bottom sheet palette; ✎ → bottom sheet inspector (max-height 76dvh, opaque `--bg-raise`, slide-up 0.2s ease **backwards**, safe-area bottom padding, 44px+ controls).

### 5) Editor - phone (<700px)
**Bottom tab bar** (50px + safe-area, active = 2px accent top border): `FLOW / CANVAS / MONITOR`. Default tab: FLOW.
- **FLOW - the happy medium (default)**: the real graph, **auto-laid-out** for one thumb - no pan, no zoom, no free drag; the page just scrolls vertically. Layout algorithm (deterministic): flow-lane stages in topological order **zigzag between a left and right column** (columns at 14px margins, node width 150px compact), 34px vertical gaps, real wires drawn between ports; then each event source gets a **rule cluster** - source card in one column, its unplaced targets stacked in the opposite column, one wire per edge so fan-out reads spatially (CLOUD WATCH visibly branches to HOLD + QUEUE + NOTIFY); already-placed targets just get wires. Unwired nodes stack at the bottom, left column. Wires use a vertical-bias bezier (±18px horizontal control, 24–70px vertical) so nothing sweeps off-canvas; event wires stay dashed amber. **Tap-to-wire** replaces drag: tapping an output port arms it (filled dot + accent ring + a hint bar above the tab bar: "WIRING: CLOUD WATCH · clouds in - tap an input port" with CANCEL); tapping an input completes the wire (same kind-mismatch refusals, replace-on-occupied), tapping the armed port again cancels. Port hit targets grow to 26px here. Tap a wire → ✕ delete button at its midpoint; ✎ on every node header → edit sheet; `+ ADD STAGE` pinned at the graph's end.
- **CANVAS - the full editor**: identical to tablet/desktop canvas (free drag, drag-to-wire, pinch-zoom about the gesture midpoint, `touch-action:none`, 0.35–1.6 clamp, floating `+ ADD STAGE`). Compact 150px nodes, summary hidden. For users who want full spatial control on a phone.
- Phone header (editor only): logo + two-line wordmark are replaced by a single truncating flow name (mono 11px) between `‹ LIBRARY` and the ◷/RUN/☾/i cluster - nothing may overlap at 390px.
- MONITOR: panel with STATE / ETA / STAGE / FRAMES readouts (mono 12–14px), scrolling log, full-width 56px RUN/STOP.

### 6) Run behavior (UI contract)
RUN walks the flow lane in topological order: stage LED busy → ok, wires downstream animate, log streams, header ETA counts down. Capture loop logs per-frame lines "`Ha 180s - HFR 2.13″ ✓ 4/20`". Event rules fire visibly (condition LED warn → busy actions → ok). STOP is immediate and logs honestly ("Run stopped by user - engine parks nothing on manual stop"). The prototype's scripted timings (incl. the cloud-dodge choreography in `fireClouds()`: hold → black slot → darks → bias skip → flats-if-panel → clear mid-queue → restore filter → re-center → refocus-if-drifted → resume) are the reference storyboard; production drives the same visuals from real WS events.

### 7) Tonight panel (`◷ TONIGHT`)
Centered overlay `min(880px,100%)`, four pill tabs `TIMELINE / STORY / PLAN / CAMPAIGN`.
- **TIMELINE**: SVG `viewBox 0 0 1000 150`, `preserveAspectRatio="none"`, width 100%. Geometry in SVG: axis at y=118 with hourly ticks (20:00→05:00), twilight bands (`--sky` @ .08), moon-up band (y=22, h=8, `--text-faint` @ .25), flats block (`--sky` @ .4), per-target imaging blocks (`--accent` @ .16, y=36 h=70) with altitude arcs (`Q` curve to y=48, `rgba(0,210,255,.6)`), dashed verticals for DUSK (accent) / DARK (faint) / ☾ 71% (faint) / FLIP (warn) / DAWN (good). **All labels live in an HTML layer absolutely positioned over the SVG** (left/top in %, translate(−50%,−50%), mono 9.5px) - do NOT put text inside the SVG (the prototype hit invisible-glyph bugs; HTML labels also stay legible at phone widths). Legend row + honesty note underneath.
- **STORY**: opens with a **mechanical brief** - a bordered prose paragraph (heading "BRIEF - GENERATED FROM THE GRAPH", 12.5px, line-height 1.65) generated deterministically from the graph, sentence per capability, params inlined verbatim: arm time (+offset, dome, twilight flats) → pool selection with constraints ("best of M33, NGC 7331, IC 1396, M45 - above 30°, at least 40° from the moon (if up), within 4 h of the meridian") → rig steps with providers/tolerances → the capture contract with every slot spelled out ("L 60 s × 45, R 60 s × 45, … - a sub only counts below HFR 3.5″") → report/advance loop → altitude-floor policy → cloud hold + resume checklist → end-of-night shutdown + nightly re-arm → safety clause → campaign completion state. Sentences appear only when the node exists; every number comes from params (edit a param, the sentence changes). Below it, the time-keyed story rows as before.
- **STORY** (brief + rows above): time column (mono 10px, right-aligned, 60px) + sentence rows; special rows `ANY` (rules, warn/danger-colored) and `BUDGET` (integration, `--good`).
- **PLAN**: caption + `COPY JSON` (accent outline) + `<pre>`-style block (mono 10px on `--bg`, bordered, max-height 56dvh) showing the **compiled plan** (§9).
- **CAMPAIGN**: per-pool-member progress rows - name (Chakra 600 11px) + right-aligned mono status ("45/45 cycles · DONE" in `--good`, else "23/45 cycles" in `--accent`) over a 6px pill progress bar (track `--line` @15%, fill = status color); below, a projected-completion note ("pool complete in ~6 clear nights, weather-modelled") + honesty line. Non-campaign flows with a pool get a hint to set DUSK WINDOW → Repeat; flows without a pool say campaigns need one. Production reads banked/quota from the session ledger and the weather-modelled projection from the scheduler.
- Data source in production: `GET /api/flows/{id}/tonight` (below). Timeline is a *rendering of the compile*, never a separate truth.

### 8) The doctor (graph checks)
Recompute on every graph edit; surface in header chip + FLOW overview. Rules shipped in the prototype (keep wording tone - explain *why*):
1. Any unwired required input → "▸ {NODE} - '{port}' input unwired" (the `panel` input is optional by design).
2. Capture with exposure ≥120s and no GUIDE upstream → trailing warning ("Add Guide, or shorten the subs").
3. Capture ≥60s with no AUTOFOCUS upstream → drift warning.
4. Capture with no SLEW+CENTER upstream → "shoots wherever the mount happens to point".
5. CONDITION with no outgoing wire → "fires into nothing".
6. HFR watchdog threshold > capture's reject threshold → "frames get rejected before the rule can ever fire".
7. QUEUE wants flats but nothing wired to `panel` → flats will be skipped.
8. Queue triggered but no HOLD/RESUME wired anywhere → "queue runs but nothing HOLDS the light loop".
9. DOME present with no SAFETY MONITOR → danger-level: "nothing closes the shutter on rain. Add one; it fails closed."
10. No SESSION REPORT sink → note-level: "the night leaves no ledger".
11. Campaign (DUSK WINDOW repeat ≠ single night) with a POOL whose `advance` is unwired → "campaign repeats nightly but nothing advances the POOL - wire SESSION REPORT 'target done' → 'advance'".
12. Campaign with `night ends` unwired → "campaign has no shutdown lane - wire 'night ends' → PARK + CLOSE".
13. SAFETY MONITOR watching clouds while a CLOUD WATCH exists → "they race, and safety aborts before the hold can ride it out. Set SAFETY → Watch for → rain + wind + power." **Semantics: safety is the non-recoverable tier (abort, fail closed, always wins); CLOUD WATCH is the transient tier (hold + resume); the max-hold timeout on HOLD/RESUME is the escalation path between tiers.**

### 9) Guided wizard (NEW FLOW)
560px sheet: (1) `WHAT ARE WE DOING TONIGHT?` - three 44px option buttons: Deep-sky target / Best of several / EAA quick look; (2) `ADD AUTOMATION` chips: Guiding, Dusk flats, Dome, Cloud-dodge calibration, HFR watchdog, Notify my phone; (3) target(s) input (comma list ⇒ pool candidates). Footer: `GENERATE FLOW` (accent) + `START BLANK` (quiet). Generation rules (match `genWizard()`): flow lane = dusk → [dome] → [dusk flats] → target|pool → slew → autofocus → [guide (skipped for EAA)] → capture (EAA: 4s g300 bin2 ×60, goal 0) → report; rules row = cloud-dodge (cloudwatch+hold+queue, +flat panel wired to `panel` iff Dusk flats picked), watchdog (capture.frame→condition→refocus), notify wired to condition, else cloudwatch, else a safety+abort pair is created. Toast: "Flow generated - every stage is editable". Every generated graph must pass the doctor.

---

## Node vocabulary (contract)
Ports: `kind: flow` (cyan; exactly one run cursor travels it) or `event` (amber; may fire any time). Wiring only kind→same-kind. **Fan-in rule**: a flow input takes exactly one wire (new wire replaces the occupant); an event input accepts MANY wires. **Loop-back rule**: the flow lane must stay acyclic (that is the compile guarantee), but event wires may point backward - SESSION REPORT `target done` → POOL `advance` is how a campaign loops; the doctor enforces DAG on flow wires only. Per node: label · cat/color · ins/outs · params (defaults in the prototype's `DEFS`) · backend mapping:

| Node | Ports (in → out) | Compiles to / backend |
|---|---|---|
| DUSK WINDOW | → window (flow), night ends (event) | `Schedule` start_mode dusk/offset/stop, min_altitude_deg (`schedule.py`) + **campaign repeat** (Single night / Nightly until pool complete / Nightly ×30): with repeat set, dawn is a scheduled hold - cursor persists, flow re-arms next dusk |
| TARGET | arm → target (flow) | `Target` (name, ra, dec, rotation_deg) |
| SAFETY MONITOR | → unsafe (event) | existing fail-closed `SafetyMonitor`; stale ⇒ unsafe, always. **Watch-for scope param**: "Rain + wind + power (pair with Cloud Watch)" or "Clouds + rain + wind (standalone)" - safety ABORTS and never holds, so a flow that also has CLOUD WATCH must scope safety away from clouds (doctor rule 13) or the two race and safety wins |
| CLOUD WATCH | → clouds in, clouds clear (events) | **new** transient-weather trigger source (weather integration); threshold % + clear-hold debounce (min). Distinct from safety: it holds, never aborts |
| DOME CONTROL | open (flow) → shutter open (flow) | **new** `Dome` device role (Alpaca Dome); bind-to-mount; on-unsafe close is non-negotiable |
| FLAT PANEL | → panel ready (event) | **new** `FlatPanel`/CoverCalibrator role; position, ADU target, solve-per-filter |
| SLEW + CENTER | run → centered | engine slew + plate-solve iterate (tolerance ′, max iterations, solver ASTAP/NINA/sim) |
| AUTOFOCUS | run → focused | V-curve sweep or native delegation (`focus/autofocus.py`) |
| GUIDE | run → guiding | PHD2/NINA/sim guider; settle ″, dither every N |
| CAPTURE LOOP | run → complete (flow), frame graded (event) | `ExposureStep` (filter, exposure_s, gain, binning, count) + reject-HFR grading + **integration goal (h)** |
| FILTER CYCLE | run → complete (flow), frame graded (event) | **new** interleaved capture strategy: slot table shot one-sub-per-slot per pass, repeated until every slot hits the cycle count. **Plan editor is structured, not freetext**: one row per filter in the RIG'S WHEEL (from the equipment panel - never a hand-typed filter name), checkbox to include + right-aligned exposure field (s) when included; row order follows the wheel; broadband defaults 60s, narrowband 180s. Channels grow evenly; per-filter focus offsets on every change; hold/resume resumes at the same slot; triggers attach to the same `frame graded` port as CAPTURE LOOP |
| DUSK FLATS | run → flats done | **new** engine stage: wait for sun-alt window (−2°…−8° etc.), shoot flats via translucent cap / panel / sky, solve to `adu_target` (PRO-5) per filter |
| CALIBRATION QUEUE | do, stop, panel (events) | **new**: rotate to black slot (cover method) → darks if stale → bias if stale → flats if stale AND panel wired → quota each → wait; `stop` exits clean at frame boundary |
| TARGET POOL | arm (flow), advance (event) → best target (flow), floor hit (event) | **new** scheduler mode over existing constraints (min alt, moon sep, max HA); strategies: best available / priority / round robin; **per-target quota** (cycles); `advance` marks the active target done in the ledger and re-scores the REMAINING members - done targets never re-selected, across nights. **Altitude-floor watch**: when the active target sinks to the floor, fire `floor hit`, suspend that target's cursor (NOT done - retries next night), hand out the next best; param "At altitude floor": advance-now (default) / keep-imaging (not recommended) |
| CONDITION | events → fire (event) | `Instruction` trigger/`Condition` compound (closed predicate vocabulary - never a scripting runtime). When-vocabulary (each maps to an existing/additive measured metric): HFR above · FWHM above · Guide RMS above · Guide star lost · Frame rejected · Star count below · Sky background above · Wind gust above · Dew margin below (ambient−dew point) · Sensor temp off setpoint · Disk space below · Airmass above · Meridian flip within · Target complete |
| NOTIFY | do → | alert sinks ntfy/Telegram/webhook (`alerting.py`), level |
| REFOCUS | do → | pause-at-boundary + autofocus + resume (existing actions) |
| HOLD / RESUME | pause, resume → | **new** action pair over engine pause/resume; params: while-paused (guider policy), max hold + on-timeout (Abort+park default), on-resume checklist: **cooler gate: re-cool + stabilize** → restore active filter (always) → re-center (solve) or trust tracking → refocus if-drifted/always/never → guide re-settle → same slot |
| ABORT + PARK | do → | existing abort: park, warm, reason → report |
| PARK + CLOSE | do → closed (event) | **new** scheduled end-of-night shutdown (NOT an abort): park mount, close closure (dust flap + dome / dome shutter / dust flap / roll-off roof), cooler hold-cold-for-day-darks or warm; preserves the campaign cursor; `closed` can chain into a CALIBRATION QUEUE for capped day darks. Safety close remains independent and unconditional |
| SESSION REPORT | session → target done (event) | `report.py` append-only ledger; `target done` fires when the active target's quota is met - wire back to POOL `advance` to run a campaign |

## Compile output (PLAN tab / run payload)
Deterministic function of the graph (see `compilePlan()`):
```json
{
  "name": "M16 - full-service night",
  "schedule": { "start_mode": "dusk", "start_offset_min": -30, "stop_mode": "dawn", "min_altitude_deg": 30 },
  "targets": [ { "name": "M16 - Eagle", "ra": "…", "dec": "…", "rotation_deg": 0,
                 "steps": [ { "filter": "Ha", "exposure_s": 180, "gain": 100, "binning": 1, "count": 20, "frame_type": "Light" } ] } ],
  "automation": { "dome": { "bind": true, "on_unsafe": "close" },
                  "dusk_flats": { "method": "Translucent lens cap", "window": "Sun −2° … −8°", "adu_target": 28500, "count": 15 },
                  "calibration_queue": { "order": ["dark","bias","flat"], "policy": "if_stale", "quota": 20, "flats_require_panel": true } },
  "instructions": [ { "when": "on_clouds_in", "action": "holdresume" }, { "when": "on_clouds_in", "action": "calib" },
                    { "when": "on_clouds_clear", "action": "holdresume" }, { "when": "on_hfr_above", "threshold": 3.2, "action": "refocus" } ]
}
```
Pool targets carry `pool_rank`, `quota_cycles`, `min_altitude_deg`, `min_moon_sep_deg`, `max_hour_angle_h`. A FILTER CYCLE stage compiles to a single step object `{strategy: "cycle", cycles, per_cycle, gain, binning, reject_hfr, slots: [{filter, exposure_s}…]}` - the engine interleaves one sub per slot per pass and resumes mid-cycle after holds; **no loop construct exists at graph level** (the graph stays acyclic - loops live inside stages, and campaign loops are event wires). A campaign flow adds `campaign: {repeat: "nightly", until: "pool_complete"|"nights_30", resume: "cursor"}`. new TriggerKinds are **additive** to the existing closed enum: `on_clouds_in`, `on_clouds_clear`, `on_night_end`, `on_target_complete`, `on_shutdown_complete`, `on_altitude_floor`; CONDITION whens map generically (`on_hfr_above`, `on_star_count_below`, …); new action `pool_advance`. FLAT PANEL `ready` → QUEUE `panel` edges are equipment topology, NOT instructions - they compile only to `automation.calibration_queue.flats_require_panel` and are excluded from the `instructions` array.

## Backend work list (server/)
1. Flow storage: `GET/POST /api/flows`, `GET/PUT/DELETE /api/flows/{id}` - persist the raw graph (nodes: id/type/x/y/params; edges: from/fromPort/to/toPort) plus library metadata (`name`, `folder` path, `tagline`, `last_run`, `last_result`) in the atomic config store pattern; the graph is the source of truth, the plan is derived. Folder CRUD: `GET/POST /api/flows/folders`, rename/delete with re-parenting. Ship the five example flows as read-only fixtures in an `Examples` folder.
2. `POST /api/flows/{id}/compile` → `{plan, issues[]}` - server-side doctor mirrors §8 (UI re-checks locally for latency, server is authoritative).
3. `POST /api/flows/{id}/run` → hands the compiled plan to `SequenceEngine`; background task; progress on the existing WS bus. New per-node events: `flow.node` `{flow_id, node_id, status: idle|busy|ok|warn|bad}` and `flow.log` `{msg, tone, ts}` so the canvas animates from truth.
4. `GET /api/flows/{id}/tonight` → resolved ephemeris for the timeline/story: dusk/dawn/dark times, per-target windows + altitude curves, transit, meridian-flip time, moon rise/illum/sep (all from `visibility.py` + `schedule.py`), integration ledger per filter (banked vs goal, from session reports).
5. `GET /api/calibration/health` → the matrix: rows keyed (kind, exposure, gain, temp, filter, rotation) with have/need counts + STALE/OK/MISSING verdicts; the queue consumes the same endpoint.
6. New device roles: `Dome`, `FlatPanel` in `devices/base.py` + Alpaca implementations + sim implementations (sim first - the whole surface must demo without hardware, like everything else).
7. Engine: hold/resume with the full resume checklist (cooler gate → restore filter → optional re-center → conditional refocus → guide re-settle → same slot), calibration-queue runner, dusk-flats stage, pool scheduler with per-target quotas + ledger-backed done marking, transient-weather trigger plumbed to the weather integration, a **cooler gate** owned by the engine: no capture stage (loop or cycle) may start or resume unless the sensor is at setpoint and stable (tolerance e.g. ±0.5°C held ~2 min); if cooling stopped for any reason (daybreak park with warm-up, power cycle, cooler fault) the gate re-cools ramp-limited and BLOCKS capture until stable, logging honestly ("waiting on cooler: −4.2°C → −10°C"). At nightly re-arm the cooler ramp starts immediately (with closure still closed is fine) so cooling overlaps slew/flats/focus rather than wasting dark time. And **campaign mode**: nightly re-arm (dawn = scheduled hold), scheduled shutdown lane (PARK + CLOSE, day darks while capped), and capture-cursor persistence that survives process restarts - a month-long unattended run must resume correctly after a power cycle. All fail closed; hold has a max-hold timeout defaulting to Abort+park.

## State management (ui/)
Extend the single Zustand store (narrow selectors - a node-status tick must re-render one node, not the canvas): `flows` (library), `graph` (nodes/edges), `sel`/`editNode` (selection vs edit-sheet are **separate**), `pan/zoom`, `wire` (pending), `statuses`, `runState` (running/eta/curStage/frames), `logs` (ring buffer ~120), `toasts`, `tonight` (fetched), `ui` (tab, sheets, palette). Drag/pan/wire use window-level pointermove/pointerup; wire drop resolves via `elementFromPoint().closest('[data-port]')`. Undo/redo for graph edits is a production must (not in prototype).

## Design tokens (prototype hex → real token)
`#06070B`→`--bg` · `#12141C`→`--bg-raise` · `rgba(12,14,22,.85/.92)`→`--bg-panel` · `rgba(120,140,200,.18)`→`--line` · `rgba(140,160,220,.35)`→`--line-bright` · `#e8ecf7`→`--text` · `#9aa6c2`→`--text-dim` · `#7683a5`→`--text-faint` · `#00D2FF`→`--accent` · `#9B51E0`→`--accent-dim` (borders/large text only - fails AA as small text) · `#3ddc97`→`--good` · `#ffb454`→`--warn` · `#ff5470`→`--bad` · `#4d7cff`→`--sky` · glow `rgba(0,210,255,.45)`→`--glow`.
Category colors: sources `--accent`, equipment+rig `--sky`, logic `--accent-dim`, actions `--warn`, abort `--bad`, sinks `--good`. Night mode: **do not** replicate the prototype's filter hack; the token swap does it.
Type: Chakra Petch 500/600 (display/labels), IBM Plex Sans 400/500 (prose), IBM Plex Mono 400/500 (values, tabular-nums) - already in `ui/` via @fontsource. Spacing/radii: panels 16, nodes 12, buttons 10, chips 999; port rows 20; canvas grid 36.

## Do-not list (how not to muck it up)
- Copy style: NO EM-DASHES anywhere (UI labels, node descriptions, generated briefs, logs, toasts, docs). Use hyphens, colons, commas, or separate sentences. If any shipped string still contains an em-dash, replace it.
- Terminology: dome azimuth follows the mount via "bind" ("Bind to mount", "bound to the mount"). Never "slave"/"slaved" in UI, code identifiers, API fields, or comments.
- No new colors, fonts, or radii; no hardcoded hex where a token exists.
- Status is never color-alone: keep the LED silhouettes, dashed event wires, dashed sim badge, glyph+word on danger.
- Overlays always portal (the `Overlay` primitive); never `fixed inset-0` inside the view tree; opaque `--bg-raise` surfaces; footer actions pinned; `dvh` + safe-area insets.
- Honest-disabled: locked controls stay pressable and say why (`HonestButton`/`LockedNote`), never bare `disabled`.
- 44px touch floor for anything driven at the scope (`.tap`/coarse-pointer bump); `.field-rig`-class compact inputs are fine for setup-once params.
- Animations: entry anims fill-mode `backwards` (never `both`); everything meaningful must survive `prefers-reduced-motion` as a static cue.
- Fail closed everywhere: stale safety = unsafe; dome closes on unsafe regardless of flow state; max-hold defaults to Abort+park.
- The graph compiles to the closed instruction grammar - resist any "just add a script node" temptation.

## Assets
- `assets/bg_nebula.png` - copied from `ui/public/bg_nebula.png` (already in the app).
- Logo: inline SVG from `src/components/Logo.tsx` (do not re-draw).
- Icons: use the app's `Icon` set (`components/icons.tsx`); the prototype's pencil is a stand-in for its edit glyph.

## Screenshots (`screenshots/`)
Ground-truth renders of the approved prototype - compare your implementation against these before calling any screen done. Captured at ~924px width except the phone pair (390px app width):
- `01-library.png` - Library: search + folder chips, MY FLOWS (with NEW FLOW card) and EXAMPLES sections.
- `02-editor-m16-full-service.png` - Editor, M16 full-service graph fitted: flow lane top, rules lane below, event wires dashed amber.
- `03-inspector-calibration-queue-matrix.png` - CALIBRATION QUEUE selected: inspector with LIBRARY HEALTH matrix + fields.
- `04-tonight-timeline.png` / `05-tonight-story.png` / `06-tonight-plan-json.png` - the three Tonight tabs.
- `07-run-cloud-dodge-hold.png` - mid-run during the cloud hold: STOP + ETA in header, toast, queue busy, active dashed event wires, dark-quota log line.
- `08-night-mode.png` - night render (prototype approximates with a filter; production = token swap, same geometry).
- `09-wizard-new-flow.png` - guided wizard sheet.
- `10-phone-flow-autograph-390px.png` - phone FLOW tab: auto-laid zigzag graph, compact nodes, real wires, bottom FLOW/CANVAS/MONITOR bar, truncating header title.
- `10b-phone-tap-to-wire-armed.png` - tap-to-wire armed: filled source port + accent hint bar ("WIRING: DUSK WINDOW · window opens - tap an input port") with CANCEL.
- `11-phone-monitor-390px.png` - phone MONITOR tab.
- `10-phone-flow-rail-390px.png` - an earlier linearized-list variant of the FLOW tab, superseded by the auto-graph; kept only as reference for stage-card anatomy.

## Files in this bundle
- `AstroDeck Flows.dc.html` - the reference prototype (template + logic in one file). Read `DEFS` (node contracts), `PRESETS` (seed flows), `issues()` (doctor), `compilePlan()` (compile), `tonight()` (timeline/story data), `genWizard()` (wizard), `fireClouds()` (cloud-dodge storyboard).
- `assets/bg_nebula.png`.
- `screenshots/` - 11 ground-truth captures (see Screenshots section).
