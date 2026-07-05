# AstroDeck UI Revamp — Joint Proposal

**Status:** proposal for review (no codebase changes until approved) · **Date:** 2026-07-05
**Authors:** Antigravity (visual language, spatial aesthetics) · Claude (information architecture, state model, layout logic)
**Prototype:** `mockup.html` (repo root) — open in a browser to see the structure realized in the Obsidian & Nebula theme.

This is a *presentation-layer* revamp. The FastAPI backend, the WebSocket/status model, the Zustand store, the RBAC/capability layer, and the native-provider routing are **unchanged** — the new UI is a re-skin + re-layout that consumes the same store selectors. That is what makes this safe to prototype and to ship behind a flag.

---

## 1. The two halves, merged

| Layer | Owner | Contribution |
|---|---|---|
| **Visual language** | Antigravity | *Obsidian & Nebula*: deep-space dark (`#06070B`) over carbon panels (`#12141C`), neon-blue→deep-purple accents, smoked glassmorphism, fiber-optic data viz. |
| **Spatial + state logic** | Claude | The modular grid mapped onto the real nightly workflow, a **task-adaptive** Center Stage, state-driven zones, the attention model → spatial prominence, RBAC-gated affordances, and the migration path. |

The merge principle: **the aesthetic makes the instrument feel alive; the structure makes it trustworthy at 2 a.m.** Glassmorphism and neon are load-bearing *information*, not decoration — a stale panel loses its glow, a Tier-2 alert is the only thing that ever burns hot.

---

## 2. Design language (Obsidian & Nebula) — formalized as tokens

So the theme maps cleanly to the codebase later (CSS variables → Tailwind v4 `@theme`), we express Antigravity's palette as a token system. Values below are the **starting point**; Antigravity owns their iteration in `mockup.html`.

```css
--void:        #06070B;   /* deep-space ground */
--carbon:      #12141C;   /* panel base */
--carbon-2:    #171a24;   /* raised panel */
--glass:       rgba(20,24,34,0.55);   /* smoked glass fill (with backdrop-blur) */
--glass-line:  rgba(120,140,200,0.14);/* hairline edge that catches light */
--nebula-blue: #4d7cff;   /* accent start */
--nebula-purple:#9a5cff;  /* accent end   */
--nebula:      linear-gradient(120deg, var(--nebula-blue), var(--nebula-purple));
--ink:         #e8ecf7;   --ink-2: #9aa6c2;  --ink-3: #5a6684;   /* text tiers */
/* semantic — SEPARATE from the nebula accent, never overloaded */
--ok: #3ddc97;  --notice: #ffb454;  --alert: #ff5470;
```

**Materials.** Every zone is a smoked-glass card: `background: var(--glass); backdrop-filter: blur(14px); border: 1px solid var(--glass-line)`. Depth comes from blur + a 1px light-catching edge, not heavy shadows. Telemetry numbers stay tabular mono so they never jitter (a hard rule carried from the current UI). Semantic color is strictly separate from the nebula accent — the accent is the "signal is live and healthy" hue; `--ok/--notice/--alert` carry judgment.

---

## 3. The spatial model — a modular grid mapped to the night

Antigravity's grid — **Center Stage / Right Dock / Bottom Drawer** — is powerful *because* it matches how the domain actually decomposes: one thing you're looking at, the gear that's doing it, and the numbers that tell you it's going well.

```
┌──────────────────────────────────────────────┬───────────────┐
│  HEALTH RIBBON  (Tier-1/2 conditions, always) │               │
├──────────────────────────────────────────────┤   RIGHT DOCK  │
│                                                │   the RIG     │
│              CENTER STAGE                      │   (hardware)  │
│         (task-adaptive hero)                   │               │
│                                                │  camera  ●C   │
│                                                │  mount   ●M   │
│                                                │  focuser ●F   │
├──────────────────────────────────────────────┤  guider  ●G   │
│  BOTTOM DRAWER — telemetry (fiber-optic)       │  + provider   │
│  guiding · HFR · thermal · meridian · disk     │    badges     │
└──────────────────────────────────────────────┴───────────────┘
```

### 3.1 Center Stage is **task-adaptive** (the key structural idea)
It is *not* a fixed live-view. It shows the single most important surface for the **active phase**, derived from `status.busy` + the current task:

| Phase (derived) | Center Stage shows | Source topic |
|---|---|---|
| Framing / idle | the live image + framing overlay | `preview` |
| Focusing | the **autofocus V-curve** (fit draws itself) + last frame | `focus` |
| Polar aligning | the **TPPA reticle** (error vector converges) | `polar` |
| Guiding setup | the guide-error scatter + graph | `guide` |
| Running a sequence | the current sub + progress + finish clock | `sequence` |

One surface, always the right one. The task switcher (segmented control) lets the operator override; the engine auto-selects during a run. This is where the "wow" lives — the hero visualizations get the full nebula + fiber-optic treatment on the largest canvas.

### 3.2 Right Dock is the **rig** — persistent, glanceable
The hardware roster, always visible: camera / mount / focuser / filterwheel / guider / power / safety, each an **LED that is shape + letter, never color alone** (carried from the current design — survives night mode and color-blindness). Tri-state connection (connected / degraded / failed / not-requested). The **provider badges** ("AF · native", "TPPA · NINA") live here — the native-parity signal. Answers "is my gear OK?" without moving your eyes to the center.

### 3.3 Bottom Drawer is **telemetry** — the fiber-optic strip
The time-series/ambient data as flowing sparklines: guiding RMS, HFR trend, cooler thermal, meridian countdown, disk. This is where "fiber-optic data viz" is literal (§6). Collapsed to a one-line summary by default; expands to full graphs.

### 3.4 Health Ribbon — the attention spine (see §4)
A thin strip above Center Stage that is **empty and invisible when all is well**, and is the only place Tier-1/Tier-2 conditions surface.

**Responsive collapse:** desktop = all three zones; tablet = Right Dock becomes a slide-over; phone = Center Stage full-bleed with a bottom tab bar, Dock + Drawer as sheets. The zone *roles* never change, only their presentation.

---

## 4. State-driven layout — the grid is a projection of the snapshot

The current app's model is unchanged and is the backbone here: a **~2 s `status` snapshot** (wholesale replace) + per-topic WS events (`preview/focus/guide/polar/sequence/safety/log`). The revamp maps that onto the grid:

- **Right Dock + Bottom Drawer** render live values from the `status` snapshot (`connected`, `mount`, `camera`, `guider`, `focuser`, `meridian`, `disk`, `safety`, `providers`).
- **Center Stage** content is driven by the topic streams (the phase table in §3.1).
- **Three liveness tiers, three visual treatments** (carried from the ui-rebuild spec, now expressed in the material): *live* = full glass + nebula glow; *set* (config the user chose) = glass, no glow; *stale* (`telemetryStale`, `safety.stale`, `nina_link.healthy=false`) = **desaturated glass, glow extinguished** — staleness is legible in the material itself.

### The attention model → spatial prominence (the heart of it)
Every state maps to one of three tiers; each tier has a fixed **place** in the grid and a fixed **loudness**:

| Tier | Meaning | Where it appears | Treatment |
|---|---|---|---|
| **0 · Ambient** | night is healthy | in-place in Dock/Drawer | calm; fiber-optic flow steady; no interruption |
| **1 · Notice** | you'll want to know | a chip in the **Health Ribbon** + the relevant zone | amber (`--notice`) chip; self-clears |
| **2 · Act / Wake** | night is at risk | the Health Ribbon expands to a **full-width sticky banner** across the top of Center Stage | `--alert` neon, the *only* hot element on screen; (remote) push |

This is the payoff of merging structure + aesthetic: because everything else is calm glass, a Tier-2 alert — the one hot, saturated, breathing element — is impossible to miss. The design system *earns* its alarm.

---

## 5. Concept → zone → state → endpoint (the wiring matrix)

The deliverable that lets us build without gumessing. Each nightly concept → its zone, the live state, and the command endpoint (reused verbatim from the current API).

| Concept | Zone | Live state | Command endpoint | Cap |
|---|---|---|---|---|
| Connect / power | Right Dock | `connected{role}`, `backend_links[]`, `providers` | `POST /api/connect/*` | `config.backend` |
| Cool camera | Bottom Drawer (thermal) | `camera.cooler{…,at_target}` | `POST /api/camera/cooler` | `control.capture` |
| Slew / GoTo | Center Stage (mount phase) | `mount{ra,dec,alt,az,slewing,tracking}` | `POST /api/mount/{goto,move,stop,park}` | `control.mount` |
| Focus | Center Stage (focus phase) | `focus{state,points,best,fit}` | `POST /api/focuser/autofocus` | `control.capture` |
| Polar align | Center Stage (polar phase) | `polar{phase,az/alt/total_error,directions}` | `POST /api/polar/{start,stop,…}` | `control.mount` |
| Guide | Bottom Drawer + Center (guide phase) | `guider{rms_*,snr,recent}` | `POST /api/guide/{start,stop,dither}` | `control.guide` |
| Capture | Center Stage (framing phase) | `looping`, `preview{…,hfr}` | `POST /api/capture` | `control.capture` |
| Run the night | Center Stage (run phase) + Ribbon | `sequence{state,progress,end_reason}` | `POST /api/sequence/{start,pause,abort}` | `control.mount` |
| Safety / disk | Health Ribbon | `safety{is_safe,reason,stale}`, `disk` | (engine-driven) | — |

RBAC: control affordances in every zone gate off `caps` (fail-closed). A **viewer** sees the full ambient/telemetry picture but no controls (hidden, not disabled) behind a "View-only" badge — exactly the current model, just re-placed into zones.

---

## 6. Fiber-optic data viz — concretely

Antigravity's "fiber-optic" idea, made buildable:

- **Sparklines** (guiding RMS, HFR, thermal) render as a `--nebula` gradient-stroke polyline over a faint grid, with a **traveling highlight** — a short bright segment animating along the path (via `stroke-dasharray` offset) so data reads like light moving down a fiber. The endpoint is a glowing node (the live value).
- **The autofocus V-curve**: the fitted curve draws itself once (existing behavior), now as a nebula-gradient fiber; measured points are glowing nodes; the best-focus minimum pulses.
- **The TPPA reticle**: the error vector is a nebula fiber from center to the current axis error; it *converges* toward center as knobs turn, trailing a faint afterglow.
- **Connections between zones**: a subtle idea for iteration — a faint fiber that lights up from the Dock's active device to the Center Stage when that device is the one working (camera fiber glows during an exposure). Optional; Antigravity's call.

Motion budget stays disciplined: the traveling-highlight flow + the two hero animations are the *only* motion; everything respects `prefers-reduced-motion` (flow freezes, curves snap to final).

---

## 7. Two front doors over one grid

The same three-zone grid serves two postures (from the personas work — First-light beginner vs Operator expert):

- **Guided Night** — a wizard *takes over Center Stage*, stepping connect → cool → polar → focus → frame → run, one gated step at a time, with plain-language verdicts. The Dock + Drawer stay as ambient reassurance ("your gear is fine, keep going"). For P1/beginners.
- **Expert Console** — the full modular grid, task-adaptive center, every number live, dense. For P2/P3.

A single segmented toggle in the top bar. Both are the same components and state — only the Center Stage orchestration differs.

---

## 8. Migration — safe, phased, reversible

Because the store/API/provider layers are untouched, this is low-risk:

1. **Prototype** (`mockup.html`) → approve the direction. ← *we are here*
2. **Token layer**: add the Obsidian & Nebula tokens as a *second theme* alongside the current one; nothing switches yet.
3. **Shell**: build the three-zone `RevampLayout` that reads the same store selectors, behind a `?revamp=1` flag / Settings toggle. Current UI stays the default fallback.
4. **Zone by zone**: port Right Dock (hardware), then Bottom Drawer (telemetry), then Center Stage phases (reusing the existing `VCurve`, `PolarReticle`, `PreviewStage`, guide graphs — they already emit the right data).
5. **Two front doors**: layer Guided Night over the finished grid.
6. **Flip the default** once parity + the two hero flows are validated on the sim and real gear; keep the flag as an escape hatch for a release.

No backend work. No new endpoints. The native-parity providers, RBAC, relay, and self-update all keep working as-is.

---

## 9. Open decisions for the user

1. **Center Stage auto-switching**: should the engine auto-swap the center during a run (recommended), or only on manual task selection? (Auto with a manual override is my recommendation.)
2. **Inter-zone fibers** (§6 last bullet): a lovely touch or too much motion? Antigravity to prototype, user to judge.
3. **Guided Night scope for v1**: full wizard, or ship Expert Console first and layer Guided later?
4. **Glass performance on the Pi**: `backdrop-filter: blur` is GPU-cheap on modern browsers but worth checking on the observatory Pi/tablet; we'll measure in the prototype before committing.

---

*Next: open `mockup.html` to see this structure in the Obsidian & Nebula theme. Claude owns the HTML structure + JS + simulated data; Antigravity owns the visual CSS (the marked `<style>` block) from here.*
