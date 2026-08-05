# NOV-9 — In-app field explainers + troubleshooting page + actionable errors

Combined design spec + TDD implementation plan. One file.

Slug: `inapp-help`

---

## 1. Design

### 1.1 Goal

A beginner meets three walls in their first session: (a) cryptic field labels
(gain, HFR, calibration frames), (b) a failure with no idea what to do, and (c)
no single place that says "my image is black — here's why." NOV-9 closes all
three with **pure, tested content cores** plus **thin renders**:

1. **Expand the glossary** (`help.ts`) with the beginner-first fields that are
   currently missing (gain, exposure, darks/flats/bias, plate-solve, guiding).
2. **A pure `failure → {title, cause, fix, topic}` map** (`lib/troubleshoot.ts`)
   that turns a raw error string into a plain cause + first fix + an in-app
   topic anchor — the broader mapping the brief asks for, alongside the browsable
   symptom→guide content that backs the Help page.
3. **A troubleshooting page** (`views/HelpView.tsx`, thin render of the pure
   data) reachable from mobile overflow, the log drawer, and directly from the
   error via a **"How to fix →"** deep-link.
4. **Surface cause+fix on the error inline** — the sequence-fail toast and the
   inline sequence-error detail in Monitor/Sequence now show cause+fix and link
   into the matching Help topic.

The glossary object and the failure map are the **pure tested cores** (tsx
inline-assert). Everything React is a thin render verified only by `tsc -b`.

### 1.2 Current-state seams (real file:line, all read)

- **Glossary today** — `ui/src/help.ts:10-29` `HELP` object, 9 keys (`offset`,
  `hfr`, `stepSize`, `dither`, `hfrReject`, `filterOffset`, `coolTo`,
  `meridianFlip`, `binning`). `HelpKey` type `help.ts:31`; safe lookup
  `helpText()` `help.ts:34-37`. **No `gain`, no `exposure`, no calibration-frame
  keys** — the exact gap the brief names.
- **How HELP is consumed** — `Field({label, hint})` renders `hint` in an
  `InfoDot` (`ui/src/components/ui.tsx:55-67`, dot at `:62`); `Stat` does the
  same (`ui.tsx:104-123`, `:108`). `InfoDot` itself = 14px info icon in a
  Tooltip (`ui.tsx:476-487`). Call sites pass `hint={HELP.offset}` etc.:
  `CaptureView.tsx:361` (Offset), `:364` (Binning), `:676` (coolTo). Checklist
  rows consume via `helpText(item.help)` → `InfoDot` (`Checklist.tsx:15,87,103`).
- **Gain / Exposure fields exist but carry NO hint** — `CaptureView.tsx:335`
  Exposure `<Field label="Exposure (s)">`, `:347` Gain
  `<Field label={\`Gain${…}\`}>`. Both are prime hint targets. (A *separate*
  photometry "Gain (e-/ADU)" field at `:401` already has its own inline hint —
  different concept, leave it.)
- **Humanize today** — `ui/src/lib/humanize.ts:13-33` `humanizeLog`,
  `:36-48` `humanizeSeqError`: small keyword→one-sentence maps. Consumed at
  `store.ts:1145` (log toast title), `store.ts:1339` + `:1342` (sequence-fail
  toast detail + notify body), `store.ts:1382` (log-error toast title), and
  rendered inline at `MonitorView.tsx:461` and `SequenceView.tsx:457`. These
  return a *sentence*, never a structured cause/fix/link — the missing slice.
- **Where a failure surfaces** — the sequence-fatal toast is built at
  `store.ts:1333-1342`: `{level:"error", kind:"sequence", ttl:0,
  title:"Sequence failed", detail:humanizeSeqError(...), action:{label:"View
  log", kind:"openLog"}}`. Rendered by `Toasts.tsx:35-87` (`ToastCard`): title
  `:48`, detail `:61-63`, single action button `:74-84` — which **only handles
  `action.kind === "openLog"`** (`:78`). Log-error toasts (`store.ts:1379-1385`)
  carry a humanized title but no detail/action.
- **Toast type** — `types.ts:472-483`; `action?: { label: string; kind:
  "openLog" }` (`:481`). `EnqueueInput.action` is `Toast["action"]`
  (`store.ts:101`) — pass-through, so widening the union flows automatically.
- **Views + nav** — `ViewName` union `types.ts:11-23` (12 members). App view map
  `App.tsx:56-73` is `Record<ViewName, …>` (**exhaustive** — adding a member
  forces an entry). Primary rail `NAV` is a hand-listed array
  (`App.tsx:41-53`) — adding a `ViewName` does **not** force a rail button
  (precedent: `report` is a `ViewName` but not in `NAV`). Mobile overflow
  `OVERFLOW_VIEWS` (`NavMoreSheet.tsx:36-48`) is also hand-listed; `report`
  rides there. `SettingsView` is a local-`useState` tab shell
  (`components/settings/SettingsView.tsx:42-53`) — deep-linking a sub-tab from a
  toast is awkward (no store handle), which is why a dedicated view wins.
- **Icons** — `info` and `alert` already exist (`icons.tsx:17`); reuse `info`
  for the Help nav/deep-link. No new icon needed.
- **Store shape** — `view: ViewName` state `store.ts:430`, initial
  `view:"connect"` `:674`; `setView` `:760`; `openLog` `:992`. New state/actions
  slot in next to these.
- **Test idiom** — no jsdom/vitest; pure logic runs under `npx tsx <file>` with a
  hand-rolled `test/eq/assert` harness. Canonical example
  `ui/src/lib/__tests__/eta.test.ts:1-197` (verified green: `26/26 passed`).
  Humanize is already covered there via `foundation.test.ts:116-140`.

### 1.3 Approach (data shapes / copy / behavior / algorithm)

**A new pure module `ui/src/lib/troubleshoot.ts`** holds two things:

1. `diagnoseFailure(raw: string | undefined): Diagnosis` — case-insensitive,
   ordered-most-specific-first substring match; first rule whose needles all
   match wins; unknown/empty → a safe generic diagnosis (never throws). This is
   the `failure → {cause, fix, docLink}` map. `topic` is the "docLink" — an
   in-app anchor (offline-first; the app ships no external help site), `null`
   when there's no dedicated guide.
2. `TROUBLESHOOTING: readonly TroubleshootEntry[]` — the browsable
   symptom→guide content the Help page renders ("My image is completely black",
   "Stars are streaks"), each keyed by the same `TroubleshootTopic` so a
   diagnosis's `topic` deep-links to its full entry. `getTroubleshootEntry()`
   joins the two.

`TroubleshootTopic` lives in **`types.ts`** (next to `ViewName`) so `Toast.action`
and the store can reference it without importing from `lib/` (mirrors how
`ViewName` lives in `types.ts`). `troubleshoot.ts` imports the type from
`../types`; it imports nothing else — no cycle, no React, no store.

**Glossary expansion** adds 7 keys to `HELP` (gain, exposure, darks, flats,
bias, plateSolve, guiding). Existing keys and their wording are untouched
(they carry prior critique fixes — see `help.ts:2-5`).

**Inline actionable errors.** The sequence-fail path (`store.ts:1333-1342`) is
rewritten to call `diagnoseFailure(seq.detail)` and emit `title=diag.title`,
`detail=\`${diag.cause} ${diag.fix}\``, and — when `diag.topic` is set — an
`action:{label:"How to fix →", kind:"openHelp", topic}`. The `Toast.action`
union widens to include that variant; `ToastCard` gains one `else if`
(`openHelp` → `openHelp(topic)`). The event log stays reachable from the header
LOG badge, so replacing "View log" with "How to fix →" on this one focal toast
loses nothing. The inline detail in `MonitorView.tsx:461` /
`SequenceView.tsx:457` gets the same cause+fix + "How to fix →" treatment.

**Deep-link plumbing.** New store field `helpTopic: TroubleshootTopic | null`
(never persisted) + actions `openHelp(topic?)` (sets `view:"help"` +
`helpTopic`) and `clearHelpTopic()`. `HelpView` scrolls the arriving topic into
view, highlights it, then one-shot-clears it after a beat so a later manual
visit isn't stuck highlighting.

Algorithm note (`diagnoseFailure` ordering): rules are ordered so a specific
compound like `["plate","solve"]` is tested before single-needle rules, and
overlapping single needles are ordered by how diagnostic they are (`guid` before
`camera` so "guiding lost, camera busy" resolves to guiding). Ordering is pinned
by tests, not by hope.

### 1.4 Placement

- **Pure cores:** `ui/src/help.ts` (glossary), `ui/src/lib/troubleshoot.ts`
  (map + page content). Tests in `ui/src/lib/__tests__/troubleshoot.test.ts`.
- **Page:** `ui/src/views/HelpView.tsx` — new `ViewName "help"`, added to
  `App.tsx` `VIEWS` (NOT to the `NAV` rail — matches `report`).
- **Reach:** `NavMoreSheet.OVERFLOW_VIEWS` gains a Help row (mobile); the log
  drawer footer gains a "Troubleshooting guide →" link (all viewports); the
  error toast/inline detail deep-link straight to the topic.
- **Error surface:** `store.ts` sequence-fail path, `Toasts.tsx`,
  `MonitorView.tsx`, `SequenceView.tsx`. Field hints: `CaptureView.tsx`.

---

## 2. Global Constraints (verbatim)

- **Privacy — real coordinates <REDACTED-LAT> / <REDACTED-LON> and the label
  "<REDACTED-SITE-LABEL>" must NEVER appear in code, tests, or docs.** The site default is
  "My Observatory" / 0.0. This feature ships only generic astronomy copy; a
  `troubleshoot.test.ts` assertion greps the serialized `HELP` + `TROUBLESHOOTING`
  for the forbidden substrings as a belt-and-braces guard.
- **Never `git add -A`.** Stage only the files this plan names.
- **UI gate:** `cd ui && npx tsc -b` (must exit 0, no diagnostics).
- **NO jsdom.** Pure logic is tested via `npx tsx` inline-assert, idiom
  `ui/src/lib/__tests__/eta.test.ts`. React is verified by the typechecker only.
- **Backend tests** (none in this feature) would run
  `server/.venv/Scripts/pytest.exe` from repo root, `-n0` single-process.
- **Client toasts** are raised via `useStore.getState().enqueueToast` — this
  feature reuses the existing `store.ts:932` enqueue path; no new toast channel.
- **Honest-disabled (§11.8):** any non-interactive control must be dim + lock +
  `aria-disabled` + `title`, never native `disabled`. (NOV-9 adds no disabled
  controls; the Help nav row and links are always live.)
- **Do not disrupt astrotown** — pure UI/content change, no backend, no deploy.

---

## 3. TDD Plan

Dependency order: **T1** and **T2** are independent pure cores. **T3** depends
on T2 (imports `diagnoseFailure`, `TroubleshootTopic`). **T4** depends on T2+T3.
**T5** depends on T2+T3. Run `cd ui && npx tsc -b` after every task.

### Interfaces (exact signatures)

```ts
// ui/src/help.ts — HELP gains 7 keys; signatures unchanged.
export const HELP: { /* …9 existing… */
  gain: string; exposure: string; darks: string; flats: string;
  bias: string; plateSolve: string; guiding: string;
} /* as const */;
export type HelpKey = keyof typeof HELP;                 // unchanged
export function helpText(k: string | undefined): string | undefined; // unchanged

// ui/src/types.ts
export type ViewName = /* …existing… */ | "help";
export type TroubleshootTopic =
  | "black-frame" | "star-trails" | "elongated-stars" | "wont-solve"
  | "camera-offline" | "guiding-lost" | "cooler-stuck" | "mount-move-failed"
  | "autofocus-failed" | "nina-error";
export interface Toast {
  /* …existing… */
  action?:
    | { label: string; kind: "openLog" }
    | { label: string; kind: "openHelp"; topic: TroubleshootTopic };
}

// ui/src/lib/troubleshoot.ts
export interface Diagnosis {
  title: string; cause: string; fix: string; topic: TroubleshootTopic | null;
}
export function diagnoseFailure(raw: string | undefined): Diagnosis;
export interface TroubleshootEntry {
  topic: TroubleshootTopic; symptom: string; cause: string;
  steps: readonly string[]; seeAlso?: readonly string[];
}
export const TROUBLESHOOTING: readonly TroubleshootEntry[];
export function getTroubleshootEntry(
  topic: TroubleshootTopic | null | undefined,
): TroubleshootEntry | undefined;

// ui/src/store.ts (AppState additions)
helpTopic: TroubleshootTopic | null;
openHelp: (topic?: TroubleshootTopic) => void;
clearHelpTopic: () => void;

// ui/src/views/HelpView.tsx
export default function HelpView(): JSX.Element;
```

---

### T1 — Expand the glossary (`help.ts`) + tests  ·  **Sonnet** (pure copy data)

*Justification:* data-only edit; correctness guarded by a schema + privacy test.

**Files:** `ui/src/help.ts`, `ui/src/lib/__tests__/troubleshoot.test.ts` (HELP
assertions live in the T2 test file since they cross-link; if T2 not yet done,
add a temporary `help.test.ts` — see step 2).

**Step 1.1** — Add 7 keys inside the `HELP` object in `help.ts`, before
`} as const;` (`help.ts:29`). Update the header comment block
(`help.ts:1-8`) to mention the beginner set.

```ts
  gain:
    "Gain is how strongly the sensor amplifies what it reads (the digital-camera analogue of ISO). Higher gain brightens faint detail and lowers read noise, but shrinks dynamic range so bright stars clip sooner. Most CMOS cameras have a recommended or 'unity' gain — start there. Gain does NOT collect more light; only longer exposures or more frames do that.",
  exposure:
    "Sub-exposure length: how long each single frame is open, in seconds. Longer subs collect more signal per frame (good for faint targets under dark skies) but risk saturating stars and trailing on a poorly-guided mount. Total integration is what matters — many shorter subs stack to the same depth with less risk.",
  darks:
    "Dark frames: exposures taken with the scope capped, at the same length, gain and temperature as your lights. They record the sensor's own thermal signal and hot pixels so stacking can subtract them. Reusable from a library if your camera holds a set temperature.",
  flats:
    "Flat frames: shots of an evenly-lit surface that record dust shadows and vignetting (darker corners). Dividing your lights by flats evens out the illumination so gradients and dust motes disappear. Take them without changing focus or rotation from your lights.",
  bias:
    "Bias frames: the shortest exposures your camera can take, with the scope capped. They capture the fixed electronic pedestal the sensor adds to every read, so calibration can remove it cleanly. Also called offset frames.",
  plateSolve:
    "Plate-solving matches the stars in a test frame against a catalogue to work out exactly where the scope is pointing. The app uses it to centre your target precisely and to re-find it after a meridian flip. It needs a focused frame with enough stars — a black or badly-defocused frame won't solve.",
  guiding:
    "Autoguiding keeps the target fixed on the sensor during long exposures. A second small camera watches a star and sends tiny correction nudges to the mount whenever it drifts. Good guiding is what lets you shoot minutes-long subs without trailed stars.",
```

**Step 1.2** — Assertions (folded into the T2 test file, which imports `HELP`):
```ts
import { HELP, helpText } from "../../help";
const NEW_KEYS = ["gain","exposure","darks","flats","bias","plateSolve","guiding"] as const;
test("help: new beginner keys are present + non-empty", () => {
  for (const k of NEW_KEYS) {
    assert(typeof (HELP as Record<string,string>)[k] === "string", `${k} missing`);
    assert((HELP as Record<string,string>)[k].trim().length > 20, `${k} too short`);
  }
});
test("help: existing keys survive (regression)", () => {
  for (const k of ["offset","hfr","binning","coolTo"]) assert(!!helpText(k), `${k} lost`);
});
test("help: unknown key → undefined", () => { eq(helpText("nope"), undefined); });
```

**Typecheck:** `cd ui && npx tsc -b` → no output, exit 0.
**Test (runs with T2):** `cd ui && npx tsx src/lib/__tests__/troubleshoot.test.ts`
→ `troubleshoot.test: N/N passed`.

---

### T2 — Pure `troubleshoot.ts` (map + page content) + tests  ·  **Sonnet**

*Justification:* substring matching + static content; the ordering and every
cross-reference are pinned by tests, so no subtle judgment ships unguarded.

**Files:** `ui/src/lib/troubleshoot.ts` (new),
`ui/src/lib/__tests__/troubleshoot.test.ts` (new),
plus `TroubleshootTopic` added to `ui/src/types.ts` (next to `ViewName`,
`types.ts:23`).

**Step 2.1** — Add to `types.ts` after the `ViewName` union (`types.ts:23`):
```ts
export type TroubleshootTopic =
  | "black-frame" | "star-trails" | "elongated-stars" | "wont-solve"
  | "camera-offline" | "guiding-lost" | "cooler-stuck" | "mount-move-failed"
  | "autofocus-failed" | "nina-error";
```

**Step 2.2** — Write `ui/src/lib/troubleshoot.ts` (full content):
```ts
// troubleshoot.ts — the pure failure→{cause,fix,topic} map (NOV-9) and the
// browsable symptom→guide content behind the in-app Help view. Both are pure,
// data-only, tsx-testable: no React, no store, no I/O. diagnoseFailure() turns a
// raw error string (a sequence detail or a log message) into a plain cause +
// first fix + an in-app topic anchor; TROUBLESHOOTING is the reference content
// the Help view renders and that a diagnosis's topic deep-links into.
import type { TroubleshootTopic } from "../types";

export interface Diagnosis {
  /** Short sentence-case headline, e.g. "Plate-solve failed". */
  title: string;
  /** Plain-language most-likely cause (one sentence). */
  cause: string;
  /** The first concrete thing to try (one sentence). */
  fix: string;
  /** In-app Help topic to deep-link to, or null when there's no dedicated guide. */
  topic: TroubleshootTopic | null;
}

interface Rule { needles: string[]; diag: Diagnosis; }

// Ordered most-specific first; the first rule whose needles ALL match wins.
const RULES: readonly Rule[] = [
  { needles: ["plate", "solve"], diag: {
      title: "Plate-solve failed",
      cause: "The frame didn't have enough recognisable stars to match the sky — usually soft focus, too short an exposure, or cloud.",
      fix: "Refocus, raise the exposure a little and try again; if it still fails, solve manually from a bright named star.",
      topic: "wont-solve" } },
  { needles: ["cool"], diag: {
      title: "Cooler didn't reach target",
      cause: "The set-point may be colder than the cooler can hold tonight, or the camera isn't getting enough power.",
      fix: "Raise the target a few degrees (aim ~25–30°C below ambient) and give the camera its own 12V supply.",
      topic: "cooler-stuck" } },
  { needles: ["guid"], diag: {
      title: "Guiding failed",
      cause: "The guide star was lost — cloud, a cable snag, or a mount lurch pushed it off the guide sensor.",
      fix: "Pick a brighter guide star on the Guide page and re-run calibration if the mount was slewed.",
      topic: "guiding-lost" } },
  { needles: ["camera"], diag: {
      title: "Camera isn't responding",
      cause: "The imaging camera dropped its USB connection — often a marginal cable, a hub power dip, or the driver crashing.",
      fix: "Re-seat the camera USB cable (a powered hub helps), then reconnect it on the Equipment page.",
      topic: "camera-offline" } },
  { needles: ["slew"], diag: {
      title: "Mount move failed",
      cause: "The slew didn't complete — the mount may be parked, past a limit, or lost communication mid-move.",
      fix: "Unpark and confirm tracking on the Mount page, then retry the slew.",
      topic: "mount-move-failed" } },
  { needles: ["mount"], diag: {
      title: "Mount move failed",
      cause: "The mount refused or couldn't finish a move — it may be parked, past a limit, or not tracking.",
      fix: "Unpark and confirm tracking on the Mount page, then retry.",
      topic: "mount-move-failed" } },
  { needles: ["focus"], diag: {
      title: "Autofocus failed",
      cause: "The routine couldn't fit a V-curve — too few stars, the step size too large, or the focuser slipping.",
      fix: "Start from roughly-good focus, lower the step size and re-run on a star-rich field.",
      topic: "autofocus-failed" } },
  { needles: ["nina"], diag: {
      title: "NINA reported an error",
      cause: "The NINA bridge returned an error — the imaging PC, a device driver, or the sequence in NINA hit a problem.",
      fix: "Open NINA on the imaging PC, clear the error there, then resume.",
      topic: "nina-error" } },
];

const GENERIC: Diagnosis = {
  title: "The run stopped",
  cause: "Something interrupted the sequence and it couldn't continue.",
  fix: "Open the event log for the exact message, then retry once the cause is clear.",
  topic: null,
};

/**
 * Map a raw error string (a sequence `detail` or a log message) to a plain
 * cause + first fix + in-app topic. Case-insensitive; first matching rule wins;
 * unknown/empty input returns a safe generic diagnosis. Never throws.
 */
export function diagnoseFailure(raw: string | undefined): Diagnosis {
  const text = (raw ?? "").toLowerCase();
  if (!text.trim()) return GENERIC;
  for (const rule of RULES) {
    if (rule.needles.every((n) => text.includes(n))) return rule.diag;
  }
  return GENERIC;
}

export interface TroubleshootEntry {
  topic: TroubleshootTopic;
  /** The symptom in the operator's own words — the page heading. */
  symptom: string;
  /** A short plain-language explanation of what's going on. */
  cause: string;
  /** Ordered things to try, most-likely-first. */
  steps: readonly string[];
  /** Optional HELP glossary keys to cross-link ("learn the terms"). */
  seeAlso?: readonly string[];
}

export const TROUBLESHOOTING: readonly TroubleshootEntry[] = [
  { topic: "black-frame", symptom: "My image is completely black",
    cause: "The sensor isn't seeing light, or the display stretch is hiding what little there is.",
    steps: [
      "Take the lens/dust cap off and open the flat panel or focuser cover.",
      "Turn on Auto-stretch in the preview — a faint sky often looks black un-stretched.",
      "Raise the exposure (try 2–5s for framing) and set gain to a mid value.",
      "Confirm the camera is actually capturing on the Capture page, not just connected.",
    ], seeAlso: ["gain", "exposure"] },
  { topic: "star-trails", symptom: "Stars are streaks or short lines",
    cause: "The sky moved during the exposure — the mount wasn't tracking, wasn't guiding, or was bumped.",
    steps: [
      "Confirm the mount is tracking (not parked) on the Mount page.",
      "Turn on guiding for exposures longer than ~20–30s.",
      "Check polar alignment — poor alignment trails stars slowly even while tracking.",
      "Shorten the sub-exposure and take more frames if guiding isn't available.",
    ], seeAlso: ["guiding", "meridianFlip"] },
  { topic: "elongated-stars", symptom: "Stars look like small eggs, not round",
    cause: "Slight drift, tilt, or flexure during the sub — less severe than full trails.",
    steps: [
      "Recalibrate and tune guiding; check for a cable dragging on the mount.",
      "Reduce sub-exposure length until stars round up, then add more subs.",
      "If only the corners are elongated, check the camera is square to the focuser (tilt).",
    ], seeAlso: ["guiding", "hfr"] },
  { topic: "wont-solve", symptom: "Plate-solving keeps failing",
    cause: "The solver can't find enough stars to match — usually focus, exposure, or cloud.",
    steps: [
      "Refocus until stars are tight (watch HFR).",
      "Raise the solve exposure so more stars register.",
      "Make sure the entered focal length / pixel scale is roughly right.",
      "Wait out passing cloud, or solve manually from a bright named star.",
    ], seeAlso: ["plateSolve", "hfr"] },
  { topic: "camera-offline", symptom: "The camera keeps disconnecting",
    cause: "USB dropouts — a marginal cable, an unpowered hub, or a dip on the camera's 12V.",
    steps: [
      "Re-seat both ends of the camera USB cable.",
      "Use a powered USB hub, or a shorter / better-shielded cable.",
      "Give a cooled camera its own 12V supply — don't share a marginal rail.",
      "Reconnect the camera on the Equipment page.",
    ] },
  { topic: "guiding-lost", symptom: "Guiding drops out or the star is lost",
    cause: "The guide star disappeared — cloud, a cable snag, or a mount lurch pushed it off the sensor.",
    steps: [
      "Pick a brighter, more central guide star.",
      "Check for cables snagging as the mount tracks.",
      "Re-run guide calibration after any slew or meridian flip.",
    ], seeAlso: ["guiding"] },
  { topic: "cooler-stuck", symptom: "The cooler won't reach the set temperature",
    cause: "The target is colder than the cooler can hold tonight, or it's power-starved.",
    steps: [
      "Set the target ~25–30°C below the current ambient, not a fixed cold number.",
      "Give the camera a dedicated 12V supply rated for the cooler's draw.",
      "Let it settle a few minutes — the cooler ramps slowly to protect the sensor.",
    ], seeAlso: ["coolTo"] },
  { topic: "mount-move-failed", symptom: "The mount won't slew or a move failed",
    cause: "The mount is parked, past a limit, or lost communication mid-slew.",
    steps: [
      "Unpark the mount and confirm it's tracking on the Mount page.",
      "Check the mount's USB/serial connection on the Equipment page.",
      "Clear any limit or safety stop before retrying the slew.",
    ] },
  { topic: "autofocus-failed", symptom: "Autofocus can't find focus",
    cause: "The routine couldn't fit a clean V-curve — too few stars, wrong step size, or a slipping focuser.",
    steps: [
      "Start from roughly-good manual focus so the V-curve is in range.",
      "Lower the focuser step size for a finer search.",
      "Run on a star-rich field, away from the darkest patches of sky.",
    ], seeAlso: ["stepSize", "hfr"] },
  { topic: "nina-error", symptom: "NINA reported an error",
    cause: "The NINA bridge hit a problem on the imaging PC — a device driver or its own sequence.",
    steps: [
      "Open NINA on the imaging PC and read the error there.",
      "Reconnect the affected device in NINA, then resume.",
      "If NINA is unreachable, check the bridge address on the Equipment page.",
    ] },
];

/** Look up the full guide entry for a topic (or undefined). Pure. */
export function getTroubleshootEntry(
  topic: TroubleshootTopic | null | undefined,
): TroubleshootEntry | undefined {
  return topic ? TROUBLESHOOTING.find((e) => e.topic === topic) : undefined;
}
```

**Step 2.3** — `ui/src/lib/__tests__/troubleshoot.test.ts` (harness copied from
`eta.test.ts:22-43`), assert:
```ts
import { diagnoseFailure, TROUBLESHOOTING, getTroubleshootEntry } from "../troubleshoot";
import { HELP, helpText } from "../../help";
// (harness: test/eq/assert as in eta.test.ts)

// --- diagnoseFailure keyword routing
test("diagnose: plate-solve → wont-solve", () => {
  const d = diagnoseFailure("Plate solve failed after 3 attempts");
  eq(d.topic, "wont-solve"); assert(d.cause.length > 0 && d.fix.length > 0, "copy");
});
test("diagnose: camera → camera-offline", () => eq(diagnoseFailure("Camera not responding").topic, "camera-offline"));
test("diagnose: cool → cooler-stuck", () => eq(diagnoseFailure("Cooler failed to reach -10C").topic, "cooler-stuck"));
test("diagnose: focus → autofocus-failed", () => eq(diagnoseFailure("Autofocus failed").topic, "autofocus-failed"));
test("diagnose: nina → nina-error", () => eq(diagnoseFailure("NINA HTTP 500").topic, "nina-error"));
test("diagnose: case-insensitive", () => eq(diagnoseFailure("MOUNT SLEW ABORTED").topic, "mount-move-failed"));
// --- ordering: guid beats camera when both present
test("diagnose: guiding wins over camera when both present", () =>
  eq(diagnoseFailure("guiding lost, camera busy").topic, "guiding-lost"));
// --- fallback
test("diagnose: unknown → generic, topic null", () => {
  const d = diagnoseFailure("totally novel xyz"); eq(d.topic, null); assert(d.title.length > 0, "title");
});
test("diagnose: empty/undefined → generic", () => {
  eq(diagnoseFailure("").topic, null); eq(diagnoseFailure(undefined).topic, null);
});
// --- referential integrity: every non-null topic a rule can emit has a page entry
test("integrity: every diagnosed topic has a TROUBLESHOOTING entry", () => {
  const probes = ["plate solve","cooler","guiding","camera","slew","mount","focus","nina"];
  for (const p of probes) {
    const t = diagnoseFailure(p).topic;
    if (t) assert(!!getTroubleshootEntry(t), `no entry for ${t}`);
  }
});
// --- page content shape
test("entries: unique topics, non-empty symptom/cause, >=1 step", () => {
  const seen = new Set<string>();
  for (const e of TROUBLESHOOTING) {
    assert(!seen.has(e.topic), `dup ${e.topic}`); seen.add(e.topic);
    assert(e.symptom.length > 0 && e.cause.length > 0 && e.steps.length >= 1, e.topic);
  }
});
test("entries: seeAlso keys all resolve in HELP", () => {
  for (const e of TROUBLESHOOTING) for (const k of e.seeAlso ?? [])
    assert(!!helpText(k), `seeAlso ${k} missing from HELP`);
});
test("getTroubleshootEntry: null/undefined → undefined", () => {
  eq(getTroubleshootEntry(null), undefined); eq(getTroubleshootEntry(undefined), undefined);
});
// --- privacy guard (Global Constraints)
test("privacy: no real coords/label in help content", () => {
  const blob = JSON.stringify(HELP) + JSON.stringify(TROUBLESHOOTING);
  for (const bad of ["37.348","121.801","<REDACTED-SITE-LABEL>"]) assert(!blob.includes(bad), `leaked ${bad}`);
});
```
(Fold the T1 HELP assertions into this file too — one `npx tsx` run covers both.)

**Test:** `cd ui && npx tsx src/lib/__tests__/troubleshoot.test.ts` →
`troubleshoot.test: N/N passed`. **Typecheck:** `cd ui && npx tsc -b` → exit 0.

---

### T3 — Types + store plumbing  ·  **Sonnet** (compiler-guarded, no logic)

*Justification:* union widening + a two-line action + reuse of the T2 map;
`tsc -b` proves exhaustiveness (the `VIEWS` Record forces the T4 entry).

**Files:** `ui/src/types.ts`, `ui/src/store.ts`.

**Step 3.1** — `types.ts`: add `"help"` to `ViewName` (`types.ts:11-23`), and
widen `Toast.action` (`types.ts:481`) to the union shown in the Interfaces
block. `TroubleshootTopic` already added in T2.

**Step 3.2** — `store.ts`: import the map + type near `humanize` import
(`store.ts:41`):
```ts
import { diagnoseFailure } from "./lib/troubleshoot";
import type { TroubleshootTopic } from "./types";   // add to existing type import if present
```
Add to the `AppState` interface: `helpTopic` after `view` (`store.ts:430`);
`openHelp`/`clearHelpTopic` after `setView` (`store.ts:577`). Add initial
`helpTopic: null,` after `view: "connect",` (`store.ts:674`). Add actions after
`setView` (`store.ts:760`):
```ts
  openHelp: (topic) => set({ view: "help", helpTopic: topic ?? null }),
  clearHelpTopic: () => set({ helpTopic: null }),
```

**Step 3.3** — Rewrite the sequence-fail toast (`store.ts:1333-1342`):
```ts
        if (seq.state === "error" && prevState !== "error") {
          const diag = diagnoseFailure(seq.detail);
          get().enqueueToast({
            level: "error",
            kind: "sequence",
            ttl: 0,
            title: diag.title,
            detail: `${diag.cause} ${diag.fix}`,
            action: diag.topic
              ? { label: "How to fix →", kind: "openHelp", topic: diag.topic }
              : { label: "View log", kind: "openLog" },
          });
          notifyAndBeep(get(), diag.title, diag.fix);
        }
```

**Verification (no runtime test — compiler is the gate):**
`cd ui && npx tsc -b` → exit 0. The `Record<ViewName,…>` at `App.tsx:56` now
errors ("help missing") until T4 lands — expected; T3+T4 are typechecked together
or T4 immediately follows. (If landing T3 alone, temporarily map
`help: () => null as never` — but prefer running T3→T4 back-to-back.)

---

### T4 — HelpView + reach (page, nav, log link, toast handler)  ·  **Sonnet**

*Justification:* thin render of pure T2 data + hand-listed nav rows; verified by
`tsc -b`, no logic branch worth a runtime test.

**Files:** `ui/src/views/HelpView.tsx` (new), `ui/src/App.tsx`,
`ui/src/components/NavMoreSheet.tsx`, `ui/src/components/LogDrawer.tsx`,
`ui/src/components/Toasts.tsx`.

**Step 4.1** — `ui/src/views/HelpView.tsx`:
```tsx
import { useEffect, useRef, type JSX } from "react";
import { useStore } from "../store";
import { TROUBLESHOOTING } from "../lib/troubleshoot";
import { HELP, type HelpKey } from "../help";
import { Panel } from "../components/ui";
import { Icon } from "../components/icons";

export default function HelpView(): JSX.Element {
  const helpTopic = useStore((s) => s.helpTopic);
  const clearHelpTopic = useStore((s) => s.clearHelpTopic);
  const focusRef = useRef<HTMLElement>(null);

  // Deep-link: scroll the arriving topic into view + highlight, then one-shot
  // clear so a later manual visit isn't stuck highlighting an old error.
  useEffect(() => {
    if (!helpTopic) return;
    focusRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    const t = window.setTimeout(() => clearHelpTopic(), 2500);
    return () => window.clearTimeout(t);
  }, [helpTopic, clearHelpTopic]);

  return (
    <div className="flex flex-col gap-4 w-full max-w-[820px]">
      <h1 className="font-display text-lg tracking-[0.2em] text-ink uppercase">Help &amp; Troubleshooting</h1>

      <Panel title="Common problems">
        <div className="flex flex-col divide-y divide-line">
          {TROUBLESHOOTING.map((e) => {
            const active = e.topic === helpTopic;
            return (
              <section
                key={e.topic}
                ref={active ? focusRef : undefined}
                aria-current={active ? "true" : undefined}
                className={`py-3 scroll-mt-20 ${active ? "-mx-2 px-2 rounded bg-accent/10" : ""}`}
              >
                <h2 className="text-sm text-ink font-medium inline-flex items-center gap-2">
                  <Icon name="alert" size={14} className="text-warn shrink-0" />
                  {e.symptom}
                </h2>
                <p className="text-xs text-dim mt-1">{e.cause}</p>
                <ol className="text-xs text-ink/90 mt-2 flex flex-col gap-1 list-decimal pl-5">
                  {e.steps.map((s, i) => <li key={i}>{s}</li>)}
                </ol>
              </section>
            );
          })}
        </div>
      </Panel>

      <Panel title="Glossary">
        <dl className="grid gap-3 sm:grid-cols-2">
          {(Object.keys(HELP) as HelpKey[]).map((k) => (
            <div key={k} className="min-w-0">
              <dt className="text-xs font-medium text-ink capitalize">{k.replace(/([A-Z])/g, " $1")}</dt>
              <dd className="text-[11px] text-dim mt-0.5 leading-snug">{HELP[k]}</dd>
            </div>
          ))}
        </dl>
      </Panel>
    </div>
  );
}
```

**Step 4.2** — `App.tsx`: import `HelpView`; add `help: HelpView,` to the `VIEWS`
Record (`App.tsx:56-73`). Do **not** add to `NAV`. (This clears the T3 exhaustive
error.)

**Step 4.3** — `NavMoreSheet.tsx`: append to `OVERFLOW_VIEWS`
(`NavMoreSheet.tsx:47`, after Reports):
```ts
  { id: "help", label: "Help", icon: "info" },
```

**Step 4.4** — `LogDrawer.tsx`: add a footer link inside the docked `<aside>`
(after `{rows}`, `LogDrawer.tsx:141`) and the mobile sheet (after the rows div,
`:162`). Extract once:
```tsx
const openHelp = useStore((s) => s.openHelp);      // add near the other selectors, LogDrawer.tsx:26-28
// footer element (render in both nodes):
<button
  type="button"
  onClick={() => { openHelp(); closeLog(); }}
  className="mt-2 text-[11px] text-accent hover:underline self-start min-h-[44px] sm:min-h-0 inline-flex items-center gap-1"
>
  <Icon name="info" size={12} /> Troubleshooting guide →
</button>
```

**Step 4.5** — `Toasts.tsx`: add the `openHelp` selector (`Toasts.tsx:37`) and
extend the action handler (`Toasts.tsx:78`):
```tsx
  const openHelp = useStore((s) => s.openHelp);
  // ...
  onClick={() => {
    if (t.action!.kind === "openLog") openLog();
    else if (t.action!.kind === "openHelp") openHelp(t.action!.topic);
  }}
```

**Verification:** `cd ui && npx tsc -b` → exit 0. (Optional manual: trigger a
sequence error → toast shows cause+fix + "How to fix →" → lands on the topic,
highlighted.)

---

### T5 — Inline actionable errors on existing surfaces  ·  **Sonnet**

*Justification:* two inline-detail swaps + two field hints; thin, typechecked.

**Files:** `ui/src/views/MonitorView.tsx`, `ui/src/views/SequenceView.tsx`,
`ui/src/views/CaptureView.tsx`.

**Step 5.1** — `MonitorView.tsx:461`: replace the single humanized line with
cause+fix + a deep-link. Import `diagnoseFailure` (near the `humanizeSeqError`
import `MonitorView.tsx:72`) and `useStore`'s `openHelp`:
```tsx
{(() => {
  const diag = diagnoseFailure(seq.detail);
  return (
    <>
      <p className="text-xs text-ink/85 mt-0.5">{diag.cause} {diag.fix}</p>
      {diag.topic && (
        <button type="button" onClick={() => openHelp(diag.topic!)}
          className="text-[11px] text-accent hover:underline mt-1">How to fix →</button>
      )}
    </>
  );
})()}
```
(`humanizeSeqError` may remain imported elsewhere; if now unused, drop the
import to keep `tsc` clean.)

**Step 5.2** — `SequenceView.tsx:457`: same treatment (import `diagnoseFailure`
alongside the existing `humanizeSeqError` import `SequenceView.tsx:14`; pull
`openHelp` from the store).

**Step 5.3** — `CaptureView.tsx`: wire glossary hints into the two bare fields:
- `:335` `<Field label="Exposure (s)">` → `<Field label="Exposure (s)" hint={HELP.exposure}>`
- `:347` `<Field label={\`Gain${…}\`}>` → add `hint={HELP.gain}`.
(`HELP` is already imported at `CaptureView.tsx:26`.)

**Verification:** `cd ui && npx tsc -b` → exit 0. Manual: hover the Gain/Exposure
info dots on Capture; open a live sequence error in Monitor → cause+fix + link.

---

## 4. Open decisions (with recommendations)

1. **Dedicated `help` view vs. a Settings "Help" tab.**
   *Recommendation: dedicated view.* A `ViewName` deep-links cleanly via the
   existing `setView`/`openHelp` (no local-`useState` sub-tab to reach into),
   costs one `VIEWS` entry (compiler-forced), and mirrors the `report` precedent
   (a non-rail `ViewName`). A Settings tab would need a new store handle to open
   a specific tab from a toast — more plumbing for less.

2. **Replace "View log" with "How to fix →" on the focal sequence toast.**
   *Recommendation: replace when a topic matches, else keep "View log".* The
   event log is always one tap away via the header LOG badge, so the focal toast
   is better spent pointing at the fix. A generic (topic-null) failure still
   offers "View log" since there's nothing more specific to show.

3. **Cross-link glossary terms from the troubleshooting steps (`seeAlso`).**
   *Recommendation: keep `seeAlso` in the data + test its integrity now, render
   it later.* The data + referential test are cheap and lock the contract; the
   Help page ships without rendering the chips to keep T4 a pure thin render.
   Wiring `seeAlso` into visible InfoDots is a trivial follow-up if wanted.

4. **Enrich the transient log-error toast (`store.ts:1379-1385`) too.**
   *Recommendation: defer.* That path currently shows only a humanized title.
   Adding detail+deep-link there is a one-block change reusing `diagnoseFailure`,
   but log-error toasts are short-lived and noisier; the sequence-fail toast is
   the high-value novice moment. Revisit after NOV-9 lands if users want it.

5. **Icon for Help.** *Recommendation: reuse `info`.* No `book`/`help` glyph
   exists in `icons.tsx:7-21`; `info` reads correctly and avoids touching the
   reconstructed icon set (`icons.tsx:1-3`).
