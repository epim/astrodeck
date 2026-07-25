// TonightPicker — "What can I image tonight?" (NOV-3). Fetches the server-ranked,
// difficulty-tagged catalog (GET /api/catalog/tonight) and lets a first-timer pick
// a slam-dunk. A pick calls onPick(entry) -> AtlasView passes openFraming, exactly
// like CatalogSearch. Beginner filter (Easy+Moderate) defaults ON.

import { useEffect, useMemo, useState, type JSX } from "react";
import { api, ApiError } from "../../api";
import type { CatalogEntry, TonightPick, TonightResponse } from "../../types";
import { useStore } from "../../store";
import { Icon } from "../icons";
import { EmptyState } from "../ui";
import {
  difficultyLabel, difficultyGlyph, difficultyTone, isBeginnerFriendly,
} from "../../lib/difficulty";

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
  const [beginnerOnly, setBeginnerOnly] = useState(true);

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
      <div className="flex items-center justify-between gap-2">
        <span className="panel-title">What can I image tonight?</span>
        {/* segmented beginner filter (not a disable — a scope toggle) */}
        <div className="inline-flex text-xs border border-line2">
          {([["Beginner", true], ["All", false]] as const).map(([label, v]) => (
            <button key={label} type="button"
              aria-pressed={beginnerOnly === v}
              onClick={() => setBeginnerOnly(v)}
              className={`px-2 py-1 ${beginnerOnly === v ? "bg-raise text-ink" : "text-dim"}`}>
              {label}
            </button>
          ))}
        </div>
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
        <ul className="flex flex-col max-h-72 overflow-y-auto border border-line2 divide-y divide-line2">
          {shown.slice(0, 20).map((p) => (
            <li key={p.id}>
              <button type="button" onClick={() => onPick(toEntry(p))}
                className="w-full text-left px-3 py-2 text-xs hover:bg-raise transition-colors flex items-center justify-between gap-2 cursor-pointer">
                <span className="min-w-0 truncate">
                  <span className="mono text-accent">{p.id}</span> {p.name}
                  <span className="text-dim"> · {p.type}</span>
                </span>
                <span className="flex items-center gap-2 shrink-0">
                  {/* difficulty badge — glyph is primary, tone secondary (§8) */}
                  <span className={`inline-flex items-center gap-1 ${
                    difficultyTone(p.difficulty) === "good" ? "text-good"
                    : difficultyTone(p.difficulty) === "warn" ? "text-warn" : "text-bad"}`}
                    title={`${difficultyLabel(p.difficulty)} · surface brightness ${p.surface_brightness} mag/arcmin²${p.difficulty_source === "curated" ? " (curated)" : ""}`}>
                    <span aria-hidden>{difficultyGlyph(p.difficulty)}</span>
                    {difficultyLabel(p.difficulty)}
                  </span>
                  {/* peak-alt chip — CatalogSearch idiom (CatalogSearch.tsx:109) */}
                  <span className={`mono ${
                    p.never_rises_above_limit ? "text-warn"
                    : p.max_alt > 40 ? "text-good" : p.max_alt < 20 ? "text-warn" : "text-dim"}`}
                    title={p.never_rises_above_limit
                      ? `Never rises above ${Math.round(altLimit)}° tonight`
                      : "Peak altitude tonight"}>
                    {p.never_rises_above_limit ? "low " : "↑"}{p.max_alt.toFixed(0)}°
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default TonightPicker;
