# AstroDeck Native-Parity UI — Implementation Brief

**For the implementing agent (Opus/Sonnet). The design lead (Fable) authored this and the visual reference; you execute it faithfully.**

Visual target: `docs/ui-rebuild/design-reference.html` (open it / read its markup+CSS). It is the source of truth for look, motion, and the two hero screens. This brief maps it onto the real React/TS/Tailwind/Zustand codebase in `ui/`. Companion product specs: `docs/ui-rebuild/01`–`04` (IA, state, personas, failure). Do **not** restyle the whole app blindly — the app already has a coherent design system (`ui/src/components/ui.tsx`); you are (a) codifying the token/aesthetic layer, (b) building the two new hero experiences, and (c) adding provider badges. Keep every existing primitive (`Led`, `HoldButton`, `Panel`, `Stat`, `Toggle`, brightness/NIGHT mode).

## 0. Non-negotiables (the taste, in one place)
1. **Verdict first, raw number second.** Every measurement shows a plain-language verdict then the figure ("Focus — excellent · HFR 1.82″"). Extend the existing `FocusVerdict`/`RmsVerdict` pattern.
2. **Shape + letter, never color alone.** Status uses the `Led` (letter inside) so it survives night mode and color blindness.
3. **Telemetry is tabular mono.** All live numbers (RA/Dec/HFR/RMS/arcmin/positions) use IBM Plex Mono with `tabular-nums`. Numbers must never reflow or jitter tick-to-tick.
4. **One accent (`--accent`, phosphor aqua), semantic color separate** (notice amber, act red, data sky-blue). Do not introduce a second accent.
5. **Three grounds via tokens:** light, dark (the identity), and `.night` (scotopic red — a real operating mode, not an afterthought). Drive all three by swapping CSS variables; never hardcode a hex in a component.
6. **Motion budget = two moments only:** the autofocus curve drawing itself, and the polar error vector converging. Respect `prefers-reduced-motion` (show final state). Nothing else animates beyond ≤150ms hovers.

## 1. Token layer (do this first)
- Add the reference's tokens (copy the `:root` / `@media (prefers-color-scheme: dark)` / `[data-theme]` / `.night` blocks) into the app's theme layer. If Tailwind v4: express them as `@theme` CSS variables in `ui/src/index.css` and reference via `var(--…)` or Tailwind color aliases. Confirm the existing brightness/NIGHT toggle flips the `.night` class (or equivalent) on the root; wire the tokens so NIGHT swaps the red palette.
- Type: register IBM Plex Sans + IBM Plex Mono as the app faces (the build already ships Plex Sans woff2 — add Plex Mono). Set mono+`tabular-nums` as the class used by `Stat`/readouts.

## 2. Provider badges (the native-parity signal — small but it's the whole point)
- New component `ui/src/components/ProviderBadge.tsx`: renders `AF · <label>` / `TPPA · <label>` as the `.prov` chip in the reference (accent-filled when `kind==="astrodeck"`, muted `.prov.ext` when `kind==="backend"`/NINA).
- Data source: `status.providers` (added to `poll_status()` by the provider-integration work) — shape `{ autofocus:{kind,label,reason}, polar_align:{kind,label,reason} }`. Add to the Zustand store's status slice + a `useProviders()` selector. **Verify the exact emitted shape** by reading `server/astrodeck/providers.py` `resolve_all()` and `hub.poll_status`; bind to what is actually emitted.
- Place badges: Focus view (Autofocus panel header), Align view (header), and Settings → Connect gets a "Capabilities" card showing both resolved providers + override dropdowns (`auto`/`backend`/`astrodeck`, gated by `config.backend`). The override writes the `providers` field on config/active profile — read `server/astrodeck/config.py` ProvidersConfig + the profile `providers` field for the real endpoints.

## 3. Hero 1 — Autofocus (Focus view)
Rebuild the "V-Curve" panel in `ui/src/views/FocusView.tsx` + `ui/src/components/graphs.tsx` `VCurve` to match the reference's SVG:
- Plot `focus.points` (`[{position,hfr}]`) as points **with error whiskers** (whisker length from per-point hfr σ / star count if present; else omit). During a live run, points appear as they arrive (they already stream on the `focus` topic) — the natural staggered reveal replaces the mockup's animation.
- Draw the fitted curve from `focus.fit.curve` (a polyline `[[x,y]]` the native engine now emits) — animate `stroke-dashoffset` on first draw only. Show the chosen minimum (`focus.best`) with the glowing marker + "BEST <position>" label + the trendline cross if `focus.fit.trendlines` present.
- Verdict block: `Focus — excellent|good|soft|failed` from HFR + `focus.fit.r2` (excellent ≥ tight HFR & R²≥0.98; failed on `state:"failed"` with the engine's message). Provider badge in the panel header.
- Focuser stat block (position/temp), Run Autofocus / Jog / Halt controls, and a refocus-armed line from the plan automation. Verify `focus` topic fields against `server/astrodeck/focus/native.py` + `focus/autofocus.py`.

## 4. Hero 2 — Polar alignment wizard (Align view)
Rebuild `ui/src/views/PolarView.tsx` + the `PolarReticle` to the reference's two-phase wizard:
- **Phase indicator**: Measure (3 solve tiles, fill as `polar.point_index` advances) → Adjust. Source: `polar.phase` (`measuring|adjusting`) + `polar.point_index` from the native TPPA session (`server/astrodeck/polar/native.py`).
- **Reticle**: tolerance rings (10′ / good / 2′), crosshair, and the **error vector** from center — length/angle from `polar.az_error` + `polar.alt_error` (arcmin). Live updates as the user turns knobs shrink it toward center; animate transitions between readings (not a canned loop). Knob-direction arrows + text from `polar.directions` (az_direction/alt_direction, e.g. "turn W" / "raise").
- **Hero total error** in big mono arcmin (`polar.total_error`) + tiered verdict: `>10′ keep going` / `2–10′ good` / `<2′ excellent — stop here`. Provider badge. Preserve Start/Pause/Resume/Stop plumbing; sticky Tier-2 on `polar.state:"error"` per doc 04.
- Confirm the native path is chosen for native rigs and NINA path for NINA rigs (provider `polar_align`); both publish the same `polar` payload so this view is backend-agnostic.

## 5. Hero 3 — Monitor glance (polish pass, not a rebuild)
`ui/src/views/MonitorView.tsx` already has the widgets. Apply the token/type treatment and the attention-tier discipline from the reference + doc 03 §5: ambient tier-0, amber chip tier-1, sticky red banner tier-2. Fold `status.providers`, `safety`, `disk`, `meridian`, `backend_links`, engine `end_reason` into the single "is my night OK?" health strip (doc 04 §6 rec 1). Keep the finish-time clock, guiding sparkline, thermal bar, meridian countdown.

## 6. Acceptance
- `npm --prefix ui run build` clean (tsc + vite); existing `ui/src/__tests__` green; add tests for `ProviderBadge`, the VCurve fit binding, and the polar phase/verdict mapping where the harness allows (note: no jsdom timer harness — trace-verify effect timing, as prior UI fixes did).
- Visual parity with `design-reference.html` in light, dark, and NIGHT.
- No hardcoded hex in components (tokens only). No second accent. Motion only on the two hero moments; `prefers-reduced-motion` honored.
- Every provider badge, V-curve fit, and polar readout binds to a **real** emitted field — grep the server for the exact key before binding; if a field the design needs isn't emitted, add it additively server-side (don't fake it client-side).
