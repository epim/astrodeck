// Pure-lib test for relay.ts - which origin this tab is on.
//
//   Run directly:  npx tsx src/next/lib/__tests__/relay.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHY THIS IS WORTH A FILE. Two screens ask the same question and the app is
// only honest if they get one answer: Settings > Connection says "you are
// here", and every control that issues a write the rig fences to the LAN is
// honest-disabled off the same fact (`gate.ts`'s `needsLan`). The failure mode
// is silent in both directions - a tab wrongly read as DIRECT arms a dozen
// buttons that 403, and a tab wrongly read as RELAY locks controls that would
// have worked - so the pathname rule and the `via` override each get a case.
//
// SABOTAGE CHECKS:
//   * make `reachFromPath` test `pathname === "/"` instead of calling
//     `deriveBase` -> "a deep link under the relay mount is still the relay"
//     goes red (`/h/home-1/settings` reads as direct).
//   * make `resolveHere` prefer the pathname over `remote.via` -> "the rig's
//     own answer wins over the pathname" goes red.
//   * have `noteRemoteStatus` store a missing/garbage `via` as "direct" instead
//     of null -> "an answer with no via leaves the pathname in charge" goes red.
//   * drop the listener notify from `noteRemoteStatus` -> "a subscriber hears
//     the rig's answer arrive" goes red (the control that rendered before the
//     fetch would never re-render).

// `relay.ts` imports `lib/base.ts`, which reads `window.location.pathname` at
// MODULE LOAD. Same convention as gate.test.ts: install the browser stub BEFORE
// anything imports it, then dynamic-`import()` the runtime values.
const loc = { pathname: "/", protocol: "http:", host: "rig.local:8800" };
const g = globalThis as unknown as { window?: unknown; localStorage?: Storage };
if (typeof g.window === "undefined") {
  g.window = {
    location: loc,
    addEventListener() {},
    removeEventListener() {},
  };
}

const {
  reachFromPath, resolveHere, noteRemoteStatus, reachHere, onRelay,
  subscribeRelay, resetRelayForTests,
} = await import("../relay");

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) {
  resetRelayForTests();
  loc.pathname = "/";
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

// ------------------------------------------------------------- the pathname

test("the LAN origin is DIRECT: root, loopback, a hash-routed deep link", () => {
  eq(reachFromPath("/"), "direct");
  eq(reachFromPath(""), "direct");
  eq(reachFromPath("/index.html"), "direct");
});

test("a deep link under the relay mount is still the relay", () => {
  // The relay serves the SPA for ANY unmatched path under the mount, so a
  // bookmark or a reload lands deeper than `/h/<home_id>/` - and that tab is
  // just as tunnelled as the one at the root. `deriveBase` already pins the
  // prefix; this asserts relay.ts asks IT rather than matching the whole path.
  eq(reachFromPath("/h/home-1/"), "relay");
  eq(reachFromPath("/h/home-1"), "relay");
  eq(reachFromPath("/h/home-1/equipment"), "relay");
  eq(reachFromPath("/h/some-other-home/settings/optics"), "relay");
});

// ------------------------------------------------------------ the rig's word

test("the rig's own answer wins over the pathname, in both directions", () => {
  // A relay that mounted somewhere this client cannot read, and a LAN tab
  // served from a path that merely looks like the mount. `via` is stamped on
  // the request the fence itself branches on, so it is the one that decides.
  eq(resolveHere("/", { via: "relay" }), "relay");
  eq(resolveHere("/h/home-1/", { via: "direct" }), "direct");
});

test("no answer yet leaves the pathname in charge", () => {
  eq(resolveHere("/h/home-1/", null), "relay");
  eq(resolveHere("/", undefined), "direct");
});

// -------------------------------------------------------- the shared answer

test("a relay origin locks and a LAN origin does not, with nothing noted", () => {
  loc.pathname = "/";
  eq(onRelay(), false, "a LAN tab must not lock the LAN-only writes:");
  eq(reachHere(), "direct");
  loc.pathname = "/h/home-1/";
  eq(onRelay(), true, "a tunnelled tab is exactly what the fence refuses:");
  eq(reachHere(), "relay");
});

test("a noted `via` overrides the pathname for every later reader", () => {
  loc.pathname = "/";
  eq(onRelay(), false);
  noteRemoteStatus({ via: "relay" });
  eq(onRelay(), true, "the rig said this request arrived over the tunnel:");
  noteRemoteStatus({ via: "direct" });
  eq(onRelay(), false);
});

test("an answer with no via leaves the pathname in charge", () => {
  loc.pathname = "/h/home-1/";
  // A 404 from an older engine, a proxy's 200, a body missing the field: none
  // of them is the rig saying "direct", and treating them as one would unlock
  // every fenced control on a tunnelled tab.
  noteRemoteStatus(null);
  eq(onRelay(), true);
  noteRemoteStatus({ via: "nonsense" } as unknown as { via: "direct" });
  eq(onRelay(), true);
});

test("a subscriber hears the rig's answer arrive", () => {
  let beats = 0;
  const off = subscribeRelay(() => { beats++; });
  noteRemoteStatus({ via: "relay" });
  eq(beats, 1, "a control rendered before the fetch never re-renders without this:");
  noteRemoteStatus({ via: "relay" });
  eq(beats, 1, "the same answer twice is not news:");
  noteRemoteStatus({ via: "direct" });
  eq(beats, 2);
  off();
  noteRemoteStatus({ via: "relay" });
  eq(beats, 2, "unsubscribed means unsubscribed:");
});

console.log(`relay.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export { passed, failed };
export const total = passed + failed;
