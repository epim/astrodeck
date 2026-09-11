// Health-strip (Monitor hero polish, implementation brief §5) pure-logic
// regression — the tier-1/2 folding of safety/disk/backend_links/meridian/
// nina_link/status.providers/end_reason into the single "is my night OK?"
// verdict. Same inline-assert / `tsx` style as the other suites (no jsdom, no
// runtime timers): deriveHealthIssues is the pure function the Monitor view
// binds to, so a regression there fails loudly here.
//
// Run directly:  npx tsx src/components/__tests__/healthStrip.test.ts
// Also type-checked by `tsc -b` in the build.
//
// Imports from lib/health.ts DIRECTLY (not "../monitor", which re-exports the
// same symbols but eagerly touches `window` via lib/base's `u()` — a plain
// tsx/node run has no DOM, so importing the component module would throw).

import { deriveHealthIssues, type HealthIssue } from "../../lib/health";

function eq<T>(a: T, b: T, msg: string): void {
  const same = Array.isArray(a) && Array.isArray(b)
    ? JSON.stringify(a) === JSON.stringify(b)
    : a === b;
  if (!same) throw new Error(`${msg}: expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}
function tiers(issues: HealthIssue[]): number[] {
  return issues.map((i) => i.tier);
}

// --- all-clear: no issues at all (tier 0 — the empty array) -----------------
eq(
  deriveHealthIssues({ safety: null, wsConnected: true }).length,
  0,
  "no signals => no issues (ambient tier-0)",
);

// --- tier 2: link down --------------------------------------------------
eq(
  tiers(deriveHealthIssues({ safety: null, wsConnected: false })),
  [2],
  "WS down is Act (tier 2)",
);

// --- tier 2: stale safety reading (fail-closed = unsafe) --------------------
eq(
  tiers(
    deriveHealthIssues({
      safety: { connected: true, streak: 0, reading: { is_safe: true, reason: "", source: "sim", stale: true, ts: 0 } },
      wsConnected: true,
    }),
  ),
  [2],
  "stale safety reading is Act (tier 2), even if is_safe was last true",
);

// --- tier 2: unsafe reading --------------------------------------------------
{
  const issues = deriveHealthIssues({
    safety: { connected: true, streak: 2, reading: { is_safe: false, reason: "clouds", source: "cloud sensor", stale: false, ts: 0 } },
    wsConnected: true,
  });
  eq(tiers(issues), [2], "unsafe reading is Act (tier 2)");
  eq(issues[0].text.includes("clouds"), true, "unsafe reason is surfaced verbatim");
}

// --- tier 2: disk critical vs tier 1: disk low -------------------------------
eq(
  tiers(deriveHealthIssues({ safety: null, disk: { free_gb: 0.5, low: true, critical: true }, wsConnected: true })),
  [2],
  "disk critical is Act (tier 2)",
);
eq(
  tiers(deriveHealthIssues({ safety: null, disk: { free_gb: 8, low: true, critical: false }, wsConnected: true })),
  [1],
  "disk low (not critical) is Notice (tier 1)",
);
eq(
  deriveHealthIssues({ safety: null, disk: { free_gb: 500, low: false, critical: false }, wsConnected: true }).length,
  0,
  "healthy disk => no issue",
);

// --- tier 2: sequence aborted/error carries the end_reason -------------------
{
  const issues = deriveHealthIssues({ safety: null, wsConnected: true, seqState: "aborted", endReason: "unsafe" });
  eq(tiers(issues), [2], "aborted sequence is Act (tier 2)");
  eq(issues[0].text.includes("unsafe"), true, "end_reason is folded into the aborted message");
}

// --- tier 2: a dropped backend role -----------------------------------------
eq(
  tiers(
    deriveHealthIssues({
      safety: null,
      wsConnected: true,
      backendLinks: [{ role: "focuser", ok: false, error: "timeout", attempted: true, connected: false }],
    }),
  ),
  [2],
  "a role that was attempted but not ok (dropped mid-run) is Act (tier 2)",
);
eq(
  deriveHealthIssues({
    safety: null,
    wsConnected: true,
    backendLinks: [{ role: "focuser", ok: false, error: null, attempted: false, connected: false }],
  }).length,
  0,
  "a role never attempted (not filled) is NOT an issue",
);

// --- meridian: flip_disabled splits Act (due now) vs Notice (not yet) --------
eq(
  tiers(
    deriveHealthIssues({
      safety: null,
      wsConnected: true,
      meridian: { status: "flip_disabled", hours_to_flip: 0, flip_enabled: false, pier_side: "east" },
    }),
  ),
  [2],
  "flip_disabled AND due now (hours_to_flip<=0) is Act (tier 2) — pier risk right now",
);
eq(
  tiers(
    deriveHealthIssues({
      safety: null,
      wsConnected: true,
      meridian: { status: "flip_disabled", hours_to_flip: 2, flip_enabled: false, pier_side: "east" },
    }),
  ),
  [1],
  "flip_disabled but still hours away is Notice (tier 1)",
);
eq(
  deriveHealthIssues({
    safety: null,
    wsConnected: true,
    meridian: { status: "counting", hours_to_flip: 2, flip_enabled: true, pier_side: "east" },
  }).length,
  0,
  "a normal counting flip is not an issue",
);

// --- meridian, THIRD state: the countdown is null, not zero and not absent ---
// A principal without view.site_derived (every viewer, every syncer) gets
// `hours_to_flip: null` from the server while `status: "flip_disabled"` rides
// through untouched (server/astrodeck/api/redact.py:98-108). The old
// `(hours_to_flip ?? 1)` read that null as "an hour away", so the tier-2 rung
// was unreachable for that role forever and the strip printed "near meridian" -
// a claim about a distance the role was never told. Three states, three
// branches. Asserting the TIER explicitly is what makes this test fail if the
// `?? 1` default is ever restored.
{
  const issues = deriveHealthIssues({
    safety: null,
    wsConnected: true,
    meridian: { status: "flip_disabled", hours_to_flip: null, flip_enabled: false, pier_side: "east" },
  });
  eq(issues.length, 1, "a redacted countdown on a disabled flip raises exactly one issue");
  eq(issues[0].tier, 2, "flip_disabled with an UNKNOWN countdown is Act (tier 2), not a Notice");
  eq(
    issues[0].text.includes("near meridian"),
    false,
    "a role that cannot see the countdown is never told how near the meridian is",
  );
  eq(
    issues[0].text.includes("hidden for your role"),
    true,
    "the unknown-countdown text names WHY there is no number, not just that the flip is off",
  );
  eq(issues[0].icon, "alert", "the unknown-countdown issue keeps the meridian icon the ladder uses");
}

// --- meridian: a principal WITH the countdown is unchanged in both directions -
{
  const due = deriveHealthIssues({
    safety: null,
    wsConnected: true,
    meridian: { status: "flip_disabled", hours_to_flip: -0.2, flip_enabled: false, pier_side: "west" },
  });
  eq(tiers(due), [2], "a real negative countdown on a disabled flip is still Act (tier 2)");
  eq(due[0].text.includes("due but disabled"), true, "the past-meridian sentence is unchanged");
  eq(due[0].text.includes("hidden for your role"), false, "a holder is not told the countdown is hidden");

  const ahead = deriveHealthIssues({
    safety: null,
    wsConnected: true,
    meridian: { status: "flip_disabled", hours_to_flip: 3, flip_enabled: false, pier_side: "east" },
  });
  eq(tiers(ahead), [1], "a real positive countdown on a disabled flip is still Notice (tier 1)");
  eq(ahead[0].text.includes("near meridian"), true, "the hours-away sentence is unchanged");
}

// --- meridian: an idle rig feeds no meridian block at all --------------------
// Both roots pass `runActive ? status.meridian : null`, so a rig with no plan
// loaded (which reports `flip_disabled` because there is no plan to enable the
// flip on) raises nothing - including the new tier-2 unknown rung, which would
// otherwise be a sticky red banner on an idle rig forever.
eq(
  deriveHealthIssues({ safety: null, wsConnected: true, meridian: null }).length,
  0,
  "no meridian block (idle rig, or a run not in flight) raises no meridian issue",
);

// --- ordering holds with the meridian notice pushed from the tier-2 section --
// The three-way meridian decision lives in the tier-2 section, so its tier-1
// outcome is pushed before the other notices. The ladder's invariant is that
// every Act precedes every Notice, and that still holds.
{
  const issues = deriveHealthIssues({
    safety: { connected: true, streak: 0, reading: { is_safe: false, reason: "wind", source: "weather", stale: false, ts: 0 } },
    disk: { free_gb: 8, low: true, critical: false },
    wsConnected: true,
    meridian: { status: "flip_disabled", hours_to_flip: 3, flip_enabled: false, pier_side: "east" },
  });
  eq(tiers(issues), [2, 1, 1], "Act still precedes every Notice with a meridian notice in the mix");
}

// --- nina_link unhealthy (but not warming up) => Notice ----------------------
eq(
  tiers(
    deriveHealthIssues({
      safety: null,
      wsConnected: true,
      ninaLink: { active: true, healthy: false, warming_up: false },
    }),
  ),
  [1],
  "NINA link unhealthy (post-warmup) is Notice (tier 1)",
);
eq(
  deriveHealthIssues({
    safety: null,
    wsConnected: true,
    ninaLink: { active: true, healthy: false, warming_up: true },
  }).length,
  0,
  "NINA link still warming up is NOT an issue yet",
);

// --- status.providers unavailable => Notice ----------------------------------
eq(
  tiers(
    deriveHealthIssues({
      safety: null,
      wsConnected: true,
      providers: { autofocus: { kind: "unavailable", label: "none", reason: "no focuser connected" } },
    }),
  ),
  [1],
  "an unavailable capability provider is Notice (tier 1)",
);

// --- status.providers solve unavailable => Notice ----------------------------
{
  const issues = deriveHealthIssues({
    safety: null,
    wsConnected: true,
    providers: {
      solve: { kind: "unavailable", label: "none", reason: "no ASTAP and a real mount is connected" },
    },
  });
  eq(tiers(issues), [1], "an unavailable solve provider is Notice (tier 1)");
  eq(
    issues[0].text.includes("Plate solving unavailable"),
    true,
    "solve unavailable issue text names the capability",
  );
}

// --- ranking: an Act issue and a Notice issue both surface, Act first -------
{
  const issues = deriveHealthIssues({
    safety: { connected: true, streak: 0, reading: { is_safe: false, reason: "wind", source: "weather", stale: false, ts: 0 } },
    disk: { free_gb: 8, low: true, critical: false },
    wsConnected: true,
  });
  eq(tiers(issues), [2, 1], "Act issues are pushed before Notice issues");
}

// --- tier 1: telemetry stale (UX-40) — socket up but values frozen ----------
{
  const issues = deriveHealthIssues({ safety: null, wsConnected: true, telemetryStale: true });
  eq(tiers(issues), [1], "telemetry stale (socket up) is Notice (tier 1)");
  eq(issues[0].text.includes("stale"), true, "telemetry-stale issue text says stale");
}
eq(
  tiers(deriveHealthIssues({ safety: null, wsConnected: false, telemetryStale: true })),
  [2],
  "link down suppresses the telemetry-stale notice — only Link-down surfaces (no double signal)",
);

console.log("healthStrip.test.ts: all assertions passed");
