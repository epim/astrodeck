// CalibrationMatrix.tsx — the LIBRARY HEALTH block inside the inspector, shown
// only for a CALIBRATION QUEUE node. §C.9.
//
// It renders `GET /api/calibration/health`, and the whole reason this file is
// more than a four-column grid is that the endpoint's most important fields are
// not rows.
//
//   * `planned: false` means no lights are planned yet, so there is nothing to
//     calibrate FOR and the row list is empty. The route's own docstring: "an
//     empty matrix MUST NOT be drawn as healthy, it must say there are no lights
//     planned yet." An empty grid with no sentence under it says the opposite of
//     the truth — it says you have everything.
//   * `counts_masters_only: true` means `have` counts stacked masters only.
//     There is no frame walk behind it, so a row reads MISSING where raw subs
//     are sitting on disk. Unlabelled, that is a confident wrong number.
//   * `assumed` is the offset and sensor temperature the demand was computed
//     with, because the node vocabulary has no offset param and the compiler
//     never emits a cooling setpoint. The route: "Both are invented values if
//     you do not say they are assumptions."
//   * a NULL `calHealth` is a request that failed. flowsSlice leaves it null
//     precisely so this component can say "could not read the library" rather
//     than draw the empty matrix that means something else entirely.
//
// §G-10(b) is open on WHERE that evidence goes (hover title / expandable row /
// a line under the block) and the server computes a whole vocabulary more of it
// — `reasons[]`, `family`, `contradicted`, `measured`, `age_days`, `master_id`.
// This file takes the cheapest honest home the contract itself reaches for
// elsewhere (plain lines under the block) for the three route-level flags only,
// and leaves the per-row evidence alone rather than inventing a disclosure
// pattern. §G-10(c) — the row count is unbounded server-side and no max-height
// rule exists anywhere — is left to the inspector column's own `overflow-y-auto`
// for the same reason.
import { useEffect, useState } from "react";
import { useStore } from "../../store";
import type { FlowCalHealth } from "./flowsTypes";
import type { FieldVariant } from "./FlowFieldRow";

/** Verdict → colour TOKEN. §C.9: "The server sends no colour — the token
 *  mapping is the UI's."
 *
 *  The verdict is also a WORD in the grid, which is what keeps this off the
 *  colour-alone list: OK / STALE / MISSING read the same in the night palette,
 *  where good/warn/bad all collapse toward coral. An unrecognised verdict gets
 *  the neutral ink rather than a guess — never the green one. */
export function verdictVar(verdict: string): string {
  switch (verdict) {
    case "OK": return "var(--good)";
    case "STALE": return "var(--warn)";
    case "MISSING": return "var(--bad)";
    default: return "var(--text-dim)";
  }
}

/** One line of the honesty block under the grid. */
export interface CalNote {
  key: string;
  text: string;
  /** `warn` is reserved for the flag that would otherwise be read as health. */
  tone: "warn" | "faint";
}

/** The route-level flags, as sentences.
 *
 *  `health` null means the request failed; that is a THIRD state, distinct from
 *  both a healthy library and an empty one, and it gets its own line. */
export function calHealthNotes(health: FlowCalHealth | null): CalNote[] {
  if (!health) {
    return [{
      key: "unread", tone: "warn",
      text: "Could not read the calibration library — this is not an empty library, it is an unanswered question.",
    }];
  }
  const notes: CalNote[] = [];
  if (!health.planned) {
    notes.push({
      key: "planned", tone: "warn",
      text: "No lights planned yet, so there is nothing to calibrate for. An empty matrix is not a healthy one.",
    });
  }
  if (health.counts_masters_only) {
    notes.push({
      key: "masters", tone: "faint",
      text: "Counts stacked masters only — a row reads MISSING where raw subs exist on disk.",
    });
  }
  const a = health.assumed;
  if (a) {
    notes.push({
      key: "assumed", tone: "faint",
      text: `Assumes offset ${a.offset}; sensor temperature ${
        a.temp_c == null ? "unknown" : `${a.temp_c}°C`}.`,
    });
  }
  return notes;
}

/** The four columns, read out of the loose row object.
 *
 *  `rows[]` is typed `Record<string, unknown>[]` because the server returns
 *  eighteen keys and four of them are drawn. Every one is rendered VERBATIM —
 *  §G-10(a) records that `summary` cannot reproduce the screenshot's BIAS and
 *  FLAT strings, and its ruling is explicit: do not synthesise a third
 *  phrasing here. */
export function readCalRow(row: Record<string, unknown>, i: number): {
  key: string; label: string; summary: string; quantity: string; verdict: string;
} {
  const s = (k: string) => (row[k] == null ? "" : String(row[k]));
  return {
    // `kind` + `summary` IS the row's server-side identity (`_row_key`); the
    // index only breaks a tie the server would not have produced.
    key: `${s("kind")}|${s("summary")}|${i}`,
    label: s("label"), summary: s("summary"),
    quantity: s("quantity"), verdict: s("verdict"),
  };
}

export default function CalibrationMatrix({ variant = "column" }: {
  variant?: FieldVariant;
}) {
  const health = useStore((s) => s.flows.calHealth);
  const flowId = useStore((s) => s.flows.record?.id ?? null);
  const fetchHealth = useStore((s) => s.flowsFetchCalHealth);
  // `calHealth` is null both before the first answer and after a failed one, and
  // those two are not the same sentence. Nothing else on the surface can tell
  // them apart, so the component remembers whether it has asked yet.
  const [asked, setAsked] = useState(false);

  useEffect(() => {
    let live = true;
    setAsked(false);
    // flowsFetchCalHealth swallows its own errors (that is what leaves
    // calHealth null), so this settles either way and never rejects.
    void fetchHealth().finally(() => { if (live) setAsked(true); });
    return () => { live = false; };
  }, [fetchHealth, flowId]);

  const rows = health?.rows ?? [];
  const grid = variant === "sheet" ? "text-[10px]" : "text-[9.5px]";

  return (
    <div
      data-calibration-matrix
      className="border border-line rounded-[10px] p-2.5 flex flex-col gap-1.5
                 bg-[color-mix(in_srgb,var(--bg)_50%,transparent)]"
    >
      <div className="font-display font-semibold text-[9px] tracking-[0.2em] text-faint">
        LIBRARY HEALTH
      </div>

      {rows.map((r, i) => {
        const row = readCalRow(r, i);
        return (
          <div
            key={row.key}
            className={`grid items-center gap-1.5 font-mono ${grid}`}
            style={{ gridTemplateColumns: "44px 1fr 42px 56px" }}
          >
            <span className="text-faint">{row.label}</span>
            {/* min-w-0: a grid item's default min-width is `auto`, so without it
                the 1fr track grows to the longest summary and the block pushes
                the 284px column sideways instead of truncating. */}
            <span className="text-dim truncate min-w-0">{row.summary}</span>
            <span className="text-ink text-right">{row.quantity}</span>
            <span className="text-right" style={{ color: verdictVar(row.verdict) }}>
              {row.verdict}
            </span>
          </div>
        );
      })}

      {!asked && !health ? (
        <div className="text-[10px] text-faint leading-[1.45]">
          Reading the calibration library…
        </div>
      ) : (
        calHealthNotes(health).map((n) => (
          <div
            key={n.key}
            className={`text-[10px] leading-[1.45] ${
              n.tone === "warn" ? "text-warn" : "text-faint"}`}
          >
            {n.text}
          </div>
        ))
      )}

      <div className="text-[10px] text-faint leading-[1.45]">
        Drives the queue's 'if stale' decisions — mirrors the calibration library.
      </div>
    </div>
  );
}
