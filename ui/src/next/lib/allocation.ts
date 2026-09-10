// allocation.ts — quick-session filter allocation (README "3. Quick session
// setup" + "Formulas to lift" -> Filter allocation):
//
//   per = hours*3600 / checked
//   need_f = floor(per / exp_f)
//   one sub per filter per pass
//
// Mono rigs split the window evenly across the checked wheel filters; OSC rigs
// (no wheel) get a single EXPOSURE row instead (README: "OSC rigs get a single
// EXPOSURE row instead"), covered here by `allocateOsc`. Pure display math —
// the engine (not the phone) decides the real plan (ARCHITECTURE.md #10).

export interface AllocationFilter {
  name: string;
  exposureS: number;
  checked: boolean;
}

export interface FilterAllocation {
  name: string;
  count: number;
  totalS: number;
}

/** Per-filter sub counts for the checked wheel slots over `hours`. Unchecked
 *  filters come back with count 0 (kept in the output so callers can render
 *  every wheel slot's row without filtering twice). */
export function allocate(hours: number, filters: AllocationFilter[]): FilterAllocation[] {
  const checked = filters.filter((f) => f.checked).length;
  if (checked === 0 || !(hours > 0)) {
    return filters.map((f) => ({ name: f.name, count: 0, totalS: 0 }));
  }
  const per = (hours * 3600) / checked;
  return filters.map((f) => {
    if (!f.checked || !(f.exposureS > 0)) return { name: f.name, count: 0, totalS: 0 };
    const count = Math.floor(per / f.exposureS);
    return { name: f.name, count, totalS: count * f.exposureS };
  });
}

export interface OscAllocation {
  count: number;
  totalS: number;
}

/** The OSC branch: one exposure length, no wheel. */
export function allocateOsc(hours: number, exposureS: number): OscAllocation {
  if (!(hours > 0) || !(exposureS > 0)) return { count: 0, totalS: 0 };
  const count = Math.floor((hours * 3600) / exposureS);
  return { count, totalS: count * exposureS };
}

/** Wall-clock finish instant (epoch ms) for a session starting at `startMs`
 *  and running `hours` hours. */
export function finishesAt(startMs: number, hours: number): number {
  return startMs + hours * 3600 * 1000;
}
