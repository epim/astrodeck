import { integratedLabel, formatLiveStack } from "../liveStack";
let passed = 0, failed = 0; const failures: string[] = [];
function test(name: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`✗ ${name}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

test("integratedLabel: seconds under a minute", () => eq(integratedLabel(48), "48 s"));
test("integratedLabel: minutes", () => eq(integratedLabel(1440), "24 min"));
test("integratedLabel: hours drops .0", () => eq(integratedLabel(3600), "1 h"));
test("integratedLabel: fractional hours", () => eq(integratedLabel(5400), "1.5 h"));
test("integratedLabel: negative clamps", () => eq(integratedLabel(-9), "0 s"));
test("formatLiveStack: singular frame", () => eq(formatLiveStack({ frames: 1, integrated_s: 120, rejected: 0, accepted: true }).frames, "1 frame"));
test("formatLiveStack: headline", () => eq(formatLiveStack({ frames: 12, integrated_s: 1440, rejected: 0, accepted: true }).headline, "12 frames · 24 min integrated"));
test("formatLiveStack: rejected suffix", () => eq(formatLiveStack({ frames: 12, integrated_s: 1440, rejected: 3, accepted: true }).rejected, "3 skipped"));
test("formatLiveStack: no rejected -> empty", () => eq(formatLiveStack({ frames: 5, integrated_s: 600, rejected: 0, accepted: true }).rejected, ""));

const total = passed + failed;
console.log(`\nliveStack.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));
export const result = { passed, failed, total };
