// filesModel.ts - the pure half of the four FILES-AND-STANDARDS editors (wave
// R7, T-R7-13): file sync, file naming, the plate-solve stamp and the rig's
// imaging standards.
//
// Everything here is DOM-free and store-free so the copy a screen prints can be
// graded without mounting it, and so the two helpers that used to live inside
// `components/settings/SyncPanel.tsx` stop dragging a legacy Tailwind component
// into the lazily-split next bundle (wave plan section 2.1's finding). The
// legacy file keeps its own copies for `#/classic`; these are not imported from
// it and it is not edited.
//
// WHAT IS *NOT* HERE, DELIBERATELY: `renderTemplatePreview` (`lib/naming.ts`)
// is a byte-for-byte mirror of the server's `render_relative_path`,
// `wcsStampOrDefault`/`wcsStampSummary`/`wcsStampAdvisory` (`lib/wcsStamp.ts`)
// and `standardsOrDefault`/`STANDARDS_NUMBER_FIELDS` (`lib/standards.ts`) are
// already pure library modules. Those are IMPORTED, never re-derived - a second
// copy of the naming renderer would be a second answer to "where will this
// frame land".

import { DEFAULT_TEMPLATE, renderTemplatePreview } from "../../../../../lib/naming";
import type { StandardsConfig, SyncPushStatus } from "../../../../../types";

// ---------------------------------------------------------------- copy rules
/** The shared logic modules predate this wave's copy rule and still hand back
 *  em-dashed sentences (`syncSummary`'s "Off - frames stay on the rig",
 *  `wcsStampSummary`, `wcsStampAdvisory`). The new UI is hyphens-only
 *  (ARCHITECTURE non-negotiable 5), and the fix belongs at the display edge
 *  rather than in a module `#/classic` also renders. */
export const plainDashes = (s: string): string => s.replace(/\s*[\u2014\u2013]\s*/g, " - ");

/** The gate's reason, but with the panel's own sentence when what is missing is
 *  the CAPABILITY rather than the link. `lockReason()` answers
 *  `needs admin access`, which is true and says nothing about what would change;
 *  these four editors each name their own subject ("changing file sync needs
 *  admin access"). A link-down reason passes through verbatim - it outranks the
 *  capability and rewording it would hide which of the two is the blocker. */
export function filesLockSentence(
  reason: string | null, phrase: string, subject: string,
): string | null {
  if (!reason) return null;
  return reason === `needs ${phrase}` ? `${subject} needs ${phrase}` : reason;
}

// ------------------------------------------------------------------ F11 sync
/** "2.31 GB" / "412 MB" - bytes at the scale a night is actually measured in.
 *  Verbatim from `SyncPanel.tsx:26`, re-exported here rather than imported
 *  (section 2.1's finding). */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  if (n >= 1e3) return `${Math.round(n / 1e3)} kB`;
  return `${Math.round(n)} B`;
}

/** How long ago, in the coarse words this card needs. `lib/telemetry.ts`'s
 *  `formatAge` is what `SyncPanel` used; it is imported by the editor, not
 *  re-implemented, and this module only spells the sentence around it. */
export type SyncTone = "ok" | "warn" | "dim";

/** The one line an operator reads. It has to distinguish four states that all
 *  look like "nothing is happening" from the outside: off, on-but-never-run,
 *  working, and broken. Verbatim from `SyncPanel.tsx:39` with the em-dashes
 *  turned into hyphens.
 *
 *  `ageWords` is passed in rather than computed so the sentence is testable
 *  without a clock. */
export function syncSummary(
  s: SyncPushStatus | null, ageWords: string | null,
): { text: string; tone: SyncTone } {
  if (!s || !s.enabled) return { text: "Off - frames stay on the rig.", tone: "dim" };
  if (!s.configured) return { text: "On, but no destination is set.", tone: "warn" };
  if (s.alarm) {
    return {
      text: `FAILING - ${s.consecutive_failures} passes in a row. Frames are NOT leaving the rig.`,
      tone: "warn",
    };
  }
  if (!s.passes) return { text: "On. No pass has run yet.", tone: "dim" };
  const when = s.last_ok_at && ageWords ? `${ageWords} ago` : "never";
  return {
    text: `${s.total_sent} frame${s.total_sent === 1 ? "" : "s"} sent `
      + `(${formatBytes(s.total_bytes)}). Last success ${when}.`,
    tone: s.last_ok_at ? "ok" : "warn",
  };
}

/** The last pass, with the two counts that say sync is not destructive:
 *  files already at the destination are left alone, and files the rig has never
 *  heard of are left alone too. Null when no pass has ever run. */
export function lastPassLine(s: SyncPushStatus | null): string | null {
  const last = s?.last;
  if (!last) return null;
  const parts = [`last pass: ${last.summary}`];
  if (last.already_there > 0) parts.push(`${last.already_there} already there`);
  if (last.extra_at_destination > 0) {
    parts.push(`${last.extra_at_destination} extra at destination (left alone)`);
  }
  return parts.join(" \u00b7 ");
}

/** What happens WITHOUT pressing anything, which is the whole promise the card
 *  is making. Null unless sync is both on and pointed somewhere. */
export function syncCadenceLine(s: SyncPushStatus | null): string | null {
  if (!s?.enabled || !s.configured) return null;
  const debounce = Math.round(s.debounce_s ?? 20);
  const sweep = Math.round((s.sweep_interval_s ?? 900) / 60);
  return `Otherwise a pass runs ${debounce}s after a frame lands, and a sweep runs `
    + `every ${sweep} min so a missed frame or an offline hour catches up on its own.`;
}

/** Why PUSH NOW cannot be pressed, in the order the user can act on. `null`
 *  means it can. The capability comes first because it is the only one the user
 *  cannot fix from this screen. */
export function pushBlockedReason(
  capReason: string | null, s: SyncPushStatus | null, busy: boolean,
): string | null {
  if (capReason) return capReason;
  if (s?.running) return "a pass is already running";
  if (!s?.configured) return "turn sync on and save a destination first";
  if (busy) return "the last change is still being saved";
  return null;
}

/** Why the master toggle cannot be turned ON yet. The server answers 422 to
 *  `enabled` with no path (`api/backends.ts:581`), so this is the same refusal
 *  said before the round trip instead of after it. Only ever blocks turning it
 *  ON - a destination-less sync can always be turned off. */
export function syncEnableBlockedReason(
  capReason: string | null, enabled: boolean, path: string,
): string | null {
  if (capReason) return capReason;
  if (!enabled && path.trim() === "") {
    return "save a destination folder first - the rig would have nowhere to push to";
  }
  return null;
}

export const SYNC_DEST_PLACEHOLDER = "\\\\nas\\astro  or  D:\\incoming";
export const SYNC_BLURB =
  "Copies each frame to another machine while the night is still running, so "
  + "processing can start before the run ends. Frames are only ever added there "
  + "- sync never deletes anything at the destination.";
export const SYNC_DEST_HINT =
  "A path this rig can write to: a mapped drive, or a UNC share exported by the "
  + "machine you process on.";
export const SYNC_SUBJECT = "changing file sync";
export const PUSH_SUBJECT = "pushing frames now";

// ---------------------------------------------------------------- F12 naming
/** The values the preview substitutes. Verbatim from `NamingPanel.tsx:232` -
 *  a preview built from different sample values than the classic panel's would
 *  be a second answer to the same question. */
export const NAMING_SAMPLE: Record<string, string> = {
  TARGET: "M42", FRAMETYPE: "Light", FILTER: "Ha", DATE: "2026-07-23",
  TIME: "213045", DATETIME: "2026-07-23_213045", NIGHT: "2026-07-23",
  FRAMENR: "0001",
};

/** The advisory path a frame would land on. ADVISORY: the server's own
 *  `render_relative_path` is authoritative, and `lib/naming.ts` is the mirror
 *  that keeps this honest (its golden vectors are shared with the Python). */
export function namingPreview(template: string): string {
  return `captures/${renderTemplatePreview(template || DEFAULT_TEMPLATE, NAMING_SAMPLE)}`;
}

/** The text a token chip inserts, and what it prints. One function so the chip
 *  label and the inserted text can never drift apart. */
export const tokenText = (t: string): string => `$$${t}$$`;

export const NAMING_SUBJECT = "changing the naming template";
export const NAMING_FOOTNOTE =
  "Folders come from the / characters; tokens like $$TARGET$$ are filled in per "
  + "frame. The default reproduces the classic layout.";

// ------------------------------------------------------------------ F13 wcs
export const WCS_TITLE = "Record where each photo points";
export const WCS_BLURB =
  "After each photo is saved, AstroDeck works out exactly where the scope was "
  + "pointing and stores it inside the file. Stacking software can then line "
  + "your photos up without figuring it out again. Costs a little time per "
  + "photo, so it is off by default.";
export const WCS_SOLVER_NOTE = "Auto picks the best installed solver.";
export const WCS_DOWNSAMPLE_NOTE =
  "Higher = faster, less precise. 2x is a good speed/accuracy trade on a Pi.";
export const WCS_MINSTARS_NOTE =
  "Skips the solve on cloud/trail frames that would fail anyway.";
export const WCS_FOOTNOTE =
  "One photo is worked out at a time in the background, so capturing never "
  + "waits. With very short exposures it can fall behind - the oldest ones are "
  + "then skipped and simply save without a position. Photos your imaging "
  + "backend saves on its own machine are never tagged.";
/** F13's sentence, verbatim: `Changing this needs <phrase>.` The subject form
 *  drops the capital and the stop so `LockNote` can prefix "Read-only - ". */
export const WCS_SUBJECT = "changing this";

// ------------------------------------------------------------ F14 standards
export const STANDARDS_BLURB =
  "What counts as a usable frame on this rig, and when a night gives up. These "
  + "apply to every night, including ones built in Flows. A plan can override "
  + "any of them for a single night.";
export const OFFSETS_LABEL = "Apply per-filter focus offsets";
export const OFFSETS_NOTE =
  "Shift the focuser by the filter's stored offset when the wheel moves, so a "
  + "filter change does not cost a refocus.";
export const STANDARDS_SUBJECT = "changing this";

/** How each numeric standard is typed. `lib/standards.ts` carries the labels,
 *  units and hints (and the anti-drift test that compares them with
 *  `config.py`); it does NOT say which are whole numbers or where the ceiling
 *  is, and a star count that accepts 12.5 or an eccentricity that accepts 4 is
 *  a value the engine will silently refuse. */
export const STANDARDS_INPUT: Record<
  keyof StandardsConfig, { integer: boolean; step: number; max?: number } | undefined
> = {
  min_stars: { integer: true, step: 1 },
  max_guide_rms: { integer: false, step: 0.1 },
  max_eccentricity: { integer: false, step: 0.01, max: 1 },
  refocus_on_temp_delta_c: { integer: false, step: 0.5 },
  max_consecutive_rejects: { integer: true, step: 1 },
  max_consecutive_rejects_night: { integer: true, step: 1 },
  apply_filter_offsets: undefined,
};

/** The plan-editor sentence, split so the link text is one word the user can
 *  read as a destination rather than a whole clause. */
export const PLAN_LINK_BEFORE = "A single night can override any of these in the ";
export const PLAN_LINK_LABEL = "plan editor";
export const PLAN_LINK_AFTER =
  ", where an overridden setting is marked and can be handed back to the rig.";
