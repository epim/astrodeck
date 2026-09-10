// tonightModel.ts - the pure half of the Tonight sheet: how the server's
// payload is read, and every string the sheet says that the pixels do not.
//
// WHAT IS SHARED AND WHAT IS TRANSCRIBED. The plan's rule for R7 is that LOGIC
// keeps being shared and only presentation is rebuilt, so the geometry builders
// (`timelineGeometry`, `TL_W`, `TL_H`, `TlRect`/`TlDash`/`TlPath`/`TlLabel`,
// `moonPercent`), `memberStatus`/`CampaignRead` and `verdictVar`/
// `calHealthNotes`/`readCalRow` are IMPORTED from their legacy modules by the
// components below - they are exported there, and a second copy of the rule
// that places DUSK on the axis is a second night. What is NOT exported there is
// transcribed here and named: `TonightPanel.tsx`'s payload readers
// (`readTonight`/`readCampaign`, module-private) and `TonightTimeline.tsx`'s
// `TONE_VAR` table (module-private).
//
// THE REFUSAL IS THE PRODUCT. `GET /api/flows/{id}/tonight` can legitimately
// answer `{ok: false, reason: "..."}` and `tonight.py` refuses rather than
// guessing a dusk from a default site. So `ok` absent reads as a REFUSAL, never
// as success, and every reader below is total: a field the server did not send
// comes back null and draws nothing.

import { accessPhrase } from "../../../../../lib/caps";
import { fmtClock } from "../../../../../lib/eta";
import { fmtTime } from "../../../../../lib/visibility";
import type { TonightTab } from "../../../../../components/flows/flowsTypes";
import type {
  TonightFlats, TonightMoon, TonightNight, TonightTarget,
} from "../../../../../components/flows/TonightTimeline";
import type { TonightStoryRow } from "../../../../../components/flows/TonightStory";
import type {
  CampaignMember, CampaignRead,
} from "../../../../../components/flows/TonightCampaign";

// ------------------------------------------------------------------ the tabs

/** In this order; TIMELINE is the default. CAMPAIGN goes last because it is the
 *  only tab that describes something OTHER than tonight. */
export const TONIGHT_TABS: readonly TonightTab[] = ["timeline", "story", "plan", "campaign"];

export const TONIGHT_TAB_LABEL: Record<TonightTab, string> = {
  timeline: "TIMELINE",
  story: "STORY",
  plan: "PLAN",
  campaign: "CAMPAIGN",
};

/** What each tab answers, on the segmented control's second line, so the choice
 *  is not four nouns with no difference between them. */
export const TONIGHT_TAB_SUB: Record<TonightTab, string> = {
  timeline: "when",
  story: "why",
  plan: "what runs",
  campaign: "banked",
};

// ------------------------------------------------------------------ the gate

/** The route is gated on `view.site_derived` rather than `view.status` because
 *  an audit recovered the observatory to 2.9 km from three viewer-legal
 *  requests (`app.py`). The sentence names the site AND the roles, from
 *  `accessPhrase`, so it stays true if the role table moves. */
export const TONIGHT_LOCK_REASON =
  `Tonight is worked out from the observatory site, so it needs ${
    accessPhrase("view.site_derived")}.`;

// ----------------------------------------------------------------- the copy

/** Em-dashes out of a string this UI did not write.
 *
 *  `calHealthNotes()` is shared logic (the three route-level flags, as
 *  sentences) and its text still carries the legacy em-dash. The R7 copy rule
 *  is hyphens, never em-dashes, and editing the legacy file is forbidden -
 *  `#/classic` renders the same sentences. One substitution at the render seam
 *  keeps a single source for the WORDS while the new UI keeps its punctuation.
 *  Nothing else about the string changes. */
export function plainDashes(text: string): string {
  return text.replace(/—/g, "-");
}

// --------------------------------------------------------- reading the payload
// `flows.tonight` is `Record<string, unknown>` at the store boundary on purpose
// (flowsSlice): the payload is large and only this surface reads it.

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const rec = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>) : null;
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

export interface TonightRead {
  ok: boolean;
  reason: string;
  night: TonightNight;
  flats: TonightFlats | null;
  moon: TonightMoon | null;
  targets: TonightTarget[];
  story: TonightStoryRow[];
  /** The graph read back as prose. "" when the server had no graph. */
  brief: string;
  campaign: CampaignRead | null;
}

/** The campaign block, read totally.
 *
 *  `banked`/`pct` stay null unless the server sent a FINITE NUMBER: a missing
 *  figure and a zero are different answers ("0 of 45 banked" says the rig
 *  looked and found nothing; "not counted" says nobody looked), and only the
 *  first should make an operator re-plan a month. */
export function readCampaign(raw: Record<string, unknown> | null): CampaignRead | null {
  if (!raw) return null;
  return {
    is_campaign: raw.is_campaign === true,
    has_pool: raw.has_pool === true,
    has_ledger: raw.has_ledger === true,
    quota: num(raw.quota) ?? 0,
    note: str(raw.note),
    members: arr(raw.members).map((m): CampaignMember => {
      const r = rec(m);
      return {
        name: r ? str(r.name) : "",
        banked: r ? num(r.banked) : null,
        quota: (r ? num(r.quota) : null) ?? 0,
        done: r ? r.done === true : false,
        pct: r ? num(r.pct) : null,
      };
    }).filter((m) => m.name !== ""),
  };
}

export function readTonight(payload: Record<string, unknown> | null): TonightRead | null {
  if (!payload) return null;
  const night = rec(payload.night);
  const flats = rec(payload.flats);
  const moon = rec(payload.moon);
  return {
    // Absent `ok` is a refusal, not success: this surface's whole job is to not
    // show a night nobody computed.
    ok: payload.ok === true,
    reason: str(payload.reason),
    night: {
      dusk_unix: night ? num(night.dusk_unix) : null,
      dawn_unix: night ? num(night.dawn_unix) : null,
      dark_start_unix: night ? num(night.dark_start_unix) : null,
      dark_end_unix: night ? num(night.dark_end_unix) : null,
    },
    flats: flats
      ? { start_unix: num(flats.start_unix), end_unix: num(flats.end_unix) }
      : null,
    moon: moon
      ? {
          illumination: num(moon.illumination),
          rise_unix: num(moon.rise_unix),
          set_unix: num(moon.set_unix),
        }
      : null,
    brief: str(payload.brief),
    campaign: readCampaign(rec(payload.campaign)),
    targets: arr(payload.targets).map((raw): TonightTarget => {
      const t = rec(raw) ?? {};
      const w = rec(t.window);
      const start = w ? num(w.start_unix) : null;
      const end = w ? num(w.end_unix) : null;
      return {
        label: str(t.label) || str(t.name),
        window: start !== null && end !== null
          ? { start_unix: start, end_unix: end } : null,
        curve: arr(t.curve).flatMap((p): [number, number][] => {
          if (!Array.isArray(p)) return [];
          const ts = num(p[0]);
          const alt = num(p[1]);
          return ts !== null && alt !== null ? [[ts, alt]] : [];
        }),
        meridian_flip_unix: num(t.meridian_flip_unix),
      };
    }),
    story: arr(payload.story).map((raw): TonightStoryRow => {
      const r = rec(raw) ?? {};
      return {
        t_unix: num(r.t_unix),
        label: str(r.label),
        msg: str(r.msg),
        tone: str(r.tone),
      };
    }),
  };
}

// ------------------------------------------------------------------- tones
// Transcribed from `TonightTimeline.tsx`'s module-private `TONE_VAR`: tone ->
// the TOKEN, never a hex, because the night palette re-derives every one of
// them. These are SVG paint and CSS `color` values, not class names, which is
// why they are inline values rather than `nx-*` classes.

/** The timeline's six paints, keyed by `TlTone`. */
export const TL_TONE_VAR: Record<string, string> = {
  accent: "var(--accent)",
  sky: "var(--sky)",
  faint: "var(--text-faint)",
  warn: "var(--warn)",
  good: "var(--good)",
  ink: "var(--text)",
};

/** `tonight.py`'s closed tone set for a story row -> the token ladder. An
 *  unknown tone falls back to body ink rather than being dropped: a sentence
 *  the server thought worth sending is worth showing even if a future rung
 *  arrives before this map does. */
const STORY_TONE_VAR: Record<string, string> = {
  text: "var(--text)",
  dim: "var(--text-dim)",
  faint: "var(--text-faint)",
  good: "var(--good)",
  warn: "var(--warn)",
  bad: "var(--bad)",
};

export function storyToneVar(tone: string): string {
  return STORY_TONE_VAR[tone] ?? "var(--text)";
}

// ------------------------------------------------------------------- stamps

/** The story row's 60 px first column.
 *
 *  A label wins when the server sent one - "ANY" and "BUDGET" are statements
 *  that the row has NO clock time, not missing data. `lib/visibility`'s
 *  `fmtTime` answers with an em-dash for a null, which this UI does not print,
 *  so the untimed-and-unlabelled case is a hyphen written here; and the
 *  server's own em-dash placeholder label is normalised the same way. */
export function storyStamp(row: { t_unix: number | null; label: string }): string {
  const label = plainDashes(row.label).trim();
  if (label !== "") return label;
  return row.t_unix == null ? "-" : fmtTime(row.t_unix);
}

/** The sheet's live line: the instants the whole night hangs off, in the
 *  server's own order. Every clause a null drops out entirely - a fabricated
 *  dusk is the failure this whole surface exists to avoid - and with none of
 *  them known the line is "" and the header renders no live row at all. */
export function nightLine(night: TonightNight | null): string {
  if (!night) return "";
  const parts: string[] = [];
  if (night.dusk_unix != null) parts.push(`dusk ${fmtTime(night.dusk_unix)}`);
  if (night.dark_start_unix != null) parts.push(`dark ${fmtTime(night.dark_start_unix)}`);
  if (night.dark_end_unix != null) parts.push(`dark ends ${fmtTime(night.dark_end_unix)}`);
  if (night.dawn_unix != null) parts.push(`dawn ${fmtTime(night.dawn_unix)}`);
  return parts.join(" · ");
}

/** The parked campaign's one line, in the same shape the cross-hub strip uses
 *  (`crossHub.ts`'s `useCampaignStrip`): with no dusk in the payload the CLAUSE
 *  is dropped and the sentence still says the true part. Never a stand-in
 *  time - an operator cannot tell an invented dusk from a measured one. */
export function resumesLine(duskUnix: number | null): string {
  return duskUnix == null
    ? "Parked - this campaign resumes by itself at dusk."
    : `Parked - this campaign resumes by itself at dusk ${fmtClock(duskUnix * 1000)}.`;
}
