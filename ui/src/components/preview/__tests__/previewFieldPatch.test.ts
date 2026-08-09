// previewFieldPatch.test.ts — the late-solve patch, in the store (#182).
//
//   Run directly:  npx tsx src/components/preview/__tests__/previewFieldPatch.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The background WCS worker finishes SECONDS after the picture is on screen, so
// the identification cannot ride the `preview` event that carried the JPEG. It
// arrives as a small `preview_field` event keyed by preview id, and the store
// merges it into the frame already held.
//
// Two things can go wrong here and neither is visible in a screenshot:
//   * patching the WRONG frame — markers from frame N drawn over frame N+1,
//     which look right and are not;
//   * not clearing on invalidation — a name that outlives the slew that left
//     the field it names, which is the most confidently wrong output this
//     feature can produce.

/* eslint-disable @typescript-eslint/no-explicit-any */

class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? this.m.get(k)! : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
}
const g = globalThis as any;
if (typeof g.localStorage === "undefined") g.localStorage = new MemStorage();
if (typeof g.window === "undefined") {
  g.window = { location: { pathname: "/", host: "localhost", protocol: "http:" } };
}
if (typeof g.document === "undefined") {
  g.document = {
    documentElement: { classList: { add() {}, remove() {}, toggle() {} },
                       style: { setProperty() {} } },
    addEventListener() {}, removeEventListener() {},
  };
}

const { useStore } = await import("../../../store");
type PreviewInfo = import("../../../types").PreviewInfo;
type PreviewField = import("../../../types").PreviewField;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

function preview(id: number): PreviewInfo {
  return {
    id,
    stats: { min: 0, max: 1, mean: 0.5, median: 0.5, std: 0.1 },
    histogram: [1], histogram_domain: "linear",
    exposure_s: 60, gain: 100, binning: 1,
    data_width: 1000, data_height: 800,
    display_width: 500, display_height: 400,
    mime: "image/jpeg", source: "sim",
    is_stretched: false, data_is_linear: true, has_lossless: true,
    full_well: 65535, auto_levels: { black: 0, mid: 0.5, white: 1 },
    ts: 1_786_255_200,
  };
}

const FIELD: PreviewField = {
  source: "solve",
  solved_at: 1_786_255_200,
  id: {
    id: "M 27", label: "Dumbbell Nebula", kind: "dso", type: "Planetary Nebula",
    describe: "Dumbbell Nebula — planetary nebula in Vulpecula",
    sep_arcmin: 2.1, confident: true, runner_up: null,
  },
  objects: [],
};

function seed(ids: number[]): void {
  for (const id of ids) {
    useStore.getState().handleEvent({
      type: "preview", data: preview(id) as unknown as Record<string, unknown>, ts: 0,
    });
  }
}

test("a late solve patches the frame it names", () => {
  seed([11, 12]);
  useStore.getState().handleEvent({
    type: "preview_field",
    data: { preview_id: 12, field: FIELD } as unknown as Record<string, unknown>,
    ts: 0,
  });
  const s = useStore.getState();
  assert(s.preview!.field?.id?.id === "M 27",
    "the live frame never received the solve, so the browser still never sees one");
  const held = s.previews.find((p) => p.id === 12);
  assert(held!.field?.id?.id === "M 27", "the ring copy was left un-patched");
});

test("a solve NEVER lands on a frame it did not come from", () => {
  seed([21, 22]);
  useStore.getState().handleEvent({
    type: "preview_field",
    data: { preview_id: 21, field: FIELD } as unknown as Record<string, unknown>,
    ts: 0,
  });
  const s = useStore.getState();
  assert(s.preview!.id === 22, "fixture: 22 should be live");
  assert(s.preview!.field === undefined,
    "a WCS solved from frame 21 was attached to frame 22. Those markers would "
    + "look right and be wrong.");
  assert(s.previews.find((p) => p.id === 21)!.field?.id?.id === "M 27",
    "and frame 21 should still have got it");
});

test("an invalidation clears the field from every frame", () => {
  seed([31, 32]);
  useStore.getState().handleEvent({
    type: "preview_field",
    data: { preview_id: 31, field: FIELD } as unknown as Record<string, unknown>,
    ts: 0,
  });
  useStore.getState().handleEvent({
    type: "preview_field",
    data: { preview_id: 32, field: FIELD } as unknown as Record<string, unknown>,
    ts: 0,
  });
  useStore.getState().handleEvent({
    type: "preview_field",
    data: { preview_id: null, field: null, reason: "slewing" } as unknown as Record<string, unknown>,
    ts: 0,
  });
  const s = useStore.getState();
  assert(s.preview!.field === undefined,
    "the identification outlived the slew that left the field it names");
  assert(s.previews.every((p) => p.field === undefined),
    "a stale identification survived on a held frame");
});

test("the typed target name survives a view change", () => {
  // It used to be CaptureView's own useState, and App.tsx keys <ViewBoundary> by
  // view — so leaving for the Atlas and coming back wiped a name the operator
  // had typed, with no warning and no way to notice until the morning.
  useStore.getState().setCaptureTarget("Veil east");
  useStore.getState().setView("atlas");
  useStore.getState().setView("capture");
  assert(useStore.getState().captureTarget === "Veil east",
    `the typed name was lost across a view change: ` +
    `${JSON.stringify(useStore.getState().captureTarget)}`);
});

const total = passed + failed;
console.log(`previewFieldPatch.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
