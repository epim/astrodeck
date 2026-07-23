// NOV-11 — query string for GET /api/preview/{id}/share.jpg. Caption target +
// sub count live only in SequenceState (never on PreviewInfo), so the client
// forwards them as query params; the server fills exposure/gain/date from
// entry.meta.
export function shareQuery(target?: string, subs?: number): string {
  const p = new URLSearchParams();
  if (target && target.trim()) p.set("target", target.trim());
  if (subs && subs > 1) p.set("subs", String(subs));
  const s = p.toString();
  return s ? `?${s}` : "";
}
