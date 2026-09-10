// tonightVerdict.ts - `GET /api/flows/{id}/tonight` folded into ONE line a
// phone can read at arm's length, and the four answers it is allowed to give.
//
// WHY A FOLD AND NOT THE PANEL. `components/flows/TonightPanel.tsx` renders the
// same payload as a timeline, a story, a plan and a campaign - four tabs, a
// tablet's worth of pixels. The Now screen's list has one sub-line per row, and
// the only question it is answering is "is this worth starting tonight?". So
// this module reduces the payload to a tone and a sentence, and keeps NONE of
// the panel's chrome.
//
// THE READER DISCIPLINE IS COPIED, NOT RE-INVENTED (`TonightPanel.tsx:51-58`).
// A field the server did not send comes back null and draws nothing; it never
// becomes a zero. That matters here more than in the panel, because a zero in
// `dark_end_unix` would silently produce a CONFIDENT "0s usable" - a computed
// number over data nobody sent, which is the exact failure the tonight route
// refuses to commit at the server end.
//
// THE REFUSAL IS STILL THE PRODUCT. `ok: false` carries the server's own
// `reason` ("No observatory site is set, ..."), and that sentence is printed
// verbatim rather than folded into a generic error: it is the one string that
// tells the operator what to go and fix.

import { fmtClock, fmtDuration } from "../../../lib/format";

export type TonightTone = "good" | "warn" | "bad" | "dim";

export interface TonightVerdict {
  tone: TonightTone;
  /** One line, lower case, no trailing stop - it renders as a row sub-line. */
  line: string;
}

/** Nothing has been asked yet. Also what a row past `TONIGHT_RESOLVE_CAP`
 *  shows, which is why it says "not checked" and not "no window". */
export const TONIGHT_UNCHECKED = "tonight not checked yet";

/** `ok: true`, a real dark window, and no target inside it. */
export const TONIGHT_NO_WINDOW = "nothing in this flow is up during dark tonight";

/** `ok: true` with `dark_start_unix`/`dark_end_unix` missing or inverted. A
 *  duration measured against a window nobody sent would be invented. */
export const TONIGHT_NO_NIGHT =
  "tonight resolved without a dark window, so there is nothing to measure against";

/** `ok: false` with an empty `reason`. Says which half is missing so it cannot
 *  be mistaken for a transport failure. */
export const TONIGHT_NO_REASON = "the server would not resolve tonight and did not say why";

/** Flow-scoped route, plan row. `resolve_tonight` takes a graph or a compiled
 *  dict, but the ONLY route exposing it is `/api/flows/{id}/tonight`
 *  (`app.py:4652`); no plan-scoped equivalent exists, and computing one client
 *  side would be the phone deciding the night, which section 0.10 of
 *  ARCHITECTURE.md forbids. */
export const TONIGHT_PER_FLOW = "tonight is worked out per flow";

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const rec = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/**
 * The one line for one flow.
 *
 * `null` -> dim; a refusal -> warn carrying the server's reason; a resolved
 * night with no target inside dark -> bad; otherwise good, carrying the UNION
 * of the target windows clipped to `[dark_start_unix, dark_end_unix]` and the
 * clock time the first of them opens.
 *
 * The union, not the sum: two targets that overlap do not give you their hours
 * twice, and a list row that said they did would be promising sky that does not
 * exist.
 */
export function tonightVerdict(
  payload: Record<string, unknown> | null,
  nowMs: number,
): TonightVerdict {
  if (!payload) return { tone: "dim", line: TONIGHT_UNCHECKED };

  // Absent `ok` is a refusal, exactly as the panel reads it: this line must
  // never show a night nobody computed.
  if (payload.ok !== true) {
    return { tone: "warn", line: str(payload.reason) || TONIGHT_NO_REASON };
  }

  const night = rec(payload.night);
  const darkStart = night ? num(night.dark_start_unix) : null;
  const darkEnd = night ? num(night.dark_end_unix) : null;
  if (darkStart === null || darkEnd === null || darkEnd <= darkStart) {
    return { tone: "warn", line: TONIGHT_NO_NIGHT };
  }

  const spans: [number, number][] = [];
  for (const raw of arr(payload.targets)) {
    const t = rec(raw);
    if (!t) continue;
    const w = rec(t.window);
    if (!w) continue;
    const start = num(w.start_unix);
    const end = num(w.end_unix);
    if (start === null || end === null) continue;
    const a = Math.max(start, darkStart);
    const b = Math.min(end, darkEnd);
    // A window that closes before dark opens contributes ZERO, not a negative
    // number that would cancel a real one out of the sum below.
    if (b > a) spans.push([a, b]);
  }
  if (spans.length === 0) return { tone: "bad", line: TONIGHT_NO_WINDOW };

  spans.sort((x, y) => x[0] - y[0]);
  let usable = 0;
  let openAt = spans[0][0];
  let closeAt = spans[0][1];
  for (let i = 1; i < spans.length; i++) {
    const [s, e] = spans[i];
    if (s <= closeAt) closeAt = Math.max(closeAt, e);
    else {
      usable += closeAt - openAt;
      openAt = s;
      closeAt = e;
    }
  }
  usable += closeAt - openAt;

  return {
    tone: "good",
    line: `${fmtDuration(usable)} usable from ${fmtClock(spans[0][0] * 1000, nowMs)}`,
  };
}
