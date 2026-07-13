// tooltipMachine.test.ts — Run with: npx tsx src/lib/__tests__/tooltipMachine.test.ts
import { tooltipNext, TOOLTIP_IDLE, type TooltipState } from "../tooltipMachine";

let passed = 0; let failed = 0; const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

function run(events: string[], from: TooltipState = TOOLTIP_IDLE): TooltipState {
  return events.reduce((s, ev) => tooltipNext(s, ev as never), from);
}

test("tap toggles open exactly once (no flash)", () => {
  const s1 = run(["tap"]);
  assert(s1.open, "first tap opens");
  const s2 = run(["tap"], s1);
  assert(!s2.open, "second tap closes");
});

test("mouse enter waits out the open delay", () => {
  const s = run(["enter-mouse"]);
  assert(!s.open && s.pendingOpen, "not open until the timer fires");
  assert(run(["open-timer"], s).open, "open after the delay");
});

test("graze: enter then leave before the delay never opens", () => {
  const s = run(["enter-mouse", "leave-mouse"]);
  assert(!s.open && !s.pendingOpen && !s.pendingClose, "back to idle");
  assert(!tooltipNext(s, "open-timer").open, "stale open-timer is a no-op");
});

test("drift: leave then re-enter within the grace stays open (the flicker fix)", () => {
  const open = run(["enter-mouse", "open-timer"]);
  const grace = run(["leave-mouse"], open);
  assert(grace.open && grace.pendingClose, "still open during grace");
  const back = run(["enter-mouse"], grace);
  assert(back.open && !back.pendingClose, "re-enter cancels the close");
  assert(tooltipNext(back, "close-timer").open, "stale close-timer is a no-op");
});

test("leave without re-enter closes after the grace", () => {
  const s = run(["enter-mouse", "open-timer", "leave-mouse", "close-timer"]);
  assert(!s.open, "closed after grace expiry");
});

test("escape / outside / blur close from any open state", () => {
  for (const ev of ["escape", "outside", "blur"] as const) {
    assert(!run(["tap", ev]).open, `${ev} closes`);
  }
});

test("focus opens immediately (keyboard)", () => {
  assert(run(["focus"]).open, "focus opens");
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ntooltipMachine.test: ${passed}/${total} passed`);
if (failures.length) { console.error(failures.join("\n")); }
export const result = { passed, failed, total };
