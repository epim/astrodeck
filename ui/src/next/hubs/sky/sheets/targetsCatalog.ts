// targetsCatalog.ts - reading ONE object out of the catalogue, for the sheets
// that are handed an id in the hash and have to turn it into a target
// (hub-sky plan A.11, D.2).
//
// TWO ROUTES, because they answer two different questions and neither answers
// both (verified in the server source):
//
//   GET /api/catalog?q=<id>&explain=1   the search. Carries ra/dec, type,
//       magnitude, size, difficulty, alt/az for a holder of view.site_derived,
//       and `notes[]` - the server's own sentences for what it could NOT answer
//       (a body it does not carry, the Moon withheld from this role, an
//       ephemeris that failed this second). A DSO row here has NO `describe`
//       and NO `constellation`.
//   GET /api/catalog/region?...         the map. Its rows DO carry `describe`,
//       `constellation` and `alias`, and never an alt or az (the route does not
//       compute them at all - a pannable map is a coordinate oracle at a high
//       sample rate).
//
// So the brief asks the search where the object is, then asks the region what
// it is. Rendering "not on file for this object" from a route that has no such
// field would be the app reporting a gap in the catalogue that is really a gap
// in the request.
//
// THE ROW-SHAPE TRAP travels with these rows and is handled by `finder/targets`'
// `displayName`/`fullName`, which are imported rather than re-derived: for a
// solar-system row `id` is the LABEL ("Jupiter") and `name` is a whole SENTENCE,
// the opposite way round from a DSO. A sheet title built from `name` prints a
// paragraph.

import { useEffect, useState } from "react";
import { api } from "../../../../api";
import type { SkyRow } from "../../../../lib/skyRegion";
import type { DifficultyTier } from "../../../../types";

/** A `/api/catalog` search row, in the widest shape the three kinds share. */
export interface SearchRow {
  id: string;
  name: string;
  type: string;
  kind?: string;
  ra_hours: number;
  dec_deg: number;
  mag: number | null;
  size_arcmin: number;
  alt?: number;
  az?: number;
  difficulty?: DifficultyTier;
  /** Solar-system rows only. */
  constellation?: string | null;
  illumination?: number;
  distance_km?: number;
  ephemeris_unix?: number;
  topocentric?: boolean;
  geocentric_reason?: "site_unset" | "not_permitted" | null;
}

export interface CatalogAnswer {
  row: SearchRow | null;
  /** The server's own explanations for what is not in `row`. Rendered verbatim;
   *  a browser that guessed a reason here is the failure `explain=1` exists to
   *  end. */
  notes: string[];
}

export async function searchCatalog(q: string): Promise<CatalogAnswer> {
  const r = await api.get<{ results?: SearchRow[]; notes?: string[] }>(
    `/api/catalog?q=${encodeURIComponent(q)}&explain=1`,
  );
  const results = Array.isArray(r?.results) ? r.results : [];
  return { row: pickRow(results, q), notes: Array.isArray(r?.notes) ? r.notes : [] };
}

/** The row the caller asked for, by id first and by name second.
 *
 *  Id first because the hash carries an id, and a search for "M31" also returns
 *  rows whose NAME mentions it. Matching on name alone opens the brief for a
 *  different object than the row that was tapped. Separators are squashed for
 *  the same reason the server squashes them: "M 31" and "M31" are one object.
 *
 *  NO MATCH IS `null`, NOT `rows[0]`. The fallback looked harmless - the search
 *  route is asked for one id and usually answers with it first - but it turned
 *  "the catalogue does not carry this" into "here is a different object with
 *  the same confidence", and every caller renders the answer as THE target: the
 *  brief titles it, and the quick sheet takes its `ra_hours`/`dec_deg` into the
 *  flow it generates. A night pointed at a neighbouring galaxy because an id
 *  was mistyped is not a rendering bug. Both callers already handle the absence
 *  (`quick.tsx` locks GENERATE FLOW on `target == null` and prints the server's
 *  own `notes[]`), so the honest answer costs nothing. */
export function pickRow(rows: readonly SearchRow[], q: string): SearchRow | null {
  const key = q.trim().toLowerCase();
  const squash = (s: string): string => s.toLowerCase().replace(/[\s_-]+/g, "");
  const byId = rows.find((r) => r.id.toLowerCase() === key)
    ?? rows.find((r) => squash(r.id) === squash(key));
  if (byId) return byId;
  return rows.find((r) => (r.name ?? "").toLowerCase() === key) ?? null;
}

/** The map's row for the same object - the only source of `describe`,
 *  `constellation` and `alias`. Null when the region query cannot see it, which
 *  is an answer the caller renders as an absence rather than as a blank. */
export async function regionRowFor(
  raHours: number,
  decDeg: number,
  id: string,
): Promise<SkyRow | null> {
  const qs = new URLSearchParams({
    ra_hours: (((raHours % 24) + 24) % 24).toFixed(6),
    dec_deg: Math.max(-90, Math.min(90, decDeg)).toFixed(6),
    radius_deg: "0.5",
    fov_deg: "1",
    limit: "40",
  });
  const r = await api.get<{ rows?: SkyRow[] }>(`/api/catalog/region?${qs.toString()}`);
  const rows = Array.isArray(r?.rows) ? r.rows : [];
  return rows.find((x) => x.id === id) ?? null;
}

export interface CatalogTargetState extends CatalogAnswer {
  loading: boolean;
  /** The transport failed - which is NOT "no such object". Flattening the two
   *  is how a dead link reads as a confident "no matches". */
  error: string | null;
}

/** One catalogue row for a hash id. Refetched when the id changes, never on a
 *  render. */
export function useCatalogTarget(q: string | null): CatalogTargetState {
  const [state, setState] = useState<CatalogTargetState>({
    row: null, notes: [], loading: q != null && q !== "", error: null,
  });
  useEffect(() => {
    if (!q) { setState({ row: null, notes: [], loading: false, error: null }); return; }
    let alive = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    void searchCatalog(q)
      .then((a) => { if (alive) setState({ ...a, loading: false, error: null }); })
      .catch((e: unknown) => {
        if (!alive) return;
        const msg = e instanceof Error ? e.message : String(e);
        setState({ row: null, notes: [], loading: false, error: msg });
      });
    return () => { alive = false; };
  }, [q]);
  return state;
}

/** Several rows for a pool. One request per member, which is what the search
 *  route offers - there is no batch form - and a pool is at most a handful. */
export function useCatalogTargets(ids: readonly string[]): {
  rows: SearchRow[];
  loading: boolean;
} {
  const key = ids.join(",");
  const [rows, setRows] = useState<SearchRow[]>([]);
  const [loading, setLoading] = useState(ids.length > 0);
  useEffect(() => {
    const list = key === "" ? [] : key.split(",");
    if (list.length === 0) { setRows([]); setLoading(false); return; }
    let alive = true;
    setLoading(true);
    void Promise.all(list.map((id) => searchCatalog(id).catch(() => ({ row: null, notes: [] }))))
      .then((answers) => {
        if (!alive) return;
        setRows(answers.map((a) => a.row).filter((r): r is SearchRow => r != null));
        setLoading(false);
      });
    return () => { alive = false; };
  }, [key]);
  return { rows, loading };
}
