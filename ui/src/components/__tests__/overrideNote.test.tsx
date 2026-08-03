// overrideNote.test.tsx — the disclosure that a control is showing a value the
// rig is not running (#129), plus the provider-chip variant map.
//
//   Run directly:  npx tsx src/components/__tests__/overrideNote.test.tsx
//   Also type-checked by `tsc -b`.
//
// No vitest, no jsdom, no layout — nothing here asserts a pixel. These are copy
// and contract assertions, and both are load-bearing for this feature:
//
//   * COPY, because "overridden" with no value is exactly the badge that would
//     have been ignored. Every rendered sentence must name the profile AND the
//     value being shadowed, or the user still cannot answer "what is running,
//     and what would run without this?".
//   * CONTRACT, because the four provider kinds used to collapse into two
//     classes — and two of those were byte-identical CSS. A test that only
//     rendered the label would have passed the entire twelve-day bug.

import { renderToStaticMarkup } from "react-dom/server";
import type { EffectiveEntry } from "../../types";
import { LayerChip, OverrideNote, RunningNote } from "../OverrideNote";
// From lib/, not from ProviderBadge itself: the component imports the store,
// which reads `window` at module scope. See lib/providerChip.ts.
import { provVariant } from "../../lib/providerChip";

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}

const entry = (p: Partial<EffectiveEntry>): EffectiveEntry => ({
  value: null,
  layer: "default",
  profile: null,
  config: null,
  default: null,
  profile_id: null,
  profile_name: null,
  reason: null,
  ...p,
});

const PINNED = entry({
  value: "sim",
  layer: "profile",
  profile: "sim",
  config: "astrodeck",
  profile_id: "p1",
  profile_name: "Backyard rig",
});

// ------------------------------------------------------- the chip carries who

test("the layer chip names the layer AND the profile", () => {
  const html = renderToStaticMarkup(<LayerChip entry={PINNED} />);
  assert(html.includes("PROFILE"), `names the layer: ${html}`);
  assert(html.includes("Backyard rig"), `names the profile: ${html}`);
  assert(
    html.includes("layer-chip-profile"),
    `carries the non-hue (dashed-border) class: ${html}`,
  );
});

test("the chip's accessible name says which profile, not just 'overridden'", () => {
  const html = renderToStaticMarkup(<LayerChip entry={PINNED} />);
  assert(
    html.includes('aria-label="Pinned by profile Backyard rig"'),
    `a11y name carries the profile: ${html}`,
  );
});

test("the camera chip is a DIFFERENT class, not a recoloured one", () => {
  const html = renderToStaticMarkup(
    <LayerChip entry={entry({ layer: "camera", value: 3.76 })} />,
  );
  assert(html.includes("layer-chip-camera"), `dotted variant: ${html}`);
  assert(!html.includes("layer-chip-profile"), `not the profile variant: ${html}`);
});

test("config / default layers render no chip at all", () => {
  eq(renderToStaticMarkup(<LayerChip entry={entry({ layer: "config" })} />), "");
  eq(renderToStaticMarkup(<LayerChip entry={null} />), "");
});

// ------------------------------------------------- the note carries the values

test("the note prints BOTH the running value and the one being shadowed", () => {
  const html = renderToStaticMarkup(<OverrideNote entry={PINNED} />);
  assert(html.includes("sim"), `what runs: ${html}`);
  assert(html.includes("astrodeck"), `what would run without the profile: ${html}`);
  assert(html.includes("Backyard rig"), `who: ${html}`);
});

test("a formatter reaches BOTH layers' values, not just the winner", () => {
  const html = renderToStaticMarkup(
    <OverrideNote
      entry={entry({ value: 250, layer: "profile", config: 530, profile_name: "R" })}
      format={(v) => `${v} mm`}
    />,
  );
  assert(html.includes("250 mm"), `winner formatted: ${html}`);
  assert(html.includes("530 mm"), `loser formatted too: ${html}`);
});

test("the way out is offered — and only when there is one", () => {
  const withClear = renderToStaticMarkup(
    <OverrideNote entry={PINNED} onClear={() => {}} clearLabel="Clear the profile pin" />,
  );
  assert(withClear.includes("Clear the profile pin"), `offers the escape: ${withClear}`);
  const readOnly = renderToStaticMarkup(<OverrideNote entry={PINNED} />);
  assert(
    !readOnly.includes("<button"),
    `no button for a caller that cannot clear: ${readOnly}`,
  );
});

test("a clear button is never offered for a CAMERA-supplied value", () => {
  // There is no pin to remove; a button here would fail silently or clear an
  // unrelated block.
  const html = renderToStaticMarkup(
    <OverrideNote
      entry={entry({ layer: "camera", value: 3.76, config: 0 })}
      onClear={() => {}}
    />,
  );
  assert(html.includes("3.76"), `still discloses the camera value: ${html}`);
  assert(!html.includes("<button"), `but offers no clear: ${html}`);
});

test("nothing renders for a key that is not overridden", () => {
  eq(renderToStaticMarkup(<OverrideNote entry={entry({ layer: "config", value: 1 })} />), "");
  eq(renderToStaticMarkup(<OverrideNote entry={null} />), "");
});

// -------------------------------------------- the compact note stays quiet

test("the compact note says nothing when the box already shows the truth", () => {
  // A profile pin whose value MATCHES the field it sits under. Copy that
  // describes what is on screen is copy that gets skipped.
  const html = renderToStaticMarkup(
    <RunningNote
      entry={entry({ value: 530, layer: "profile", config: 530, profile_name: "R" })}
    />,
  );
  eq(html, "");
});

test("the compact note speaks when the box and the rig disagree", () => {
  const html = renderToStaticMarkup(
    <RunningNote
      entry={entry({ value: 250, layer: "profile", config: 530, profile_name: "R" })}
      format={(v) => `${v} mm`}
    />,
  );
  assert(html.includes("250 mm"), `prints what runs: ${html}`);
  // Terse by design — the WHY is stated once in the panel's banner. What this
  // must still carry on its own is the LAYER, which the chip supplies (and
  // which the accessible name expands to name the profile).
  assert(html.includes("layer-chip-profile"), `carries the layer: ${html}`);
  assert(html.includes('aria-label="Pinned by profile R"'), `a11y names it: ${html}`);
});

test("the compact note explains a 0 box the rig is not running at 0", () => {
  const html = renderToStaticMarkup(
    <RunningNote
      entry={entry({ value: 3.76, layer: "camera", config: 0 })}
      format={(v) => `${v} µm`}
    />,
  );
  assert(html.includes("3.76 µm"), `prints the camera's value: ${html}`);
  assert(html.includes("camera"), `names the source: ${html}`);
});

// ------------------------------------------------ four kinds, four treatments

test("every provider kind gets its OWN chip class", () => {
  const seen = new Map<string, string>();
  for (const kind of ["astrodeck", "sim", "backend", "astap", "unavailable"] as const) {
    seen.set(kind, provVariant(kind));
  }
  // astrodeck vs sim is THE pair that went unnoticed for twelve days.
  assert(
    seen.get("astrodeck") !== seen.get("sim"),
    `native and simulated must differ, got "${seen.get("astrodeck")}" / "${seen.get("sim")}"`,
  );
  // NINA vs Unavailable were the other byte-identical pair.
  assert(
    seen.get("backend") !== seen.get("unavailable"),
    `backend and unavailable must differ, got "${seen.get("backend")}" / "${seen.get("unavailable")}"`,
  );
  // astap shares the "external tool" treatment with backend — deliberate.
  eq(seen.get("astap"), seen.get("backend"), "astap rides the external variant");
});

test("the pre-first-poll slot is the faint one, not a healthy-looking chip", () => {
  eq(provVariant("pending"), " prov-na");
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\noverrideNote.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
