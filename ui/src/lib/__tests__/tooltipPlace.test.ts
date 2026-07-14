// tooltipPlace.test.ts — pure tests for lib/tooltipPlace.ts (tooltip fix brief).
// Inline-assert harness (no vitest); runs via `npx tsx`.
import { placeTooltip } from "../tooltipPlace";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq(a: number, b: number, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${b}, got ${a}`);
}

// 1. Centered fit on the default (top) side — exact left/top math.
//    centerX = 100 + 40/2 - 80/2 = 80; top = 100 - 6 - 30 = 64.
test("centered fit on top (default side)", () => {
  const p = placeTooltip({
    trigger: { left: 100, top: 100, width: 40, height: 20 },
    bubble: { width: 80, height: 30 },
    viewport: { width: 1000, height: 800 },
  });
  assert(p.side === "top", `side top, got ${p.side}`);
  eq(p.left, 80, "left");
  eq(p.top, 64, "top");
});

// 2. Right-edge clamp: trigger near the viewport right edge → bubble left clamps
//    to viewport.width - pad - bubble.width = 1000 - 8 - 80 = 912.
test("right-edge clamp", () => {
  const p = placeTooltip({
    trigger: { left: 980, top: 400, width: 10, height: 20 },
    bubble: { width: 80, height: 30 },
    viewport: { width: 1000, height: 800 },
  });
  assert(p.side === "top", `side top, got ${p.side}`);
  eq(p.left, 912, "left clamps to right margin");
  eq(p.top, 364, "top"); // 400 - 6 - 30
});

// 3. Left-edge clamp (mirror): centerX would be negative → clamps to pad (8).
test("left-edge clamp", () => {
  const p = placeTooltip({
    trigger: { left: 2, top: 400, width: 10, height: 20 },
    bubble: { width: 80, height: 30 },
    viewport: { width: 1000, height: 800 },
  });
  assert(p.side === "top", `side top, got ${p.side}`);
  eq(p.left, 8, "left clamps to pad");
});

// 4. Flip top→bottom when the trigger sits too close to the top.
//    top would need top=5-6-30=-31 (fails pad); flips to bottom: top=5+20+6=31.
test("flip top->bottom near the top edge", () => {
  const p = placeTooltip({
    trigger: { left: 100, top: 5, width: 40, height: 20 },
    bubble: { width: 80, height: 30 },
    viewport: { width: 1000, height: 800 },
    side: "top",
  });
  assert(p.side === "bottom", `flipped to bottom, got ${p.side}`);
  eq(p.top, 31, "top below trigger");
});

// 5. Flip bottom→top mirror: trigger near the bottom edge.
//    bottom would need 785+10+6+30=831 > 792 (fails); flips to top: 785-6-30=749.
test("flip bottom->top near the bottom edge", () => {
  const p = placeTooltip({
    trigger: { left: 100, top: 785, width: 40, height: 10 },
    bubble: { width: 80, height: 30 },
    viewport: { width: 1000, height: 800 },
    side: "bottom",
  });
  assert(p.side === "top", `flipped to top, got ${p.side}`);
  eq(p.top, 749, "top above trigger");
});

// 6. side="right" with vertical (cross-axis) clamp near the bottom edge.
//    right fits horizontally (left=100+40+6=146); centerY=780+10-30=760 clamps
//    to viewport.height - pad - bubble.height = 800 - 8 - 60 = 732.
test("side=right with vertical clamp", () => {
  const p = placeTooltip({
    trigger: { left: 100, top: 780, width: 40, height: 20 },
    bubble: { width: 80, height: 60 },
    viewport: { width: 1000, height: 800 },
    side: "right",
  });
  assert(p.side === "right", `side right, got ${p.side}`);
  eq(p.left, 146, "left beside trigger");
  eq(p.top, 732, "top clamps to bottom margin");
});

// 7. Tiny viewport: neither side fits → keep the flipped side, clamp the main
//    axis to pad, never returning off-screen/negative coords.
test("tiny viewport keeps flipped side and pins to pad", () => {
  const p = placeTooltip({
    trigger: { left: 20, top: 20, width: 10, height: 10 },
    bubble: { width: 60, height: 60 },
    viewport: { width: 50, height: 50 },
    side: "top",
  });
  assert(p.side === "bottom", `flipped to bottom, got ${p.side}`);
  eq(p.left, 8, "left pinned to pad");
  eq(p.top, 8, "top pinned to pad");
});

console.log(`tooltipPlace.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
