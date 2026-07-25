// renderLevels.test.ts — pins the WYSIWYG contract for the full-res export.
// The whole "Download full-res PNG" feature rests on this mapping being right,
// and on Auto mode collapsing EXACTLY onto preview.auto_levels.
import type { StretchParams } from "../../types";
import { isExactWysiwyg, renderQuery, toRenderLevels } from "../renderLevels";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void){try{fn();passed++;}catch(e){failed++;failures.push(`✗ ${n}: ${(e as Error).message}`);}}
function eq<T>(a: T, b: T, m=""){ if(a!==b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function near(a: number, b: number, m="", tol=1e-5){ if(Math.abs(a-b)>tol) throw new Error(`${m} expected ~${b}, got ${a}`); }

const AUTO = { black: 0.12, mid: 0.34, white: 1 };
const S = (o: Partial<StretchParams>): StretchParams => ({
  auto: true, black: 0, mid: 0.5, white: 1, brightness: 0, contrast: 0, advancedOpen: false, ...o,
});

test("Auto + neutral brightness is EXACT: it reproduces preview.auto_levels", () => {
  const L = toRenderLevels(S({}), AUTO);
  near(L.black, AUTO.black, "black");
  near(L.mid, AUTO.mid, "mid");
  near(L.white, AUTO.white, "white");
  eq(isExactWysiwyg(S({})), true);
  // ...and only that case may claim exactness
  eq(isExactWysiwyg(S({ brightness: 0.5 })), false, "brightness nudge is a bake");
  eq(isExactWysiwyg(S({ auto: false })), false, "manual is a bake");
});

test("brightness + manual map onto the linear domain (Decision A1 approximation)", () => {
  // Auto + brighter => a LOWER midtone (more signal lifted), clip points untouched
  const b = toRenderLevels(S({ brightness: 0.5 }), AUTO);
  near(b.black, AUTO.black, "black unchanged by brightness");
  near(b.white, AUTO.white, "white unchanged by brightness");
  near(b.mid, 0.198741, "brightened mid", 1e-4);
  eq(b.mid < AUTO.mid, true, "brighter => lower mid");

  // Manual: the display-domain clip points are pulled back through the auto MTF
  const m = toRenderLevels(S({ auto: false, black: 0.1, mid: 0.4, white: 0.9 }), AUTO);
  near(m.black, 0.167643, "manual black", 1e-4);
  near(m.white, 0.843871, "manual white", 1e-4);
  near(m.mid, 0.262218, "manual mid", 1e-4);
  eq(m.black > AUTO.black && m.white < AUTO.white, true, "manual tightens the window");

  // degenerate manual (white <= black) must never emit an inverted window
  const d = toRenderLevels(S({ auto: false, black: 0.8, mid: 0.8, white: 0.2 }), AUTO);
  eq(d.white > d.black, true, "window stays ordered");
  eq(d.mid > 0 && d.mid < 1, true, "mid stays a usable MTF balance");

  // no auto_levels on the frame => a safe identity-ish window, never a throw
  const f = toRenderLevels(S({}), null);
  near(f.black, 0, "fallback black");
  near(f.white, 1, "fallback white");
});

test("renderQuery emits the full triple the server needs", () => {
  const q = renderQuery(S({}), AUTO);
  eq(q.startsWith("?"), true, "query prefix");
  const p = new URLSearchParams(q.slice(1));
  // ALL THREE always: /render.png only takes the explicit-levels branch with the
  // complete triple — dropping one would silently fall back to auto and ignore
  // the user's stretch.
  eq([...p.keys()].join(","), "black,mid,white");
  near(Number(p.get("black")), AUTO.black, "black param");
  near(Number(p.get("mid")), AUTO.mid, "mid param");
  near(Number(p.get("white")), AUTO.white, "white param");
  // stable string for the same inputs => the server's max-age=3600 cache hits
  eq(renderQuery(S({}), AUTO), q);
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
