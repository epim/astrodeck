// base.test.ts — regression for the live "Couldn't load drivers — Unexpected token
// '<', <!doctype…" bug (2026-07-19). The relay mounts the app at `/h/<home_id>/`
// and serves the SPA HTML for any unmatched deep path, so `window.location.pathname`
// can be DEEPER than the mount root. The old base = `window.location.pathname`
// verbatim then prefixed every API URL too deeply, so `/api/drivers` resolved to the
// home's SPA catch-all (200 text/html) and `res.json()` threw on the HTML. The base
// MUST pin to the `/h/<home_id>` mount prefix regardless of how deep the location is.
//
// Inline-assert harness; runs via `npx tsx src/lib/__tests__/base.test.ts` and
// compiles under `tsc -b` (mirrors caps.test.ts / ids.test.ts). base.ts reads
// `window.location.pathname` at import time, so stub a DEEP location BEFORE importing
// to exercise the exact failing production shape.

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const g = globalThis as unknown as { window?: unknown };
// The exact failing production shape: the SPA loaded at a sub-path under the mount.
if (typeof g.window === "undefined") {
  g.window = { location: { pathname: "/h/home-1/equipment", protocol: "https:", host: "astrodeck-relay.fly.dev" } };
}

const { BASE, u, deriveBase } = await import("../base");

// -- deriveBase matrix (pure) ------------------------------------------------
test("deriveBase: local root -> ''", () => {
  assert(deriveBase("/") === "", `got ${deriveBase("/")}`);
  assert(deriveBase("/index.html") === "", `got ${deriveBase("/index.html")}`);
  assert(deriveBase("/equipment") === "", `got ${deriveBase("/equipment")}`);
});

test("deriveBase: relay mount root -> /h/<home_id>", () => {
  assert(deriveBase("/h/home-1/") === "/h/home-1", `got ${deriveBase("/h/home-1/")}`);
  assert(deriveBase("/h/home-1") === "/h/home-1", `got ${deriveBase("/h/home-1")}`);
});

test("deriveBase: DEEP relay path pins to the mount prefix (the bug)", () => {
  // Pre-fix these returned the whole pathname, mis-prefixing every API URL.
  assert(deriveBase("/h/home-1/equipment") === "/h/home-1", `got ${deriveBase("/h/home-1/equipment")}`);
  assert(deriveBase("/h/home-1/equipment/deep/er") === "/h/home-1", `got ${deriveBase("/h/home-1/equipment/deep/er")}`);
  assert(deriveBase("/h/abc123/auth/google/callback") === "/h/abc123", `got ${deriveBase("/h/abc123/auth/google/callback")}`);
});

// -- module wiring against the exact failing shape ---------------------------
test("BASE pins to the mount even when the page loaded at a sub-path", () => {
  // window.location.pathname stubbed to "/h/home-1/equipment" above.
  assert(BASE === "/h/home-1", `BASE was ${BASE} (pre-fix: /h/home-1/equipment)`);
});

test("u('/api/drivers') resolves to the correct tunnelled API URL", () => {
  // Pre-fix: "/h/home-1/equipment/api/drivers" -> relay tunnels /equipment/api/
  // drivers -> home SPA catch-all -> index.html 200 -> res.json() throws '<'.
  assert(u("/api/drivers") === "/h/home-1/api/drivers", `u() was ${u("/api/drivers")}`);
  assert(u("/ws") === "/h/home-1/ws", `u('/ws') was ${u("/ws")}`);
});

// -- report ------------------------------------------------------------------
console.log(`base.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
