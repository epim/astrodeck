# design-sync notes — AstroDeck

## The shape of this repo, and the one decision it forces

`ui/` is a **private Vite APPLICATION**, not a publishable library package:

- `package.json` has no `main`, no `module`, no `exports`, and `"private": true`
- `npm run build` is `tsc -b && vite build` — it emits an APP bundle
  (`ui/dist/index.html` + `assets/`), not a library entry
- nothing emits `.d.ts` anywhere in the tree

RESOLVED 2026-08-09 by ADDING A LIBRARY BUILD rather than taking the
converter's synth-entry last resort. Synth-entry produces thin `.d.ts`, and a
thin contract is worse than a missing one: the design agent trusts it and
misuses the API in every design it makes.

Three new files, all committed:

- `ui/src/design-system.ts` — the barrel. Exports ONLY; nothing with an
  import-time side effect (the store, the API client, `index.css`) is in it, so
  importing `Panel` cannot start polling a telescope.
- `ui/tsconfig.lib.json` — declaration emit. A SEPARATE config because the app's
  tsconfig sets `noEmit` and `allowImportingTsExtensions` (which *requires*
  `noEmit`); turning emit on in the shared file would break `npm run build`.
- `ui/vite.lib.config.ts` — lib mode, ES only, React external (a second React
  in the bundle gives consumers two copies and the hook errors that follow).

`npm run build:lib` → `ui/dist-lib/index.es.js` (~124 kB) + `dist-lib/types/`.

**THE ORDERING TRAP, which cost a silent failure:** the first version ran
`tsc && vite build`. Vite has `emptyOutDir: true`, so it wiped `dist-lib/` —
including the declarations tsc had just written — and the build reported
success with NO `.d.ts` at all. The script now runs **vite first, tsc second**.
If you ever reorder it, check `ls dist-lib/types` before believing the exit
code.

Verified: the app build (`npm run build`) and the UI suite (2072 tests) are both
still green — the library build is additive.

## Scope agreed for the first sync

Design system only — NOT all 115 components:

- `src/components/ui.tsx` — 17 exports (Panel, Led, HonestButton, LockedChip,
  Tooltip, InfoDot, lockedProps, LOCKED_CLASS, …)
- `src/components/ui/` — CameraDial, RingPicker, StepDial, SegmentedControl,
  PickerButton, CameraPickers, ActivityRing

The other ~100 components are screen-specific compositions (GuideQuickBar,
PolarReticle, FilterNamesModal) — app code, not library parts a design agent
should build new screens from. `componentSrcMap` is the lever if that changes.

## Source shape

No Storybook and no `*.stories.*` anywhere (confirmed by glob over the whole
repo, not just the root) — hence `shape: "package"` and authored previews
graded on the absolute rubric.

## Gotchas already hit

- The repo root is `C:\Users\bear\astro`; the UI package is `ui/`. Config lives
  at the ROOT `.design-sync/`, `pkg` points into `ui`.
- Tests are a custom tsx runner (`node --import tsx run-tests.mjs`), not vitest
  or jest. `run-tests.mjs` sums `passed + failed` from each file's exported
  result — a test appended BELOW a file's summary block is not counted. Any
  preview-adjacent test must go inside the counted region.
- Windows + Git Bash. PowerShell heredocs through `ssh` eat `$var` and
  backticks; write a `.ps1` to the scratchpad and `scp` it instead.

## Re-sync risks

- **Nothing is uploaded yet.** The project `cd466e63-1d00-46be-b8b2-af610ce36e18`
  exists and is EMPTY and un-anchored (no `_ds_sync.json`). That is the
  documented safe state: the next run re-verifies everything and nothing rots.
- If option 1 (synth-entry) is taken, the `.d.ts` quality is a standing risk —
  re-check `<Name>Props` against the real component signatures before trusting
  a card, and record any component whose props came out empty.
- Two design-system components changed on 2026-08-09 and any preview must
  reflect them: `CameraDial` gained an honest-disabled locked face for stages
  under `DIAL_MIN_R`, and the icon set was replaced with v2 (stroke now
  auto-compensates by size when `strokeWidth` is omitted).
