// Self-contained unit tests for lane-1A pure helpers (no test runner required).
//
// There is no vitest/jest wired into this UI yet (build is `tsc -b && vite
// build`), so these tests use a tiny inline assert harness. They compile under
// `tsc -b` and can be executed directly with a TS-aware runner, e.g.:
//     npx tsx src/lib/__tests__/foundation.test.ts
// When a real runner lands, each `test(...)` block maps 1:1 to an `it(...)`.

import { scale, fov, ARCSEC_PER_RAD } from "../optics";
import { deriveNinaHealth } from "../health";
import { humanizeLog, humanizeSeqError } from "../humanize";
import { horizonVerdict } from "../horizon";
import { buildPreflight, preflightVerdict } from "../preflight";
import type { RigStatus, SequencePlan, SiteInfo, PreflightAlt } from "../../types";

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
function near(a: number, b: number, tol: number, msg = ""): void {
  if (Math.abs(a - b) > tol) throw new Error(`${msg} expected ~${b}, got ${a}`);
}

// ---------------------------------------------------------------- optics
test("optics: constant matches backend", () => {
  eq(ARCSEC_PER_RAD, 206.265, "ARCSEC_PER_RAD");
});

test("optics: known scale case 530mm/3.76um ~ 1.46 arcsec/px", () => {
  near(scale(530, 3.76), 1.4632, 0.01, "scale");
});

test("optics: fov is bin-independent (bin arg ignored in fov)", () => {
  const f = fov(530, 3.76, 9576, 6388);
  near(f.w, 3.89, 0.05, "fov.w");
  near(f.h, 2.6, 0.05, "fov.h");
  near(f.diag, Math.hypot(f.w, f.h), 1e-9, "fov.diag");
});

test("optics: scale 0 when focal length 0", () => {
  eq(scale(0, 3.76), 0, "scale fl=0");
});

// ---------------------------------------------------------------- health
function st(partial: Partial<RigStatus>): RigStatus {
  return { connected: {}, looping: false, ...partial } as RigStatus;
}

test("health: na when not nina", () => {
  const h = deriveNinaHealth(st({ mode: "sim" }));
  eq(h.state, "na", "non-nina");
  eq(h.active, false, "active");
});

test("health: na when nina but link inactive", () => {
  const h = deriveNinaHealth(
    st({ mode: "nina", nina_link: { active: false, last_ok_age_s: null, last_error: null, healthy: false, warming_up: false } }),
  );
  eq(h.state, "na", "inactive link");
});

test("health: warming suppresses red", () => {
  const h = deriveNinaHealth(
    st({ mode: "nina", nina_link: { active: true, last_ok_age_s: null, last_error: "boom", healthy: false, warming_up: true } }),
  );
  eq(h.state, "warming", "warming");
});

test("health: ok when healthy", () => {
  const h = deriveNinaHealth(
    st({ mode: "nina", nina_link: { active: true, last_ok_age_s: 3, last_error: null, healthy: true, warming_up: false } }),
  );
  eq(h.state, "ok", "ok");
  eq(h.ageMs, 3000, "ageMs");
});

test("health: stale when not healthy but has recent age", () => {
  const h = deriveNinaHealth(
    st({ mode: "nina", nina_link: { active: true, last_ok_age_s: 10, last_error: null, healthy: false, warming_up: false } }),
  );
  eq(h.state, "stale", "stale");
});

test("health: error when last_error and old", () => {
  const h = deriveNinaHealth(
    st({ mode: "nina", nina_link: { active: true, last_ok_age_s: 47, last_error: "HTTP 500", healthy: false, warming_up: false } }),
  );
  eq(h.state, "error", "error");
  eq(h.lastError, "HTTP 500", "lastError");
});

test("health: down when never replied (no error)", () => {
  const h = deriveNinaHealth(
    st({ mode: "nina", nina_link: { active: true, last_ok_age_s: null, last_error: null, healthy: false, warming_up: false } }),
  );
  eq(h.state, "down", "down");
});

// ---------------------------------------------------------------- humanize
test("humanize: camera capture failure", () => {
  const s = humanizeLog({ source: "capture", message: "camera not responding (timeout)" });
  assert(/camera/i.test(s), `got: ${s}`);
});

test("humanize: plate solve", () => {
  const s = humanizeLog({ source: "solve", message: "plate solve failed" });
  assert(/solve/i.test(s), `got: ${s}`);
});

test("humanize: accepts a raw string", () => {
  const s = humanizeLog("some unmapped detail");
  assert(s.length > 0, "non-empty");
});

test("humanize: seq error falls back to detail", () => {
  const s = humanizeSeqError("totally novel failure mode xyz");
  assert(s.includes("novel"), `got: ${s}`);
});

test("humanize: seq error empty → generic sentence", () => {
  const s = humanizeSeqError(undefined);
  assert(s.length > 0, "non-empty");
});

// ---------------------------------------------------------------- horizon
test("horizon: unknown on default site", () => {
  eq(horizonVerdict(40, 15, true), "unknown", "default site");
});
test("horizon: unknown on null alt", () => {
  eq(horizonVerdict(null, 15, false), "unknown", "null alt");
});
test("horizon: below when alt<0", () => {
  eq(horizonVerdict(-3, 15, false), "below", "below");
});
test("horizon: low when 0<=alt<min", () => {
  eq(horizonVerdict(8, 15, false), "low", "low");
});
test("horizon: ok when alt>=min", () => {
  eq(horizonVerdict(40, 15, false), "ok", "ok");
});

// ---------------------------------------------------------------- preflight
const SITE_REAL: SiteInfo = { latitude: 51.5, longitude: -0.13, is_default: false, horizon_min_deg: 15 };
const SITE_DEFAULT: SiteInfo = { latitude: 0, longitude: 0, is_default: true, horizon_min_deg: 15 };

function plan(overrides: Partial<SequencePlan> = {}): SequencePlan {
  return {
    name: "P", targets: [], guide: false, dither_every: 0, dither_pixels: 0,
    autofocus_every: 0, cool_to: null, cool_timeout_s: 600, apply_filter_offsets: false,
    refocus_on_temp_delta_c: 0, meridian_flip: false, recover_guiding: false,
    hfr_reject_factor: 0, park_when_done: false, warm_cooler_when_done: false, ...overrides,
  };
}
function lightTarget(name: string): SequencePlan["targets"][number] {
  return { name, ra_hours: 1, dec_deg: 1, center: true, autofocus_first: false, calibration: false, steps: [] };
}

test("preflight: every CheckItem.word is non-empty", () => {
  const items = buildPreflight(
    st({ mode: "alpaca", connected: { camera: { name: "c", kind: "camera", connected: true } } }),
    plan({ targets: [lightTarget("M31")] }),
    SITE_REAL,
    { M31: { alt: 40, az: 90, verdict: "ok", horizon_min_deg: 15, site_is_default: false } },
  );
  assert(items.length > 0, "has items");
  for (const it of items) assert(it.word.length > 0, `word empty for ${it.id}`);
});

test("preflight: horizon disabled on default site", () => {
  const items = buildPreflight(
    st({ mode: "alpaca" }),
    plan({ targets: [lightTarget("M31")] }),
    SITE_DEFAULT,
    {},
  );
  const h = items.find((i) => i.id === "horizon");
  eq(h?.status, "disabled", "horizon disabled");
});

test("preflight: below-horizon target blocks", () => {
  const alt: Record<string, PreflightAlt> = {
    M31: { alt: -5, az: 90, verdict: "below", horizon_min_deg: 15, site_is_default: false },
  };
  const items = buildPreflight(
    st({ mode: "alpaca", connected: { camera: { name: "c", kind: "camera", connected: true }, telescope: { name: "t", kind: "telescope", connected: true } }, mount: { ra_hours: 0, dec_deg: 0, ra_str: "", dec_str: "", alt: 0, az: 0, tracking: true, parked: false, slewing: false } }),
    plan({ targets: [lightTarget("M31")] }),
    SITE_REAL,
    alt,
  );
  const h = items.find((i) => i.id === "horizon");
  eq(h?.status, "blocked", "horizon blocked");
  eq(preflightVerdict(items), "blocked", "overall blocked");
});

test("preflight: missing camera blocks overall", () => {
  const items = buildPreflight(st({ mode: "alpaca" }), plan({ targets: [lightTarget("M31")] }), SITE_REAL, {});
  eq(preflightVerdict(items), "blocked", "no camera → blocked");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nfoundation.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
