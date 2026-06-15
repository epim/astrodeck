# AstroDeck Batch 2 — code-review fix list (33 confirmed)

I have all the confirmed findings. Let me produce the deduplicated, prioritized fix list.

---

# AstroDeck Batch 2 — Prioritized Fix List

## SHIP-BLOCKER VERDICT

**YES — Batch 2 is BLOCKED from committing.** There are **7 P1 defects** (after dedup), several of which break a primary production backend (NINA) or defeat a core safety feature on the most common path. None is a crash/data-loss (no P0), but multiple P1s are silent feature-death on shipping hardware paths. Fix all P0/P1 below before committing; P2s are strongly recommended in the same batch since several share the exact root cause (full_well plumbing, `.png` route) as the P1s.

After deduplication: **25 unique findings** (3 dedup collapses noted below). **0 P0, 7 P1, 8 P2, 10 P3.**

### Deduplications applied
- **DUP-A (3 reports → 1):** Alpaca `full_well` not copied to frame. Findings #1, #14 (and the sim half of #14) are the **same defect**. Merged into **P1-1**. Note: #14 correctly extends scope to `sim.py` AND notes NINA's `data_is_linear=False` independently disables the clip tag — so the clip feature is dead on *every* backend. Fix must cover Alpaca + sim + the NINA `data_is_linear` interaction.
- **DUP-B (3 reports → 1):** Monitor `PreviewTile` uses `.png` route. Findings #6, #13, #21 are the **same defect** (same lines 561/574). Merged into **P1-2**.
- **DUP-C (2 reports → 1):** Meridian-flip + `_begin_frame` flag reset. Findings #2 and #3 are **one coupled bug** (the `_begin_frame` reset makes the flip flag and the dither/AF flags all ineffective). Merged into **P2-1** as a single ordered fix.

---

## P1 — SHIP-BLOCKERS (fix all before commit)

### P1-1 · Clip/saturation pipeline dead on Alpaca + sim (and NINA tag) — `devices/alpaca.py`, `devices/sim.py`, `devices/base.py`
- **Change:** In `AlpacaCamera.expose()` (alpaca.py:377-381) set `frame.full_well = self.full_well` (and `frame.data_is_linear = True`) before returning, mirroring `nina.py:303`. In `SimCamera.expose()` (sim.py:114-123) give SimCamera a `full_well` (e.g. 65535 / its render saturation) and set it the same way. **Cleanest:** add `full_well: int | None = None` and `data_is_linear: bool = True` as fields on the `CameraFrame` dataclass (base.py:43-55) and populate in every backend.
- **Why:** `MaxADU` is probed into `self.full_well` at connect specifically to make the clip mask honest, but the frame never carries it, so `getattr(frame,"full_well",None)` is `None` for every Alpaca/sim frame → `clipAvailable=false`, clip overlay permanently disabled, `star_marks` saturation flag disabled, `CLIPPED` tag never shows. The entire `MaxADU→full_well` feature is silently non-functional on the two linear backends it exists for.
- **Note:** NINA sets `data_is_linear=False`, which the frontend also treats as clip-disabled — so verify the clip feature actually works end-to-end on *some* path after this fix; it may currently be dead everywhere.

### P1-2 · Monitor "Last frame" tile permanently STALE on NINA — `ui/src/components/monitor.tsx:561,574`
- **Change:** Change both `<img src>` in `PreviewTile` (incoming line 561 and fallback line 574) from `` `/api/preview/${previewId}.png` `` to the canonical mime-correct route `` `/api/preview/${previewId}` `` (and `` `/api/preview/${shownId}` `` for the fallback). `/thumb.jpg` is an even better thumbnail choice (always JPEG, kept ~50 frames) and also fixes evicted older sim frames.
- **Why:** The `.png` compat route returns 200 only when a lossless base is held OR `mime==image/png`. NINA frames are JPEG with no lossless → `.png` 404s for **every** NINA frame; sim/Alpaca 404 for any frame older than `PREVIEW_LINEAR_KEEP=2`. `onError` fires → tile shows STALE permanently on NINA rigs and never paints a live thumbnail. Matches `PreviewStage.tsx:143` and the tile's own (currently-false) docstring. The route test only checks the freshest frame, so it passes while the real consumer breaks (Risk-7 consumer-not-migrated).

### P1-3 · "Stretched PNG" download 404s for every NINA frame — `ui/src/components/preview/PreviewToolbar.tsx:161-168`
- **Change:** Gate the "Stretched PNG" menuitem on a PNG actually being available: `preview.has_lossless || preview.mime === 'image/png'`. When false, hide it or render the disabled+lock variant (mirroring the FITS item). **Also** handle the 404 at fetch time / only offer it for the latest 1-2 frames — `_trim_previews` nulls `entry.lossless` while the published `has_lossless:true` is never updated, so the stale flag alone leaves an eviction race.
- **Why:** The menuitem is an unconditional `<a href=".../png">`. For NINA (`lossless=None`, `mime=image/jpeg`) the server raises 404, so clicking downloads a 404 error page — violating the §12.5 honesty rule ("never offer a download that will 404"), the rule the sibling FITS item correctly honors. The file's own header comment documents the intended "never a 404" contract.

### P1-4 · Night-vision red-tint missing on linear preview (canvas) — `ui/src/index.css:120`
- **Change:** Broaden the selector to cover the canvas: `img.astro, canvas.astro, .astro-surface img.astro, .astro-surface canvas.astro { filter: var(--img-filter); }` (or simply `.astro { filter: var(--img-filter); }`). Verify NINA's inline `ninaFilter` still composes (it prepends `var(--img-filter)`, so it overrides correctly).
- **Why:** `--img-filter` (night red-tint) applies only to `img.astro`. The sim/Alpaca **dev-default + most common** path renders `<canvas className="astro">`, which no rule matches, so in night mode the linear preview renders full-brightness untinted grayscale — the brightest thing on screen blows dark adaptation, defeating night mode's whole purpose. PreviewStage/useImageRemap comments assume the tint applies to the canvas; the selector excludes it.

### P1-5 · `compute_histogram` blocks the asyncio event loop — `server/astrodeck/hub.py` (`_publish_preview`, ~lines 548 & 569)
- **Change:** Wrap both calls: `await asyncio.to_thread(compute_histogram, data)` for the NINA-branch `"histogram"` and the linear-branch `"histogram_linear"`.
- **Why:** Both `compute_histogram` calls (full-array `np.histogram` over the 16-bit frame, tens of ms on a large sensor) run synchronously on the event-loop thread, while every other heavy op in the same method is correctly offloaded with `asyncio.to_thread`. They are the only heavy numpy ops left on the loop, stalling the WS/telemetry loop once per captured frame.

### P1-6 · No screen-brightness dimmer on phones (<640px) — `ui/src/App.tsx:259-272`
- **Change:** Surface a brightness control on mobile: drop `hidden sm:flex` and let the cluster wrap, OR add a compact dimmer (at least the slider + the two `.step-btn` steppers, already ≥44px) into the mobile header/bottom-nav or a one-tap sheet behind the night toggle.
- **Why:** The entire dimmer cluster is `hidden sm:flex` (display:none below 640px). On a 375px phone there is no way to *reduce* brightness: the floating "100%" reset only appears once dimmed and only *raises* brightness; Shift+B needs a hardware keyboard and also only resets to 1.0. A one-handed field user on the primary form factor cannot dim the screen at all.

### P1-7 · Night severity ladder luminance is non-monotonic — `ui/src/index.css:70-72`
- **Change:** Re-derive the night status trio so luminance is monotonic with severity: make `--bad` the lightest (raise toward ~0.42 relL, near `--danger-ink #ff8f8f`), keep `--warn` clearly below it, `--good` lowest. Pull `--warn` toward red (lower green; e.g. `#d06a5a`) to protect dark adaptation. Saturation ordering is already correct — don't touch it.
- **Why:** Measured WCAG luminance: `--warn #e0905c`=0.366 > `--bad #ff7070`=0.340 > `--good`=0.215. So `--warn` is *brighter* than `--bad` (and only ~7% apart), directly contradicting the spec/acceptance claim (stated 3×) that alarm `--bad` is the brightest rung — the lightness channel does not encode severity as documented. `--warn` is also orange-amber (~24° hue), which the spec's "minimal orange" rule tried to avoid (amber harms dark adaptation more than deep red). Shape/glyph/blink redundancy carries the real signal, so this is making the documented luminance cue true, not a safety regression — hence P1, not P0.

---

## P2 — fix in this batch (several share P1 root causes)

### P2-1 · Event wall-time double-counted in overhead EMA (dither/AF/flip) — `server/astrodeck/sequence/engine.py` (`_begin_frame` line 444; `_maybe_meridian_flip` ~532) [merges findings #2+#3]
- **Change (ordered, both parts required):**
  1. Remove `self._frame_had_event = False` from `_begin_frame` (line 444); instead reset it inside `_record_frame` **after** reading it (after ~line 459).
  2. In `_maybe_meridian_flip`, after `self._record_event_cost('flip', ...)` (line 532), add `self._frame_had_event = True` (matching the dither/AF blocks).
  - Final order must be: begin frame → (dither/AF/flip set flag) → capture → record reads flag → reset.
- **Why:** `_begin_frame` unconditionally resets the flag to `False` *after* dither (419)/refocus (425) set it `True` but *before* `_record_frame` reads it, so the guard is always `False` and the dither/AF/flip wall-time is folded into `_overhead_ema` — the exact double-count the flag was meant to prevent (cost is also added analytically via `_record_event_cost`). The flip additionally never raises the flag at all. A ~45s AF or ~90s flip (α=0.1 EMA) whipsaws the ETA/finish clock. Estimate-only (no capture/safety impact) → P2.

### P2-2 · `saved_local` never re-published for local saves — `server/astrodeck/hub.py` (capture save branch ~479-485 vs `_publish_preview` ~593)
- **Change:** After `note_preview_saved()`, `bus.publish` a minimal preview-update (e.g. `'preview'` with `id` + `saved_path` + `saved_local`, or a dedicated `'preview_saved'` event) and merge it in `store.ts` into the matching ring entry + current preview. **OR** compute the path and `save_fits` *before* `_publish_preview` so `saved_local` is correct in the first event.
- **Why:** For sim/Alpaca the FITS is saved *after* the `preview` WS event already emitted `saved_local=false`/`saved_path=None`. `note_preview_saved` updates `entry.meta` but never re-publishes, and the sequence/live-loop only sees the WS event. So WS consumers permanently show `saved_local=false` and the disabled "FITS (on host)" lock for every locally-saved frame — even though `/api/preview/{id}/fits` would serve it. (Tooltip is also doubly wrong: "saved on the NINA host" for a local save.)

### P2-3 · Monitor CLIP chip ignores `full_well` — `ui/src/views/MonitorView.tsx:675-679`
- **Change:** `const fw = p.full_well; return p.data_is_linear && fw != null && p.stats.max >= fw;` (the `source==='nina'` guard becomes redundant but is harmless).
- **Why:** `isClipping` hardcodes `p.stats.max >= 65535`, ignoring `full_well`. A 12/14-bit-in-16-bit CMOS saturates well below 65535 (exactly the case `full_well` exists for), so a genuinely clipped Alpaca/sim frame never trips the Monitor CLIP chip — inconsistent with `PreviewStage.tsx:188-193` and `StretchHistogram.tsx:75` which gate correctly. (Same Batch-2 clip-honesty theme as P1-1.)

### P2-4 · NINA disabled B/M/W handles move when Brightness dragged — `ui/src/components/preview/StretchHistogram.tsx:218-224,251`
- **Change:** On the NINA path render B/M/W handles at fixed neutral positions (0 / 0.5 / 1) independent of `effectiveLevels(brightness)`; keep them static so the "fixed at source" claim holds visually.
- **Why:** The disabled (aria-disabled, not-allowed) mid handle is positioned from `effectiveLevels(stretch)`, whose `mid` depends on `brightness`. Dragging the NINA Brightness slider visibly slides the supposedly-fixed disabled handle — a self-contradiction the user can see, against the component's own documented "fixed at the source" contract. (Gated behind Advanced disclosure + NINA-only; cosmetic.)

### P2-5 · `.preview-label` / `.preview-scalebar` hardcoded white at night — `ui/src/index.css:404-409,419`
- **Change:** Add `:root.night .preview-label { color: var(--text) }` and `:root.night .preview-scalebar { stroke: var(--accent) }`, keeping the `--halo` text-shadow. **Also** remove/conditionalize the inline `text-white` on the LIVE span (`FrameFilmstrip.tsx:96`) — that utility wins over the CSS override.
- **Why:** Both classes are hardcoded `#fff` with no night override; they render over the live-preview image (filmstrip HFR/age/LIVE labels, ScaleBar bar). White light on a focusing screen at night breaks dark adaptation — the exact thing night mode exists to prevent. (`.preview-reticle`/`.preview-chip` correctly redshift; only these two leak white.)

### P2-6 · Double "HOLD TO" on Abort button — `ui/src/views/MonitorView.tsx:293`
- **Change:** Change `ariaLabel="Hold to abort sequence"` to `ariaLabel="Abort sequence"`. (Optionally rename the monitor adapter's misleading `ariaLabel` prop to `label` to prevent recurrence.) Audit confirms this is the only affected site (App.tsx "Unlock screen" is correct).
- **Why:** The monitor `HoldButton` adapter forwards `ariaLabel` into the canonical `label` slot, which prepends "HOLD TO" → visible armed text "HOLD TO HOLD TO ABORT SEQUENCE" and SR "Press Enter again to hold to abort sequence". Broken copy + garbled announcement on the single most safety-critical control.

### P2-7 · Zoom −/+ tap targets below 44px width — `ui/src/components/preview/PreviewToolbar.tsx:95-101`
- **Change:** Use the codebase's canonical utility: `className="btn tap !px-2.5"` (drop the now-redundant `min-h-11`); `.tap` sets both min-width and min-height to `--tap-min` (44px). (`tap-lg`/56px is arguably more spec-correct for arrow primaries.) Fit/100% have text labels and are fine.
- **Why:** `btn !px-2.5 min-h-11` guarantees 44px height but width is ~30-33px (10px padding + ~9px glyph), below the 44px floor on a primary one-handed control — violates the project's own ≥44px spec (which even flags this exact anti-pattern for chips).

### P2-8 · Per-move `localStorage` write in B/M/W drag hot path — `ui/src/store.ts:506-512` (`setStretch` → `persistPreview`)
- **Change:** Make `persistPreview` a no-op when the persisted subset `{auto, advancedOpen, overlays}` is unchanged vs the last-written payload (skip `setItem` if equal), or only call it from the `auto`/`advancedOpen` branches.
- **Why:** Dragging a handle fires `onStretch` dozens/sec; `setStretch` runs `persistPreview` (synchronous `JSON.stringify` + `localStorage.setItem`) on every move. The persisted subset never changes during a B/M/W drag, so every write is pure waste added to the drag hot path alongside the per-move canvas remap. (Tiny payload, so P2 not P1 — the canvas pass dominates.)

---

## P3 — cleanup / polish (can defer, but several are one-liners)

### P3-1 · Retain `linear` numpy array with no Pass-1 consumer — `server/astrodeck/hub.py:582`
- **Change:** Set `linear=None` in Pass 1 (or feature-flag retention) until `/crop` and `/render` land; `/lossless.png` already covers paused/zoom. If kept as scaffolding, add an explicit "deliberately unused in Pass 1" comment at line 582.
- **Why:** ~125 MB/frame uint16 array retained for the latest 2 frames for two routes that are stubbed 501; nothing reads `entry.linear` in Pass 1. Pure memory cost on the Pi (bounded ~250 MB, and one array aliases `last_frame`, so not all net-new) → P3.

### P3-2 · `display_width` 0-from-decode-failure doesn't fall back — `ui/src/components/preview/PreviewStage.tsx:82-83` + `server/astrodeck/hub.py` (`_image_dims` ~620-628)
- **Change:** Client: `preview.display_width || preview.data_width || 0` (falsy, not just nullish). Server: default `display_width/height` to data dims when `_image_dims` returns (0,0) — also protects the linear canvas path + ScaleBar.
- **Why:** `_image_dims` returns (0,0) on PIL decode failure; `0` is not nullish, so `?? data_width` doesn't fall back → `dispW=0`, `fitScale` short-circuits to 1, stage renders an invisible 0-wide image with no error state. Only on a NINA decode failure (edge path) → P3.

### P3-3 · Snap-to-fit skipped on container resize — `ui/src/components/preview/PreviewStage.tsx:117-125`
- **Change:** When `viewport.fit===true`, re-apply `fitScale` (`scale:fitScale, x:0, y:0`) on `stageSize`/`fitScale` change; only preserve the stored viewport across resize when `fit===false` (user actively zoomed).
- **Why:** The effect's guard keys only on `${dispW}x${dispH}`, so a container resize (phone rotate, panel reflow, breakpoint collapse) with unchanged image dims keeps the old scale/offset — image sits oversized/off-center until the user manually presses Fit. Recoverable in one click → P3.

### P3-4 · Brightness slider silently flips Manual→Auto (linear) — `ui/src/components/preview/StretchHistogram.tsx:251`
- **Change:** In Manual mode either disable/hide the simple Brightness slider (it has no defined effect when `!auto`), or have Brightness nudge `mid` in place without flipping `auto` (mirror the NINA branch's `auto: isNina ? stretch.auto : true` pattern).
- **Why:** On the linear path Brightness forces `auto:true` every change, so touching it in Manual mode silently discards the user's B/M/W and jumps the image to Auto. Recoverable via the (non-obvious) Auto toggle but permanently lost if the user next drags a handle → P3.

### P3-5 · `max_marks`/`max_stars` implicit coupling — `server/astrodeck/imaging/stars.py:132-140` + `server/tests/test_imaging.py`
- **Change:** Make it explicit: cap `star_marks` `max_marks` to `detect_stars`' `max_stars`, or assert `len(marks)==min(count, MAX_MARKS)` and add a comment that `max_marks >= max_stars`.
- **Why:** `count=len(stars)` (capped 200) vs `marks` capped at 400; the test's `len(marks)==count` holds only because 200<400. Raising `max_stars` above 400 would silently diverge, and the test (20-star synthetic field) wouldn't catch it. Latent coupling, not a live failure → P3.

### P3-6 · Dead `.crosshair` CSS — `ui/src/index.css:330-336`
- **Change:** Delete the `.crosshair { ... }` rule.
- **Why:** Spec mandates removal (3 places) now that `<Reticle/>` replaces it; zero `className="crosshair"` consumers remain. Pure cleanup → P3.

### P3-7 · Undefined `var(--line2)` token — `ui/src/components/polar.tsx:51`
- **Change:** Change `stroke="var(--line2)"` to `stroke="var(--line-bright)"` (the raw token `--color-line2` maps to), matching sibling components.
- **Why:** `--line2` is never defined as a raw custom property (Tailwind v4 `@theme` only emits `--color-line2`), so the stroke has no fallback → dashed tolerance rings render invisibly. Cosmetic decorative rings → P3.

### P3-8 · MonitorView coarse tick re-renders heavy cells — `ui/src/views/MonitorView.tsx:81-88,437-451`
- **Change:** Wrap `PreviewTile` (and `ThermometerBar`) in `React.memo` and stabilize its `meta`/`clip` props with `useMemo`; better, move the `now`-dependent "last frame N ago"/STALE/stall derivations into a small leaf component owning its own 1s tick so the coarse tick leaves `MonitorView` entirely.
- **Why:** `useCoarseTick()` re-runs all of `MonitorView` every second, re-rendering the unmemoized `PreviewTile` with fresh inline props — contradicting the file's own "tickers live only in leaf cells" comment. Impact is negligible (keyed `<img>` keeps its DOM node; Sparklines already memoized) → P3 cleanup, not P2.

### P3-9 · `PreviewStage` `role="img"` on an interactive control — `ui/src/components/preview/PreviewStage.tsx:229-233`
- **Change:** Use `role="group"` (or keep `role="img"`) plus a visually-hidden `aria-live="polite"` region echoing the scale % on keyboard zoom/pan. Avoid `role="application"` (suppresses the AT virtual cursor; heavier than needed).
- **Why:** The stage is `tabIndex=0` with arrow-pan/+/-/0/1 keyboard handlers but `role="img"` tells AT it's a static graphic — SR users get no cue it's interactive, and viewport changes aren't announced. Advanced niche feature; sighted keyboard users unaffected → P3.

### P3-10 · HoldButton announces no progress on pointer hold — `ui/src/components/ui.tsx:151-251`
- **Change:** Either accept+document the keyboard two-step as the AT path, or add an `armed`-gated `sr-only` `aria-live="polite"` note for the pointer path (per the spec's UI-states matrix, polite not assertive), mirroring the keyboard one.
- **Why:** The aria-live region is gated on `kbArmed` only, so a pointer/switch hold conveys progress purely visually. The keyboard two-step is a fully functional announced AT path, so this is an edge gap → P3. (Note: a second, separate `HoldButton` in `monitor.tsx` shares the gap — review separately.)

### P3-11 · `crossOrigin="anonymous"` on same-origin canvas image — `ui/src/components/preview/useImageRemap.ts:41`
- **Change:** Best treated as a borderline non-issue. If touched, only set `crossOrigin` when `baseUrl` is cross-origin — do **not** drop it outright (it's the correct defensive choice for a `getImageData` read-back path).
- **Why:** The base image is fetched same-origin, so `crossOrigin` is redundant today. The finding's "drop it" fix is a wash (dropping it would *break* a future cross-origin deployment via canvas taint). Lowest priority → P3 / near-non-issue.

---

## Pass-1 honesty-rule & contract violations (called out explicitly)

These are the findings where the code violates a stated spec/contract or an in-code documented intent — i.e. the implementation "lies" relative to its own claims:

1. **P1-3 — §12.5 "never offer a download that will 404"** violated by the unconditional "Stretched PNG" item (the FITS item honors the same rule; the file header even documents "§12.5 — never a 404").
2. **P1-2 — reconciled live-preview contract §4.4/4.5** ("`.png` returns a real PNG or 404; `/api/preview/{id}` is the canonical client URL"): Monitor was never migrated off `.png` (Risk-7). The tile's own docstring claims "identical URL to CaptureView" while using a different URL.
3. **P1-7 — night spec acceptance criterion** ("alarm `--bad` is the BRIGHTEST rung", asserted 3× in-file) is false: `--warn` is brighter than `--bad`. Also violates the spec's "minimal orange" rule (`--warn` is amber).
4. **P2-4 — component's documented "B/M/W fixed at the source" contract** is visibly contradicted (disabled handle moves with the Brightness slider).
5. **P3-2 — `_image_dims` (0,0)-on-failure contract** is mishandled by a `??` that only guards nullish, silently producing a 0-sized invisible stage with no error state.
6. **P3-6 — spec mandate to delete `.crosshair`** not honored (dead CSS left in).
7. **P3-8 / P3-1 — in-code design comments contradicted by implementation:** MonitorView's "tickers live only in leaf cells" comment (coarse tick lives on the parent), and hub's `linear`-retention scaffolding retained with no Pass-1 consumer (partially documented, but retention is live).
8. **Test-honesty gaps (not blockers, but flag for the author):** `test_preview_routes_resolve` only checks the freshest frame, so it passes while the real `.png` consumer 404s (P1-2); `test_measure_frame_single_pass_matches_median_hfr` would not catch a `max_stars` bump (P3-5). Both give false confidence.

**Bottom line:** Do not commit Batch 2 until P1-1 through P1-7 are fixed. Strongly recommend also landing P2-1, P2-2, P2-3 in the same commit (they share the `full_well`/`.png`/EMA root causes with the P1s and are cheap). P3s can follow.
