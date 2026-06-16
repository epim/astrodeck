// preflight-gate.test.ts — FIX-D / F-P0.1 safety regression.
//
// The ship-blocker fix wires the pre-flight gate into Run Sequence. The Run
// button is owned by SequenceView; it computes `const { verdict } = usePreflight(plan)`
// and uses that single verdict to (a) DISABLE Run when blocked and (b) route the
// click through <PreflightModal/> (Review) instead of POSTing /api/sequence/start.
//
// usePreflight is a thin wrapper over the pure buildPreflight() + preflightVerdict()
// (lib/preflight.ts) plus a shared altitude poll; the React/polling layer is not
// unit-testable without a DOM runner, but the SAFETY-CRITICAL contract the view
// hangs off of IS pure: "a blocked CheckItem ⇒ verdict==='blocked'", and the view
// derives `disabled` and the start-vs-redirect decision solely from that verdict.
//
// These tests pin that contract so the gate can never silently fail-OPEN (let a
// blocked plan start). Same inline-assert harness as foundation.test.ts:
//     npx tsx src/lib/__tests__/preflight-gate.test.ts

import { buildPreflight, preflightVerdict } from "../preflight";
import type { CheckItem, RigStatus, SequencePlan, SiteInfo, PreflightAlt, Target } from "../../types";

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
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

// ---------------------------------------------------------------- fixtures
const SITE_REAL: SiteInfo = { latitude: 51.5, longitude: -0.13, is_default: false, horizon_min_deg: 15 };

function st(partial: Partial<RigStatus>): RigStatus {
  return { connected: {}, looping: false, ...partial } as RigStatus;
}
function plan(overrides: Partial<SequencePlan> = {}): SequencePlan {
  return {
    name: "P", targets: [], guide: false, dither_every: 0, dither_pixels: 0,
    autofocus_every: 0, cool_to: null, cool_timeout_s: 600, apply_filter_offsets: false,
    refocus_on_temp_delta_c: 0, meridian_flip: false, recover_guiding: false,
    hfr_reject_factor: 0, park_when_done: false, warm_cooler_when_done: false, ...overrides,
  };
}
function lightTarget(name: string, steps: Target["steps"] = []): Target {
  return { name, ra_hours: 1, dec_deg: 1, center: true, autofocus_first: false, calibration: false, steps };
}
const camOk = { connected: { camera: { name: "c", kind: "camera", connected: true } } } as Partial<RigStatus>;

// The view's exact gate predicates, mirrored here so the test fails if the
// contract the view relies on ever drifts:
//   disabled  := running || totalFrames===0 || verdict==='blocked'
//   onClick   := opens PreflightModal (NEVER posts start directly)
// We assert on `verdict` because that is the single value the view consumes.
function runDisabledByVerdict(items: CheckItem[]): boolean {
  return preflightVerdict(items) === "blocked";
}

// ====================================================================
// THE SHIP-BLOCKER CONTRACT: a blocked CheckItem disables/redirects Run.
// ====================================================================

test("gate: disconnected camera (blocked) ⇒ verdict blocked ⇒ Run disabled", () => {
  const items = buildPreflight(
    st({ mode: "alpaca" }), // no camera connected -> camera row blocked
    plan({ targets: [lightTarget("M31")] }),
    SITE_REAL,
    {},
  );
  assert(items.some((i) => i.id === "camera" && i.status === "blocked"), "camera blocked present");
  eq(preflightVerdict(items), "blocked", "verdict");
  assert(runDisabledByVerdict(items), "Run is disabled when a CheckItem is blocked");
});

test("gate: below-horizon target (blocked) ⇒ verdict blocked ⇒ Run disabled", () => {
  const alt: Record<string, PreflightAlt> = {
    M31: { alt: -4, az: 90, verdict: "below", horizon_min_deg: 15, site_is_default: false },
  };
  const items = buildPreflight(
    st({ ...camOk, mode: "alpaca", connected: { ...camOk.connected, telescope: { name: "t", kind: "telescope", connected: true } } }),
    plan({ targets: [lightTarget("M31")] }),
    SITE_REAL,
    alt,
  );
  assert(items.some((i) => i.id === "horizon" && i.status === "blocked"), "horizon blocked present");
  assert(runDisabledByVerdict(items), "below-horizon plan cannot start");
});

test("gate: disk-critical (blocked) ⇒ Run disabled — unattended run cannot start with no disk", () => {
  const items = buildPreflight(
    st({ ...camOk, mode: "alpaca", disk: { free_gb: 2, low: true, critical: true } } as Partial<RigStatus>),
    plan({ targets: [lightTarget("M31")] }),
    SITE_REAL,
    {},
  );
  assert(items.some((i) => i.id === "disk" && i.status === "blocked"), "disk blocked present");
  assert(runDisabledByVerdict(items), "disk-critical plan cannot start");
});

test("gate: can't-cool but plan wants cooling (blocked) ⇒ Run disabled", () => {
  const items = buildPreflight(
    st({ ...camOk, mode: "alpaca", camera: { can_cool: false, temperature: null } } as Partial<RigStatus>),
    plan({ targets: [lightTarget("M31")], cool_to: -10 }),
    SITE_REAL,
    {},
  );
  assert(items.some((i) => i.id === "cooling" && i.status === "blocked"), "cooling blocked present");
  assert(runDisabledByVerdict(items), "can't-cool plan cannot start");
});

// --- fail-OPEN guard: the gate must NOT block a ready plan, only a blocked one.
test("gate: fully-ready plan ⇒ verdict NOT blocked ⇒ Run enabled (no false lock-out)", () => {
  const items = buildPreflight(
    st({
      mode: "alpaca",
      connected: {
        camera: { name: "c", kind: "camera", connected: true },
        telescope: { name: "t", kind: "telescope", connected: true },
      },
      mount: { parked: false } as RigStatus["mount"],
      disk: { free_gb: 500, low: false, critical: false },
    } as Partial<RigStatus>),
    plan({ targets: [lightTarget("M31")] }),
    SITE_REAL,
    { M31: { alt: 50, az: 90, verdict: "ok", horizon_min_deg: 15, site_is_default: false } },
  );
  eq(preflightVerdict(items), "ok", "ready verdict");
  assert(!runDisabledByVerdict(items), "ready plan is NOT blocked");
});

// --- a warn (e.g. accepted low-horizon) must remain runnable (force path), the
//     modal proceeds past warnings with a single tap. Only `blocked` locks Run.
test("gate: low-horizon (warn, not blocked) ⇒ Run NOT disabled — proceeds via force", () => {
  const items = buildPreflight(
    st({
      mode: "alpaca",
      connected: {
        camera: { name: "c", kind: "camera", connected: true },
        telescope: { name: "t", kind: "telescope", connected: true },
      },
      mount: { parked: false } as RigStatus["mount"],
    } as Partial<RigStatus>),
    plan({ targets: [lightTarget("M31")] }),
    SITE_REAL,
    { M31: { alt: 8, az: 90, verdict: "low", horizon_min_deg: 15, site_is_default: false } },
  );
  const horizon = items.find((i) => i.id === "horizon");
  eq(horizon?.status, "warn", "low horizon is warn");
  eq(preflightVerdict(items), "warn", "verdict warn");
  assert(!runDisabledByVerdict(items), "low-horizon warn does not disable Run");
  // The view derives force from exactly this predicate: a horizon WARN row.
  const forceLowHorizon = items.some((i) => i.id === "horizon" && i.status === "warn");
  assert(forceLowHorizon, "force=true threaded for accepted low-horizon run");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\npreflight-gate.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
