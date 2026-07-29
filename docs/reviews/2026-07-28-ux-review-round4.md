# AstroDeck — Consolidated UX Review (4 personas, round 4)

**Build under review:** UI bundle built 2026-07-28 07:32 (last commit in it: `3c0f545`). Only two UI commits landed after it — the ASIAIR backend panel and `9bb8a9c` (subtitle deletion, 14:27). **Everything below except the Equipment subtitle stands against current `main`.** Where I could reach the source, I root-caused the finding and cite the line; those are marked **CODE-CONFIRMED**.

**Headline:** the mono persona reached its goal — the Flow-C blocker is genuinely dead. Nothing in this round is a layout wall. What replaced it is worse in a quieter way: **four separate paths now write wrong data while reporting success.** Three of them are single-line plumbing bugs in controlled inputs, and one is an architectural split between "connected" and "assigned" that produced a different catastrophe for each of three personas.

---

## 1. Systemic patterns

### S1 — Two state models, "connected" and "assigned", presented as one screen
**Hit by all four personas, in four different disguises. This is the most expensive structure in the product.**

The rig you are *driving* and the rig the app can *save* are different objects. `Simulator rig`, `Detect hardware rig` and a boot profile populate the **device** layer; the **assignment map** is a per-browser `localStorage` object — a fact the code itself admits in a comment at `ui/src/views/EquipmentView.tsx:526-534` ("these picks live in ONE browser's localStorage… with nothing on screen ever having said so"). Everything downstream inherits the split:

- **Profiles capture assignments, not connections.** So `Save` is gated on `assignedCount === 0` (`EquipmentView.tsx:567`) — with the **native `disabled` attribute**, no reason, no tooltip. Designer connected via the guide, got a live rig with an empty assignment map, and could never enable SAVE: *"real keystrokes, short name, long name, blur, Enter — disabled throughout."* **CODE-CONFIRMED as a genuine dead end.** Novice succeeded only because `doSimRig` writes the map into *that* browser (`EquipmentView.tsx:242-247`) — the two reports do not contradict, they bracket the bug.
- **ACTIVATE connects the profile's device set**, so activating a thin profile tears down a live rig. The confirmation dialog exists but is gated on `resolvesRealMotion()` — real mount/focuser — not on "this will disconnect 11 live devices" (`ProfileList.tsx:111-129`). Novice, on a sim rig, correctly got no dialog and lost the whole rig in one tap, on the step the guide told him was how he'd get his rig *back*.
- **The Equipment page shows both models side by side and they disagree.** Pro: `GUIDING · UNASSIGNED` in the device row, `GUIDING · CONNECTED` with a green tick in LINK STATUS, three feet apart, on the night's load-bearing question. Novice: every row "unassigned" beside a green CONNECTED. Designer: the same status printed twice.

The explanatory banner ("this rig was started somewhere else…") is good writing papering over a model problem. **Fix direction:** one authority. Rows render what is connected; a profile captures the connected rig; ACTIVATE confirms on *devices it will drop*; if the assignment map must stay per-browser, it must never be the thing profiles are made of.

### S2 — Controlled-input plumbing corrupts what you type, in the two fields that decide the night
**CODE-CONFIRMED in both components. Different bugs, same family, same consequence: wrong values that never error.**

- **Plan numeric fields** (`ui/src/views/SequenceView.tsx:415-418`): `num = (v, fallback) => Number.isFinite(n) && v !== "" ? n : fallback`. An empty string returns the *previous* value, so the field re-renders as its old contents and **can never be emptied**. Every edit therefore concatenates. Pro's transcript — 120 → backspace → `12` → `1` → `1` (refuses to empty) → type 300 → **1300** — is exactly what this code does. Same for gain, count, dither cadence, meridian lead, and the cooling setpoint (where the fallback re-inserts `-10`, producing `-1010`).
- **Filter slot names** (`FilterNamesModal.tsx:74-105` + `EquipmentView.tsx:807`): the focus-trap effect's deps are `[open, onClose]`, and the parent passes `onClose={() => setOpen(false)}` — a new identity on **every** parent render. Any device-status poll re-runs the effect, whose first act is `panel.querySelector("input, button:not([disabled])").focus()` → **Slot 1's name field.** Type one character, the poll lands, focus jumps, the rest of your typing lands in Slot 1. This explains pro's precise transcript *and* why mono (typing a full name without a poll landing mid-word) never saw it. Not a contradiction — a race.

The house pattern for this already exists two files away: `CaptureView.tsx:388-400` holds exposure as a **string** and validates at render. The plan editor simply doesn't use it.

**Why this is rank 1:** the values these fields hold are exposure time, sensor setpoint, frame count, and the FITS `FILTER` header. Getting them wrong produces a full night of plausible-looking, unusable data, and the run reports success.

### S3 — The reason-on-a-locked-control pattern is the best thing in the product, and it is unenforced exactly where it costs most
All four personas independently called this out as excellent — "Unavailable — the cooler is already off", "Next unlocks once your real location is saved", and a padlocked STARS button that pops *"No per-star data for this frame"* **on touch**. Then all four found the exceptions, and the exceptions cluster on the highest-stakes controls:

| Control | What it does when blocked | Who |
|---|---|---|
| `RUN SEQUENCE` (disabled) | `aria-disabled=true`, empty `aria-label`, no title — the one disabled control in the app that says nothing | pro |
| Profiles `SAVE` | native `disabled`, three hidden preconditions — sitting beside `Connect Rig`, which the team *deliberately* converted to aria-disabled + a printed reason (`EquipmentView.tsx:494-510`) | designer, novice |
| `DETECT HARDWARE RIG` | fires 7 real scans (all 200), then reports **nothing**; the "WORKING…" state appears on a *different* button | novice |
| Plan catalog search | silent on no-match — while the **Atlas** search has a good no-match state naming the catalog's coverage | pro, designer |

It is not a missing pattern; it is an unlinted one. A rule banning the native `disabled` attribute on interactive controls, plus one house "no result" component, closes the whole column.

### S4 — Success reported before it is earned ("green while wrong")
The app's writing standard is explicitly against this — *"No faults reported — but weather monitoring is off, so nothing is watching the sky"* is the single best sentence in the product, and pro said so unprompted. The code doesn't match the writing:

- A 409 `no camera connected` leaves `READING…` + a striped `DOWNLOADING…` bar running for ~40 s (novice).
- The per-frame timer reads `frame 00:00 / 300s` with an empty bar for the entire exposure (mono) — **CODE-CONFIRMED**, see #3 below.
- `✓ Roof open` renders a green tick beside a red "unsafe — rain sensor wet" banner (pro, desktop **and** phone).
- The stacking bundle manifest reads `"masters": {dark:false, flat:false, bias:false}` and `"warnings": []` minutes after the darks/flats/bias were shot (pro).
- Pre-flight downgrades a physically impossible cooling setpoint to **WARN** and lets the run start (pro).
- *"You're all set — every step is done — clear skies"* over a frame of wherever the mount happened to be pointing (novice).

### S5 — Phone and tablet are the stated primary devices; width and reach are validated at desktop
Not one of these is visible on a 1440px screen:

- **Atlas empty state is 606px wide** on 390/412/440 phones (measured independently by novice and designer). **Root cause found:** commit `6d890ab` (2026-07-23, *"NOV-3 what can I image tonight? ranked picker"*) widened the card `max-w-md → max-w-xl` so the new list would fit — the spec even says so (`docs/superpowers/specs/2026-07-23-difficulty-picker-design.md:1018-1020`). The feature built **for the novice** broke the screen the novice is sent to, and the same component fits perfectly on the Tonight view.
- `RUN SEQUENCE` at **8.7 screenfuls** on phone, 5.3 on tablet, 6.4 on desktop — and it moves further away with every session ever run.
- Landscape rail: 8 of 14 destinations hidden behind a scroll, under a rounded bottom edge that reads "the list ends here" (scrollHeight 765 / clientHeight 330).
- Catalogue suggestion altitude badges clipped 30px past a tablet's right edge — the badge exists to carry the number you choose by.
- 36×20 toggles for *meridian flip* and *apply filter focus offsets*, in an app that ships 44×48 targets elsewhere.
- `(i)` explanations for gain/offset/binning are **hover-only** on a device with no hover — while the padlock tooltips *do* open on touch.

**The setup guide is where S1, S4 and S5 land on one user in one session:** it sends the beginner to the clipped Atlas (S5), tells him to save a profile that can't describe his rig (S1), and congratulates him for a frame of nothing (S4).

---

## 2. Ranked issues

Ranked by expected cost to a real user. Phone/tablet weighted above desktop; data loss and lost nights first.

| # | Issue | Bucket | Hit by | Verdict |
|---|---|---|---|---|
| 1 | Plan numeric fields cannot be cleared; every edit concatenates (120→1300, −10→−1010) | MEASURED DEFECT | pro | **CODE-CONFIRMED** `SequenceView.tsx:415` |
| 2 | Profile system built on the per-browser assignment map: SAVE dead-ends, ACTIVATE tears down a live rig with no confirm, panels contradict | BLOCKED TASK + MEASURED DEFECT | all four | **CODE-CONFIRMED** `EquipmentView.tsx:567`, `ProfileList.tsx:113-129` |
| 3 | Per-frame exposure timer frozen at 00:00 for the whole sub | MEASURED DEFECT | mono | **CODE-CONFIRMED** `engine.py:1329-1334` |
| 4 | Filter slot rename: one character per field, remainder corrupts Slot 1; names go to FITS headers | MEASURED DEFECT + BLOCKED TASK | pro | **CODE-CONFIRMED** `FilterNamesModal.tsx:74-105` |
| 5 | Atlas empty state 606px on every phone; the BEGINNER filter is unreachable | BLOCKED TASK | novice, designer | **CONFIRMED**, cause traced to `6d890ab` |
| 6 | First tap after a scroll / during async reflow is swallowed | MEASURED DEFECT (symptom) | novice, mono, designer | **symptom CONFIRMED, cause NEEDS-REPRO** — see below |
| 7 | A Bias step blocks the entire run; the rejected value is the app's own default and is inside the range the error quotes | BLOCKED TASK | pro | **CODE-CONFIRMED** `lib/exposure.ts:19` vs `SequenceView.tsx:994` |
| 8 | `RUN SEQUENCE` is 5–9 screens down and recedes with history | BLOCKED TASK (temporarily) | mono, pro | CONFIRMED, measured on 3 viewports |
| 9 | Resuming splits a project into same-named reports; bundle ships 1 of 4 lights; "nights" counts runs | MEASURED DEFECT | mono | CONFIRMED vs `/api/sequence/state` |
| 10 | Stacking bundle ships with no masters and `warnings: []`; rebuild is a manual Settings trip nothing points to | MEASURED DEFECT | pro | CONFIRMED (manifest read) |
| 11 | Safety gate is not evaluated during cooling / pre-exposure phases | MEASURED DEFECT | pro | **CODE-CONFIRMED** `engine.py:632-634` |
| 12 | `✓ Roof open` during rain; trip behaviour never stated anywhere before it happens | MEASURED DEFECT | pro | CONFIRMED |
| 13 | Impossible cooling setpoint: WARN not ERROR → 10 min burned, then fail-open shoot-warm | MEASURED DEFECT | pro | **CORRECTED** — see §5 |
| 14 | 409 leaves a fake `READING… / DOWNLOADING…` running ~40 s | MEASURED DEFECT | novice | CONFIRMED |
| 15 | Guided flow never slews; Capture keeps the `M42` placeholder while the plan says NGC 7000 | MEASURED DEFECT | novice | CONFIRMED |
| 16 | Renaming slots silently blanks the filter on every saved plan while the card prints the old names | MEASURED DEFECT | pro | CONFIRMED |
| 17 | No wheel connected → "filter steps ignored", run proceeds, SHO plan becomes 9 unfiltered subs | MEASURED DEFECT | pro | CONFIRMED |
| 18 | `DETECT HARDWARE RIG` reports nothing on an empty scan; progress shows on the wrong button | MEASURED DEFECT | novice | CONFIRMED (7×200 in the network log) |
| 19 | Setup guide: 5→6 steps, vanishes on reload, phone copy on tablet, tells you to reconfigure hardware mid-exposure, snaps backward at first light | MEASURED DEFECT | novice, mono, designer | CONFIRMED (3 personas) |
| 20 | Plan catalog search silent on no-match; catalog is 25 objects | MEASURED DEFECT | pro | CONFIRMED |
| 21 | 36×20 toggles on meridian flip / filter offsets / safety gate | MEASURED DEFECT | mono, designer | CONFIRMED, below the app's own standard |
| 22 | Landscape rail hides 8 of 14 destinations with no affordance | MEASURED DEFECT | novice | CONFIRMED |
| 23 | Sky map cannot be panned by touch; the pan control is below the fold and unreferenced | BLOCKED TASK | designer | CONFIRMED (pixels identical) |
| 24 | `(i)` tooltips don't open on touch; a near-miss focuses the gain field | MEASURED DEFECT | novice | CONFIRMED (padlock tooltips do work — the pattern exists) |
| 25 | Cooling presented as a mandatory step with a locked NEXT, for beginners whose first camera has no cooler | TASTE (well-argued) | novice | — |
| 26 | Nothing tells a filter shooter what the project still owes per filter | TASTE (design gap) | mono | — |
| 27 | Night mode: OFF toggles brighter than ON; "red = danger" collapses; Atlas canvas undimmed at `brightness(1)` | TASTE / night-vision | designer, mono | measured, judged |
| 28 | 11px data floor; 10px per-filter totals; 9px "hold to confirm" | TASTE | mono, designer | measured, judged |
| 29 | `-68° above the horizon`, `BIASS (1)`, RA as `0.712h`, `59940m` vs `999h 0m`, letterboxed first-light preview, `—` status column, "Rig" vs "Equipment" | TASTE / craft | various | — |

---

## 3. Detail on the top items

**#3 — the frozen timer, root-caused.** `_begin_frame()` stamps `_frame_started_at = time.time()` (`engine.py:2069-2080`) and the very next statement publishes a snapshot (`engine.py:1329-1334`). `_set_state` builds `progress` *by value*, including `server_now_ms` from `compute_eta()`. Nothing calls `_set_state` again until the frame completes — at which point `_frame_started_at` has already been zeroed (line 2103, before line 2109). So every poll for the entire exposure returns a frozen pair where `server_now_ms == frame_started_at_ms`, and the client's `SubFrameBar` computes an age of 0.0 s forever. Mono's measurement is exactly right. **Fix:** recompute `server_now_ms` at read time on `/api/sequence/state`, or anchor the client's clock offset once and interpolate from `Date.now()`.

**#6 — the eaten tap: symptom confirmed, cause not.** This is the single most-hit symptom of the round (novice 4/4 with a delay sweep; designer 6 instances instrumented; mono 3 times) and I could not confirm the cause. **There is no scroll-cancel or touch-guard anywhere in `ui/src`** — I grepped for `touchstart`/`touchend`/`isScrolling`/`suppressClick`/`pointer-events` gating and found only legitimate overlay usage. So novice's "find the scroll-cancel guard and shorten its window" has nothing to find. Designer's instrumented evidence is the strongest and points elsewhere: `touchend`'s target was `HTML` (empty) once and a *different control* (a `MOVE TO / −1° / +1° / HALT` mount cluster) once — i.e. the page reflowed under the finger as async panels populated. That fits "controls below the fold", which are precisely the ones that just came into view and are still filling in. Against that: designer also showed **mouse click succeeding at the same pixel where two touch taps failed**, which reflow alone doesn't explain, and mono explicitly attributed the whole thing to the harness. All four drove CDP synthetic touch, so a harness artifact is live.

**Verdict: NEEDS-REPRO on a real finger on a real phone before engineering time is spent — but repro it first, this week.** If it is real it is rank 2 or 3, and the "tap lands on a mount-motion control" variant is the expensive-mistake class. Reserving space for async panels (skeletons / fixed heights) is worth doing regardless.

**#7 — the Bias block, root-caused.** `isExposureValueInvalid(n) => !isFinite(n) || n <= 0 || n > 3600` (`lib/exposure.ts:19`), and the error string is *"Exposure must be 0–3600s"* (`SequenceView.tsx:994`). Zero is inside the stated range and is what a bias frame *is*. The same rule makes every newly-added Dark step (default exposure 0) instantly invalid. The message also says "the highlighted step(s)" while nothing is highlighted, and doesn't name the step — pro had to bisect four steps to find it. Three separate fixes, all small: exempt Bias, name the step, actually highlight it.

**#11 — the safety hole, root-caused.** `_cool_and_wait`'s loop awaits `self._checkpoint()` each iteration, and `_checkpoint` is *only* `await self._paused.wait()` (`engine.py:632-634`). It honours a pause; it never reads the safety monitor. That evaluation lives on the frame-boundary path — which is why pro saw the gate fire correctly (~35 s) on a run that reached its frames, and not at all during a 4.5-minute cooling wait with the roof open. Pro's framing is right: the hole is exactly where an unattended rig is least supervised.

---

## 4. Answers to the questions this round was run for

### Did the mono persona reach its goal?
**Yes — and the Flow-C blocker is genuinely, measurably dead.** Plan on tablet portrait now has zero horizontal overflow (`main.scrollWidth 732 == clientWidth 732`), AUTOMATION is a single full-width vertical stack, and every one of dither cadence, dither size, refocus-every-N, refocus-on-ΔT, **apply filter focus offsets**, meridian flip and meridian warn lead is reachable with a finger. Mono configured a real Ha/OIII/SII project — 60 frames, 5h — in about nine interactions, ran it, aborted it, and resumed it the next night. The `+ STEP` inheritance (exposure and count carried down from the row above) is the specific thing that made it nine interactions and not thirty.

Two caveats on the win. First, mono nearly quit anyway — not on layout, but hunting for `RUN SEQUENCE`, after trying Plan, Tonight and Capture and concluding the app had no way to start a plan. Second, mono's *data* did not survive the round trip: after one interrupted night the project reads `ACCEPTED 1 · Ha 1 frames` against a session that actually holds 4, and the bundle contains one light. So the persona can now **build** the project it couldn't build last round, and still cannot **account for** it.

### What did Flow G (the rig changes) expose?
Flow G was the highest-yield addition of the round. It found three defects nobody had ever been in a position to see, and — importantly — it also *confirmed two previous fixes held*.

**What it broke.** Re-branding filters is currently impossible (#4): one character per field, remainder corrupting Slot 1, and the app itself states those names go into FITS headers and filenames. Downstream of that, renaming slots **silently blanks the filter on every saved plan** while the target card keeps printing the old names ("Ha 0h 0m OIII 0h 0m SII 0h 0m") — run it and you shoot unfiltered. And the seven focus offsets (0/12/10/15/120/110/115) stay bolted to completely different glass, with the Equipment row still reading "focus offsets set". Both mono and pro found the stale-offset problem independently, from opposite directions: mono renamed one slot for new glass and got no warning; pro renamed all seven. It is the worst-shaped failure in the product — it doesn't error, it just makes every frame slightly soft, forever.

**What it exposed about calibration.** The real calibration run is the good news and the bad news in the same breath. The FITS headers are *perfect* — `IMAGETYP`, `FILTER='Ha 3nm'`, `EXPTIME`, `GAIN`, `XBINNING`, `CCD-TEMP`, `OBJECT`, `INSTRUME` on every frame; filenames carry the filter; `frames.csv` carries 16 columns; the bundle groups under `NGC 3372/Ha_3nm/2s_g100_bin1`; calibration frames are correctly excluded from the integration total. And then the bundle you'd actually download at 8am says `"masters": {dark:false, flat:false, bias:false}` with `"warnings": []`, minutes after you shot all three, while the panel promises "together with the matching calibration frames". Masters only exist after a manual `Settings → Calibration → Rebuild library` that nothing points to. After the rebuild, matching is correct and keyed properly (flats by filter). So the machinery is right and the hand-off lies.

**What it exposed about removal.** Pulling the wheel is handled cleanly on Capture (filter buttons vanish, no orphans, the run line reads honestly "no filter 2s [2/3]") — but pre-flight says only "no filter wheel — filter steps ignored" without naming the steps, and leaves RUN SEQUENCE enabled. A saved SHO plan becomes nine identical unfiltered subs and the run reports "all targets complete".

### Did anything reappear that a previous programme claimed to fix?
**No true regressions. Two near-misses worth stating precisely, because one reviewer called a regression that isn't one.**

- **"your rig — one row per device."** All four personas reported it; designer explicitly flagged it as *"the exact string the owner already deleted as slop, back in the build — signals regression risk."* That is **wrong**, and I checked: `git log -S` shows the string was **introduced** on 2026-07-26 by `07f169a` (a UX-fix wave answering "#51: the nav calls this EQUIPMENT and every button calls it a rig") and deleted today by `9bb8a9c` at 14:27 — *after* the 07:32 bundle they drove. It was never deleted-then-reverted. Current source reads just "your rig". Fair to the build, and fair to the reviewers: it was on their screens.
- **The Atlas 606px overflow is not a regression, it is a new defect introduced by a fix.** `6d890ab` (2026-07-23) widened the empty-state card specifically so the new "what can I image tonight?" picker would fit — the design doc instructs it in so many words. The novice feature broke the novice screen. That is the pattern worth watching, not "someone reverted a string".

**Everything the previous programmes claimed, held, and the reviewers verified it by measurement rather than taking it on trust:** the Atlas map scroll trap is fixed *and signposted* (finger on the 700×700 WebGL canvas scrolls the page, 0→396 / 41→437, `trapped: False`, plus a visible "⇕ SWIPE SCROLLS PAGE" badge); the setup wizard is a **docked bar at 17.9% + 6.8% nav**, not a half-screen modal, and reopens from Help resuming at the first unfinished step; `"M 31"` with a space finds M31; the cooler COOL/WARM no longer wraps; Flat steps still default to a real filter, 3 s, and target ADU 25000 with the "≈ 38% of a 16-bit well" explanation; and the NoFilter grouping bug did **not** come back — mono and pro both checked for it specifically.

---

## 5. Strong claims, sanity-checked

| Claim | Verdict |
|---|---|
| "Profile SAVE can never be enabled" (designer, blocker) | **CONFIRMED as stated, for that state.** `assignedCount === 0` gates it (`EquipmentView.tsx:567`). Novice's contradicting success is explained: `doSimRig` writes the map in *that* browser. Not "never" — but permanently dead for anyone on a second device, a fresh context, or a boot-profile connect, with no reason shown. |
| "Renaming filter slots is impossible" (pro, blocker) | **CONFIRMED with root cause.** Mono's successful rename is a race, not a refutation. |
| "Numeric fields concatenate" (pro, blocker) | **CONFIRMED** at `SequenceView.tsx:415`. Trigger is *editing an existing value*; novice and mono typed into fresh fields and never hit it. |
| "An unreachable setpoint hangs the run forever, no timeout" (pro, blocker) | **CORRECTED.** There *is* a deadline: `cool_timeout_s = 600` (`models.py:175`). Pro's 296-second transcript simply hadn't reached it. What actually happens is arguably worse and definitely quieter: 10 minutes burned, then the default `require_cooling=False` / `cooling_action="warn"` path logs a warning to the bus and **shoots lights at whatever temperature the sensor is at**, with no UI surface. Keep the finding, restate it: not "hangs forever", but "burns ten minutes then silently fails open". The pre-flight WARN-not-ERROR half stands unchanged. |
| "The safety gate is not evaluated during cooling" (pro) | **CONFIRMED** at `engine.py:632-634`. |
| "Taps are swallowed for 1–2s after any scroll" (novice, blocker) | **Symptom CONFIRMED by three personas on real CDP touch; the stated cause is CONFIRMED FALSE** — there is no such guard in the source. **NEEDS-REPRO on real hardware.** Do not send anyone hunting a timer. |
| "Settings tab strip has a touch-dead zone at ALERTS" (designer) | **NEEDS-REPRO.** One persona, contradicted within its own evidence (UPDATES activates by touch at the same strip), and same-pixel mouse success is also the eaten-tap signature. Bundle it with #6's repro. |
| "TRACKING at ALT −35° with no below-horizon flag" (designer) | **DOWNGRADED — reviewer missed the existing surface.** Pro measured Mount showing `ALTITUDE ⚠ 16.9°` and Monitor showing `BELOW HORIZON — mount at −54°`. The real finding is narrower and still fair: the *header chip* doesn't carry it. Craft, not data-integrity. |
| "Activating a profile disconnects the rig with no confirmation" (novice, blocker) | **CONFIRMED, and the no-confirm is by design** — the dialog is gated on `resolvesRealMotion()`, so a sim or assignments-only profile correctly skips it. The gate is on the wrong predicate. |

---

## 6. What genuinely works

Worth protecting, because three of these were praised independently by personas who never spoke to each other.

- **The reason-on-a-locked-control pattern.** *"Unavailable — the cooler is already off"*, *"Next unlocks once your real location is saved"*, *"Start guiding first — a dither nudges the star and re-settles"*, *"Add camera gain + read noise above to enable Suggest"*. Novice specifically wanted it said loudly that a padlocked STARS button pops *"No per-star data for this frame"* **on touch**.
- **The observing-site sanity check** — named by every persona as the best copy in the product. It prints the region the coordinates actually point at and the current solar altitude there, updates live as you type, and the save toast echoes what was stored. It caught mono's sign error and pro's hemisphere in the act. Ship this pattern everywhere.
- **The pre-flight checklist.** Per-item READY / WARN / NOT NEEDED **with reasons**, repeated as a confirm modal at the moment of commitment with the WARN sorted to the top, and night-safe (shape + word, not hue). Pro — a fifteen-year NINA/SGP/ACP user — called it best-in-class.
- **The app refusing to overclaim.** *"No faults reported — but weather monitoring is off, so nothing is watching the sky."* *"You aborted the run at 3/60 frames. Nothing failed — the frames already captured are saved."* *"Resume picks up at frame 3 of 60. Re-run starts over from frame 1 and re-shoots what you already have."* That last sentence removes the single most expensive ambiguity in multi-night work.
- **Night mode is a system, not a filter.** Full hue conversion with zero green/white leaks, difficulty by glyph *and* word, chart series by dash pattern, windows by hatch, wallpaper suppressed to black. Audited across 5 viewports × 2 themes: contrast failures effectively zero, text overlaps zero, unnamed controls zero.
- **Filters are first-class** — slot names one tap from Equipment, subtitled with the actual slots, explained in situ ("Names appear in FITS headers and saved filenames"), with `LEARN OFFSETS AUTOMATICALLY` and a per-step filter in Plan.
- **Hold-to-abort resists a real finger** (quick tap: nothing; 2.5 s: aborted) and the sequencer cooled to −9.8 °C of a −10 °C setpoint on its own before the first light frame. Mono expected to babysit that.
- **The FITS/CSV/bundle data model** is correct end to end — the calibration *plumbing* is right even where the *hand-off* lies.
- **`Tonight`** answers "what do I photograph?" properly, fits a 412px phone with zero overflow, and encodes difficulty with a glyph so it survives night mode. Novice's closing observation is the most useful sentence any of the four wrote: *the app already has the answer, and the guided flow sends the beginner to the copy of that component that is 194 pixels too wide.*

---

## 7. Where a complaint is an imported preference, not a defect

Reported honestly, but do not treat these as bugs:

- **"No narrowband starter template"** (mono) — a real product judgement, not a defect. NINA/SGP habits. Worth doing (generate starters from the fitted wheel), but nothing is broken.
- **"RA rendered as `0.712h`"** (designer) — genuine domain convention, correctly identified; it is a formatting choice, not wrong data.
- **"The day-theme wallpaper shows through panels"** (pro) — aesthetic preference against a deliberate brand decision. Night mode already suppresses it. Note it, don't fix it on one reviewer's say-so.
- **"LOAD vs ACTIVATE need disambiguating"** (novice) — fair, but the underlying danger is S1, not the verb pair.
- **"Column headers repeat above every step row"** (mono) — the repetition is a deliberate fix for phone-width stacking, documented in `SequenceView.tsx:989-991`. Making it responsive is right; calling it sloppiness isn't.
- **"'Telemetry catching up' understates a lost connection"** (novice) — correct for a beginner, arguably correct for everyone, but it is a wording judgement about honest behaviour that already works (it appears on drop and clears itself on reconnect with no false "reconnected" claim).

---

## 8. Discarded

- **Raw audit aggregates** — "Plan 254 sub-12px nodes", "touch<44px: 60", "tiny<12px: 245". Counts without a judgement about which ones hurt. *The judged subsets are kept* (10px per-filter totals, 9px "hold to confirm", 36×20 automation toggles) — those name the specific element and why it matters.
- **"Night header shows `(L) LINK` where day shows only the avatar"** (designer) — reviewer already suspected a responsive-label difference and flagged needs-repro; no evidence of harm.
- **"Two panels on one screen disagree" as a separate minor** (novice) — merged into S1; it is the same defect as pro's LINK STATUS finding, not an additional one.
- **"Equipment subtitle is a regression"** (designer) — the *string* stays (all four saw it); the *regression claim* is discarded, disproven by `git log -S`.
- **"TRACKING at −35° with no warning"** as a data-integrity finding — the warning exists on Mount and Monitor; downgraded to craft, kept only as "the header chip omits it".
- **"The setup guide's phone-specific copy on a tablet"** as a standalone finding — merged into #19; it is one symptom of a guide that doesn't know its layout.
- **"Two-finger pan does nothing on the sky map"** as a separate defect from "no pan affordance" — merged into #23; one problem, one fix.

---

## 9. If only five things get fixed this week

1. `SequenceView.tsx:415` — hold numeric fields as strings, commit on blur. (One function. Rank 1.)
2. `FilterNamesModal` — `useCallback` the parent's `onClose`, or drop it from the effect deps. (One line. Unblocks all of Flow G2.)
3. `AtlasView.tsx:109` — `max-w-xl` → `w-full max-w-full min-w-0`. (One class. Unblocks the beginner's step 3.)
4. `engine.py` — recompute `server_now_ms` at read time, and evaluate the safety gate inside `_checkpoint`. (Two changes, one file. Fixes the dead liveness cue and the unattended safety hole.)
5. Repro #6 on a real phone with a real finger before anything else about it is believed.

Then the structural one, which is a design decision and not a one-liner: **make a profile capture the rig that is connected, not the rig one browser happens to have picked from a dropdown.**