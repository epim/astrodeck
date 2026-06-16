// PreflightStrip.tsx — compact pre-run readiness strip (onboarding spec §2a).
// A `dense` Checklist rendered inline above the Run button, always visible when a
// plan has frames. Inline-first: warnings are listed in plain language; the modal
// is opened only when a `blocked` item exists.
//
// Derivation is shared via the exported usePreflight() hook so the Run button and
// the strip agree on one verdict. Items are re-derived on each `status` tick via
// narrow selectors (no broad useStore()); per-target altitude comes from the
// one-shot GET /api/sequence/preflight, refetched every 30s while mounted.

import { useEffect, useMemo, useRef, useState, type JSX } from "react";
import { api } from "../api";
import { useStore, useStatus, useSite } from "../store";
import type { CheckItem, PreflightAlt, SequencePlan, SiteInfo } from "../types";
import { buildPreflight, preflightVerdict, type PreflightActions } from "../lib/preflight";
import { Checklist } from "./Checklist";

export type PreflightVerdict = "ok" | "warn" | "blocked";

const DEFAULT_SITE: SiteInfo = {
  latitude: 0,
  longitude: 0,
  is_default: true,
  horizon_min_deg: 15,
};

const ALT_REFRESH_MS = 30000;

/** Light (non-calibration) targets that have real coordinates to check. */
function lightTargets(plan: SequencePlan) {
  return plan.targets.filter((t) => !t.calibration);
}

type AltMap = Record<string, PreflightAlt | undefined>;

// ---------------------------------------------------------------------------
// Shared altitude poller (F-preflight-poll). Once F-P0.1 wires the gate, three
// consumers call usePreflight() concurrently (the view, the strip, the modal).
// Each used to mount its own 30s interval -> N× the /api/sequence/preflight
// load. We de-dupe per (fetchKey + site signature): the FIRST subscriber for a
// key starts a single interval; the rest just attach a listener and read the
// cached map. The interval stops when the last subscriber unmounts.
// ---------------------------------------------------------------------------
type PollEntry = {
  refs: number;
  intervalId: number | null;
  inflight: boolean;
  data: AltMap;
  listeners: Set<(m: AltMap) => void>;
  lights: { name: string; ra_hours: number; dec_deg: number }[];
};
const altPolls = new Map<string, PollEntry>();

async function runFetch(key: string, entry: PollEntry): Promise<void> {
  if (entry.inflight) return; // collapse overlapping ticks
  entry.inflight = true;
  try {
    const entries = await Promise.all(
      entry.lights.map(async (t) => {
        try {
          const pf = await api.get<PreflightAlt>(
            `/api/sequence/preflight?ra_hours=${t.ra_hours}&dec_deg=${t.dec_deg}`,
          );
          return [t.name, pf] as const;
        } catch {
          return [t.name, undefined] as const;
        }
      }),
    );
    if (!altPolls.has(key)) return; // all subscribers left mid-flight
    entry.data = Object.fromEntries(entries);
    for (const fn of entry.listeners) fn(entry.data);
  } finally {
    entry.inflight = false;
  }
}

function subscribeAlt(
  key: string,
  lights: { name: string; ra_hours: number; dec_deg: number }[],
  onChange: (m: AltMap) => void,
): () => void {
  let entry = altPolls.get(key);
  if (!entry) {
    entry = { refs: 0, intervalId: null, inflight: false, data: {}, listeners: new Set(), lights };
    altPolls.set(key, entry);
  }
  entry.lights = lights; // freshest coords for this key
  entry.refs++;
  entry.listeners.add(onChange);
  onChange(entry.data); // hand back any cached map immediately
  if (entry.intervalId == null) {
    void runFetch(key, entry);
    entry.intervalId = window.setInterval(() => void runFetch(key, altPolls.get(key)!), ALT_REFRESH_MS);
  }
  return () => {
    const e = altPolls.get(key);
    if (!e) return;
    e.listeners.delete(onChange);
    e.refs--;
    if (e.refs <= 0) {
      if (e.intervalId != null) window.clearInterval(e.intervalId);
      altPolls.delete(key);
    }
  };
}

/**
 * Live per-target altitude, polled once per distinct (targets + site) signature
 * no matter how many usePreflight() consumers mount. Returns {} on a default
 * site or an empty plan (the horizon row is disabled/skipped there).
 */
function useSharedAltById(plan: SequencePlan, site: SiteInfo): AltMap {
  const [altById, setAltById] = useState<AltMap>({});

  const fetchKey = useMemo(
    () => lightTargets(plan).map((t) => `${t.name}|${t.ra_hours}|${t.dec_deg}`).join(","),
    [plan],
  );

  useEffect(() => {
    const lights = lightTargets(plan).map((t) => ({ name: t.name, ra_hours: t.ra_hours, dec_deg: t.dec_deg }));
    if (site.is_default || lights.length === 0) {
      setAltById({});
      return; // server returns unknown on default site; the row is disabled/skipped
    }
    const siteSig = `${site.latitude}|${site.longitude}|${site.horizon_min_deg}`;
    const key = `${siteSig}::${fetchKey}`;
    return subscribeAlt(key, lights, setAltById);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchKey, site.is_default, site.latitude, site.longitude, site.horizon_min_deg]);

  return altById;
}

/**
 * Shared readiness derivation. Returns the CheckItem[] + the overall verdict.
 * Fetches live per-target altitude (skips the call entirely on a default site —
 * the server returns `unknown` there and the horizon row is `disabled`).
 */
export function usePreflight(plan: SequencePlan, actions: PreflightActions = {}): {
  items: CheckItem[];
  verdict: PreflightVerdict;
  altById: Record<string, PreflightAlt | undefined>;
} {
  const status = useStatus();
  const site = useSite() ?? DEFAULT_SITE;
  // Altitude is polled through a shared, de-duped poller so the view + strip +
  // modal (all calling usePreflight concurrently) drive a single fetch loop.
  const altById = useSharedAltById(plan, site);

  // Keep actions stable-ish without forcing callers to memoize: stash in a ref.
  const actionsRef = useRef(actions);
  actionsRef.current = actions;

  const items = useMemo(
    () => buildPreflight(status, plan, site, altById, actionsRef.current),
    [status, plan, site, altById],
  );
  const verdict = useMemo(() => preflightVerdict(items), [items]);

  return { items, verdict, altById };
}

/** Plain-language one-liners for inline warnings above the Run button (resolves B8). */
function warnLines(items: CheckItem[]): string[] {
  return items
    .filter((i) => i.status === "warn" || i.status === "disabled")
    .map((i) => {
      if (i.id === "horizon" && i.status === "disabled") return "Altitude unverified — set your location in Settings.";
      if (i.id === "guiding") return "Will run unguided.";
      if (i.detail?.value) return `${i.label}: ${i.detail.value}`;
      return `${i.label} needs attention.`;
    });
}

/**
 * The inline strip. When `blocked`, it renders a danger note with a Review action
 * (the integrator wires `onReview` to open <PreflightModal/>); otherwise it lists
 * any warnings in plain language. The Run button itself is owned by the view; use
 * `usePreflight()` (above) to color it from the same verdict.
 */
export function PreflightStrip({
  plan,
  actions,
  onReview,
  resume = false,
}: {
  plan: SequencePlan;
  actions?: PreflightActions;
  onReview?: () => void;
  resume?: boolean;
}): JSX.Element | null {
  const setView = useStore((s) => s.setView);
  const { items, verdict } = usePreflight(plan, actions);

  const totalFrames = plan.targets.reduce((a, t) => a + t.steps.reduce((b, s) => b + s.count, 0), 0);
  if (totalFrames === 0) return null; // only shown once a plan has frames

  const blockedCount = items.filter((i) => i.status === "blocked").length;
  // Resume warns, never blocks (resolves H24) — surface blockers as warnings.
  const effectiveVerdict: PreflightVerdict = resume && verdict === "blocked" ? "warn" : verdict;
  const warnings = warnLines(items);

  return (
    <div className="flex flex-col gap-2">
      <div className="panel p-2">
        <Checklist items={items} dense />
      </div>

      {effectiveVerdict === "blocked" && (
        <div className="flex items-center gap-2 border border-bad/50 bg-bad/5 px-3 py-2">
          <span aria-hidden className="text-bad text-[16px] leading-none">✕</span>
          <span className="text-sm text-ink flex-1">
            {blockedCount === 1 ? "1 thing needs fixing before you can run." : `${blockedCount} things need fixing before you can run.`}
          </span>
          {onReview && (
            <button type="button" className="btn btn-danger !py-1 !px-2.5 min-h-11 sm:min-h-9" onClick={onReview}>
              Review
            </button>
          )}
        </div>
      )}

      {effectiveVerdict !== "blocked" && warnings.length > 0 && (
        <ul className="flex flex-col gap-1 px-1">
          {warnings.map((w, i) => (
            <li key={i} className="flex items-start gap-2 text-[12px] text-dim">
              <span aria-hidden className="text-warn leading-none mt-px">△</span>
              <span className="leading-snug">
                {w}
                {w.startsWith("Altitude unverified") && (
                  <button
                    type="button"
                    className="ml-2 underline text-accent"
                    onClick={() => setView("settings")}
                  >
                    Set location
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
