// drivers.test.ts — pure-logic tests for the Backend Drivers lane (spec §4.2).
// Inline-assert harness (no vitest in this repo); runs via `npx tsx`.
import {
  DRIVER_DEFAULT_PORT,
  driverTypeChip,
  offersSummary,
  validateDriverForm,
} from "../../components/settings/driversMeta";
import type { DriverInfo } from "../../types";

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
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

const info = (over: Partial<DriverInfo>): DriverInfo => ({
  id: "nina-abcd", type: "nina", label: "NINA", enabled: true, implicit: false,
  status: { reachable: true, error: null, detail: null, probed_at: 0 },
  offers: { devices: [], tasks: [] },
  ...over,
});

test("offersSummary lists devices then tasks", () => {
  const d = info({
    offers: {
      devices: [
        { role: "camera", name: "ASI2600MM" },
        { role: "telescope", name: "EQ6-R" },
      ],
      tasks: ["autofocus", "polar_align"],
    },
  });
  eq(offersSummary(d),
     "camera: ASI2600MM · telescope: EQ6-R · tasks: autofocus, polar_align");
});

test("offersSummary empty => 'nothing offered'", () => {
  eq(offersSummary(info({})), "nothing offered");
});

test("validateDriverForm accepts blank port (server defaults it)", () => {
  eq(validateDriverForm("nina", "astrotown.lan", ""), null);
});

test("validateDriverForm rejects bad type / blank host / bad port", () => {
  // A type with no DRIVER_DEFAULT_PORT entry. ("asiair" used to stand in here;
  // it is a real ASIAIR driver type now, so it needs a genuinely unknown one.)
  eq(validateDriverForm("no-such-driver-type", "h", "") !== null, true);
  eq(validateDriverForm("nina", "  ", "") !== null, true);
  eq(validateDriverForm("nina", "h", "0") !== null, true);
  eq(validateDriverForm("nina", "h", "99999") !== null, true);
  eq(validateDriverForm("nina", "h", "abc") !== null, true);
});

test("default ports match the server table", () => {
  eq(DRIVER_DEFAULT_PORT.nina, 1888);
  eq(DRIVER_DEFAULT_PORT.alpaca, 11111);
  eq(DRIVER_DEFAULT_PORT.phd2, 4400);
  // ASIAIR main JSON-RPC port (server config.py DRIVER_DEFAULT_PORTS).
  eq(DRIVER_DEFAULT_PORT.asiair, 4700);
});

// The ASIAIR bridge is a network driver like the others: a host is required,
// the port defaults server-side, and it must be a KNOWN type so "Add" cannot
// be submitted for a backend the server never registered.
test("validateDriverForm accepts an ASIAIR with just a host", () => {
  eq(validateDriverForm("asiair", "10.0.0.1", ""), null);
  eq(validateDriverForm("asiair", "  ", "") !== null, true);
});

// UX review #42: built-in rows printed the driver's name twice, in two
// typefaces ("Simulator  Simulator", "ASTAP  ASTAP"), because label === the
// type label for every implicit driver.
test("driverTypeChip suppresses a chip that just repeats the label", () => {
  eq(driverTypeChip("Simulator", "sim"), "");
  eq(driverTypeChip("ASTAP", "astap"), "");
  eq(driverTypeChip("AstroDeck native", "astrodeck"), "");
  eq(driverTypeChip("ASCOM (local)", "ascom-local"), "");
  // case- and whitespace-insensitive
  eq(driverTypeChip("simulator", "sim"), "");
  eq(driverTypeChip("AstroDeck  native", "astrodeck"), "");
});

test("driverTypeChip keeps a chip that adds information", () => {
  eq(driverTypeChip("Rig PC", "nina"), "NINA");
  eq(driverTypeChip("Roof box", "alpaca"), "Alpaca server");
  eq(driverTypeChip("AM5N", "zwo-am5"), "ZWO AM5 (native serial)");
  // unknown type falls back to the raw id rather than vanishing
  eq(driverTypeChip("Whatever", "brand-new"), "brand-new");
});

console.log(`drivers.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  // No @types/node in this Vite/browser project; reach process via globalThis so
  // `tsc -b` (browser lib, no Node types) still compiles this file cleanly
  // (same workaround as src/__tests__/ws.test.ts).
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
