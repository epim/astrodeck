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

// UX-2026-07-28: this panel used to present a bare, unbounded "Ranking tonight…"
// and — if the request died — a dead-end sentence with no way to try again. The
// endpoint really is the app's most expensive GET (it ephemeris-ranks the whole
// catalog), so on a Pi-class rig or a busy box the beginner's single best path
// ended on a spinner that never resolved into anything they could act on.
//
// Two honest bounds now: after SLOW_AFTER_S the wait says how long it has been
// waiting and when it will stop; when it does stop, it says so and offers a
// retry. The invariant is that this panel always reaches an answer or an
// explanation — never an open-ended spinner.
const SLOW_AFTER_S = 3;
// The budget api.ts's timeoutFor() applies to this path. It is the only number
// we PROMISE the user here, so it has to track that default — if the client
// budget moves, this sentence stops being true.
const GIVE_UP_S = 15;

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string; timedOut: boolean }
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

  // `attempt` is the retry seam: bumping it re-runs the effect, which is the
  // whole fetch. `waited` is the honest progress readout — a wait a user can
  // see the length of is a wait they can decide about.
  const [attempt, setAttempt] = useState(0);
  const [waited, setWaited] = useState(0);

  useEffect(() => {
    let alive = true;
    setState({ kind: "loading" });
    setWaited(0);
    const t0 = Date.now();
    const tick = setInterval(() => {
      if (alive) setWaited(Math.floor((Date.now() - t0) / 1000));
    }, 1000);
    const stop = () => clearInterval(tick);
    api
      .get<TonightResponse>(`/api/catalog/tonight?alt_limit=${encodeURIComponent(altLimit)}`)
      .then((res) => { stop(); if (alive) setState({ kind: "ok", res }); })
      .catch((e) => {
        stop();
        if (!alive) return;
        setState({
          kind: "error",
          message: e instanceof ApiError ? e.message : "couldn't rank tonight",
          timedOut: e instanceof ApiError && e.timedOut,
        });
      });
    return () => { alive = false; stop(); };
  }, [altLimit, attempt]);

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

      {/* Bounded wait. Under SLOW_AFTER_S this is byte for byte the old line —
          a fast rig should not be made to look like it is struggling. Past it,
          the wait states its own length and its own deadline. The seconds are
          aria-hidden so a screen reader isn't read a new number every second;
          the sentence beside them carries the same meaning, once. */}
      {state.kind === "loading" && (
        <p role="status" className="flex flex-col gap-1 text-xs text-dim px-1 py-2">
          <span>
            Ranking tonight…
            {waited >= SLOW_AFTER_S && (
              <span className="mono ml-1.5" aria-hidden>{waited}s</span>
            )}
          </span>
          {waited >= SLOW_AFTER_S && (
            <span className="text-[11px]">
              Still working — this works out where every catalog object will be
              tonight, which is the slow part on a small rig. It stops after{" "}
              {GIVE_UP_S}s and offers a retry rather than spinning forever.
            </span>
          )}
        </p>
      )}
      {/* Dead end no longer. It says what happened, distinguishes "no answer"
          from "nothing is up", and hands back a control. The icon keeps the
          state off hue alone for the red night palette. */}
      {state.kind === "error" && (
        <div role="alert" className="flex flex-col items-start gap-2 px-1 py-2">
          <p className="flex items-start gap-1.5 text-xs text-warn">
            <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
            <span>
              {state.timedOut
                ? `No answer in ${GIVE_UP_S}s, so tonight wasn't ranked.`
                : `Couldn't rank tonight — ${state.message}.`}{" "}
              <span className="text-dim">
                This list is empty for that reason, not because nothing is up.
              </span>
            </span>
          </p>
          <button type="button" className="btn" onClick={() => setAttempt((a) => a + 1)}>
            Try again
          </button>
        </div>
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
