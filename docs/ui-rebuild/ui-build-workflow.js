export const meta = {
  name: 'native-parity-ui-build',
  description: 'Build the native-parity UI (tokens, provider badges, autofocus + TPPA heroes, monitor) per the Fable design reference',
  phases: [
    { title: 'Commit-provider', detail: 'verify + commit the provider integration if uncommitted (sonnet)' },
    { title: 'Tokens', detail: 'design tokens + type + provider badge foundation (opus)' },
    { title: 'Heroes', detail: 'autofocus V-curve + TPPA wizard in parallel (opus)' },
    { title: 'Monitor', detail: 'monitor polish + capabilities card (sonnet)' },
    { title: 'Review', detail: 'design-fidelity + binding review (opus)' },
    { title: 'Gate', detail: 'ui build + tests + commit (sonnet)' },
  ],
}

const S = {
  type: 'object', required: ['status', 'summary'],
  properties: {
    status: { enum: ['ok', 'partial', 'failed'] },
    summary: { type: 'string' },
    files: { type: 'array', items: { type: 'string' } },
    concerns: { type: 'array', items: { type: 'string' } },
  },
}

const ROOT = 'C:/Users/bear/astro'
const REF = `${ROOT}/docs/ui-rebuild/design-reference.html`
const BRIEF = `${ROOT}/docs/ui-rebuild/06-implementation-brief.md`
const COMMON = `You are implementing the AstroDeck native-parity UI. The design lead already produced the visual target and brief — you execute faithfully, you do NOT redesign.
READ FIRST, EVERY TIME: ${BRIEF} (the executor brief — non-negotiables, code mapping, real API bindings) and ${REF} (the visual reference — look, motion, the hero SVGs; read its markup+CSS). Stack: React + TS + Tailwind v4 + Zustand in ${ROOT}/ui; single WS /ws for reads, REST POST for commands, no router (view = store.view). Build check: npm --prefix ${ROOT}/ui run build (tsc -b && vite build). Existing design system: ui/src/components/ui.tsx (Led, HoldButton, Panel, Stat, Toggle, Stepper, Segmented) + graphs.tsx (VCurve, GuideGraph, PolarReticle) + views/*.
RULES (from the brief §0): verdict-first then raw number; status = shape+letter (Led) never color alone; all telemetry mono + tabular-nums; ONE accent (--accent) + separate semantics; three grounds (light/dark/.night) driven only by token swaps, never hardcoded hex in a component; motion only on the two hero moments (autofocus curve draw, polar vector converge) with prefers-reduced-motion honored. Bind every value to a REAL emitted server field — grep server/astrodeck for the exact key (status.providers, focus.fit, polar.phase/directions) before binding; if a needed field is missing, add it additively server-side, do not fake client-side. Touch ONLY your assigned files. Return the structured summary.`

phase('Commit-provider')
const committed = await agent(`${COMMON}
You are the pre-flight. The provider-integration work (server-side: providers.py, focus/native.py, polar/native.py, config/profile provider fields, hub.poll_status providers surfacing + their tests) may be uncommitted in the tree. Steps: (1) git -C ${ROOT} status --short. (2) FIX A CONFIRMED DEFECT before committing — server/astrodeck/polar/native.py: the motion-fence check (around lines 78-79 and 222-227, _check_alive) raises asyncio.CancelledError on a motion-epoch advance, and run_native re-raises it WITHOUT publishing a terminal polar event; but the epoch-bump paths (mount STOP, park, jog rate-0 STOP, deadman halt, sequence safety abort) do NOT call hub.polar.stop(), so a STOP/park/deadman/safety halt mid-measure leaves the UI reticle stuck on stale state:"running". Change _check_alive to raise DeviceError (which run_native already maps to a terminal polar state:"error" publish) instead of CancelledError, so a terminal polar event ALWAYS fires on abort — mirror how focus/native.py catches BaseException and publishes state:"failed". Add a regression test in server/tests/test_polar_native.py: bump the motion epoch mid-measure and assert a terminal polar event (state error/idle, not running) is published. (3) Run the server suite ${ROOT}/server/.venv/Scripts/python.exe -m pytest -q from ${ROOT}/server; if green, git add the provider-integration files + tests (including this fix) and commit with message "feat(providers): per-capability provider layer routing native autofocus + TPPA (Rust engine)" — do NOT commit unrelated files (ui/, docs/ui-rebuild/), do NOT push. If the suite is RED, report the failures in concerns and still proceed (the UI work is independent). (4) If already committed/clean, say so. Report what you committed (sha) and the emitted shapes of status.providers / focus.fit / polar payload (read providers.py resolve_all, focus/native.py, polar/native.py) so the UI agents bind correctly — include these shapes verbatim in your summary.`,
  { label: 'commit:provider', phase: 'Commit-provider', schema: S, model: 'sonnet', effort: 'medium' })
log('Pre-flight: ' + (committed ? committed.summary : 'null').slice(0, 200))

phase('Tokens')
const tokens = await agent(`${COMMON}
FOUNDATION task — do this alone first (the hero agents depend on it). Files (create/edit ONLY these): ui/src/index.css (or the Tailwind theme entry — locate it), ui/src/components/ProviderBadge.tsx (new), ui/src/store.ts (add providers to the status slice + useProviders selector — additive, do not disturb other slices), ui/src/lib/ if a tokens helper fits.
Do: (1) Port the reference's token system (the :root / prefers-color-scheme / [data-theme] / .night CSS-variable blocks) into the app theme so light, dark, and NIGHT all resolve from token swaps; confirm the existing brightness/NIGHT control flips the .night class on the root (read how NIGHT mode works today — grep 'night' in ui/src — and wire tokens to it, do not rebuild the toggle). (2) Ensure IBM Plex Sans + IBM Plex Mono are the app faces; make the mono+tabular-nums treatment the default for Stat values / telemetry. (3) Build ProviderBadge.tsx = the reference's .prov chip (accent-filled for kind 'astrodeck' "AstroDeck native", muted .prov.ext for kind 'backend'/'NINA'); props {cap:'autofocus'|'polar_align'}, reads useProviders(). (4) store: add providers to status state + useProviders() returning status.providers (shape from the pre-flight report / server providers.py resolve_all). Verify: npm --prefix ${ROOT}/ui run build clean. Report the token names and the ProviderBadge API for the hero agents.`,
  { label: 'ui:tokens', phase: 'Tokens', schema: S, model: 'opus', effort: 'high' })
if (!tokens || tokens.status === 'failed') { log('Token foundation failed — aborting UI build'); return { committed, tokens } }

phase('Heroes')
const heroes = await parallel([
  () => agent(`${COMMON}
HERO 1 — Autofocus. The token layer + ProviderBadge are DONE (import them; useProviders() exists). Files (edit ONLY these): ui/src/views/FocusView.tsx, ui/src/components/graphs.tsx (the VCurve component only), ui/src/components/preview/FocusVerdict.tsx (extend if present), ui/src/components/__tests__ if a test fits.
Match the reference's Autofocus hero (section 02): plot focus.points as points WITH error whiskers; draw the fitted curve from focus.fit.curve (animate stroke-dashoffset first-draw only, reduced-motion => final state); glowing best-focus marker from focus.best with "BEST <position>" + trendline cross if focus.fit.trendlines; verdict block (Focus — excellent|good|soft|failed from HFR + focus.fit.r2 + state) with a ProviderBadge cap='autofocus' in the header; focuser stat block + Run Autofocus/Jog/Halt + refocus-armed line. GREP the focus topic payload in server (focus/native.py, focus/autofocus.py) and the store's focus handling for exact field names before binding; if fit/curve isn't emitted yet, add it additively server-side. Verify build clean.`,
    { label: 'ui:autofocus', phase: 'Heroes', schema: S, model: 'opus', effort: 'high' }),
  () => agent(`${COMMON}
HERO 2 — TPPA polar wizard. Token layer + ProviderBadge DONE. Files (edit ONLY these): ui/src/views/PolarView.tsx, ui/src/components/graphs.tsx (the PolarReticle component only — coordinate: HERO 1 also edits graphs.tsx VCurve; touch ONLY PolarReticle, leave VCurve to the other agent, no overlapping edits), ui/src/components/__tests__ if a test fits.
Match the reference's Polar hero (section 03): phase indicator Measure(3 solve tiles from polar.point_index)→Adjust (polar.phase); reticle with tolerance rings (10'/good/2') + crosshair + error vector from polar.az_error/alt_error (arcmin) that transitions smoothly between readings (NOT a canned loop) toward center as the user adjusts; knob-direction arrows+text from polar.directions; big mono total_error arcmin + tiered verdict (>10' keep going / 2-10' good / <2' excellent-stop-here) + ProviderBadge cap='polar_align'; preserve Start/Pause/Resume/Stop; sticky Tier-2 on polar.state==='error'. GREP polar/native.py + polar/session.py + the store polar handling for exact fields (phase, point_index, directions, *_error) before binding; add additively server-side if a needed field is missing. Verify build clean.`,
    { label: 'ui:tppa', phase: 'Heroes', schema: S, model: 'opus', effort: 'high' }),
])
log(`Heroes: ${heroes.filter(Boolean).map(h => h.status).join(', ')}`)

phase('Monitor')
const monitor = await agent(`${COMMON}
POLISH task (not a rebuild) + the Capabilities card. Files (edit ONLY these): ui/src/views/MonitorView.tsx, ui/src/components/monitor.tsx, ui/src/components/settings/BackendPicker.tsx or a new ui/src/components/settings/CapabilitiesCard.tsx (+ SettingsView Connect tab wiring), ui/src/components/__tests__ if a test fits.
Do: (1) Apply the token/type treatment + attention-tier discipline (doc 03 §5: ambient tier-0, amber chip tier-1, sticky red banner tier-2) to MonitorView, folding status.providers + safety + disk + meridian + backend_links + engine end_reason into the single "is my night OK?" health strip (reference section 04). Keep finish-time clock, guiding sparkline, thermal bar, meridian countdown. (2) Capabilities card in Settings → Connect: shows both resolved providers (ProviderBadge + reason) with override dropdowns auto|backend|astrodeck, gated by the config.backend capability, writing the providers config/profile override (read server config.py ProvidersConfig + profile providers field for the real endpoint). Verify build clean.`,
  { label: 'ui:monitor', phase: 'Monitor', schema: S, model: 'sonnet', effort: 'medium' })

phase('Review')
const review = await agent(`You are the design-fidelity + correctness reviewer for the AstroDeck native-parity UI just built. Read ${REF}, ${BRIEF}, then the changed files (ui/src/index.css, components/ProviderBadge.tsx, store.ts, views/FocusView.tsx, views/PolarView.tsx, views/MonitorView.tsx, components/graphs.tsx, components/monitor.tsx, settings capabilities card). Report ONLY real defects (file:line, concrete issue, confidence): (1) hardcoded hex where a token belongs; a second accent introduced; NIGHT/light theme broken. (2) a binding to a field the server does not emit (cross-check server/astrodeck: status.providers, focus.fit/points/best, polar phase/point_index/directions/*_error) — this is the highest-risk class. (3) motion beyond the two hero moments, or reduced-motion not honored. (4) verdict-first / tabular-nums / shape+letter violated in a load-bearing readout. (5) any TS/build error. Do not restyle; do not report taste nitpicks that match the reference. Read files fully; you may run npm --prefix ${ROOT}/ui run build.`,
  { label: 'ui:review', phase: 'Review', schema: S, model: 'opus', effort: 'high' })

phase('Gate')
const gate = await agent(`Final gate for the AstroDeck native-parity UI at ${ROOT}. (1) Apply any CONFIRMED high-confidence fixes the reviewer reported (below — ${JSON.stringify((review && review.concerns) || [])}); skip low-confidence or taste items. (2) Run npm --prefix ${ROOT}/ui run build (tsc + vite) and any ui test script until green; fix only clear build/test-side issues. (3) Also run ${ROOT}/server/.venv/Scripts/python.exe -m pytest -q from ${ROOT}/server IF any server file was touched additively by the hero agents, to confirm no server regression. (4) When green: git -C ${ROOT} add the ui/ changes (+ any additive server field changes) and commit "feat(ui): native-parity UI — provider badges, live autofocus V-curve, TPPA wizard, monitor health strip"; do NOT push. Report the final build result, commit sha, and anything left for a human.`,
  { label: 'ui:gate', phase: 'Gate', schema: S, model: 'sonnet', effort: 'medium' })

return { committed, tokens, heroes, monitor, review, gate }