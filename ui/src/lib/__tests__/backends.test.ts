// backends.test.ts — pure-logic tests for the pluggable-backend picker lane
// (W1.C / W1.6). No DOM, no store: just the tri-state link mapping and the
// RigSpec ConnSpec builder. Matches the inline-assert harness used across the UI
// (no vitest wired yet); compiles under `tsc -b` and runs via `npx tsx`.

import { linkTriState } from "../../components/settings/BackendLinkGrid";
import {
  buildConnSpec,
  addrKind,
  needsAddressing,
  defaultPortFor,
} from "../../components/settings/backendMeta";
import type { BackendLink } from "../../types";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

const link = (p: Partial<BackendLink>): BackendLink => ({
  role: "camera",
  ok: false,
  error: null,
  attempted: false,
  connected: false,
  ...p,
});

// ------------------------------------------------------------ tri-state map
test("tri-state: not attempted → skipped (not a red LED)", () => {
  eq(linkTriState(link({ attempted: false, ok: false, connected: false })), "skipped");
});

test("tri-state: attempted + connected → connected", () => {
  eq(linkTriState(link({ attempted: true, ok: true, connected: true })), "connected");
});

test("tri-state: attempted + ok but link down → degraded", () => {
  // ok at connect time but the live link dropped (connected=false): degraded, not failed.
  eq(linkTriState(link({ attempted: true, ok: true, connected: false })), "degraded");
});

test("tri-state: attempted + failed → failed", () => {
  eq(linkTriState(link({ attempted: true, ok: false, connected: false, error: "no route" })), "failed");
});

// --------------------------------------------------------- backend metadata
test("meta: sim needs no addressing", () => {
  eq(addrKind("sim"), "none");
  eq(needsAddressing("sim"), false);
});

test("meta: native is alpaca-addressed with default port 11111", () => {
  eq(addrKind("native"), "alpaca");
  eq(defaultPortFor("native"), 11111);
});

test("meta: nina host+port, default 1888", () => {
  eq(addrKind("nina"), "nina");
  eq(defaultPortFor("nina"), 1888);
});

test("meta: phd2 is host-only, default 4400", () => {
  eq(addrKind("phd2"), "host");
  eq(defaultPortFor("phd2"), 4400);
});

test("meta: asiair is host-only (its ports are fixed), default 4700", () => {
  eq(addrKind("asiair"), "host");
  eq(defaultPortFor("asiair"), 4700);
  // no per-role dev_type/dev_num: one box serves every role it fills
  const cs = buildConnSpec("camera", "asiair", { host: "asiair.example", dev_type: "camera", dev_num: 3 });
  eq(cs.host, "asiair.example");
  assert(cs.dev_type === undefined, "asiair takes no dev_type");
  assert(cs.dev_num === undefined, "asiair takes no dev_num");
});

test("meta: unknown backend falls back to generic alpaca", () => {
  eq(addrKind("some-future-backend"), "alpaca");
});

// ------------------------------------------------------------ ConnSpec build
test("build: sim role drops all addressing", () => {
  const cs = buildConnSpec("camera", "sim", { host: "1.2.3.4", port: "11111" });
  eq(cs.backend, "sim");
  eq(cs.role, "camera");
  assert(cs.host === undefined, "sim host dropped");
  assert(cs.port === undefined, "sim port dropped");
});

test("build: native keeps host/port/dev_type/dev_num", () => {
  const cs = buildConnSpec("focuser", "native", {
    host: "192.168.1.50",
    port: "11111",
    dev_type: "focuser",
    dev_num: "0",
  });
  eq(cs.host, "192.168.1.50");
  eq(cs.port, 11111);
  eq(cs.dev_type, "focuser");
  eq(cs.dev_num, 0);
});

test("build: native dev_num 0 is preserved (not dropped as falsy)", () => {
  const cs = buildConnSpec("camera", "native", { dev_num: "0" });
  eq(cs.dev_num, 0);
});

test("build: nina keeps host+port, no device fields", () => {
  const cs = buildConnSpec("camera", "nina", {
    host: "127.0.0.1",
    port: "1888",
    dev_type: "camera",
    dev_num: "0",
  });
  eq(cs.host, "127.0.0.1");
  eq(cs.port, 1888);
  assert(cs.dev_type === undefined, "nina ignores dev_type");
  assert(cs.dev_num === undefined, "nina ignores dev_num");
});

test("build: empty host string is dropped (no blank host posted)", () => {
  const cs = buildConnSpec("camera", "native", { host: "   ", port: "" });
  assert(cs.host === undefined, "blank host dropped");
  assert(cs.port === undefined, "empty port dropped");
});

test("build: managed-PHD2 extra is attached only when present", () => {
  const without = buildConnSpec("guider", "phd2", { host: "127.0.0.1" });
  assert(without.extra === undefined, "no extra when none given");
  const withManaged = buildConnSpec("guider", "phd2", { host: "127.0.0.1" }, { managed: true });
  eq((withManaged.extra as { managed: boolean }).managed, true);
});

test("build: invalid/zero port is dropped", () => {
  eq(buildConnSpec("camera", "native", { port: "0" }).port, undefined);
  eq(buildConnSpec("camera", "native", { port: "abc" }).port, undefined);
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nbackends.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
