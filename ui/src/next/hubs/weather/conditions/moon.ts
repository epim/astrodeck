// moon.ts - the MOON tile's number, its sentence, and the one target both it
// and the dome are allowed to be about.
//
// THE MOON COMES WITH A TARGET. `GET /api/visibility` is the only route that
// carries moon illumination, altitude and rise/set (`catalog/visibility.py:664`,
// `MoonInfo` at `types.ts:1679-1687`), and it REQUIRES ra/dec. There is no
// target-free moon endpoint. So the tile is honest-absent with a reason when
// nothing has been picked, rather than being fed a placeholder coordinate: the
// separation that came back from a made-up target would be a real number about
// nothing, and "61 deg from M31" is the one clause here a user acts on.
//
// A SEPARATION IS ONLY EVER FROM A TARGET THE USER PICKED (plan A.1.3), which
// is why `contextTarget` resolves in a fixed order and returns null rather than
// guessing.

import { api } from "../../../../api";
import type { MoonInfo, Target, VisibilityNight } from "../../../../types";

export interface ContextTarget {
  name: string;
  ra_hours: number;
  dec_deg: number;
  /** Where the name came from, for the tile's own caption. */
  source: "running" | "route";
}

/** The target this hub's moon tile and dome marker are about.
 *
 *  1. the RUNNING session's target, resolved against the loaded plan for its
 *     coordinates (`useSeq().target` is a NAME, not a position);
 *  2. the target named in the hash (`?target=` from the Sky hub, `?t=` in the
 *     older shorthand), resolved the same way;
 *  3. nothing.
 *
 *  A plan merely open in the editor is deliberately NOT a fallback: the dome
 *  panel applies the same rule to its own dots ("never a plan merely open in
 *  the editor - that isn't 'on the sky'"), and two surfaces on one screen
 *  disagreeing about which object tonight is about is worse than one of them
 *  saying nothing. */
export function contextTarget(
  running: string | null | undefined,
  params: Record<string, string>,
  planTargets: Target[] | null | undefined,
): ContextTarget | null {
  const list = planTargets ?? [];
  const find = (name: string): Target | undefined =>
    list.find((t) => t.name.trim().toLowerCase() === name.trim().toLowerCase());

  const run = running ? find(running) : undefined;
  if (run && Number.isFinite(run.ra_hours) && Number.isFinite(run.dec_deg)) {
    return { name: run.name, ra_hours: run.ra_hours, dec_deg: run.dec_deg, source: "running" };
  }
  const routed = params.target ?? params.t ?? "";
  const hit = routed ? find(routed) : undefined;
  if (hit && Number.isFinite(hit.ra_hours) && Number.isFinite(hit.dec_deg)) {
    return { name: hit.name, ra_hours: hit.ra_hours, dec_deg: hit.dec_deg, source: "route" };
  }
  return null;
}

/** "02:40" in the viewer's own timezone, or "--:--" for a null/absent time. */
export function fmtClock(unix: number | null | undefined): string {
  if (typeof unix !== "number" || !Number.isFinite(unix)) return "--:--";
  const d = new Date(unix * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** What the moon does next, in the order the operator can use it: when it
 *  leaves, else when it arrives, else the fact that it does neither tonight.
 *  `rise_unix`/`set_unix` are BOTH null when the moon is up (or down) for the
 *  whole night (`MoonInfo`, `types.ts:1685-1686`), which is why the altitude
 *  is what disambiguates them and not a third field. */
export function moonSetClause(moon: MoonInfo): string {
  if (typeof moon.set_unix === "number") return `sets ${fmtClock(moon.set_unix)}`;
  if (typeof moon.rise_unix === "number") return `rises ${fmtClock(moon.rise_unix)}`;
  return moon.alt > 0 ? "up all night" : "below the horizon all night";
}

/** The MOON tile's sub-line. The separation clause appears only when a target
 *  was actually picked - a degrees-from figure with no named object is a number
 *  the reader has to invent a meaning for. */
export function moonSub(moon: MoonInfo, targetName: string | null): string {
  const set = moonSetClause(moon);
  if (!targetName) return set;
  return `${set} · ${Math.round(moon.separation_deg)}° from ${targetName}`;
}

/** Tonight for one target: the moon block, the samples, transit and the dark
 *  window's own copy of itself. view.site_derived (`visibility.py:668`). */
export function fetchVisibility(raHours: number, decDeg: number): Promise<VisibilityNight> {
  const ra = Math.round(raHours * 1000) / 1000;
  const dec = Math.round(decDeg * 100) / 100;
  return api.get<VisibilityNight>(
    `/api/visibility?ra=${encodeURIComponent(ra)}&dec=${encodeURIComponent(dec)}`,
  );
}

/** The target's own line for the dome's notes: when it peaks, when it drops
 *  through the horizon limit the server ranked it against, and how much dark
 *  time is left above that limit. Every clause is dropped when it is not true,
 *  which is the prototype's own `.filter(Boolean).join(" · ")` rule
 *  (`proto/logic.js:169`) - a notes line of "not applicable" reads as data. */
export function targetNotes(night: VisibilityNight | null, nowTs: number): string {
  if (!night) return "";
  const parts: string[] = [];
  const limit = night.alt_limit_deg;

  if (night.never_rises_above_limit) {
    parts.push(`never reaches ${Math.round(limit)}° from here tonight`);
  } else if (typeof night.transit_unix === "number") {
    const word = night.transit_unix > nowTs ? "transit" : "transited";
    parts.push(`${word} ${fmtClock(night.transit_unix)} · ${Math.round(night.transit_alt)}°`);
  }

  const samples = night.samples ?? [];
  const ahead = samples.filter((s) => s.t_unix >= nowTs);
  const drop = ahead.find((s, i) => i > 0 && s.alt < limit && ahead[i - 1].alt >= limit);
  if (drop) parts.push(`below ${Math.round(limit)}° at ${fmtClock(drop.t_unix)}`);

  // "usable" counts the dark samples still ahead that clear the limit, at the
  // grid's own step - not a wall-clock difference, so a target that dips and
  // comes back is not credited with the gap.
  if (ahead.length > 1) {
    const stepS = ahead[1].t_unix - ahead[0].t_unix;
    const usable = ahead.filter((s) => s.alt >= limit && s.sun_alt < -12).length * stepS;
    if (usable > 0) {
      const h = Math.floor(usable / 3600);
      const m = Math.round((usable % 3600) / 60);
      parts.push(`${h > 0 ? `${h}h ` : ""}${m}m usable`);
    }
  }
  return parts.join(" · ");
}
