// api/ephemeris.ts: satellite and comet elements, and the passes they predict
// (D-SKY-1). Server: server/astrodeck/catalog/ephemeris/routes.py.
//
// Three routes and three different capabilities, which is the whole shape of
// this surface:
//
//   GET  /api/ephemeris/status    view.status            cache state, no sky
//   POST /api/ephemeris/refresh   config.site_optics     starts an outbound fetch
//   GET  /api/satellites/passes   view.site_derived      where and when to look
//
// The split is deliberate. Knowing that the element file is nine days old says
// nothing about where the rig is; knowing when the ISS clears the trees says
// exactly where it is, to within a few kilometres. So the cheap status route is
// readable by anyone who can see the rig at all, and the answer that geolocates
// it is not.
//
// THE REFRESH ROUTE REACHES THE INTERNET, which is why it sits behind an admin
// capability and behind the relay fence: a tunnelled cookie must not be able to
// make somebody's rig fetch from CelesTrak. Offer it where the server's own
// stale note sends the operator - Sky settings - and nowhere else.

import { api } from "../api";
import type { EphemerisStatus, PassesResponse } from "../types";

/** `GET /api/ephemeris/status` - what has been downloaded and how old it is.
 *
 *  Cheap and safe to poll, but poll it only WHILE `fetching` is non-empty and
 *  stop on the first tick where it is empty. There is nothing else on this
 *  payload that moves on its own: `age_days` creeps by one a day. */
export const getEphemerisStatus = (): Promise<EphemerisStatus> =>
  api.get<EphemerisStatus>("/api/ephemeris/status");

/** Which element set a refresh should fetch. `"all"` is the default and the
 *  only thing a general REFRESH ELEMENTS button should send; the two narrow
 *  values exist for a card that offers to refresh one row. */
export type EphemerisWhich = "all" | "satellites" | "comets";

/** 202 body of `POST /api/ephemeris/refresh` (`routes.py:60-75`). */
export interface EphemerisRefreshStarted {
  started: true;
  which: EphemerisWhich;
}

/** `POST /api/ephemeris/refresh` - start an outbound fetch. 202, not 200: the
 *  answer is "started", not "done". The MPC file is 160 kB and CelesTrak is
 *  sometimes slow, and a route that blocked on it would hold a worker for the
 *  whole request timeout. The result lands in `getEphemerisStatus()`.
 *
 *  REQUIRES `config.site_optics` AND A LAN CONNECTION (the path is on the relay
 *  fence). A caller must gate the control with `useLock`, not with a try/catch.
 *
 *  THE ONE REFUSAL, and it is not an error: **409 `code: "already_fetching"`**
 *  when one is already running, so a button pressed twice does not become two
 *  requests to an upstream that asks us to fetch four times a day. It throws an
 *  `ApiError` with `.code === "already_fetching"`; branch on THAT, never on the
 *  message.
 *
 *  MIND THE MESSAGE ON THAT ONE. `routes.py:71-73` answers a FLAT
 *  `{"code": "already_fetching", "which": "..."}` with no `detail` key, so
 *  `parseApiError` has no sentence to lift and `ApiError.message` is the
 *  JSON-stringified body. That is not copy. The UI supplies the sentence - the
 *  server deliberately did not write one, because the honest wording depends on
 *  whether the operator can see the fetch happening or not. */
export const refreshEphemeris = (
  which: EphemerisWhich = "all",
): Promise<EphemerisRefreshStarted> =>
  api.post<EphemerisRefreshStarted>("/api/ephemeris/refresh", { which });

/** Query for `getSatellitePasses`. Every field is optional and every default is
 *  the server's own (`routes.py:81-84`), so an omitted field and a field sent
 *  at its default produce the same answer. */
export interface PassesQuery {
  /** How far ahead to search, hours. 0 < hours <= 72; default 24. */
  hours?: number;
  /** Ignore passes that never reach this altitude. -90..90; default 0, which
   *  means "anything that clears the horizon". */
  minAltDeg?: number;
  /** NORAD ids to restrict the search to. Repeatable on the wire (`ids=25544&
   *  ids=48274`), so this is a list and not a comma-joined string. Omitted
   *  means every satellite in the cache. */
  ids?: number[];
}

/** `GET /api/satellites/passes` - the visible passes for the next `hours`.
 *
 *  DO NOT POLL THIS. It is thousands of SGP4 evaluations and a matching number
 *  of frame transforms, run on a worker thread so it does not stall the 2 s
 *  status poll (`routes.py:100-108`). Fetch it once per lock and once per
 *  explicit refresh.
 *
 *  REQUIRES `view.site_derived` FOR THE WHOLE ROUTE. A principal without it
 *  gets a 403 from the dependency, not an empty list - show the server's
 *  withheld sentence from the `/api/catalog` notes instead of calling this.
 *
 *  409 when the SITE IS UNSET (`SatellitesUnavailable`, a bare-string detail, so
 *  `ApiError.message` IS the sentence and carries no code). 409 rather than 500
 *  or an empty list on purpose: the server is fine and the rig is not ready, and
 *  an empty list here would read as "no passes tonight", which is a different
 *  and false statement. */
export function getSatellitePasses(q: PassesQuery = {}): Promise<PassesResponse> {
  const p = new URLSearchParams();
  if (q.hours !== undefined) p.set("hours", String(q.hours));
  if (q.minAltDeg !== undefined) p.set("min_alt_deg", String(q.minAltDeg));
  // Repeated key, not a joined list: FastAPI binds `ids: list[int] | None =
  // Query(None)` from repeats, and "25544,48274" would 422 as one bad int.
  for (const id of q.ids ?? []) p.append("ids", String(id));
  const qs = p.toString();
  return api.get<PassesResponse>(`/api/satellites/passes${qs ? `?${qs}` : ""}`);
}
