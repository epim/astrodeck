// TonightPicker — "What can I image tonight?" (NOV-3). Fetches the server-ranked,
// difficulty-tagged catalog (GET /api/catalog/tonight) and lets a first-timer pick
// a slam-dunk. A pick calls onPick(entry) -> AtlasView passes openFraming, exactly
// like CatalogSearch. Beginner filter (Easy+Moderate) defaults ON.
//
// UX-2026-07-26 (the one place two personas openly disagreed): the novice called
// this "the single best thing in the app"; the pro called it under-informative
// and wanted transit time, best-window span and moon separation — all three of
// which `/api/catalog/tonight` ALREADY returns and this component dropped on the
// floor. The BEGINNER/ALL toggle is the seam that settles it: BEGINNER is byte
// for byte the row the novice praised, ALL becomes the dense pro row. The choice
// is remembered, because a pro who picks ALL every night is telling us something.

import { useEffect, useMemo, useState, type JSX } from "react";
import { api, ApiError } from "../../api";
import type { CatalogEntry, TonightPick, TonightResponse } from "../../types";
import { useStore } from "../../store";
import { Icon } from "../icons";
import { EmptyState, InfoDot, SegmentedControl } from "../ui";
import {
  difficultyLabel, difficultyGlyph, difficultyTone, isBeginnerFriendly,
} from "../../lib/difficulty";
import { fmtTime, fmtWindow, moonSepGlyph, moonSepTone } from "../../lib/visibility";

// Remembered scope (master §A.2 persistence rules — a plain localStorage pref,
// no store slice). Unset / unreadable => "beginner", so the novice default is
// what a fresh install still gets.
const SCOPE_KEY = "astrodeck-tonight-scope";

function loadScope(): "beginner" | "all" {
  try {
    return localStorage.getItem(SCOPE_KEY) === "all" ? "all" : "beginner";
  } catch {
    return "beginner";
  }
}

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; res: TonightResponse };

// A TonightPick is CatalogEntry-shaped except alt/az (the framer seeds altaz
// itself from ra/dec via openFraming). Build the entry the parent expects.
function toEntry(p: TonightPick): CatalogEntry {
  return {
    id: p.id, name: p.name, type: p.type,
    ra_hours: p.ra_hours, dec_deg: p.dec_deg,
    mag: p.mag, size_arcmin: p.size_arcmin,
    alt: p.max_alt, az: 0,
    difficulty: p.difficulty,
    surface_brightness: p.surface_brightness,
    difficulty_source: p.difficulty_source,
  };
}

export function TonightPicker({
  onPick,
}: {
  onPick: (e: CatalogEntry) => void;
}): JSX.Element {
  const altLimit = useStore((s) => s.site?.horizon_min_deg ?? 30);
  const [state, setState] = useState<Load>({ kind: "loading" });
  const [scope, setScope] = useState<"beginner" | "all">(loadScope);
  const beginnerOnly = scope === "beginner";
  const setScopePersisted = (v: "beginner" | "all") => {
    setScope(v);
    try {
      localStorage.setItem(SCOPE_KEY, v);
    } catch {
      /* quota / unavailable — in-memory only for this session */
    }
  };

  useEffect(() => {
    let alive = true;
    setState({ kind: "loading" });
    api
      .get<TonightResponse>(`/api/catalog/tonight?alt_limit=${encodeURIComponent(altLimit)}`)
      .then((res) => { if (alive) setState({ kind: "ok", res }); })
      .catch((e) => {
        if (!alive) return;
        setState({ kind: "error",
          message: e instanceof ApiError ? e.message : "couldn't rank tonight" });
      });
    return () => { alive = false; };
  }, [altLimit]);

  const picks = state.kind === "ok" ? state.res.picks : [];
  const shown = useMemo(
    () => (beginnerOnly ? picks.filter((p) => isBeginnerFriendly(p.difficulty)) : picks),
    [picks, beginnerOnly],
  );

  return (
    <div className="flex flex-col gap-2 w-full text-left">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="panel-title inline-flex items-center gap-1.5">
          What can I image tonight?
          {/* The two chips on every row are the jargon here. Their explanation
              used to live ONLY in `title=`, which never fires on touch — and this
              list's audience is a first-timer on a phone. Tooltip/InfoDot has a
              tap path, a keyboard path, and an Escape. */}
          <InfoDot
            label="How these targets are rated and ranked"
            content={
              <>
                <strong>Difficulty</strong> comes from how bright the object is
                spread over its size: ● Easy, ◐ Moderate, ○ Hard. Beginner shows
                only Easy + Moderate.
                <br />
                <strong>Ranking and the ↑ number</strong> are the highest the
                object gets tonight. Higher means less air to shoot through, so
                the list is ordered by that peak altitude. “low” means it never
                clears your {Math.round(altLimit)}° horizon limit tonight.
                <br />
                <strong>All</strong> adds three more per row: the time it
                transits (↑), the best window to shoot it ([ ]), and how many
                degrees it sits from the Moon (☾ / ⚠ under 30° / ✕ under 15°).
                Your choice of Beginner or All is remembered.
              </>
            }
          />
        </span>
        {/* Scope filter (not a disable). The house SegmentedControl already is a
            real radiogroup with roving tabindex, arrow keys and a 44px floor —
            the hand-rolled aria-pressed pair it replaces was ~24px tall. */}
        <SegmentedControl
          ariaLabel="Difficulty filter"
          value={scope}
          onChange={(v) => setScopePersisted(v === "all" ? "all" : "beginner")}
          options={[
            { value: "beginner", label: "Beginner" },
            { value: "all", label: "All" },
          ]}
        />
      </div>

      {state.kind === "ok" && state.res.site_is_default && (
        <div className="flex items-start gap-1.5 text-[12px] text-warn border border-line2 bg-black/20 px-2 py-1">
          <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
          <span>Using a default location — set yours in Settings for accurate visibility.</span>
        </div>
      )}

      {state.kind === "loading" && (
        <p className="text-xs text-dim px-1 py-2">Ranking tonight…</p>
      )}
      {state.kind === "error" && (
        <p className="text-xs text-warn px-1 py-2">
          Couldn&apos;t rank tonight — {state.message}
        </p>
      )}
      {state.kind === "ok" && shown.length === 0 && (
        <EmptyState size="inline" icon="atlas" title="No beginner targets up tonight — try All." />
      )}

      {state.kind === "ok" && shown.length > 0 && (
        /* The 288px clamp put six of twenty suggestions in a nested scroll
           region with ~700px of empty wallpaper underneath it, on the tablet,
           with gloves on. Keep a clamp (the list must not shove the rest of the
           page off-screen) but let it use the height that is actually there. */
        <ul className="flex flex-col max-h-72 sm:max-h-[55vh] overflow-y-auto border border-line2 divide-y divide-line2">
          {shown.slice(0, 20).map((p) => (
            <li key={p.id}>
              {/* Tapping a target IS this view, and its audience is a first-timer
                  on a phone in the dark — the house floor is 44px (§8). */}
              <button type="button" onClick={() => onPick(toEntry(p))}
                className="tap min-h-[44px] w-full text-left px-3 py-2 text-xs hover:bg-raise transition-colors flex flex-col gap-0.5 cursor-pointer">
                <span className="flex items-center justify-between gap-2 w-full">
                <span className="min-w-0 truncate">
                  <span className="mono text-accent">{p.id}</span> {p.name}
                  <span className="text-dim"> · {p.type}</span>
                </span>
                <span className="flex items-center gap-2 shrink-0">
                  {/* Difficulty badge — glyph is primary, tone secondary (§8).
                      The per-target detail rides in aria-label (screen readers
                      + the row's accessible name); the SCALE it belongs to is
                      explained by the header InfoDot, which has a tap path. No
                      `title=` — it never fires on touch. */}
                  <span className={`inline-flex items-center gap-1 ${
                    difficultyTone(p.difficulty) === "good" ? "text-good"
                    : difficultyTone(p.difficulty) === "warn" ? "text-warn" : "text-bad"}`}
                    aria-label={`${difficultyLabel(p.difficulty)} — surface brightness ${p.surface_brightness} mag/arcmin²${p.difficulty_source === "curated" ? ", curated" : ""}`}>
                    <span aria-hidden>{difficultyGlyph(p.difficulty)}</span>
                    {difficultyLabel(p.difficulty)}
                  </span>
                  {/* peak-alt chip — CatalogSearch idiom (CatalogSearch.tsx:109) */}
                  <span className={`mono ${
                    p.never_rises_above_limit ? "text-warn"
                    : p.max_alt > 40 ? "text-good" : p.max_alt < 20 ? "text-warn" : "text-dim"}`}
                    aria-label={p.never_rises_above_limit
                      ? `Never rises above your ${Math.round(altLimit)}° horizon limit tonight — peaks at ${p.max_alt.toFixed(0)}°`
                      : `Peak altitude tonight ${p.max_alt.toFixed(0)}°`}>
                    {p.never_rises_above_limit ? "low " : "↑"}{p.max_alt.toFixed(0)}°
                  </span>
                </span>
                </span>
                {/* ALL = the dense row. Three numbers the server already sends
                    and the UI used to drop: when it's highest, the span worth
                    shooting, and how far the Moon is. Each keeps its non-hue
                    glyph (↑ / [ ] / ☾⚠✕) so it survives the red night palette,
                    and carries its own aria-label because the glyphs alone are
                    not a name. BEGINNER never renders this. */}
                {!beginnerOnly && (
                  <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] mono text-dim">
                    <span aria-label={`Transits at ${fmtTime(p.transit_unix)}`}>
                      <span aria-hidden>↑</span> {fmtTime(p.transit_unix)}
                    </span>
                    <span
                      aria-label={
                        p.best_window
                          ? `Best window ${fmtWindow(p.best_window)}`
                          : "No best window tonight"
                      }
                    >
                      <span aria-hidden>[ ]</span> {fmtWindow(p.best_window)}
                    </span>
                    <span
                      className={
                        moonSepTone(p.moon_sep_deg) === "good" ? "text-dim"
                        : moonSepTone(p.moon_sep_deg) === "warn" ? "text-warn" : "text-bad"
                      }
                      aria-label={`Moon separation ${Math.round(p.moon_sep_deg)} degrees`}
                    >
                      <span aria-hidden>{moonSepGlyph(p.moon_sep_deg)}</span>{" "}
                      {Math.round(p.moon_sep_deg)}°
                    </span>
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default TonightPicker;
