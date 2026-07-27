// sessionDates.ts — the one-line identity string on a session card.
//
// REVIEW #35: five session cards all read "Tonight" with nothing to tell them
// apart, and the professional clicked RESUME on the wrong one. `created_ts` and
// `updated_ts` are already on every row from `/api/sessions`; neither was
// rendered. Pure + dependency-free so it can be tested without a DOM.

/**
 * "Sep 4 · tonight" for a session that started and last shot today,
 * "Sep 1 → Sep 4 · tonight" once it spans nights,
 * "Sep 1 → Sep 4 · Sep 4" once it is old enough that a relative phrase lies.
 *
 * The span is the point: a multi-night session's identity IS its date range,
 * which is exactly what distinguishes two projects both named "Tonight".
 * Local time, because the user is standing in it.
 */
export function sessionDates(
  created_ts: number, updated_ts: number, now = Date.now() / 1000,
): string {
  const day = (ts: number) => new Date(ts * 1000)
    .toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const start = day(created_ts);
  const last = day(updated_ts);
  const hrs = (now - updated_ts) / 3600;
  const recency = hrs < 18 ? "tonight" : hrs < 42 ? "last night" : last;
  return start === last ? `${start} · ${recency}` : `${start} → ${last} · ${recency}`;
}
